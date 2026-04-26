"""식사 기록 저장 관련 비즈니스 로직 (meals.py 라우트에서 분리)"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas.meals import (
    MealRecordResponse,
    SaveMealRequest,
    SaveRecommendedMealRequest,
)
from app.db.models import Food, UserFoodHistory, UserIngredient
from app.db.models_food_nutrients import FoodNutrient
from app.db.models_user_contributed import UserContributedFood
from app.services import food_matching_service as _food_matching_module
from app.services.health_score_service import (
    calculate_korean_nutrition_score,
    calculate_nrf93_score,
    create_health_score,
)


async def save_meal_records(
    request: SaveMealRequest,
    session: AsyncSession,
    user_id: int,
) -> List[MealRecordResponse]:
    """
    음식 기록 저장 + 건강 점수 자동 계산

    1. UserFoodHistory에 음식 기록 저장
    2. FoodNutrient에서 영양소 정보 조회
    3. HealthScore 자동 계산 및 저장
    """
    saved_records: List[MealRecordResponse] = []

    for food_item in request.foods:
        # 1. UserFoodHistory 저장
        history = UserFoodHistory(
            user_id=user_id,
            food_id=food_item.food_id,
            food_name=food_item.food_name,
            consumed_at=datetime.now(),
            portion_size_g=food_item.portion_size_g
            # memo=request.memo  # 임시로 제거 (DB에 memo 컬럼 없음)
        )
        session.add(history)
        await session.flush()  # history_id 생성
        await session.refresh(history)

        # 2. FoodNutrient에서 영양소 정보 조회
        nutrient_stmt = select(FoodNutrient).where(
            FoodNutrient.food_id == food_item.food_id
        )
        nutrient_result = await session.execute(nutrient_stmt)
        nutrient = nutrient_result.scalar_one_or_none()

        # 3. 건강 점수 계산
        health_score_data = None
        if nutrient:
            # 한국식 영양 점수 계산
            score_result = await calculate_korean_nutrition_score(
                protein=nutrient.protein or 0,
                fiber=nutrient.fiber or 0,
                calcium=nutrient.calcium or 0,
                iron=nutrient.iron or 0,
                sodium=nutrient.sodium or 0,
                sugar=nutrient.added_sugar or 0,
                saturated_fat=nutrient.saturated_fat or 0
            )

            # 4. HealthScore 저장
            health_score_obj = await create_health_score(
                session=session,
                history_id=history.history_id,
                user_id=user_id,
                food_id=food_item.food_id,
                reference_value=int(nutrient.reference_value) if nutrient.reference_value else None,
                kcal=food_item.calories,
                positive_score=score_result["positive_score"],
                negative_score=score_result["negative_score"],
                final_score=score_result["final_score"],
                food_grade=score_result["food_grade"],
                calc_method=score_result["calc_method"]
            )

            health_score_data = {
                "final_score": health_score_obj.final_score,
                "food_grade": health_score_obj.food_grade
            }

        saved_records.append(MealRecordResponse(
            history_id=history.history_id,
            user_id=history.user_id,
            food_id=history.food_id,
            food_name=history.food_name,
            consumed_at=history.consumed_at,
            portion_size_g=history.portion_size_g,
            calories=food_item.calories,
            health_score=health_score_data["final_score"] if health_score_data else None,
            food_grade=health_score_data["food_grade"] if health_score_data else None
        ))

    await session.commit()
    return saved_records


async def save_recommended_meal(
    request: SaveRecommendedMealRequest,
    session: AsyncSession,
    user_id: int,
    llm_provider: Callable[[], Any],
) -> Tuple[MealRecordResponse, str]:
    """
    추천 음식 선택 및 저장

    **전체 플로우:**
    1. 사용된 식재료 처리 (is_used = True 또는 수량 감소)
    2. GPT로 음식의 칼로리 + 영양소 추론
    3. NRF9.3 점수 계산
    4. Food 테이블 확인/생성
    5. UserFoodHistory 저장
    6. HealthScore 저장

    Returns:
        (MealRecordResponse, success_message) 튜플
    """
    # ========== STEP 0: 음식명 정규화 ==========
    normalized_food_name = _food_matching_module.normalize_food_name(
        request.food_name, request.ingredients_used
    )
    if normalized_food_name != request.food_name:
        print(f"🔄 음식명 정규화: '{request.food_name}' → '{normalized_food_name}'")
        request.food_name = normalized_food_name

    # ========== STEP 1: 식재료 사용 처리 ==========
    # ingredients_with_quantity 우선, 없으면 레거시 방식
    missing_ingredients: List[str] = []
    if request.ingredients_with_quantity:
        print(f"🥕 STEP 1: 식재료 사용 처리 (체크된 재료 = DB에서 완전 삭제)")
        for ingredient_usage in request.ingredients_with_quantity:
            ingredient_name = ingredient_usage.name

            stmt = select(UserIngredient).where(
                UserIngredient.user_id == user_id,
                UserIngredient.ingredient_name == ingredient_name,
                UserIngredient.is_used == False
            ).order_by(UserIngredient.created_at.asc())  # 오래된 것부터

            result = await session.execute(stmt)
            ingredient = result.scalar_one_or_none()

            if ingredient:
                # 체크된 재료는 DB에서 완전 삭제 (DELETE)
                await session.delete(ingredient)
                print(f"  🗑️ {ingredient_name}: DB에서 완전 삭제!")
            else:
                print(f"  ⚠️ {ingredient_name}: 식재료 테이블에 없음")
                missing_ingredients.append(ingredient_name)

        # 없는 재료가 있으면 경고 메시지
        if missing_ingredients:
            print(f"  ⚠️ 현재 식재료에 없는 재료: {', '.join(missing_ingredients)}")
    else:
        # 레거시: ingredients_used 배열 (체크 없이 저장된 경우)
        print(f"🥕 STEP 1: 식재료 사용 처리 (레거시) - {request.ingredients_used}")
        for ingredient_name in request.ingredients_used:
            stmt = select(UserIngredient).where(
                UserIngredient.user_id == user_id,
                UserIngredient.ingredient_name == ingredient_name,
                UserIngredient.is_used == False
            ).order_by(UserIngredient.created_at.asc())  # 오래된 것부터

            result = await session.execute(stmt)
            ingredient = result.scalar_one_or_none()

            if ingredient:
                # DB에서 완전 삭제
                await session.delete(ingredient)
                print(f"  🗑️ {ingredient_name}: DB에서 완전 삭제!")
            else:
                print(f"  ⚠️ {ingredient_name}: UserIngredient에 없음 (건너뜀)")

    await session.flush()

    # ========== STEP 2: GPT로 영양소 추론 ==========
    print(f"🤖 STEP 2: GPT로 {request.food_name}의 영양소 추론")

    try:
        client = llm_provider()

        prompt = f"""당신은 영양학 전문가입니다. 다음 음식의 영양 정보를 JSON 형식으로 추정해주세요.

음식: {request.food_name}
섭취량: {request.portion_size_g}g

다음 영양소를 추정해서 JSON 형식으로 반환해주세요:
{{
  "calories": 칼로리(kcal),
  "protein_g": 단백질(g),
  "carb_g": 탄수화물(g),
  "fat_g": 지방(g),
  "fiber_g": 식이섬유(g),
  "vitamin_a_ug": 비타민A(μg RAE),
  "vitamin_c_mg": 비타민C(mg),
  "vitamin_e_mg": 비타민E(mg),
  "calcium_mg": 칼슘(mg),
  "iron_mg": 철분(mg),
  "potassium_mg": 칼륨(mg),
  "magnesium_mg": 마그네슘(mg),
  "saturated_fat_g": 포화지방(g),
  "added_sugar_g": 첨가당(g),
  "sodium_mg": 나트륨(mg)
}}

**중요:** 반드시 JSON 형식만 반환하고, 다른 설명은 포함하지 마세요.
영양소가 미미하거나 없으면 0으로 표시하세요."""

        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.3,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are a nutrition expert. Always respond in valid JSON format only."},
                {"role": "user", "content": prompt},
            ],
        )
        nutrition_data = json.loads(response.choices[0].message.content)
        print(f"  ✅ 영양소 추론 완료: {nutrition_data['calories']}kcal")

    except Exception as e:
        print(f"  ⚠️ GPT 추론 실패, 기본값 사용: {e}")
        # 폴백: 기본값
        nutrition_data = {
            "calories": 400,
            "protein_g": 15.0,
            "carb_g": 50.0,
            "fat_g": 10.0,
            "fiber_g": 3.0,
            "vitamin_a_ug": 100.0,
            "vitamin_c_mg": 10.0,
            "vitamin_e_mg": 2.0,
            "calcium_mg": 100.0,
            "iron_mg": 2.0,
            "potassium_mg": 300.0,
            "magnesium_mg": 50.0,
            "saturated_fat_g": 3.0,
            "added_sugar_g": 5.0,
            "sodium_mg": 800.0
        }

    # 없는 재료가 있으면 사용자에게 알림
    if missing_ingredients:
        missing_msg: Optional[str] = f"⚠️ 다음 재료는 현재 식재료에 없습니다: {', '.join(missing_ingredients)}"
        # 계속 진행하되 메시지 포함
    else:
        missing_msg = None

    # ========== STEP 3: NRF9.3 점수 계산 ==========
    print(f"📊 STEP 3: NRF9.3 점수 계산")
    score_result = await calculate_nrf93_score(
        protein_g=nutrition_data["protein_g"],
        fiber_g=nutrition_data["fiber_g"],
        vitamin_a_ug=nutrition_data["vitamin_a_ug"],
        vitamin_c_mg=nutrition_data["vitamin_c_mg"],
        vitamin_e_mg=nutrition_data["vitamin_e_mg"],
        calcium_mg=nutrition_data["calcium_mg"],
        iron_mg=nutrition_data["iron_mg"],
        potassium_mg=nutrition_data["potassium_mg"],
        magnesium_mg=nutrition_data["magnesium_mg"],
        saturated_fat_g=nutrition_data["saturated_fat_g"],
        added_sugar_g=nutrition_data["added_sugar_g"],
        sodium_mg=nutrition_data["sodium_mg"],
        reference_value_g=request.portion_size_g
    )
    print(f"  ✅ NRF9.3 점수: {score_result['final_score']}, 등급: {score_result['food_grade']}")

    # ========== STEP 4: food_nutrients에서 실제 음식 매칭 ==========
    print(f"🍽️ STEP 4: food_nutrients 매칭 처리")

    matching_service = _food_matching_module.get_food_matching_service()

    # DB에서 실제 음식 매칭 (user_id 전달)
    matched_food_nutrient = await matching_service.match_food_to_db(
        session=session,
        food_name=request.food_name,
        ingredients=request.ingredients_used if request.ingredients_used else [],
        food_class_hint=None,
        user_id=user_id
    )

    # 매칭된 food_id 사용
    if matched_food_nutrient:
        actual_food_id = matched_food_nutrient.food_id
        actual_food_class_1 = getattr(matched_food_nutrient, 'food_class1', None)
        actual_food_class_2 = getattr(matched_food_nutrient, 'food_class2', None)

        # FoodNutrient인지 UserContributedFood인지 확인
        if isinstance(matched_food_nutrient, FoodNutrient):
            print(f"✅ food_nutrients 매칭 성공: {actual_food_id} - {matched_food_nutrient.nutrient_name}")
        else:
            print(f"✅ user_contributed_foods 매칭 성공: {actual_food_id} - {matched_food_nutrient.food_name}")
    else:
        # 매칭 실패 시: user_contributed_foods에 새로 추가
        print(f"⚠️ 매칭 실패, user_contributed_foods에 새로 추가")

        # 재료 문자열 변환
        ingredients_str = ", ".join(request.ingredients_used) if request.ingredients_used else None

        # 새로운 food_id 생성
        actual_food_id = f"USER_{user_id}_{int(datetime.now().timestamp())}"[:200]
        actual_food_class_1 = "사용자추가"
        actual_food_class_2 = request.ingredients_used[0] if request.ingredients_used else None

        # user_contributed_foods에 추가
        new_contributed_food = UserContributedFood(
            food_id=actual_food_id,
            user_id=user_id,
            food_name=request.food_name,
            nutrient_name=request.food_name,
            food_class1=actual_food_class_1,
            food_class2=actual_food_class_2,
            ingredients=ingredients_str,
            unit="g",
            reference_value=request.portion_size_g,
            protein=nutrition_data.get("protein", 0),
            carb=nutrition_data.get("carb", 0),
            fat=nutrition_data.get("fat", 0),
            fiber=nutrition_data.get("fiber", 0),
            vitamin_a=nutrition_data.get("vitamin_a", 0),
            vitamin_c=nutrition_data.get("vitamin_c", 0),
            calcium=nutrition_data.get("calcium", 0),
            iron=nutrition_data.get("iron", 0),
            potassium=nutrition_data.get("potassium", 0),
            magnesium=nutrition_data.get("magnesium", 0),
            saturated_fat=nutrition_data.get("saturated_fat", 0),
            added_sugar=nutrition_data.get("added_sugar", 0),
            sodium=nutrition_data.get("sodium", 0),
            usage_count=1
        )
        session.add(new_contributed_food)
        await session.flush()

        print(f"✅ user_contributed_foods에 저장: {actual_food_id} - {request.food_name}")

    # Food 테이블 확인/생성
    food_stmt = select(Food).where(Food.food_id == actual_food_id)
    food_result = await session.execute(food_stmt)
    food = food_result.scalar_one_or_none()

    if not food:
        # 사용한 재료 문자열로 변환 (콤마 구분)
        ingredients_str = ", ".join(request.ingredients_used) if request.ingredients_used else None

        # 새로 생성
        food = Food(
            food_id=actual_food_id,
            food_name=request.food_name,
            category="추천음식",
            food_class_1=actual_food_class_1,
            food_class_2=actual_food_class_2,
            ingredients=ingredients_str
        )
        session.add(food)
        await session.flush()
        print(f"  ✅ Food 생성: {actual_food_id}, 재료: {ingredients_str}")
    else:
        # 이미 존재하면 그대로 사용 (이름이 달라도 ID가 같으면 같은 음식으로 간주)
        print(f"  ✅ Food 이미 존재: {actual_food_id} (기존 이름: {food.food_name})")

    food_id = actual_food_id

    # ========== STEP 5: UserFoodHistory 저장 ==========
    print(f"📝 STEP 5: UserFoodHistory 저장")

    # 🔍 디버깅: DB 스키마 확인 (AsyncEngine용)
    def get_table_columns(sync_conn):
        from sqlalchemy import inspect as sync_inspect
        inspector = sync_inspect(sync_conn)
        return inspector.get_columns("UserFoodHistory")

    columns = await session.connection(execution_options={"isolation_level": "AUTOCOMMIT"})
    column_info = await columns.run_sync(get_table_columns)
    print(f"🔍 DB 실제 컬럼 목록: {[col['name'] for col in column_info]}")

    print(f"📝 STEP 5: UserFoodHistory 저장 - meal_type={request.meal_type}")
    history = UserFoodHistory(
        user_id=user_id,
        food_id=food_id,
        food_name=request.food_name,
        consumed_at=datetime.now(),
        portion_size_g=request.portion_size_g,
        meal_type=request.meal_type  # ✨ meal_type 추가
        # memo=request.memo  # 임시로 제거 (DB에 memo 컬럼 없음)
    )
    session.add(history)
    await session.flush()
    await session.refresh(history)
    print(f"  ✅ History ID: {history.history_id}")

    # ========== STEP 6: HealthScore 저장 ==========
    print(f"💯 STEP 6: HealthScore 저장")
    health_score_obj = await create_health_score(
        session=session,
        history_id=history.history_id,
        user_id=user_id,
        food_id=food_id,
        reference_value=int(request.portion_size_g),
        kcal=nutrition_data["calories"],
        positive_score=int(score_result["positive_score"]),
        negative_score=int(score_result["negative_score"]),
        final_score=int(score_result["final_score"]),
        food_grade=score_result["food_grade"],
        calc_method=score_result["calc_method"]
    )
    print(f"  ✅ HealthScore 저장 완료")

    await session.commit()

    # ========== 응답 생성 ==========
    response_data = MealRecordResponse(
        history_id=history.history_id,
        user_id=history.user_id,
        food_id=history.food_id,
        food_name=history.food_name,
        consumed_at=history.consumed_at,
        portion_size_g=history.portion_size_g,
        calories=nutrition_data["calories"],
        health_score=health_score_obj.final_score,
        food_grade=health_score_obj.food_grade
    )

    # 메시지 생성
    success_message = f"✅ {request.food_name} 기록 완료! NRF9.3 점수: {score_result['final_score']:.1f}점"
    if missing_msg:
        success_message += f"\n\n{missing_msg}"

    return response_data, success_message
