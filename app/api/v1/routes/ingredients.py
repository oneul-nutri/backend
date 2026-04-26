"""식재료 관련 라우트"""
from datetime import datetime
from functools import lru_cache
from typing import List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas.common import ApiResponse
from app.api.v1.schemas.ingredient import (
    SaveIngredientsRequest,
    SaveIngredientsData,
    IngredientResponse,
    RecommendationData,
)
from app.api.dependencies import require_authentication
from app.core.config import get_settings
from app.db.models import UserIngredient, User, DiseaseAllergyProfile
from app.db.session import get_session
from app.services.roboflow_service import get_roboflow_service
from app.services.gpt_vision_service import get_gpt_vision_service

router = APIRouter()
settings = get_settings()


@lru_cache
def get_recommendation_llm() -> AsyncOpenAI:
    """식재료 추천용 OpenAI 클라이언트 팩토리.

    이름은 LangChain 시절 그대로 유지 — 테스트 patch 지점 하위 호환.
    모델/temperature는 호출 시점 인자로 지정.
    """
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY 환경 변수가 필요합니다.")
    return AsyncOpenAI(api_key=settings.openai_api_key)


async def save_major_conversation(session: AsyncSession, user: User, raw_text: str) -> None:
    """대화 내용을 LLM으로 요약해 User.major_conversation에 저장"""
    client = get_recommendation_llm()
    try:
        summary_prompt = f"다음 내용을 400자 이내 한국어로 요약하세요:\n\n{raw_text}"
        summary_response = await client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.7,
            messages=[
                {"role": "system", "content": "당신은 요약 도우미입니다."},
                {"role": "user", "content": summary_prompt},
            ],
        )
        summary = (summary_response.choices[0].message.content or "").strip()
    except Exception as exc:
        print(f"⚠️ 대화 요약 실패, 원문 일부 저장: {exc}")
        summary = raw_text[:400]
    user.major_conversation = summary[:2000]
    await session.commit()


@router.post("/save", response_model=ApiResponse[SaveIngredientsData])
async def save_ingredients(
    request: SaveIngredientsRequest,
    session: AsyncSession = Depends(get_session),
    user_id: int = Depends(require_authentication)
) -> ApiResponse[SaveIngredientsData]:
    """
    식재료 저장
    
    Roboflow로 분석한 식재료들을 데이터베이스에 저장합니다.
    이미 같은 이름의 식재료가 있으면 수량을 누적합니다.
    
    **Args:**
        request: 저장할 식재료 목록
        session: DB 세션
        
    **Returns:**
        저장된 식재료 정보
    """
    try:
        saved_ingredients = []
        
        for item in request.ingredients:
            # 같은 사용자의 같은 이름 식재료 조회 (is_used 상관없이)
            stmt = select(UserIngredient).where(
                UserIngredient.user_id == user_id,
                UserIngredient.ingredient_name == item.name
            ).order_by(UserIngredient.created_at.desc()).limit(1)
            
            result = await session.execute(stmt)
            existing_ingredient = result.scalar_one_or_none()
            
            if existing_ingredient:
                # 이미 존재하면 재활용 (is_used = False로 복구)
                if existing_ingredient.is_used:
                    existing_ingredient.is_used = False
                    print(f"  ♻️ {item.name}: 사용됨 → 재활용 (is_used = False)")
                else:
                    print(f"  ✅ {item.name}: 이미 보유 중 (스킵)")
                saved_ingredient = existing_ingredient
            else:
                # 새로 추가
                new_ingredient = UserIngredient(
                    user_id=user_id,
                    ingredient_name=item.name,
                    is_used=False
                )
                session.add(new_ingredient)
                await session.flush()  # ID 생성을 위해 flush
                await session.refresh(new_ingredient)  # 모든 필드 다시 로드
                saved_ingredient = new_ingredient
                print(f"  ➕ {item.name}: 새로 추가")
            
            saved_ingredients.append(IngredientResponse(
                ingredient_id=saved_ingredient.ingredient_id,
                user_id=saved_ingredient.user_id,
                ingredient_name=saved_ingredient.ingredient_name,
                created_at=saved_ingredient.created_at,
                is_used=saved_ingredient.is_used
            ))
        
        await session.commit()
        
        return ApiResponse(
            success=True,
            data=SaveIngredientsData(
                saved_count=len(saved_ingredients),
                ingredients=saved_ingredients
            ),
            message=f"✅ {len(saved_ingredients)}개의 식재료가 저장되었습니다!"
        )
        
    except Exception as e:
        await session.rollback()
        print(f"❌ 식재료 저장 실패: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"식재료 저장 중 오류가 발생했습니다: {str(e)}")


@router.get("/list", response_model=ApiResponse[List[IngredientResponse]])
async def get_ingredients(
    session: AsyncSession = Depends(get_session),
    user_id: int = Depends(require_authentication)
) -> ApiResponse[List[IngredientResponse]]:
    """
    저장된 식재료 목록 조회
    
    현재 사용자가 저장한 식재료 중 아직 사용하지 않은 것들을 조회합니다.
    
    **Args:**
        session: DB 세션
        
    **Returns:**
        식재료 목록
    """
    try:
        stmt = select(UserIngredient).where(
            UserIngredient.user_id == user_id,
            UserIngredient.is_used == False
        ).order_by(UserIngredient.created_at.desc())
        
        result = await session.execute(stmt)
        ingredients = result.scalars().all()
        
        ingredient_list = [
            IngredientResponse(
                ingredient_id=ing.ingredient_id,
                user_id=ing.user_id,
                ingredient_name=ing.ingredient_name,
                created_at=ing.created_at,
                is_used=ing.is_used
            )
            for ing in ingredients
        ]
        
        return ApiResponse(
            success=True,
            data=ingredient_list,
            message=f"✅ {len(ingredient_list)}개의 식재료를 조회했습니다."
        )
        
    except Exception as e:
        print(f"❌ 식재료 조회 실패: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"식재료 조회 중 오류가 발생했습니다: {str(e)}")


@router.get("/my-ingredients", response_model=ApiResponse[List[IngredientResponse]])
async def get_my_ingredients(
    session: AsyncSession = Depends(get_session),
    user_id: int = Depends(require_authentication)
) -> ApiResponse[List[IngredientResponse]]:
    """
    내 보유 식재료 목록 조회 (사용하지 않은 것만)
    
    현재 사용자가 저장한 식재료 중 아직 사용하지 않은 것들을 조회합니다.
    프론트엔드에서 레시피 추천 시 보유 재료 확인용으로 사용됩니다.
    
    **Args:**
        session: DB 세션
        
    **Returns:**
        식재료 목록
    """
    try:
        print(f"🔍 보유 식재료 조회 요청: user_id={user_id}")
        
        stmt = select(UserIngredient).where(
            UserIngredient.user_id == user_id,
            UserIngredient.is_used == False
        ).order_by(UserIngredient.created_at.desc())
        
        result = await session.execute(stmt)
        ingredients = result.scalars().all()
        
        print(f"📦 조회된 식재료: {len(ingredients)}개")
        for ing in ingredients:
            print(f"  - {ing.ingredient_name} (is_used={ing.is_used})")
        
        ingredient_list = [
            IngredientResponse(
                ingredient_id=ing.ingredient_id,
                user_id=ing.user_id,
                ingredient_name=ing.ingredient_name,
                created_at=ing.created_at,
                is_used=ing.is_used
            )
            for ing in ingredients
        ]
        
        return ApiResponse(
            success=True,
            data=ingredient_list,
            message=f"✅ {len(ingredient_list)}개의 보유 식재료를 조회했습니다."
        )
        
    except Exception as e:
        print(f"❌ 보유 식재료 조회 실패: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"보유 식재료 조회 중 오류가 발생했습니다: {str(e)}")


@router.get("/recommendations", response_model=ApiResponse[RecommendationData])
async def get_food_recommendations(
    session: AsyncSession = Depends(get_session),
    user_id: int = Depends(require_authentication)
) -> ApiResponse[RecommendationData]:
    """
    보유 재료 기반 음식 추천 (전략 패턴 적용)
    
    **Args:**
        session: DB 세션
        
    **Returns:**
        LLM 생성 음식 추천
    """
    from app.services.recipe_recommendation_service import get_recommendation_strategy
    
    try:
        # 1. 사용자 정보 조회
        user_stmt = select(User).where(User.user_id == user_id)
        user_result = await session.execute(user_stmt)
        user = user_result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
        
        # 2. 알러지/질병 조회
        profile_stmt = select(DiseaseAllergyProfile).where(
            DiseaseAllergyProfile.user_id == user_id
        )
        profile_result = await session.execute(profile_stmt)
        profiles = profile_result.scalars().all()
        
        allergies = [p.allergy_name for p in profiles if p.allergy_name]
        diseases = [p.disease_name for p in profiles if p.disease_name]
        
        # 3. 미사용 식재료 조회
        ingredient_stmt = select(UserIngredient).where(
            UserIngredient.user_id == user_id,
            UserIngredient.is_used == False
        ).order_by(UserIngredient.created_at.desc())
        
        ingredient_result = await session.execute(ingredient_stmt)
        ingredients = ingredient_result.scalars().all()
        
        # 재료 데이터 준비 (count 제거됨)
        user_ingredients = [ing.ingredient_name for ing in ingredients]
        
        if not ingredients:
            # 재료 없을 때 기본 레시피
            import json
            default_recipe = {
                "foods": [{
                    "name": "기본 그린 샐러드",
                    "description": "간단한 채소 샐러드. 재료 추가 시 더 다양한 추천!",
                    "calories": 150,
                    "recommended_meal_type": "lunch",
                    "ingredients": ["양상추", "방울토마토", "오이", "올리브오일"],
                    "missing_ingredients": [],
                    "steps": ["재료 씻기", "썰기", "드레싱 뿌리기", "완성"]
                }]
            }
            recommendation_text = json.dumps(default_recipe, ensure_ascii=False)
            
            return ApiResponse(
                success=True,
                data=RecommendationData(
                    recommendations=recommendation_text,
                    ingredients_used=[],
                    total_ingredients=0
                ),
                message="✅ 기본 샐러드 🥗"
            )
        
        # 4. 전략 패턴 (토큰 효율화)
        health_info_dict = {
            'goal': user.health_goal,
            'age': user.age,
            'weight': user.weight,
            'allergies': allergies,
            'diseases': diseases
        }
        
        strategy = get_recommendation_strategy()
        prompt = strategy.build_prompt(user_ingredients, health_info_dict)
        
        # 5. LLM 호출
        try:
            client = get_recommendation_llm()
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.7,
                messages=[
                    {"role": "system", "content": "전문 영양사. JSON만 응답."},
                    {"role": "user", "content": prompt},
                ],
            )
            recommendation_text = response.choices[0].message.content or ""
            await save_major_conversation(session, user, recommendation_text)
            
        except Exception as e:
            print(f"⚠️ LLM 실패, 폴백: {e}")
            
            # 폴백 (간소화)
            import json
            ingredients_list = [name for name, _ in user_ingredients]
            fallback = {"foods": [{
                "name": f"{ingredients_list[0]} 볶음" if ingredients_list else "샐러드",
                "description": "간단한 요리",
                "calories": 200,
                "recommended_meal_type": "lunch",
                "ingredients": ingredients_list[:3] + ["소금", "기름"],
                "missing_ingredients": [],
                "steps": ["재료 준비", "볶기", "완성"]
            }]}
            recommendation_text = json.dumps(fallback, ensure_ascii=False)
            await save_major_conversation(session, user, recommendation_text)
        
        # 메시지 (간소화)
        n = len(ingredients)
        msg_map = {0: "기본 🥗", 1: f"{n}개 간단 🌱", 2: f"{n}개 활용 🥗"}
        response_message = msg_map.get(n, f"{n}개 맞춤 🍳")
        
        return ApiResponse(
            success=True,
            data=RecommendationData(
                recommendations=recommendation_text,
                ingredients_used=[ing.ingredient_name for ing in ingredients],
                total_ingredients=len(ingredients)
            ),
            message=response_message
        )
        
    except Exception as e:
        print(f"❌ 음식 추천 생성 실패: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"음식 추천 생성 중 오류가 발생했습니다: {str(e)}")


@router.post("/analyze-with-roboflow-gpt")
async def analyze_ingredients_with_roboflow_gpt(
    file: UploadFile = File(...)
):
    """
    Roboflow + GPT Vision으로 식재료 분석
    
    1. Roboflow로 Bounding Box 탐지
    2. 각 Box로 이미지 Crop
    3. GPT Vision으로 정확한 재료명 확인
    
    **Args:**
        file: 업로드된 이미지 파일
        
    **Returns:**
        분석된 식재료 리스트
    """
    try:
        # 이미지 읽기
        image_bytes = await file.read()
        
        # Roboflow 서비스
        roboflow_service = get_roboflow_service()
        gpt_service = get_gpt_vision_service()
        
        # 1. Roboflow로 객체 탐지
        detections = roboflow_service.detect_ingredients(image_bytes)
        
        if not detections:
            return ApiResponse(
                success=True,
                data={
                    "ingredients": [],
                    "message": "식재료를 찾을 수 없습니다. 다른 이미지를 업로드해주세요."
                },
                message="식재료가 탐지되지 않았습니다."
            )
        
        # 2. 원본 이미지에 Bounding Box 그리기
        roboflow_hints = [det.get("class", det.get("className", "-")) for det in detections]
        image_with_boxes = roboflow_service.draw_bboxes_on_image(image_bytes, detections)
        
        # 3. GPT Vision으로 통합 분석
        identified_ingredients = await gpt_service.analyze_ingredients_with_boxes(
            image_with_boxes,
            len(detections),
            roboflow_hints
        )
        
        # 결과 조합
        analyzed_ingredients = []
        
        for i in range(len(detections)):
            detection = detections[i]
            ingredient_name = identified_ingredients[i] if i < len(identified_ingredients) else "알 수 없음"
            
            roboflow_class = detection.get("class", detection.get("className", "-"))
            confidence = detection.get("confidence", 0)
            
            analyzed_ingredients.append({
                "name": ingredient_name,
                "roboflow_prediction": roboflow_class,
                "confidence": confidence,
                "bbox": {
                    "x": detection.get("x"),
                    "y": detection.get("y"),
                    "width": detection.get("width"),
                    "height": detection.get("height")
                }
            })
        
        # GPT Vision이 추가로 발견한 객체 (Few-shot 결과)
        if len(identified_ingredients) > len(detections):
            for i in range(len(detections), len(identified_ingredients)):
                additional_ingredient = identified_ingredients[i]
                
                analyzed_ingredients.append({
                    "name": additional_ingredient,
                    "roboflow_prediction": "-",
                    "confidence": 1.0,
                    "bbox": None
                })
        
        # 결과 출력
        print(f"✅ 식재료 분석 완료: {len(analyzed_ingredients)}개")
        
        return ApiResponse(
            success=True,
            data={
                "ingredients": analyzed_ingredients,
                "total_detected": len(detections),
                "total_analyzed": len(analyzed_ingredients)
            },
            message=f"✅ {len(analyzed_ingredients)}개의 식재료가 분석되었습니다!"
        )
        
    except Exception as e:
        print(f"❌ 식재료 분석 실패: {e}")
        raise HTTPException(status_code=500, detail=f"식재료 분석 중 오류가 발생했습니다: {str(e)}")
