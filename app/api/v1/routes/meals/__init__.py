"""음식 기록 및 건강 점수 관리 API 패키지"""
from functools import lru_cache

from fastapi import APIRouter
from openai import AsyncOpenAI

from app.core.config import get_settings

settings = get_settings()


@lru_cache
def get_nutrition_llm() -> AsyncOpenAI:
    """save-recommended에서 쓰는 OpenAI 클라이언트 팩토리.

    이름은 LangChain 시절 그대로 유지 — test_meals_critical.py의
    `patch("app.api.v1.routes.meals.get_nutrition_llm", ...)` 지점과
    호환되도록. 모델/temperature는 호출 시점에 지정한다.
    """
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY 환경 변수가 필요합니다.")
    return AsyncOpenAI(api_key=settings.openai_api_key)


# 서브 라우터 import는 get_nutrition_llm 정의 이후에 수행해야 한다.
# record.py가 `from app.api.v1.routes import meals as _meals_pkg`를 통해
# 패키지 모듈을 참조하는데, 요청 시점에 get_nutrition_llm 속성이 필요하기 때문이다.
from app.api.v1.routes.meals.history import router as _history_router  # noqa: E402
from app.api.v1.routes.meals.record import router as _record_router  # noqa: E402
from app.api.v1.routes.meals.stats import router as _stats_router  # noqa: E402

router = APIRouter()
router.include_router(_record_router)
router.include_router(_history_router)
router.include_router(_stats_router)
