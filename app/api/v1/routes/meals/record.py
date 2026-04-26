"""음식 기록 저장 라우트 (/save, /save-recommended)"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_authentication
from app.api.v1.routes import meals as _meals_pkg
from app.api.v1.schemas.common import ApiResponse
from app.api.v1.schemas.meals import (
    MealRecordResponse,
    SaveMealRequest,
    SaveRecommendedMealRequest,
)
from app.db.session import get_session
from app.services import meal_record_service

router = APIRouter()


@router.post("/save", response_model=ApiResponse[List[MealRecordResponse]])
async def save_meal_records(
    request: SaveMealRequest,
    session: AsyncSession = Depends(get_session),
    user_id: int = Depends(require_authentication)
) -> ApiResponse[List[MealRecordResponse]]:
    """
    음식 기록 저장 + 건강 점수 자동 계산

    1. UserFoodHistory에 음식 기록 저장
    2. FoodNutrient에서 영양소 정보 조회
    3. HealthScore 자동 계산 및 저장

    **Args:**
        request: 음식 기록 정보
        session: DB 세션

    **Returns:**
        저장된 음식 기록 + 건강 점수
    """
    try:
        saved_records = await meal_record_service.save_meal_records(
            request=request,
            session=session,
            user_id=user_id,
        )

        return ApiResponse(
            success=True,
            data=saved_records,
            message=f"✅ {len(saved_records)}개의 음식이 기록되었습니다!"
        )

    except Exception as e:
        await session.rollback()
        print(f"❌ 음식 기록 저장 실패: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"음식 기록 저장 중 오류 발생: {str(e)}")


@router.post("/save-recommended", response_model=ApiResponse[MealRecordResponse])
async def save_recommended_meal(
    request: SaveRecommendedMealRequest,
    session: AsyncSession = Depends(get_session),
    user_id: int = Depends(require_authentication)
) -> ApiResponse[MealRecordResponse]:
    """
    추천 음식 선택 및 저장

    **전체 플로우:**
    1. 사용된 식재료 처리 (is_used = True 또는 수량 감소)
    2. GPT로 음식의 칼로리 + 영양소 추론
    3. NRF9.3 점수 계산
    4. Food 테이블 확인/생성
    5. UserFoodHistory 저장
    6. HealthScore 저장

    **Args:**
        request: 추천 음식 저장 요청
        session: DB 세션

    **Returns:**
        저장된 음식 기록 + NRF9.3 점수
    """
    try:
        # llm_provider는 _meals_pkg 속성 조회로 전달해 테스트의
        # patch("app.api.v1.routes.meals.get_nutrition_llm", ...)가
        # 요청 시점에 정상 적용되도록 한다.
        response_data, success_message = await meal_record_service.save_recommended_meal(
            request=request,
            session=session,
            user_id=user_id,
            llm_provider=_meals_pkg.get_nutrition_llm,
        )

        return ApiResponse(
            success=True,
            data=response_data,
            message=success_message
        )

    except Exception as e:
        await session.rollback()
        print(f"❌ 추천 음식 저장 실패: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"추천 음식 저장 중 오류 발생: {str(e)}")
