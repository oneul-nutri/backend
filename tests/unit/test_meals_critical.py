"""
Characterization tests for the three critical meal endpoints.

These tests document CURRENT behavior, not ideal behavior.
They exist to catch regressions during the upcoming service-layer
refactor and LangChain → OpenAI SDK migration.

Fixtures (defined in conftest.py):
  async_client  — unauthenticated, for 401 assertions
  authed_client — auth overridden to TEST_USER_ID, session rolls back after test
  db_session    — the same AsyncSession used by authed_client (same transaction)

Event loop 규칙:
  DB fixture를 사용하는 클래스는 반드시 @mark.asyncio(loop_scope="session")을 선언한다.
  pytest.ini의 asyncio_default_fixture_loop_scope=session 과 일치해야 하며,
  불일치 시 asyncmy "Future attached to a different loop" RuntimeError가 발생한다.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest import mark
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserFoodHistory

TEST_USER_ID = 99999  # must match conftest.TEST_USER_ID

# ── helpers ───────────────────────────────────────────────────────────────────

def _food_payload(
    food_id: str = "TEST_FAKE_001",
    food_name: str = "테스트음식",
    portion_g: float = 200.0,
    calories: int = 350,
    meal_type: str = "lunch",
) -> dict:
    return {
        "meal_type": meal_type,
        "foods": [
            {
                "food_id": food_id,
                "food_name": food_name,
                "portion_size_g": portion_g,
                "calories": calories,
                "protein": 15.0,
                "carbs": 45.0,
                "fat": 8.0,
            }
        ],
    }


# ── POST /meals/save ──────────────────────────────────────────────────────────

@mark.asyncio(loop_scope="session")
class TestSaveMeal:
    async def test_requires_authentication(self, async_client: AsyncClient):
        response = await async_client.post("/api/v1/meals/save", json=_food_payload())
        assert response.status_code == 401

    async def test_happy_path_response_shape(self, authed_client: AsyncClient):
        response = await authed_client.post("/api/v1/meals/save", json=_food_payload())

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert isinstance(body["data"], list)

        record = body["data"][0]
        required = {"history_id", "user_id", "food_id", "food_name", "consumed_at", "portion_size_g", "calories"}
        assert required.issubset(record.keys())

    async def test_saved_record_belongs_to_test_user(self, authed_client: AsyncClient):
        response = await authed_client.post("/api/v1/meals/save", json=_food_payload())

        assert response.status_code == 200
        record = response.json()["data"][0]
        assert record["user_id"] == TEST_USER_ID
        assert record["food_name"] == "테스트음식"
        assert record["calories"] == 350

    async def test_food_not_in_nutrient_db_saves_with_null_health_score(
        self, authed_client: AsyncClient
    ):
        # Any food_id absent from food_nutrients → health_score and food_grade are None.
        # This is intentional: the endpoint does not fail on missing nutrient data.
        payload = _food_payload(food_id="DEFINITELY_NOT_IN_DB_XYZ99999")
        response = await authed_client.post("/api/v1/meals/save", json=payload)

        assert response.status_code == 200
        record = response.json()["data"][0]
        assert record["health_score"] is None
        assert record["food_grade"] is None

    async def test_multiple_foods_create_separate_records(
        self, authed_client: AsyncClient
    ):
        payload = {
            "meal_type": "dinner",
            "foods": [
                {"food_id": "MULTI_A", "food_name": "음식A", "portion_size_g": 100.0, "calories": 100},
                {"food_id": "MULTI_B", "food_name": "음식B", "portion_size_g": 150.0, "calories": 200},
            ],
        }
        response = await authed_client.post("/api/v1/meals/save", json=payload)

        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) == 2
        assert {r["food_name"] for r in data} == {"음식A", "음식B"}

    async def test_record_is_persisted_in_db_session(
        self, authed_client: AsyncClient, db_session: AsyncSession
    ):
        # Verifies the endpoint actually writes to the DB (not just returns a fake response).
        response = await authed_client.post(
            "/api/v1/meals/save", json=_food_payload(food_name="영속성확인")
        )
        assert response.status_code == 200
        history_id = response.json()["data"][0]["history_id"]

        stmt = select(UserFoodHistory).where(UserFoodHistory.history_id == history_id)
        row = (await db_session.execute(stmt)).scalar_one_or_none()

        assert row is not None
        assert row.food_name == "영속성확인"
        assert row.user_id == TEST_USER_ID

    async def test_missing_required_field_returns_422(self, authed_client: AsyncClient):
        bad_payload = {"meal_type": "lunch"}  # foods field missing
        response = await authed_client.post("/api/v1/meals/save", json=bad_payload)
        assert response.status_code == 422


# ── GET /meals/history ────────────────────────────────────────────────────────

@mark.asyncio(loop_scope="session")
class TestMealHistory:
    async def test_requires_authentication(self, async_client: AsyncClient):
        response = await async_client.get("/api/v1/meals/history")
        assert response.status_code == 401

    async def test_empty_history_for_fresh_user(self, authed_client: AsyncClient):
        # TEST_USER_ID has no records in this rolled-back transaction.
        response = await authed_client.get(
            "/api/v1/meals/history", params={"include_diet_plans": False}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"] == []

    async def test_history_contains_just_saved_meal(self, authed_client: AsyncClient):
        await authed_client.post(
            "/api/v1/meals/save",
            json=_food_payload(food_name="이력조회테스트"),
        )

        response = await authed_client.get(
            "/api/v1/meals/history", params={"include_diet_plans": False}
        )

        assert response.status_code == 200
        food_names = [r["food_name"] for r in response.json()["data"]]
        assert "이력조회테스트" in food_names

    async def test_pagination_limit_is_respected(self, authed_client: AsyncClient):
        for i in range(3):
            await authed_client.post(
                "/api/v1/meals/save",
                json=_food_payload(food_id=f"PAGE_{i}", food_name=f"페이지음식{i}"),
            )

        response = await authed_client.get(
            "/api/v1/meals/history",
            params={"limit": 2, "include_diet_plans": False},
        )

        assert response.status_code == 200
        # Pagination limit is applied to UserFoodHistory query, but DietPlanMeal
        # records are appended afterwards — pass include_diet_plans=False to get
        # a clean count.
        assert len(response.json()["data"]) <= 2

    async def test_response_record_has_all_required_fields(
        self, authed_client: AsyncClient
    ):
        await authed_client.post("/api/v1/meals/save", json=_food_payload())

        response = await authed_client.get(
            "/api/v1/meals/history", params={"include_diet_plans": False}
        )
        assert response.status_code == 200
        record = response.json()["data"][0]
        required = {"history_id", "user_id", "food_id", "food_name", "consumed_at", "portion_size_g", "calories"}
        assert required.issubset(record.keys())

    async def test_diet_plans_excluded_when_flag_false(
        self, authed_client: AsyncClient
    ):
        # With include_diet_plans=False, diet_plan_ prefixed food_ids must not appear.
        response = await authed_client.get(
            "/api/v1/meals/history", params={"include_diet_plans": False}
        )
        assert response.status_code == 200
        for record in response.json()["data"]:
            assert not record["food_id"].startswith("diet_plan_")


# ── POST /meals/save-recommended ─────────────────────────────────────────────

# Shared mock values for the LLM nutrition response.
# 의도적으로 fallback 기본값(calories=400)과 다른 값 사용:
# mock이 실제로 호출되지 못하고 서비스가 fallback으로 흘러가면
# 이 숫자가 400으로 찍혀 테스트가 실패하도록 한다 (silent regression 방지).
_MOCK_NUTRITION = {
    "calories": 450,
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
    "sodium_mg": 800.0,
}


def _make_openai_mock(content: str) -> MagicMock:
    """AsyncOpenAI.chat.completions.create가 `content`를 돌려주도록 형태를 맞춘 mock."""
    message = MagicMock()
    message.content = content
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]

    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=response)
    return client


@pytest.fixture
def mock_llm_and_matching():
    """
    save-recommended 테스트용 외부 의존성 패치:
    1. get_nutrition_llm — 실제 OpenAI 호출 대신 _MOCK_NUTRITION JSON을 반환하는
       AsyncOpenAI mock 반환
    2. food_matching_service — 복잡한 DB/LLM 매칭 우회 (None 반환 →
       UserContributedFood 생성 경로 유도)
    """
    mock_client = _make_openai_mock(json.dumps(_MOCK_NUTRITION))

    mock_svc = MagicMock()
    mock_svc.match_food_to_db = AsyncMock(return_value=None)

    with (
        patch("app.api.v1.routes.meals.get_nutrition_llm", return_value=mock_client),
        patch(
            "app.services.food_matching_service.normalize_food_name",
            side_effect=lambda name, _: name,
        ),
        patch(
            "app.services.food_matching_service.get_food_matching_service",
            return_value=mock_svc,
        ),
    ):
        yield {"client": mock_client, "svc": mock_svc}


@mark.asyncio(loop_scope="session")
class TestSaveRecommendedMeal:
    async def test_requires_authentication(self, async_client: AsyncClient):
        payload = {
            "food_name": "제육볶음",
            "ingredients_used": [],
            "meal_type": "lunch",
            "portion_size_g": 300.0,
        }
        response = await async_client.post("/api/v1/meals/save-recommended", json=payload)
        assert response.status_code == 401

    async def test_happy_path_returns_saved_record(
        self, authed_client: AsyncClient, mock_llm_and_matching
    ):
        payload = {
            "food_name": "제육볶음",
            "ingredients_used": [],
            "meal_type": "lunch",
            "portion_size_g": 300.0,
        }
        response = await authed_client.post("/api/v1/meals/save-recommended", json=payload)

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True

        record = body["data"]
        assert record["user_id"] == TEST_USER_ID
        assert record["food_name"] == "제육볶음"
        assert record["calories"] == _MOCK_NUTRITION["calories"]
        assert "history_id" in record
        assert "consumed_at" in record

    async def test_llm_failure_falls_back_to_default_nutrition(
        self, authed_client: AsyncClient
    ):
        # When the LLM raises, the endpoint silently uses hardcoded fallback values
        # (calories=400) rather than returning 500.  Document that behavior here.
        failing_client = MagicMock()
        failing_client.chat.completions.create = AsyncMock(
            side_effect=Exception("LLM unavailable")
        )

        mock_svc = MagicMock()
        mock_svc.match_food_to_db = AsyncMock(return_value=None)

        with (
            patch("app.api.v1.routes.meals.get_nutrition_llm", return_value=failing_client),
            patch(
                "app.services.food_matching_service.normalize_food_name",
                side_effect=lambda name, _: name,
            ),
            patch(
                "app.services.food_matching_service.get_food_matching_service",
                return_value=mock_svc,
            ),
        ):
            response = await authed_client.post(
                "/api/v1/meals/save-recommended",
                json={
                    "food_name": "김치찌개",
                    "ingredients_used": [],
                    "meal_type": "lunch",
                    "portion_size_g": 300.0,
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["calories"] == 400  # hardcoded fallback default

    async def test_missing_ingredients_do_not_block_save(
        self, authed_client: AsyncClient, mock_llm_and_matching
    ):
        # Ingredients listed in ingredients_used that are absent from UserIngredient
        # should emit a warning but NOT cause the request to fail.
        payload = {
            "food_name": "볶음밥",
            "ingredients_used": ["당근", "계란"],  # not in DB for TEST_USER_ID
            "meal_type": "lunch",
            "portion_size_g": 250.0,
        }
        response = await authed_client.post("/api/v1/meals/save-recommended", json=payload)

        assert response.status_code == 200
        assert response.json()["success"] is True

    async def test_record_persisted_in_db(
        self,
        authed_client: AsyncClient,
        db_session: AsyncSession,
        mock_llm_and_matching,
    ):
        payload = {
            "food_name": "DB저장확인",
            "ingredients_used": [],
            "meal_type": "lunch",
            "portion_size_g": 300.0,
        }
        response = await authed_client.post("/api/v1/meals/save-recommended", json=payload)
        assert response.status_code == 200

        history_id = response.json()["data"]["history_id"]
        stmt = select(UserFoodHistory).where(UserFoodHistory.history_id == history_id)
        row = (await db_session.execute(stmt)).scalar_one_or_none()

        assert row is not None
        assert row.user_id == TEST_USER_ID
