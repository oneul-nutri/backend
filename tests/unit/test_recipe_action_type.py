"""
/api/v1/recipes/recommendations 의 action_type 상태머신 계약 테스트.

LangChain → OpenAI SDK 교체(TODO #4) 진행 시 내부 구현이 바뀌어도
프론트와의 action_type 계약(CONFIRMATION / RECOMMENDATION_RESULT / TEXT_ONLY)은
유지되어야 한다. 이 테스트가 그 계약을 고정한다.

Fixtures:
  async_client  — 비인증 (tests/conftest.py)
  db_session    — rollback 격리 async session (tests/conftest.py)
  recipe_client — 이 파일에서 정의. get_current_active_user를
                  gender/age/weight가 채워진 mock User로 오버라이드한다.
                  실제 DB row는 만들지 않는다 — 질병/섭취 이력 쿼리는
                  rollback 세션에서 빈 결과를 돌려준다.
"""
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from pytest import mark
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_active_user
from app.db.models import User
from app.db.session import get_session
from app.main import app

TEST_USER_ID = 99999


def _make_mock_user() -> User:
    """recipes 라우트가 요구하는 최소 필드(gender/age/weight/nickname)를 채운 User."""
    user = User()
    user.user_id = TEST_USER_ID
    user.username = "pytest_recipe_user"
    user.nickname = "테스터"
    user.email = "pytest_recipe@internal.test"
    user.password = "hashed"
    user.gender = "M"
    user.age = 30
    user.weight = 70.0
    user.health_goal = "maintain"
    return user


@pytest.fixture
async def recipe_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """recipes 라우트용 인증 클라이언트."""

    async def _override_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    async def _override_user() -> User:
        return _make_mock_user()

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_active_user] = _override_user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        yield client

    app.dependency_overrides.clear()


def _make_mock_service(
    *,
    call_tool: bool,
    meal_type: str | None,
    recommendations: list | None = None,
    health_warning: str | None = None,
) -> MagicMock:
    """recipe_recommendation_service의 주요 async 메서드를 스텁한 MagicMock."""
    service = MagicMock()
    service.decide_recipe_tool = AsyncMock(return_value={
        "call_tool": call_tool,
        "assistant_reply": "스텁 응답",
        "suggestions": ["제안1", "제안2"],
        "meal_type": meal_type,
        "intent_summary": "",
        "risk_flags": [],
    })

    if recommendations is not None:
        pipeline = MagicMock()
        pipeline.get_recommendations = AsyncMock(return_value={
            "recommendations": recommendations,
            "health_warning": health_warning,
            "user_friendly_message": "추천 메시지",
            "inferred_preference": "테스트 선호",
        })
        service.launch_parallel_recipe_pipeline = MagicMock(return_value=pipeline)

    service.generate_action_suggestions = AsyncMock(return_value=["더 추천", "다른 식사"])
    service.evaluate_health_warning = AsyncMock(return_value={
        "requires_confirmation": False,
        "assistant_reply": "",
        "suggestions": [],
    })
    return service


# ── 인증 계약 ────────────────────────────────────────────────────────────────

@mark.asyncio(loop_scope="session")
async def test_recipe_recommendations_requires_authentication(async_client: AsyncClient):
    response = await async_client.post(
        "/api/v1/recipes/recommendations",
        json={"user_request": "점심 추천"},
    )
    assert response.status_code == 401


# ── action_type 3분기 ────────────────────────────────────────────────────────

@mark.asyncio(loop_scope="session")
class TestRecipeActionType:
    async def test_non_food_request_returns_text_only(self, recipe_client: AsyncClient):
        """음식과 무관한 요청은 서비스 호출 전에 TEXT_ONLY로 조기 반환된다.

        non_food_keywords("날씨" 등)가 포함되면 LLM/DB 호출 없이 안내 메시지만 돌려줌.
        """
        response = await recipe_client.post(
            "/api/v1/recipes/recommendations",
            json={"user_request": "오늘 날씨 어때"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["action_type"] == "TEXT_ONLY"

    async def test_clarify_returns_text_only(self, recipe_client: AsyncClient):
        """decide_recipe_tool이 call_tool=False를 반환하면 대화형 안내(TEXT_ONLY)."""
        mock_service = _make_mock_service(call_tool=False, meal_type=None)

        with patch(
            "app.api.v1.routes.recipes.get_recipe_recommendation_service",
            return_value=mock_service,
        ):
            response = await recipe_client.post(
                "/api/v1/recipes/recommendations",
                json={"user_request": "점심 뭐 먹지"},
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["action_type"] == "TEXT_ONLY"
        mock_service.decide_recipe_tool.assert_awaited_once()

    async def test_missing_meal_type_returns_confirmation(self, recipe_client: AsyncClient):
        """call_tool=True지만 meal_type이 결정되지 않으면 끼니 확인(CONFIRMATION)."""
        mock_service = _make_mock_service(call_tool=True, meal_type=None)

        with patch(
            "app.api.v1.routes.recipes.get_recipe_recommendation_service",
            return_value=mock_service,
        ):
            response = await recipe_client.post(
                "/api/v1/recipes/recommendations",
                # '닭고기 요리'엔 음식 키워드가 충분히 있고, meal_type 키워드는 없음
                json={"user_request": "닭고기 요리 먹고싶어"},
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["action_type"] == "CONFIRMATION"

    async def test_full_pipeline_returns_recommendation_result(self, recipe_client: AsyncClient):
        """call_tool=True + meal_type이 있으면 파이프라인 → RECOMMENDATION_RESULT + 레시피."""
        recommendations = [
            {
                "name": "닭가슴살 샐러드",
                "description": "담백한 점심",
                "calories": 400,
                "cooking_time": "15분",
                "difficulty": "쉬움",
                "suitable_reason": "단백질 보충",
            }
        ]
        mock_service = _make_mock_service(
            call_tool=True,
            meal_type="lunch",
            recommendations=recommendations,
            health_warning=None,
        )

        with patch(
            "app.api.v1.routes.recipes.get_recipe_recommendation_service",
            return_value=mock_service,
        ):
            response = await recipe_client.post(
                "/api/v1/recipes/recommendations",
                json={"user_request": "점심 닭고기 요리 추천해줘"},
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["action_type"] == "RECOMMENDATION_RESULT"
        assert data["data"]["recipes"][0]["name"] == "닭가슴살 샐러드"
