"""식단 관련 Pydantic 스키마"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class MealNutrients(BaseModel):
    """식단 영양소"""

    protein: int
    carbs: int
    fat: int
    sodium: int


class MealRecommendation(BaseModel):
    """식단 추천"""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    name: str
    calories: int
    description: str
    is_selected: bool = Field(alias="isSelected")
    nutrients: MealNutrients | None = None


class MealRecommendationsResponse(BaseModel):
    """식단 추천 응답 데이터"""

    recommendations: list[MealRecommendation]
    timestamp: str | None = None


class MealSelectionRequest(BaseModel):
    """식단 선택 요청"""

    model_config = ConfigDict(populate_by_name=True)

    meal_id: int = Field(alias="mealId")
    user_id: str = Field(alias="userId")
    timestamp: str | None = None


class SelectedMealInfo(BaseModel):
    """선택된 식단 정보"""

    id: int
    name: str
    calories: int


class MealSelectionData(BaseModel):
    """식단 선택 응답 데이터"""

    success: bool
    message: str
    selected_meal: SelectedMealInfo = Field(alias="selectedMeal")


# ========== 음식 기록/건강 점수 API 스키마 (routes/meals 패키지에서 사용) ==========

class FoodItem(BaseModel):
    """음식 아이템"""
    food_id: str = Field(..., description="음식 ID (food_nutrients 테이블)")
    food_name: str = Field(..., description="음식 이름")
    portion_size_g: float = Field(..., description="섭취량 (g)")
    calories: int = Field(..., description="칼로리")
    protein: float = Field(0.0, description="단백질 (g)")
    carbs: float = Field(0.0, description="탄수화물 (g)")
    fat: float = Field(0.0, description="지방 (g)")
    sodium: float = Field(0.0, description="나트륨 (mg)")
    fiber: Optional[float] = Field(0.0, description="식이섬유 (g)")


class SaveMealRequest(BaseModel):
    """음식 기록 저장 요청"""
    meal_type: str = Field(..., description="식사 유형: 아침/점심/저녁/간식")
    foods: List[FoodItem] = Field(..., description="음식 목록")
    memo: Optional[str] = Field(None, description="메모")
    image_url: Optional[str] = Field(None, description="음식 사진 URL")


class IngredientUsage(BaseModel):
    """사용한 재료와 수량"""
    name: str = Field(..., description="재료 이름")
    quantity: int = Field(1, description="사용한 수량")


class SaveRecommendedMealRequest(BaseModel):
    """추천 음식 선택 및 저장 요청"""
    food_name: str = Field(..., description="선택한 음식 이름")
    ingredients_used: List[str] = Field(..., description="사용된 식재료 목록 (레거시)")
    ingredients_with_quantity: Optional[List[IngredientUsage]] = Field(None, description="재료와 수량")
    meal_type: str = Field("점심", description="식사 유형: 아침/점심/저녁/간식")
    portion_size_g: float = Field(300.0, description="예상 섭취량 (g)")
    memo: Optional[str] = Field(None, description="메모")


class MealRecordResponse(BaseModel):
    """음식 기록 응답"""
    history_id: int
    user_id: int
    food_id: str
    food_name: str
    consumed_at: datetime
    portion_size_g: float
    calories: int
    health_score: Optional[int] = None
    food_grade: Optional[str] = None
    meal_type: Optional[str] = None  # 식사 유형 추가


class DashboardStatsResponse(BaseModel):
    """대시보드 통계 응답"""
    total_calories_today: int = Field(..., description="오늘 총 칼로리")
    total_calories_week: int = Field(..., description="이번 주 총 칼로리")
    avg_health_score: float = Field(..., description="오늘 평균 건강 점수")
    today_score_feedback: Optional[str] = Field(None, description="오늘 점수 피드백 메시지")  # ✨ 추가됨
    previous_day_score: Optional[float] = Field(None, description="전날 평균 건강 점수")
    score_change: Optional[float] = Field(None, description="전날 대비 점수 변화")
    frequent_foods: List[dict] = Field(..., description="자주 먹는 음식 Top 5")
    daily_calories: List[dict] = Field(..., description="일일 칼로리 (최근 7일)")
    nutrition_balance: dict = Field(..., description="영양소 밸런스")


class CategoryScore(BaseModel):
    """카테고리별 점수"""
    name: str = Field(..., description="카테고리 이름")
    score: float = Field(..., description="점수")
    max_score: float = Field(100.0, description="최대 점수")
    trend: str = Field(..., description="트렌드: up, down, same")
    feedback: str = Field(..., description="피드백 메시지")


class ScoreDetailResponse(BaseModel):
    """상세 점수 현황 응답"""
    overall_score: float = Field(..., description="전체 점수")
    quality_score: Optional[float] = Field(None, description="식단 품질 점수 (평균 HealthScore)")  # ✨ 추가
    quantity_score: Optional[float] = Field(None, description="양적 달성도 점수 (0~100 환산)")  # ✨ 추가
    calorie_ratio: Optional[float] = Field(None, description="목표 대비 칼로리 비율 (%)")  # ✨ 추가
    previous_score: Optional[float] = Field(None, description="전날 점수")
    score_change: Optional[float] = Field(None, description="점수 변화")
    categories: List[CategoryScore] = Field(..., description="카테고리별 점수")
    weekly_trend: List[dict] = Field(..., description="주간 트렌드")


class MostEatenFood(BaseModel):
    """자주 먹은 음식"""
    food_id: str = Field(..., description="음식 ID")
    food_name: str = Field(..., description="음식 이름")
    eat_count: int = Field(..., description="먹은 횟수")
