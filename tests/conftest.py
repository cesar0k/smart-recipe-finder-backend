import asyncio
import os
from typing import AsyncGenerator, Generator

import httpx
import pytest
from _pytest.fixtures import FixtureRequest
from alembic.config import Config
from httpx import ASGITransport
from pytest import MonkeyPatch
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy_utils import create_database, database_exists, drop_database

from alembic import command
from app.core.security import create_access_token, hash_password
from app.core.vector_store import VectorStore
from app.models.recipe import Recipe
from app.models.user import User
from tests.testing_config import testing_settings


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
def set_testing_settings() -> None:
    os.environ["POSTGRES_DB"] = testing_settings.TEST_DB_NAME
    os.environ["CHROMA_COLLECTION_NAME"] = "recipes_test_collection"


@pytest.fixture(scope="session", autouse=True)
def prepare_db() -> Generator[None, None, None]:
    if database_exists(testing_settings.SYNC_TEST_DATABASE_ADMIN_URL):
        drop_database(testing_settings.SYNC_TEST_DATABASE_ADMIN_URL)
    create_database(testing_settings.SYNC_TEST_DATABASE_ADMIN_URL)

    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option(
        "sqlalchemy.url", testing_settings.ASYNC_TEST_DATABASE_ADMIN_URL
    )
    command.upgrade(alembic_cfg, "head")

    yield

    drop_database(testing_settings.SYNC_TEST_DATABASE_ADMIN_URL)


@pytest.fixture(scope="session")
async def db_engine() -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine(
        testing_settings.ASYNC_TEST_DATABASE_ADMIN_URL, pool_pre_ping=True
    )
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
def test_vector_store() -> Generator[VectorStore, None, None]:
    store = VectorStore(force_new=True)
    yield store
    try:
        store.client.delete_collection(store.collection_name)
    except Exception:
        pass


@pytest.fixture(scope="function")
async def async_client(
    db_engine: AsyncEngine,
    test_vector_store: VectorStore,
    monkeypatch: MonkeyPatch,
    request: FixtureRequest,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    from app.db.session import get_db
    from app.main import app

    monkeypatch.setattr("app.services.recipe_service.vector_store", test_vector_store)
    monkeypatch.setattr("app.core.vector_store.vector_store", test_vector_store)

    is_eval_test = (
        request.node.get_closest_marker("eval") is not None
        or request.node.get_closest_marker("no_db_cleanup") is not None
    )

    if not is_eval_test:
        test_vector_store.clear()
        async with db_engine.begin() as conn:
            await conn.execute(delete(Recipe))

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with async_sessionmaker(
            bind=db_engine, expire_on_commit=False
        )() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
async def auth_headers(
    db_engine: AsyncEngine,
) -> AsyncGenerator[dict[str, str], None]:
    """Create a test admin user and return auth headers with a valid JWT."""
    async with async_sessionmaker(
        bind=db_engine, expire_on_commit=False
    )() as session:
        user = User(
            email="test-admin@test.local",
            username="test_admin",
            hashed_password=hash_password("test_password"),
            role="admin",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        token = create_access_token(user.id, user.role)
        yield {"Authorization": f"Bearer {token}"}

        await session.delete(user)
        await session.commit()
