"""Payload use cases: create (transform + interleave, reusing earlier work) and read."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from caching_service import domain
from caching_service.application.ports import (
    PayloadRepository,
    TransformationCache,
    Transformer,
)


class PayloadNotFoundError(LookupError):
    pass


class TransformerError(RuntimeError):
    """The external transformer failed; the results that did succeed are already cached."""


@dataclass(frozen=True, slots=True)
class CreatedPayload:
    id: UUID
    created: bool  # False when an identical payload already existed


class PayloadService:
    def __init__(
        self,
        transformer: Transformer,
        cache: TransformationCache,
        payloads: PayloadRepository,
    ) -> None:
        self._transformer = transformer
        self._cache = cache
        self._payloads = payloads

    async def create(self, list_1: Sequence[str], list_2: Sequence[str]) -> CreatedPayload:
        payload_id = domain.payload_id(list_1, list_2)
        # Identical input means identical output: skip the cache and the transformer entirely.
        if await self._payloads.get(payload_id) is not None:
            return CreatedPayload(id=payload_id, created=False)

        transformed = await self._transform_all({*list_1, *list_2})
        output = domain.build_output(
            [transformed[value] for value in list_1],
            [transformed[value] for value in list_2],
        )
        await self._payloads.add(payload_id, output)
        return CreatedPayload(id=payload_id, created=True)

    async def get(self, payload_id: UUID) -> str:
        output = await self._payloads.get(payload_id)
        if output is None:
            raise PayloadNotFoundError(payload_id)
        return output

    async def _transform_all(self, values: set[str]) -> dict[str, str]:
        """Transformed form of every distinct value, calling the transformer only on misses."""
        known = await self._cache.get_many(values)
        missing = [value for value in values if value not in known]
        if not missing:
            return known

        # ponytail: unbounded fan-out (capped by request limits); wrap the calls in an
        # asyncio.Semaphore if the real service enforces a rate limit.
        results = await asyncio.gather(
            *(self._transformer.transform(value) for value in missing),
            return_exceptions=True,
        )
        fresh = {
            value: result
            for value, result in zip(missing, results, strict=True)
            if isinstance(result, str)
        }
        # Persist successes before reporting failures, so a retry only pays for what failed.
        await self._cache.put_many(fresh)
        failure = next((r for r in results if isinstance(r, BaseException)), None)
        if failure is not None:
            raise TransformerError("transformer call failed") from failure
        return known | fresh
