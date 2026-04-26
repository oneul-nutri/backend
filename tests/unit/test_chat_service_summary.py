"""
ChatService._generate_incremental_summary 서비스 단위 계약 테스트.

TODO #4 Step 5에서 LangChain LCEL(prompt | llm | StrOutputParser)에서
OpenAI SDK chat.completions.create로 전환된다.

용도: /api/v1/chat_v2 라우트가 `summarize_conversation_if_needed`를
background task로 예약 → 내부에서 `_generate_incremental_summary` 호출.
실패 시 background task 런타임에서 예외가 흡수되므로 서비스는
fallback 없이 예외를 그대로 전파한다 (현재 행동 보존).

Mock guard values 원칙:
- mock 반환 문자열은 "요약A" 같은 고정 guard 값 사용.
- mock이 안 타면 파싱/코드 경로 어디선가 다른 값이 리턴 → 바로 드러남.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.chat_service import ChatService

# Step 2에서 학습한 규칙: 공유 engine 풀이 session loop에 묶이도록 강제.
pytestmark = pytest.mark.asyncio(loop_scope="session")

# Guard value: 이 문자열이 반환되지 않으면 mock이 타지 않은 것.
_MOCK_SUMMARY = "사용자는 단백질 섭취 목표를 설정했고 저염 식단을 선호함."


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


def _service_with_mock(mock_client: MagicMock) -> ChatService:
    """OPENAI_API_KEY 체크 + redis/db 의존성 우회."""
    service = ChatService.__new__(ChatService)
    service.redis_client = None
    service.db_session = None
    service.client = mock_client
    return service


async def test_generate_summary_returns_llm_text():
    """LLM mock이 응답한 텍스트를 그대로 반환한다.

    guard: _MOCK_SUMMARY 문자열이 반환되지 않으면 mock 미작동.
    """
    mock_client = _make_openai_mock(_MOCK_SUMMARY)
    service = _service_with_mock(mock_client)

    result = await service._generate_incremental_summary(
        old_summary="이전 요약 없음",
        full_chat_history="user: 단백질 많이 먹고 싶어\nassistant: 닭가슴살을 추천합니다.",
    )

    assert result == _MOCK_SUMMARY
    mock_client.chat.completions.create.assert_awaited_once()


async def test_generate_summary_passes_context_to_prompt():
    """old_summary와 full_chat_history가 user 메시지 content에 포함되어야 한다."""
    mock_client = _make_openai_mock(_MOCK_SUMMARY)
    service = _service_with_mock(mock_client)

    old_summary = "이전엔 탄수화물 관련 대화함"
    chat_history = "user: 저염식 식단 알려줘\nassistant: 나트륨 2g 이하 권장"

    await service._generate_incremental_summary(
        old_summary=old_summary,
        full_chat_history=chat_history,
    )

    call_kwargs = mock_client.chat.completions.create.await_args.kwargs
    messages = call_kwargs["messages"]
    # 마지막 메시지가 user role (system + user 순서)
    user_msg = next(m for m in messages if m["role"] == "user")
    user_text = user_msg["content"]

    assert old_summary in user_text
    assert chat_history in user_text


async def test_generate_summary_propagates_exception_on_llm_failure():
    """LLM 호출이 예외를 던지면 service가 흡수하지 않고 그대로 전파한다.

    현재 행동 보존: 이 메서드는 background task에서 호출되므로
    fallback이 없다 (chat_v2.py의 background_tasks.add_task 경유).
    TODO #4 중 기능 변경 금지 원칙에 따라 fallback 추가하지 않음.
    """
    failing_client = MagicMock()
    failing_client.chat.completions.create = AsyncMock(
        side_effect=RuntimeError("LLM unavailable")
    )
    service = _service_with_mock(failing_client)

    with pytest.raises(RuntimeError, match="LLM unavailable"):
        await service._generate_incremental_summary(
            old_summary="",
            full_chat_history="대화 내용",
        )