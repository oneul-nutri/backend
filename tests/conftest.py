from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.api.dependencies import require_authentication
from app.db.models import Food, User
from app.db.session import engine, get_session

# User ID injected into all authenticated test requests.
# Chosen to be outside any realistic production range.
TEST_USER_ID = 99999


@pytest.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """Unauthenticated client — use for testing 401 responses."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        yield client


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Real async DB session wrapped in a transaction that rolls back after each
    test.  The endpoint's session.commit() calls become SAVEPOINT RELEASEs so
    data written by the endpoint is visible to queries in the same test, but
    nothing is written to the real DB.

    Requires SQLAlchemy >= 2.0 (join_transaction_mode="create_savepoint").
    """
    # NullPool이 아닌 공유 engine을 쓰는 이유: session-scoped event loop 덕분에
    # 모든 connection이 같은 loop에 묶여 pool 재사용이 안전하다.
    # loop scope를 바꾸면 "Future attached to a different loop" 오류가 발생하므로
    # pytest.ini의 asyncio_default_fixture_loop_scope와 반드시 맞춰야 한다.
    async with engine.connect() as conn:
        await conn.begin()
        session = AsyncSession(
            bind=conn,
            # commit() 호출이 SAVEPOINT RELEASE로 변환되어 롤백 격리가 유지된다.
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )
        yield session
        await session.close()
        await conn.rollback()


_TEST_USERNAME = "pytest_internal_99999"
_TEST_EMAIL = "pytest_internal_99999@internal.test"


@pytest.fixture
async def test_user(db_session: AsyncSession) -> User:
    """Insert a minimal User row for TEST_USER_ID. Rolled back after each test."""
    await db_session.execute(
        delete(User).where(
            (User.user_id == TEST_USER_ID) | (User.username == _TEST_USERNAME)
        )
    )
    await db_session.flush()
    user = User(
        user_id=TEST_USER_ID,
        username=_TEST_USERNAME,
        email=_TEST_EMAIL,
        password="hashed",
        health_goal="maintain",
    )
    db_session.add(user)
    await db_session.flush()
    return user


# food_ids used across test_meals_critical.py — must exist in Food table (FK)
# "DEFINITELY_NOT_IN_DB_XYZ99999" is intentionally absent from food_nutrients
_TEST_FOOD_IDS = [
    "TEST_FAKE_001",
    "DEFINITELY_NOT_IN_DB_XYZ99999",
    "MULTI_A",
    "MULTI_B",
    "PAGE_0",
    "PAGE_1",
    "PAGE_2",
]


@pytest.fixture
async def test_foods(db_session: AsyncSession) -> None:
    """Insert minimal Food rows for test food_ids. Rolled back after each test."""
    await db_session.execute(delete(Food).where(Food.food_id.in_(_TEST_FOOD_IDS)))
    await db_session.flush()
    for food_id in _TEST_FOOD_IDS:
        db_session.add(Food(food_id=food_id, food_name=f"테스트_{food_id}"))
    await db_session.flush()


@pytest.fixture
async def authed_client(
    db_session: AsyncSession,
    test_user: User,
    test_foods: None,
) -> AsyncGenerator[AsyncClient, None]:
    """
    Authenticated client with:
    - require_authentication overridden → returns TEST_USER_ID
    - get_session overridden → yields the test db_session (rolls back after test)
    """

    async def _override_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    async def _override_auth() -> int:
        return TEST_USER_ID

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[require_authentication] = _override_auth

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        yield client

    app.dependency_overrides.clear()
