"""Fixtures for tests that run the real adapters against a throwaway database."""

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from caching_service.infrastructure.database import Base, create_schema


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
