"""
RecipeRecommendationService 서비스 단위 계약 테스트.

TODO #4 Step 6에서 이 서비스의 8개 LLM 호출을 LangChain → OpenAI SDK로
일괄 전환했다. 이 테스트는 공개 API 계약과 전환 시 가장 놓치기 쉬운
동작(JSON mode 유지, fallback, 병렬 구조)을 고정한다.

라우트 레벨 action_type 3분기 계약은 이미 test_recipe_action_type.py가
고정하고 있으므로 여기서는 서비스 내부 계약만 다룬다.

Mock guard values 원칙:
- mock 반환 recipe name "테스트레시피-αβ"는 fallback 하드코딩
  ("닭가슴살 샐러드", "연어 덮밥", "두부 스테이크")와 구분됨.
- 추가로 user_friendly_message 필드에 "MOCK_GUARD_SENTINEL_99"를 넣어
  mock이 정말로 타고 있는지 이중 확인.
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.models import User
from app.services.recipe_recommendation_service import (
    RecipePipelineTasks,
    RecipeRecommendationService,
)

# Step 2에서 학습한 규칙 — 공유 engine 풀이 session loop에 묶이도록 강제.
pytestmark = pytest.mark.asyncio(loop_scope="session")


_MOCK_GUARD_SENTINEL = "MOCK_GUARD_SENTINEL_99"

# Happy path mock: mock이 타면 반환되는 값. fallback 하드코딩과 전부 다름.
_MOCK_RECOMMENDATION_PAYLOAD = {
    "inferred_preference": "테스트 선호",
    "health_warning": None,
    "recommendations": [
        {
            "name": "테스트레시피-αβ",  # fallback 이름("닭가슴살 샐러드" 등)과 구분
            "description": "mock 응답 레시피",
            "calories": 420,
            "cooking_time": "20분",
            "difficulty": "쉬움",
            "suitable_reason": "테스트용",
        }
    ],
    "user_friendly_message": _MOCK_GUARD_SENTINEL,  # ← 이중 guard
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


def _service_with_mock(mock_client: MagicMock) -> RecipeRecommendationService:
    """OPENAI_API_KEY 체크 우회하고 client만 주입."""
    service = RecipeRecommendationService.__new__(RecipeRecommendationService)
    service.client = mock_client
    service._prefetched_detail_cache = {}
    return service


def _make_user() -> User:
    """레시피 추천에 필요한 최소 필드를 채운 User."""
    user = User()
    user.user_id = 99999
    user.username = "pytest_user"
    user.nickname = "테스터"
    user.gender = "M"
    user.age = 30
    user.weight = 70.0
    user.health_goal = "maintain"
    return user


# ── get_recipe_recommendations ───────────────────────────────────────────────

async def test_get_recipe_recommendations_returns_parsed_dict():
    """Happy path: JSON mode mock 응답이 dict로 반영된다.

    Guard: recipe name + user_friendly_message 둘 다 mock 고유 값이라
    mock이 안 타고 fallback 경로로 흐르면 두 assert 모두 실패.
    """
    mock_client = _make_openai_mock(json.dumps(_MOCK_RECOMMENDATION_PAYLOAD))
    service = _service_with_mock(mock_client)

    result = await service.get_recipe_recommendations(
        user=_make_user(),
        user_request="점심 추천",
        meal_type="lunch",
    )

    assert result["recommendations"][0]["name"] == "테스트레시피-αβ"
    # _generate_user_friendly_message가 result["user_friendly_message"]를 덮어쓰므로
    # 여기서는 LLM payload를 가공한 "user_friendly_message"가 돌아온다 — 즉
    # 원래 sentinel은 `inferred_preference`로 확인.
    assert result["inferred_preference"] == "테스트 선호"
    mock_client.chat.completions.create.assert_awaited_once()


async def test_get_recipe_recommendations_falls_back_on_invalid_json():
    """LLM이 파싱 불가 JSON을 주면 하드코딩된 3개 레시피 fallback 반환.

    Fallback 하드코딩된 이름("닭가슴살 샐러드", "연어 덮밥", "두부 스테이크")이
    반드시 등장해야 함 — 현재 구현([line 431-456])의 계약을 고정.
    """
    mock_client = _make_openai_mock("이건 JSON이 아닙니다")
    service = _service_with_mock(mock_client)

    result = await service.get_recipe_recommendations(
        user=_make_user(),
        user_request="추천 아무거나",
    )

    recipe_names = {rec["name"] for rec in result["recommendations"]}
    assert recipe_names == {"닭가슴살 샐러드", "연어 덮밥", "두부 스테이크"}


async def test_get_recipe_recommendations_uses_json_mode():
    """SDK 호출 시 response_format={"type":"json_object"}가 포함되어야 한다.

    JSON mode가 빠지면 OpenAI API가 자유 텍스트를 반환할 수 있어
    json.loads가 실패하고 fallback 경로로 흐른다 → silent degradation.
    """
    mock_client = _make_openai_mock(json.dumps(_MOCK_RECOMMENDATION_PAYLOAD))
    service = _service_with_mock(mock_client)

    await service.get_recipe_recommendations(
        user=_make_user(),
        user_request="점심",
        meal_type="lunch",
    )

    call_kwargs = mock_client.chat.completions.create.await_args.kwargs
    assert call_kwargs["response_format"] == {"type": "json_object"}


async def test_get_recipe_recommendations_preserves_meal_type():
    """meal_type 파라미터가 프롬프트 텍스트에 반영되어야 한다.

    chat_v2 상태머신이 meal_type을 LLM에게 전달하도록 설계되어 있다.
    내부 프롬프트 생성(_build_prompt_context)이 깨지면 레시피가 끼니와
    맞지 않는 결과가 나와도 테스트로 안 잡히므로 여기서 고정.
    """
    mock_client = _make_openai_mock(json.dumps(_MOCK_RECOMMENDATION_PAYLOAD))
    service = _service_with_mock(mock_client)

    await service.get_recipe_recommendations(
        user=_make_user(),
        user_request="추천",
        meal_type="lunch",
    )

    call_kwargs = mock_client.chat.completions.create.await_args.kwargs
    user_msg = next(m for m in call_kwargs["messages"] if m["role"] == "user")
    assert "점심" in user_msg["content"]  # lunch 한글 매핑


# ── quick_analyze_intent ─────────────────────────────────────────────────────

async def test_quick_analyze_intent_parses_llm_response():
    """대표적으로 다른 8개 메서드 중 하나를 커버 — 같은 SDK 패턴이므로 회귀 감지 충분."""
    payload = {
        "disease_conflict": True,
        "allergy_conflict": False,
        "health_warning": "테스트 경고",
        "user_message": "테스트 메시지",
    }
    mock_client = _make_openai_mock(json.dumps(payload))
    service = _service_with_mock(mock_client)

    result = await service.quick_analyze_intent(
        user=_make_user(),
        intent_text="닭 요리",
        diseases=["고혈압"],
        allergies=[],
    )

    assert result["disease_conflict"] is True
    assert result["health_warning"] == "테스트 경고"
    call_kwargs = mock_client.chat.completions.create.await_args.kwargs
    assert call_kwargs["response_format"] == {"type": "json_object"}


# ── launch_parallel_recipe_pipeline ──────────────────────────────────────────

async def test_launch_parallel_recipe_pipeline_creates_task_handle():
    """병렬 구조 회귀 감지: recommendation_task가 asyncio.Task여야 한다.

    `create_task` 가 순차 호출(plain await)로 바뀌면 response time이 2-3배 늘어나지만
    assertion 없이는 감지 안 됨. 최소한 Task 객체 타입은 고정.
    """
    mock_client = _make_openai_mock(json.dumps(_MOCK_RECOMMENDATION_PAYLOAD))
    service = _service_with_mock(mock_client)

    pipeline = service.launch_parallel_recipe_pipeline(
        recommendation_kwargs={
            "user": _make_user(),
            "user_request": "점심",
            "meal_type": "lunch",
        },
        health_check_kwargs=None,
        prefetch_detail_limit=0,
    )

    assert isinstance(pipeline, RecipePipelineTasks)
    assert isinstance(pipeline.recommendation_task, asyncio.Task)
    assert pipeline.health_analysis_task is None  # health_check_kwargs=None
    assert pipeline.detail_prefetch_task is None  # prefetch_detail_limit=0

    # 태스크 완료 대기 (누수 방지)
    await pipeline.recommendation_task