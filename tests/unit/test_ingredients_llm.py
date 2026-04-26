"""
ingredients.py LLM 호출 2곳 계약 테스트.

Step 7에서 LangChain(ChatOpenAI + ainvoke)에서 OpenAI SDK로 전환됐다.
라우트 레벨 테스트가 없어서 서비스 수준 단위 테스트가 유일한 regression guard.

대상:
1. save_major_conversation — 대화 요약 저장 (실패 시 raw_text[:400] fallback)
2. 라우트의 추천 LLM 호출 — 여기서는 save_major_conversation만 단위 테스트.
   두 번째 호출은 동일 SDK 패턴이고 save_major_conversation이 패턴 회귀를 대표함.

Mock guard value:
- mock 반환 "MOCK_SUMMARY_SENTINEL_42"는 fallback raw_text[:400] 결과와 절대 겹치지 않음.
- mock 미작동 시 user.major_conversation이 raw_text[:400]가 되어 assertion 실패.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1.routes.ingredients import save_major_conversation
from app.db.models import User

pytestmark = pytest.mark.asyncio(loop_scope="session")


_MOCK_SUMMARY_SENTINEL = "MOCK_SUMMARY_SENTINEL_42"


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


def _make_session_mock() -> MagicMock:
    """SQLAlchemy AsyncSession mock — commit만 필요."""
    session = MagicMock()
    session.commit = AsyncMock()
    return session


def _make_user() -> User:
    user = User()
    user.user_id = 99999
    user.username = "pytest_user"
    user.major_conversation = None
    return user


async def test_save_major_conversation_stores_llm_summary():
    """Happy path: LLM 요약 결과가 user.major_conversation에 저장된다.

    guard: _MOCK_SUMMARY_SENTINEL이 저장된 텍스트에 포함되지 않으면 mock 미작동.
    """
    mock_client = _make_openai_mock(_MOCK_SUMMARY_SENTINEL)
    session = _make_session_mock()
    user = _make_user()

    with patch("app.api.v1.routes.ingredients.get_recommendation_llm", return_value=mock_client):
        await save_major_conversation(session, user, raw_text="원본 대화")

    assert _MOCK_SUMMARY_SENTINEL in (user.major_conversation or "")
    session.commit.assert_awaited_once()


async def test_save_major_conversation_falls_back_to_raw_text_on_llm_error():
    """LLM 예외 발생 시 raw_text[:400]로 대체한다."""
    failing_client = MagicMock()
    failing_client.chat.completions.create = AsyncMock(side_effect=Exception("LLM unavailable"))
    session = _make_session_mock()
    user = _make_user()
    raw_text = "긴 원본 대화 " * 100  # 충분히 긴 텍스트

    with patch("app.api.v1.routes.ingredients.get_recommendation_llm", return_value=failing_client):
        await save_major_conversation(session, user, raw_text=raw_text)

    assert user.major_conversation == raw_text[:400]
    session.commit.assert_awaited_once()


async def test_save_major_conversation_truncates_to_2000_chars():
    """요약 결과가 2000자를 초과하면 truncate.

    현재 구현의 하드 리미트(`summary[:2000]`) 보존.
    """
    long_summary = "가" * 3000  # 3000자 요약 (비현실적이지만 상한 테스트)
    mock_client = _make_openai_mock(long_summary)
    session = _make_session_mock()
    user = _make_user()

    with patch("app.api.v1.routes.ingredients.get_recommendation_llm", return_value=mock_client):
        await save_major_conversation(session, user, raw_text="원본")

    assert len(user.major_conversation) == 2000