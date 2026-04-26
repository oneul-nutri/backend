"""대시보드/점수 통계 관련 비즈니스 로직 (meals.py 라우트에서 분리)"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas.meals import (
    CategoryScore,
    DashboardStatsResponse,
    ScoreDetailResponse,
)
from app.db.models import HealthScore, User, UserFoodHistory
from app.db.models_food_nutrients import FoodNutrient
from app.services.health_score_service import calculate_daily_comprehensive_score
from app.services.user_service import calculate_daily_calories


async def get_dashboard_stats(
    session: AsyncSession,
    user_id: int,
) -> DashboardStatsResponse:
    """
    대시보드 통계 조회

    - 오늘/이번 주 총 칼로리
    - 평균 건강 점수
    - 자주 먹는 음식 Top 5
    - 최근 7일 일일 칼로리
    - 영양소 밸런스
    """
    today = datetime.now().date()

    # 0. 사용자 정보 조회 및 목표 칼로리 계산
    user_stmt = select(User).where(User.user_id == user_id)
    user_result = await session.execute(user_stmt)
    user = user_result.scalar_one_or_none()

    target_calories = calculate_daily_calories(user) if user else 2000

    # 1. 오늘 총 칼로리
    today_stmt = select(func.sum(HealthScore.kcal)).where(
        and_(
            HealthScore.user_id == user_id,
            func.date(UserFoodHistory.consumed_at) == today
        )
    ).join(UserFoodHistory, HealthScore.history_id == UserFoodHistory.history_id)

    today_result = await session.execute(today_stmt)
    total_calories_today = today_result.scalar() or 0

    # 2. 이번 주 총 칼로리 (일요일 시작)
    # TODO: 주 시작일 계산 로직 추가

    # 3. 오늘 평균 건강 점수 (종합 점수로 개선)
    today_avg_stmt = select(func.avg(HealthScore.final_score)).join(
        UserFoodHistory, HealthScore.history_id == UserFoodHistory.history_id
    ).where(
        and_(
            HealthScore.user_id == user_id,
            func.date(UserFoodHistory.consumed_at) == today
        )
    )
    today_avg_result = await session.execute(today_avg_stmt)
    raw_avg_score = today_avg_result.scalar() or 0

    # ✨ 종합 점수 계산 (양 + 질) - HealthScoreService 활용
    comp_result = calculate_daily_comprehensive_score(
        total_calories=int(total_calories_today),
        target_calories=target_calories,
        avg_quality_score=float(raw_avg_score)
    )
    avg_health_score = comp_result["final_score"]
    score_feedback = comp_result["feedback"]  # ✨ 피드백 추출
    print(f"📊 종합 점수 계산: {raw_avg_score:.1f}(질) x {comp_result['quantity_factor']}(양) = {avg_health_score}")

    # 4. 전날 평균 건강 점수 (전날도 종합 점수로 계산해야 정확하지만, 일단 단순 평균 사용하거나 0 처리)
    # 개선점: 전날 데이터도 동일한 로직으로 계산하면 좋음
    yesterday = today - timedelta(days=1)
    yesterday_avg_stmt = select(func.avg(HealthScore.final_score)).join(
        UserFoodHistory, HealthScore.history_id == UserFoodHistory.history_id
    ).where(
        and_(
            HealthScore.user_id == user_id,
            func.date(UserFoodHistory.consumed_at) == yesterday
        )
    )
    yesterday_avg_result = await session.execute(yesterday_avg_stmt)
    previous_day_score = yesterday_avg_result.scalar()

    # 전날 대비 점수 변화 계산
    score_change = None
    if previous_day_score is not None and avg_health_score > 0:
        score_change = round(avg_health_score - previous_day_score, 1)

    # 5. 자주 먹는 음식 Top 5
    frequent_stmt = select(
        UserFoodHistory.food_name,
        func.count(UserFoodHistory.food_name).label('count')
    ).where(
        UserFoodHistory.user_id == user_id
    ).group_by(
        UserFoodHistory.food_name
    ).order_by(
        func.count(UserFoodHistory.food_name).desc()
    ).limit(5)

    frequent_result = await session.execute(frequent_stmt)
    frequent_foods = [
        {"food_name": row[0], "count": row[1]}
        for row in frequent_result.all()
    ]

    # 6. 최근 7일 일일 칼로리
    seven_days_ago = today - timedelta(days=6)  # 오늘 포함 7일

    daily_stmt = select(
        func.date(UserFoodHistory.consumed_at).label('date'),
        func.sum(HealthScore.kcal).label('total_calories')
    ).join(
        HealthScore,
        UserFoodHistory.history_id == HealthScore.history_id
    ).where(
        and_(
            UserFoodHistory.user_id == user_id,
            func.date(UserFoodHistory.consumed_at) >= seven_days_ago,
            func.date(UserFoodHistory.consumed_at) <= today
        )
    ).group_by(
        func.date(UserFoodHistory.consumed_at)
    ).order_by(
        func.date(UserFoodHistory.consumed_at)
    )

    daily_result = await session.execute(daily_stmt)
    daily_data = {row[0]: int(row[1]) for row in daily_result.all()}

    # 7일치 데이터 채우기 (데이터 없는 날은 0)
    daily_calories = []
    for i in range(7):
        date = seven_days_ago + timedelta(days=i)
        calories = daily_data.get(date, 0)
        daily_calories.append({
            "date": date.strftime("%m/%d"),
            "calories": calories
        })

    # 7. 이번 주 총 칼로리 (지난 7일 합계)
    total_calories_week = sum(item["calories"] for item in daily_calories)

    # 8. 영양소 밸런스 (최근 7일)
    portion_ratio = func.coalesce(
        func.coalesce(UserFoodHistory.portion_size_g, 0)
        / func.nullif(func.coalesce(FoodNutrient.reference_value, 0), 0),
        0,
    )
    nutrition_stmt = (
        select(
            func.sum(func.coalesce(FoodNutrient.protein, 0) * portion_ratio),
            func.sum(func.coalesce(FoodNutrient.carb, 0) * portion_ratio),
            func.sum(func.coalesce(FoodNutrient.fat, 0) * portion_ratio),
        )
        .select_from(UserFoodHistory)
        .join(FoodNutrient, UserFoodHistory.food_id == FoodNutrient.food_id)
        .where(
            and_(
                UserFoodHistory.user_id == user_id,
                func.date(UserFoodHistory.consumed_at) >= seven_days_ago,
            )
        )
    )
    nutrition_result = await session.execute(nutrition_stmt)
    protein, carbs, fat = nutrition_result.one_or_none() or (0, 0, 0)

    total_macros = (protein or 0) + (carbs or 0) + (fat or 0)
    nutrition_balance = {
        "protein": round(protein * 100 / total_macros, 1) if total_macros > 0 else 0,
        "carbs": round(carbs * 100 / total_macros, 1) if total_macros > 0 else 0,
        "fat": round(fat * 100 / total_macros, 1) if total_macros > 0 else 0,
    }

    return DashboardStatsResponse(
        total_calories_today=int(total_calories_today),
        total_calories_week=total_calories_week,
        avg_health_score=float(avg_health_score),
        today_score_feedback=score_feedback,  # ✨ 추가됨
        previous_day_score=float(previous_day_score) if previous_day_score is not None else None,
        score_change=score_change,
        frequent_foods=frequent_foods,
        daily_calories=daily_calories,
        nutrition_balance=nutrition_balance
    )


async def get_score_detail(
    session: AsyncSession,
    user_id: int,
) -> ScoreDetailResponse:
    """
    상세 점수 현황 조회

    - 오늘 전체 점수
    - 전날 대비 점수 변화
    - 카테고리별 점수 (칼로리 균형, 영양소 균형, 식사 패턴 등)
    - 주간 트렌드
    """
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)

    # 1. 오늘 전체 평균 점수
    today_score_stmt = select(func.avg(HealthScore.final_score)).join(
        UserFoodHistory, HealthScore.history_id == UserFoodHistory.history_id
    ).where(
        and_(
            HealthScore.user_id == user_id,
            func.date(UserFoodHistory.consumed_at) == today
        )
    )
    today_score_result = await session.execute(today_score_stmt)
    overall_score = today_score_result.scalar() or 0

    # 2. 전날 평균 점수
    yesterday_score_stmt = select(func.avg(HealthScore.final_score)).join(
        UserFoodHistory, HealthScore.history_id == UserFoodHistory.history_id
    ).where(
        and_(
            HealthScore.user_id == user_id,
            func.date(UserFoodHistory.consumed_at) == yesterday
        )
    )
    yesterday_score_result = await session.execute(yesterday_score_stmt)
    previous_score = yesterday_score_result.scalar()

    # score_change 계산은 종합 점수 산출 후로 이동
    score_change = None

    # 3. 오늘 섭취한 음식들의 영양소 정보 조회
    today_foods_stmt = select(
        HealthScore.kcal,
        HealthScore.final_score,
        FoodNutrient.protein,
        FoodNutrient.carb,
        FoodNutrient.fat,
        FoodNutrient.fiber,
        FoodNutrient.sodium,
        FoodNutrient.saturated_fat,
        FoodNutrient.added_sugar
    ).join(
        UserFoodHistory, HealthScore.history_id == UserFoodHistory.history_id
    ).outerjoin(
        FoodNutrient, UserFoodHistory.food_id == FoodNutrient.food_id
    ).where(
        and_(
            HealthScore.user_id == user_id,
            func.date(UserFoodHistory.consumed_at) == today
        )
    )

    foods_result = await session.execute(today_foods_stmt)
    foods_data = foods_result.all()

    # 4. 사용자 정보 조회 (목표 칼로리 등)
    user_stmt = select(User).where(User.user_id == user_id)
    user_result = await session.execute(user_stmt)
    user = user_result.scalar_one_or_none()

    # 목표 칼로리 계산 (공통 함수 사용)
    target_calories = calculate_daily_calories(user) if user else 2000

    # 5. 종합 점수 및 세부 지표 계산
    categories: List[CategoryScore] = []

    # 기본값 설정
    raw_quality_score = overall_score  # 기존 단순 평균 점수 (질)
    quantity_score_val = 0.0
    calorie_ratio_val = 0.0

    if foods_data:
        # 총 칼로리
        total_calories = sum(row[0] or 0 for row in foods_data)

        # ✨ 종합 점수 재계산 (양 + 질)
        comp_result = calculate_daily_comprehensive_score(
            total_calories=int(total_calories),
            target_calories=target_calories,
            avg_quality_score=float(raw_quality_score)
        )

        overall_score = comp_result["final_score"]  # 종합 점수로 교체
        quantity_score_val = comp_result["quantity_factor"] * 100
        calorie_ratio_val = comp_result["calorie_ratio"]

        # 전날 대비 점수 변화 재계산 (종합 점수 기준)
        score_change = None
        if previous_score is not None:
            score_change = round(overall_score - previous_score, 1)

        # 칼로리 균형 점수 (목표 대비 90-110% = 100점, 그 외는 감점)
        # calculate_daily_comprehensive_score 로직과 유사하지만 카테고리 표시용으로 유지
        calorie_ratio = (total_calories / target_calories * 100) if target_calories > 0 else 0
        if 90 <= calorie_ratio <= 110:
            calorie_score = 100
        elif 80 <= calorie_ratio < 90 or 110 < calorie_ratio <= 120:
            calorie_score = 80
        elif 70 <= calorie_ratio < 80 or 120 < calorie_ratio <= 130:
            calorie_score = 60
        else:
            calorie_score = max(0, 100 - abs(calorie_ratio - 100))

        calorie_trend = 'same'
        if previous_score is not None:
            # 전날 칼로리 비교는 별도로 계산 필요하지만, 간단히 점수 기반으로 판단
            calorie_trend = 'up' if overall_score > previous_score else 'down' if overall_score < previous_score else 'same'

        # 칼로리 피드백 메시지 생성
        if 90 <= calorie_ratio <= 110:
            calorie_feedback = f"목표 칼로리 {target_calories}kcal 대비 {total_calories:.0f}kcal 섭취. 적절한 칼로리 섭취량입니다."
        elif calorie_ratio < 90:
            calorie_feedback = f"목표 칼로리 {target_calories}kcal 대비 {total_calories:.0f}kcal 섭취. 칼로리 섭취량이 부족합니다."
        else:
            calorie_feedback = f"목표 칼로리 {target_calories}kcal 대비 {total_calories:.0f}kcal 섭취. 칼로리 섭취량이 초과입니다."

        categories.append(CategoryScore(
            name="칼로리 균형",
            score=round(calorie_score, 1),
            max_score=100.0,
            trend=calorie_trend,
            feedback=calorie_feedback
        ))

        # 영양소 균형 점수 (단백질, 탄수화물, 지방 비율)
        total_protein = sum(row[2] or 0 for row in foods_data)
        total_carbs = sum(row[3] or 0 for row in foods_data)
        total_fat = sum(row[4] or 0 for row in foods_data)
        total_macros = total_protein + total_carbs + total_fat

        if total_macros > 0:
            protein_ratio = (total_protein / total_macros) * 100
            carbs_ratio = (total_carbs / total_macros) * 100
            fat_ratio = (total_fat / total_macros) * 100

            # 권장 비율: 단백질 15-20%, 탄수화물 50-60%, 지방 20-30%
            nutrition_score = 100
            if not (15 <= protein_ratio <= 25):
                nutrition_score -= 10
            if not (45 <= carbs_ratio <= 65):
                nutrition_score -= 10
            if not (20 <= fat_ratio <= 35):
                nutrition_score -= 10
            nutrition_score = max(0, nutrition_score)
        else:
            nutrition_score = 0

        # 영양소 균형 피드백 메시지 생성
        if nutrition_score >= 80:
            nutrition_feedback = f"단백질 {total_protein:.1f}g, 탄수화물 {total_carbs:.1f}g, 지방 {total_fat:.1f}g. 균형 잡힌 영양소 비율입니다."
        else:
            nutrition_feedback = f"단백질 {total_protein:.1f}g, 탄수화물 {total_carbs:.1f}g, 지방 {total_fat:.1f}g. 영양소 비율이 불균형합니다."

        categories.append(CategoryScore(
            name="영양소 균형",
            score=round(nutrition_score, 1),
            max_score=100.0,
            trend=calorie_trend,
            feedback=nutrition_feedback
        ))

        # 식이섬유 점수
        total_fiber = sum(row[5] or 0 for row in foods_data)
        fiber_target = 25.0  # 일일 권장량
        fiber_score = min(100, (total_fiber / fiber_target) * 100) if fiber_target > 0 else 0

        # 식이섬유 피드백 메시지 생성
        if fiber_score >= 80:
            fiber_feedback = f"식이섬유 {total_fiber:.1f}g 섭취. 충분한 섭취량입니다."
        else:
            fiber_feedback = f"식이섬유 {total_fiber:.1f}g 섭취. 섭취량이 부족합니다. 채소와 과일을 더 섭취해보세요."

        categories.append(CategoryScore(
            name="식이섬유",
            score=round(fiber_score, 1),
            max_score=100.0,
            trend='same',
            feedback=fiber_feedback
        ))

        # 나트륨 점수 (낮을수록 좋음)
        total_sodium = sum(row[6] or 0 for row in foods_data)
        sodium_target = 2000.0  # 일일 권장량
        sodium_ratio = (total_sodium / sodium_target) * 100 if sodium_target > 0 else 0
        sodium_score = max(0, 100 - sodium_ratio)  # 낮을수록 좋으므로 역산

        # 나트륨 피드백 메시지 생성
        if sodium_score >= 70:
            sodium_feedback = f"나트륨 {total_sodium:.0f}mg 섭취. 적절한 수준입니다."
        else:
            sodium_feedback = f"나트륨 {total_sodium:.0f}mg 섭취. 나트륨 섭취량이 초과입니다. 저염식을 권장합니다."

        categories.append(CategoryScore(
            name="나트륨 관리",
            score=round(sodium_score, 1),
            max_score=100.0,
            trend='same',
            feedback=sodium_feedback
        ))

        # 포화지방 점수 (낮을수록 좋음)
        total_saturated_fat = sum(row[7] or 0 for row in foods_data)
        saturated_fat_target = 15.0  # 일일 권장량
        saturated_fat_ratio = (total_saturated_fat / saturated_fat_target) * 100 if saturated_fat_target > 0 else 0
        saturated_fat_score = max(0, 100 - saturated_fat_ratio)

        # 포화지방 피드백 메시지 생성
        if saturated_fat_score >= 70:
            saturated_fat_feedback = f"포화지방 {total_saturated_fat:.1f}g 섭취. 적절한 수준입니다."
        else:
            saturated_fat_feedback = f"포화지방 {total_saturated_fat:.1f}g 섭취. 포화지방 섭취량이 초과입니다. 섭취를 줄여보세요."

        categories.append(CategoryScore(
            name="포화지방 관리",
            score=round(saturated_fat_score, 1),
            max_score=100.0,
            trend='same',
            feedback=saturated_fat_feedback
        ))
    else:
        # 데이터 없음
        categories.append(CategoryScore(
            name="칼로리 균형",
            score=0.0,
            max_score=100.0,
            trend='same',
            feedback="오늘 식사 기록이 없습니다."
        ))

    # 6. 주간 트렌드 (최근 7일)
    seven_days_ago = today - timedelta(days=6)
    weekly_trend_stmt = select(
        func.date(UserFoodHistory.consumed_at).label('date'),
        func.avg(HealthScore.final_score).label('avg_score')
    ).join(
        HealthScore, UserFoodHistory.history_id == HealthScore.history_id
    ).where(
        and_(
            UserFoodHistory.user_id == user_id,
            func.date(UserFoodHistory.consumed_at) >= seven_days_ago,
            func.date(UserFoodHistory.consumed_at) <= today
        )
    ).group_by(
        func.date(UserFoodHistory.consumed_at)
    ).order_by(
        func.date(UserFoodHistory.consumed_at)
    )

    weekly_result = await session.execute(weekly_trend_stmt)
    weekly_data = {row[0]: row[1] for row in weekly_result.all()}

    weekly_trend = []
    for i in range(7):
        date = seven_days_ago + timedelta(days=i)
        score = weekly_data.get(date, 0)
        weekly_trend.append({
            "date": date.strftime("%m-%d"),
            "score": round(float(score), 1) if score else 0
        })

    return ScoreDetailResponse(
        overall_score=round(float(overall_score), 1),
        quality_score=round(float(raw_quality_score), 1) if raw_quality_score is not None else 0, # ✨ 추가
        quantity_score=round(float(quantity_score_val), 1), # ✨ 추가
        calorie_ratio=round(float(calorie_ratio_val), 1), # ✨ 추가
        previous_score=round(float(previous_score), 1) if previous_score is not None else None,
        score_change=score_change,
        categories=categories,
        weekly_trend=weekly_trend
    )
