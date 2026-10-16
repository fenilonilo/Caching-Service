from uuid import uuid4

from caching_service.application.service import PayloadService
from caching_service.infrastructure.repositories import (
    SqlPayloadRepository,
    SqlTransformationCache,
)


async def test_cache_returns_only_stored_values(sessions) -> None:
    cache = SqlTransformationCache(sessions)
    await cache.put_many({"a": "A", "b": "B"})
    assert await cache.get_many(["a", "b", "missing"]) == {"a": "A", "b": "B"}


async def test_cache_round_trips_non_ascii_and_long_strings(sessions) -> None:
    long_value = "x" * 10_000  # longer than a PostgreSQL B-tree entry may be
    cache = SqlTransformationCache(sessions)
    await cache.put_many(
        {"straße": "STRASSE", "café ☕": "CAFÉ ☕", long_value: long_value.upper()}
    )
    assert await cache.get_many(["straße", "café ☕", long_value]) == {
        "straße": "STRASSE",
        "café ☕": "CAFÉ ☕",
        long_value: long_value.upper(),
    }


async def test_cache_put_is_idempotent_and_keeps_first_value(sessions) -> None:
    cache = SqlTransformationCache(sessions)
    await cache.put_many({"a": "A"})
    await cache.put_many({"a": "ignored", "b": "B"})  # concurrent writers must not fail
    assert await cache.get_many(["a", "b"]) == {"a": "A", "b": "B"}


async def test_cache_accepts_empty_input(sessions) -> None:
    cache = SqlTransformationCache(sessions)
    await cache.put_many({})
    assert await cache.get_many([]) == {}


async def test_payload_round_trip_and_unknown_id(sessions) -> None:
    payloads = SqlPayloadRepository(sessions)
    payload_id = uuid4()
    await payloads.add(payload_id, "A, B")
    assert await payloads.get(payload_id) == "A, B"
    assert await payloads.get(uuid4()) is None


async def test_adding_existing_payload_is_a_noop(sessions) -> None:
    payloads = SqlPayloadRepository(sessions)
    payload_id = uuid4()
    await payloads.add(payload_id, "A, B")
    await payloads.add(payload_id, "A, B")  # two identical POSTs racing each other
    assert await payloads.get(payload_id) == "A, B"


async def test_no_connection_is_held_while_the_transformer_runs(engine, sessions) -> None:
    checked_out_during_transform: list[int] = []

    class ProbeTransformer:
        async def transform(self, value: str) -> str:
            checked_out_during_transform.append(engine.pool.checkedout())
            return value.upper()

    service = PayloadService(
        ProbeTransformer(), SqlTransformationCache(sessions), SqlPayloadRepository(sessions)
    )
    await service.create(["a"], ["b"])

    # A held connection would starve the pool for the whole (slow, remote) transformer call.
    assert checked_out_during_transform == [0, 0]
