from uuid import uuid4

from caching_service.infrastructure.repositories import (
    SqlPayloadRepository,
    SqlTransformationCache,
)


async def test_cache_returns_only_stored_values(session) -> None:
    cache = SqlTransformationCache(session)
    await cache.put_many({"a": "A", "b": "B"})
    assert await cache.get_many(["a", "b", "missing"]) == {"a": "A", "b": "B"}


async def test_cache_round_trips_non_ascii_and_long_strings(session) -> None:
    long_value = "x" * 10_000  # longer than a PostgreSQL B-tree entry may be
    cache = SqlTransformationCache(session)
    await cache.put_many(
        {"straße": "STRASSE", "café ☕": "CAFÉ ☕", long_value: long_value.upper()}
    )
    assert await cache.get_many(["straße", "café ☕", long_value]) == {
        "straße": "STRASSE",
        "café ☕": "CAFÉ ☕",
        long_value: long_value.upper(),
    }


async def test_cache_put_is_idempotent_and_keeps_first_value(session) -> None:
    cache = SqlTransformationCache(session)
    await cache.put_many({"a": "A"})
    await cache.put_many({"a": "ignored", "b": "B"})  # concurrent writers must not fail
    assert await cache.get_many(["a", "b"]) == {"a": "A", "b": "B"}


async def test_cache_accepts_empty_input(session) -> None:
    cache = SqlTransformationCache(session)
    await cache.put_many({})
    assert await cache.get_many([]) == {}


async def test_payload_round_trip_and_unknown_id(session) -> None:
    payloads = SqlPayloadRepository(session)
    payload_id = uuid4()
    await payloads.add(payload_id, "A, B")
    assert await payloads.get(payload_id) == "A, B"
    assert await payloads.get(uuid4()) is None


async def test_adding_existing_payload_is_a_noop(session) -> None:
    payloads = SqlPayloadRepository(session)
    payload_id = uuid4()
    await payloads.add(payload_id, "A, B")
    await payloads.add(payload_id, "A, B")  # two identical POSTs racing each other
    assert await payloads.get(payload_id) == "A, B"
