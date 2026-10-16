"""SQL adapters for the application ports.

Each method opens a short session of its own: every write commits on its own, so transformer
results, which are expensive to obtain, survive even when the rest of the request fails, and
no connection is held between calls, so none is held while the transformer runs.
"""

import hashlib
from collections.abc import Collection, Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from caching_service.infrastructure.database import Base, PayloadRow, TransformationRow


def _cache_key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _insert_ignoring_conflicts(
    session: AsyncSession, model: type[Base]
) -> postgresql.Insert | sqlite.Insert:
    # Concurrent requests may write the same row; ON CONFLICT DO NOTHING makes that harmless.
    if session.get_bind().dialect.name == "postgresql":
        return postgresql.insert(model).on_conflict_do_nothing()
    return sqlite.insert(model).on_conflict_do_nothing()


class SqlTransformationCache:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get_many(self, values: Collection[str]) -> dict[str, str]:
        value_by_key = {_cache_key(value): value for value in values}
        if not value_by_key:
            return {}
        async with self._sessions() as session:
            rows = await session.execute(
                select(TransformationRow.key, TransformationRow.output).where(
                    TransformationRow.key.in_(list(value_by_key))
                )
            )
            return {value_by_key[key]: output for key, output in rows}

    async def put_many(self, results: Mapping[str, str]) -> None:
        if not results:
            return
        rows = [{"key": _cache_key(value), "output": output} for value, output in results.items()]
        async with self._sessions.begin() as session:
            await session.execute(_insert_ignoring_conflicts(session, TransformationRow), rows)


class SqlPayloadRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get(self, payload_id: UUID) -> str | None:
        async with self._sessions() as session:
            return await session.scalar(
                select(PayloadRow.output).where(PayloadRow.id == payload_id)
            )

    async def add(self, payload_id: UUID, output: str) -> None:
        async with self._sessions.begin() as session:
            statement = _insert_ignoring_conflicts(session, PayloadRow).values(
                id=payload_id, output=output
            )
            await session.execute(statement)
