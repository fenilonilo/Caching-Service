"""Fixtures for tests that run the real adapters: a throwaway database and the app in-process."""

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from caching_service.config import Settings
from caching_service.infrastructure.database import Base, create_schema
from caching_service.main import create_app
from tests.support import CountingTransformer


def sqlite_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    # TEST_DATABASE_URL runs the repository tests against PostgreSQL instead (see README).
    return os.environ.get("TEST_DATABASE_URL", sqlite_url(tmp_path / "repositories.db"))


@pytest.fixture
async def session(database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(database_url)
    await create_schema(engine)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def transformer() -> CountingTransformer:
    return CountingTransformer(fail_on={"boom"})


@pytest.fixture
def client(tmp_path: Path, transformer: CountingTransformer) -> Iterator[TestClient]:
    # Always a fresh SQLite file: app tests need isolation, not a specific database engine.
    settings = Settings(database_url=sqlite_url(tmp_path / "app.db"))
    with TestClient(create_app(settings, transformer)) as client:
        yield client
