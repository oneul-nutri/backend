"""
chat_v2._generate_clarify_payload 서비스 단위 계약 테스트.

Step 7에서 LangChain LCEL(ChatPromptTemplate + ChatOpenAI.ainvoke)에서
OpenAI SDK로 전환됨. 반환 payload shape는 프론트 UI 분기의 계약이라
(action_type, needs_tool_call, message, suggestions) 보존 필수.

Mock guard value:
- mock 응답의 message를 "MOCK_CLARIFY_SENTINEL_AB"로 고정 → payload fallback
  기본값("사용자님의 요청을 이해했어요...")과 구분되어 mock 미작동 감지.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1.routes.chat_v2 import _generate_clarify_payload

pytestmark = pytest.mark.asyncio(loop_scope="session")


_MOCK_MESSAGE_SENTINEL = "MOCK_CLARIFY_SENTINEL_AB"


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


async def test_generate_clarify_payload_returns_llm_fields():
    """Happy path: LLM JSON 응답이 그대로 dict로 반환된다.

    Guard: _MOCK_MESSAGE_SENTINEL이 반환 payload["message"]에 들어있지 않으면
    mock이 타지 않고 fallback 경로로 흘러간 것.
    """
    mock_payload = {
        "response_id": "test-resp-1",
        "action_type": "TEXT_ONLY",
        "message": _MOCK_MESSAGE_SENTINEL,
        "suggestions": ["추천1", "추천2"],
        "needs_tool_call": False,
    }
    mock_client = _make_openai_mock(json.dumps(mock_payload))

    with patch("app.api.v1.routes.chat_v2.get_clarify_llm", return_value=mock_client):
        result = await _generate_clarify_payload(
            summary="이전 대화",
            user_message="안녕",
        )

    assert result["message"] == _MOCK_MESSAGE_SENTINEL
    assert result["action_type"] == "TEXT_ONLY"
    assert result["needs_tool_call"] is False


async def test_generate_clarify_payload_uses_json_mode():
    """SDK 호출 시 response_format={"type":"json_object"} 필수.

    JSON mode 빠지면 LLM이 자유 텍스트 반환 가능 → json.loads 실패 → fallback 경로.
    """
    mock_client = _make_openai_mock(json.dumps({"message": _MOCK_MESSAGE_SENTINEL}))

    with patch("app.api.v1.routes.chat_v2.get_clarify_llm", return_value=mock_client):
        await _generate_clarify_payload(summary="", user_message="안녕")

    call_kwargs = mock_client.chat.completions.create.await_args.kwargs
    assert call_kwargs["response_format"] == {"type": "json_object"}


async def test_generate_clarify_payload_fills_defaults_on_invalid_json():
    """LLM이 파싱 불가 응답을 주면 fallback 기본값으로 채운다.

    프론트 UI 분기 계약: action_type/message/response_id는 반드시 존재해야 함.
    """
    mock_client = _make_openai_mock("not json")

    with patch("app.api.v1.routes.chat_v2.get_clarify_llm", return_value=mock_client):
        result = await _generate_clarify_payload(summary="", user_message="안녕")

    # 기본값 필드 존재 확인
    assert "response_id" in result
    assert result.get("action_type") == "TEXT_ONLY"
    assert "message" in result
    # guard: 실제 fallback 메시지가 들어갔는지
    assert result["message"] != _MOCK_MESSAGE_SENTINEL