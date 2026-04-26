"""음식 섭취 이력 라우트 (/history, /history/{id}, /most-eaten)"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_authentication
from app.api.v1.schemas.common import ApiResponse
from app.api.v1.schemas.meals import MealRecordResponse, MostEatenFood
from app.db.session import get_session
from app.services import meal_history_service

router = APIRouter()


@router.get("/history", response_model=ApiResponse[List[MealRecordResponse]])
async def get_meal_history(
    limit: int = 20,
    offset: int = 0,
    include_diet_plans: bool = True,
    session: AsyncSession = Depends(get_session),
    user_id: int = Depends(require_authentication)
) -> ApiResponse[List[MealRecordResponse]]:
    """
    음식 섭취 기록 조회 (추천 식단 포함)

    **Args:**
        limit: 조회 개수
        offset: 오프셋
        include_diet_plans: 추천 식단 포함 여부 (기본 True)
        session: DB 세션

    **Returns:**
        음식 기록 목록 (UserFoodHistory + DietPlanMeal 통합)
    """
    try:
        records = await meal_history_service.get_meal_history(
            session=session,
            user_id=user_id,
            limit=limit,
            offset=offset,
            include_diet_plans=include_diet_plans,
        )

        return ApiResponse(
            success=True,
            data=records,
            message=f"✅ {len(records)}개의 기록 조회 완료"
        )

    except Exception as e:
        print(f"❌ 음식 기록 조회 실패: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"기록 조회 중 오류 발생: {str(e)}")


@router.delete("/history/{history_id}", response_model=ApiResponse[dict])
async def delete_meal_history(
    history_id: int,
    session: AsyncSession = Depends(get_session),
    user_id: int = Depends(require_authentication)
) -> ApiResponse[dict]:
    """
    음식 섭취 기록 삭제

    **Args:**
        history_id: 삭제할 기록 ID
        session: DB 세션

    **Returns:**
        삭제 결과
    """
    try:
        deleted_id, food_name = await meal_history_service.delete_meal_history(
            session=session,
            user_id=user_id,
            history_id=history_id,
        )

        return ApiResponse(
            success=True,
            data={"history_id": deleted_id, "deleted": True},
            message=f"✅ '{food_name}' 기록이 삭제되었습니다."
        )

    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        print(f"❌ 음식 기록 삭제 실패: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"기록 삭제 중 오류 발생: {str(e)}")


@router.get("/most-eaten", response_model=ApiResponse[List[MostEatenFood]])
async def get_most_eaten_foods(
    limit: int = 4,
    session: AsyncSession = Depends(get_session),
    user_id: int = Depends(require_authentication)
) -> ApiResponse[List[MostEatenFood]]:
    """
    자주 먹은 음식 TOP N

    **처리 과정:**
    1. UserFoodHistory에서 food_id별 카운트
    2. 내림차순 정렬
    3. 상위 N개 반환

    **Args:**
        limit: 반환할 음식 개수 (기본 4개)
        session: DB 세션
        user_id: 사용자 ID

    **Returns:**
        자주 먹은 음식 목록
    """
    try:
        most_eaten_list = await meal_history_service.get_most_eaten_foods(
            session=session,
            user_id=user_id,
            limit=limit,
        )

        return ApiResponse(
            success=True,
            data=most_eaten_list,
            message=f"✅ 자주 먹은 음식 {len(most_eaten_list)}개를 조회했습니다."
        )

    except Exception as e:
        print(f"❌ 자주 먹은 음식 조회 실패: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"자주 먹은 음식 조회 중 오류 발생: {str(e)}")
