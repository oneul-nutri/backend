"""식사 이력 조회/삭제 관련 비즈니스 로직 (meals.py 라우트에서 분리)"""
from __future__ import annotations

from datetime import datetime
from typing import List, Tuple

from fastapi import HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas.meals import MealRecordResponse, MostEatenFood
from app.db.models import DietPlan, DietPlanMeal, HealthScore, UserFoodHistory


async def get_meal_history(
    session: AsyncSession,
    user_id: int,
    limit: int = 20,
    offset: int = 0,
    include_diet_plans: bool = True,
) -> List[MealRecordResponse]:
    """
    음식 섭취 기록 조회 (추천 식단 포함)

    UserFoodHistory + DietPlanMeal 통합
    """
    # UserFoodHistory + HealthScore 조인 조회
    stmt = select(UserFoodHistory, HealthScore).where(
        UserFoodHistory.user_id == user_id
    ).outerjoin(
        HealthScore,
        and_(
            HealthScore.history_id == UserFoodHistory.history_id,
            HealthScore.user_id == UserFoodHistory.user_id
        )
    ).order_by(
        UserFoodHistory.consumed_at.desc()
    ).limit(limit).offset(offset)

    result = await session.execute(stmt)
    rows = result.all()

    records: List[MealRecordResponse] = []
    for history, health_score in rows:
        records.append(MealRecordResponse(
            history_id=history.history_id,
            user_id=history.user_id,
            food_id=history.food_id,
            food_name=history.food_name,
            consumed_at=history.consumed_at,
            portion_size_g=history.portion_size_g or 0,
            calories=health_score.kcal if health_score else 0,
            health_score=health_score.final_score if health_score else None,
            food_grade=health_score.food_grade if health_score else None,
            meal_type=history.meal_type  # 식사 유형 추가
        ))

    # ✅ 추천 식단(DietPlanMeal) 조회 및 통합
    if include_diet_plans:
        diet_stmt = select(DietPlanMeal, DietPlan).where(
            DietPlan.user_id == user_id
        ).join(DietPlan, DietPlanMeal.diet_plan_id == DietPlan.diet_plan_id)

        diet_result = await session.execute(diet_stmt)
        diet_rows = diet_result.all()

        for meal, plan in diet_rows:
            # food_name: "식단명: 음식메뉴" 형식으로 표시
            food_name = f"{plan.plan_name}: {meal.food_description or meal.meal_name}"

            records.append(MealRecordResponse(
                history_id=-meal.meal_id,  # 음수 ID로 구분
                user_id=user_id,
                food_id=f"diet_plan_{meal.diet_plan_id}",
                food_name=food_name,
                consumed_at=plan.created_at or datetime.now(),
                portion_size_g=0,
                calories=int(meal.calories) if meal.calories else 0,
                health_score=None,
                food_grade=None,
                meal_type=meal.meal_type
            ))

    # 날짜 기준 정렬 (최신순)
    records.sort(key=lambda x: x.consumed_at, reverse=True)

    return records


async def delete_meal_history(
    session: AsyncSession,
    user_id: int,
    history_id: int,
) -> Tuple[int, str]:
    """
    음식 섭취 기록 삭제

    Returns:
        (history_id, food_name) 튜플
    """
    # 기록 존재 여부 및 권한 확인
    stmt = select(UserFoodHistory).where(
        and_(
            UserFoodHistory.history_id == history_id,
            UserFoodHistory.user_id == user_id
        )
    )
    result = await session.execute(stmt)
    history = result.scalar_one_or_none()

    if not history:
        raise HTTPException(
            status_code=404,
            detail="기록을 찾을 수 없거나 삭제 권한이 없습니다."
        )

    # HealthScore도 함께 삭제
    health_score_stmt = select(HealthScore).where(
        and_(
            HealthScore.history_id == history_id,
            HealthScore.user_id == user_id
        )
    )
    health_score_result = await session.execute(health_score_stmt)
    health_score = health_score_result.scalar_one_or_none()

    if health_score:
        await session.delete(health_score)

    food_name = history.food_name

    # UserFoodHistory 삭제
    await session.delete(history)
    await session.commit()

    return history_id, food_name


async def get_most_eaten_foods(
    session: AsyncSession,
    user_id: int,
    limit: int = 4,
) -> List[MostEatenFood]:
    """
    자주 먹은 음식 TOP N

    **처리 과정:**
    1. UserFoodHistory에서 food_id별 카운트
    2. 내림차순 정렬
    3. 상위 N개 반환
    """
    print(f"🍽️ 자주 먹은 음식 조회: user_id={user_id}, limit={limit}")

    # food_id별 카운트 쿼리
    # 같은 food_id는 하나로 합치고, 가장 최근 음식명 사용
    # Subquery: 각 food_id의 가장 최근 기록 찾기
    latest_food_subquery = (
        select(
            UserFoodHistory.food_id,
            UserFoodHistory.food_name,
            func.row_number().over(
                partition_by=UserFoodHistory.food_id,
                order_by=UserFoodHistory.consumed_at.desc()
            ).label('rn')
        )
        .where(UserFoodHistory.user_id == user_id)
        .subquery()
    )

    # 메인 쿼리: food_id별 카운트 + 최근 음식명 조인
    stmt = (
        select(
            UserFoodHistory.food_id,
            latest_food_subquery.c.food_name,  # 가장 최근 음식명
            func.count(UserFoodHistory.history_id).label('eat_count')
        )
        .join(
            latest_food_subquery,
            (UserFoodHistory.food_id == latest_food_subquery.c.food_id) &
            (latest_food_subquery.c.rn == 1)
        )
        .where(UserFoodHistory.user_id == user_id)
        .group_by(UserFoodHistory.food_id, latest_food_subquery.c.food_name)
        .order_by(func.count(UserFoodHistory.history_id).desc())
        .limit(limit)
    )

    result = await session.execute(stmt)
    rows = result.all()

    most_eaten_list = [
        MostEatenFood(
            food_id=row.food_id,
            food_name=row.food_name,
            eat_count=row.eat_count
        )
        for row in rows
    ]

    print(f"✅ 자주 먹은 음식 {len(most_eaten_list)}개 조회 완료")
    for idx, food in enumerate(most_eaten_list, 1):
        print(f"  {idx}. {food.food_name}: {food.eat_count}번")

    return most_eaten_list
