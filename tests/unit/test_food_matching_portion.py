"""
FoodMatchingService.interpret_portion 서비스 단위 계약 테스트.

TODO #4 Step 3의 실질 LLM 경로는 이 메서드 하나뿐이다
(_gpt_similarity_match는 중복 __init__ 버그로 항상 dead였음 — 삭제됨).

Mock guard values 원칙:
- happy path 반환값은 fallback 기본값(100.0g)과 다른 숫자(450.0)로 고정.
- LLM mock이 안 타고 fallback으로 흘러가면 450.0 → 100.0 불일치로 실패.
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.food_matching_service import FoodMatchingService

# pytest.ini의 asyncio_default_fixture_loop_scope=session 과 loop scope 일치.
# 미설정 시 공유 engine 풀이 다른 loop에서 소비되어 후속 DB 테스트가 깨진다.
pytestmark = pytest.mark.asyncio(loop_scope="session")


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


def _service_with_mock(mock_client: MagicMock | None) -> FoodMatchingService:
    """OPENAI_API_KEY 체크를 우회하고 client만 주입한 서비스 인스턴스."""
    service = FoodMatchingService.__new__(FoodMatchingService)
    service.client = mock_client
    return service


async def test_interpret_portion_returns_llm_weight_on_valid_json():
    """LLM이 유효 JSON을 반환하면 weight_g를 float로 돌려준다.

    guard: 450.0은 fallback 기본값 100.0과 달라서, mock이 안 타면 테스트가 실패함.
    """
    mock_client = _make_openai_mock(json.dumps({"weight_g": 450}))
    service = _service_with_mock(mock_client)

    result = await service.interpret_portion("라면", "큰 그릇")

    assert result == 450.0
    mock_client.chat.completions.create.assert_awaited_once()


async def test_interpret_portion_falls_back_on_llm_error():
    """LLM 호출이 예외를 던지면 기본값 100.0g를 돌려준다."""
    failing_client = MagicMock()
    failing_client.chat.completions.create = AsyncMock(
        side_effect=Exception("LLM unavailable")
    )
    service = _service_with_mock(failing_client)

    result = await service.interpret_portion("불명 음식", "한 그릇")

    assert result == 100.0


async def test_interpret_portion_returns_default_when_client_missing():
    """OPENAI_API_KEY 미설정 시(`client is None`) LLM 호출 없이 100.0g 반환."""
    service = _service_with_mock(None)

    result = await service.interpret_portion("라면", "큰 그릇")

    assert result == 100.0
