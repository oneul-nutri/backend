"""대시보드/점수 통계 라우트 (/dashboard-stats, /score-detail)"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_authentication
from app.api.v1.schemas.common import ApiResponse
from app.api.v1.schemas.meals import DashboardStatsResponse, ScoreDetailResponse
from app.db.session import get_session
from app.services import meal_stats_service

router = APIRouter()


@router.get("/dashboard-stats", response_model=ApiResponse[DashboardStatsResponse])
async def get_dashboard_stats(
    session: AsyncSession = Depends(get_session),
    user_id: int = Depends(require_authentication)
) -> ApiResponse[DashboardStatsResponse]:
    """
    대시보드 통계 조회

    - 오늘/이번 주 총 칼로리
    - 평균 건강 점수
    - 자주 먹는 음식 Top 5
    - 최근 7일 일일 칼로리
    - 영양소 밸런스

    **Args:**
        session: DB 세션

    **Returns:**
        대시보드 통계 데이터
    """
    try:
        stats = await meal_stats_service.get_dashboard_stats(
            session=session,
            user_id=user_id,
        )

        return ApiResponse(
            success=True,
            data=stats,
            message="✅ 대시보드 통계 조회 완료"
        )

    except Exception as e:
        print(f"❌ 대시보드 통계 조회 실패: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"통계 조회 중 오류 발생: {str(e)}")


@router.get("/score-detail", response_model=ApiResponse[ScoreDetailResponse])
async def get_score_detail(
    session: AsyncSession = Depends(get_session),
    user_id: int = Depends(require_authentication)
) -> ApiResponse[ScoreDetailResponse]:
    """
    상세 점수 현황 조회

    - 오늘 전체 점수
    - 전날 대비 점수 변화
    - 카테고리별 점수 (칼로리 균형, 영양소 균형, 식사 패턴 등)
    - 주간 트렌드

    **Args:**
        session: DB 세션

    **Returns:**
        상세 점수 현황 데이터
    """
    try:
        score_detail = await meal_stats_service.get_score_detail(
            session=session,
            user_id=user_id,
        )

        return ApiResponse(
            success=True,
            data=score_detail,
            message="✅ 상세 점수 현황 조회 완료"
        )

    except Exception as e:
        print(f"❌ 상세 점수 현황 조회 실패: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"상세 점수 현황 조회 중 오류 발생: {str(e)}")
