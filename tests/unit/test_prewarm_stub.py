"""
POST /api/v1/chat/prewarm 계약 테스트.

Step 7에서 LangChain ReAct 에이전트 워밍업을 no-op stub으로 대체했다.
프론트는 fire-and-forget 방식으로만 이 엔드포인트를 호출하므로
2xx + 최소 shape 유지가 계약의 전부다.
"""
from typing import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from pytest import mark

from app.api.dependencies import get_current_active_user
from app.db.models import User
from app.main import app

pytestmark = pytest.mark.asyncio(loop_scope="session")


TEST_USER_ID = 99999


def _make_mock_user() -> User:
    user = User()
    user.user_id = TEST_USER_ID
    user.username = "pytest_prewarm_user"
    user.email = "pytest_prewarm@internal.test"
    user.password = "hashed"
    user.health_goal = "maintain"
    return user


@pytest.fixture
async def prewarm_client() -> AsyncGenerator[AsyncClient, None]:
    async def _override_user() -> User:
        return _make_mock_user()

    app.dependency_overrides[get_current_active_user] = _override_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        yield client
    app.dependency_overrides.clear()


async def test_prewarm_requires_authentication(async_client: AsyncClient):
    """비인증 요청은 401."""
    response = await async_client.post("/api/v1/chat/prewarm")
    assert response.status_code == 401


async def test_prewarm_returns_ready_shape(prewarm_client: AsyncClient):
    """stub이 2xx + success/message 필드를 유지해야 한다.

    프론트(recommend/page.tsx)는 body를 검사하지 않지만, 하위 호환 안전을 위해
    기존 shape 유지.
    """
    response = await prewarm_client.post("/api/v1/chat/prewarm")
    assert response.status_code == 200
    body = response.json()
    assert body.get("success") is True
    assert "message" in body