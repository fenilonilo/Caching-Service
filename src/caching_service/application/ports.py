"""Boundaries the use cases depend on. Adapters in `infrastructure` implement them, which
keeps the application layer free of SQLAlchemy, HTTP and the concrete transformer."""

from collections.abc import Collection, Mapping
from typing import Protocol
from uuid import UUID


class Transformer(Protocol):
    """The external string transformation service. Calls are expensive: minimise them."""

    async def transform(self, value: str) -> str: ...


class TransformationCache(Protocol):
    async def get_many(self, values: Collection[str]) -> dict[str, str]:
        """Cached outputs for the given inputs; inputs never transformed are left out."""
        ...

    async def put_many(self, results: Mapping[str, str]) -> None:
        """Store outputs; entries that already exist are kept as they are."""
        ...


class PayloadRepository(Protocol):
    async def get(self, payload_id: UUID) -> str | None: ...

    async def add(self, payload_id: UUID, output: str) -> None:
        """Store a payload; adding an existing id is a no-op, since creators may race."""
        ...
