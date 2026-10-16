from uuid import uuid4

import pytest

from caching_service.application.service import (
    CreatedPayload,
    PayloadNotFoundError,
    PayloadService,
    TransformerError,
)
from tests.support import (
    SAMPLE_LIST_1,
    SAMPLE_LIST_2,
    SAMPLE_OUTPUT,
    CountingTransformer,
    InMemoryCache,
    InMemoryPayloads,
)


@pytest.fixture
def transformer() -> CountingTransformer:
    return CountingTransformer(fail_on={"boom"})


@pytest.fixture
def cache() -> InMemoryCache:
    return InMemoryCache()


@pytest.fixture
def payloads() -> InMemoryPayloads:
    return InMemoryPayloads()


@pytest.fixture
def service(transformer, cache, payloads) -> PayloadService:
    return PayloadService(transformer, cache, payloads)


async def test_create_then_get_returns_interleaved_transformed_output(service) -> None:
    created = await service.create(SAMPLE_LIST_1, SAMPLE_LIST_2)
    assert created.created
    assert await service.get(created.id) == SAMPLE_OUTPUT


async def test_identical_request_reuses_id_without_transformer_calls(service, transformer) -> None:
    first = await service.create(SAMPLE_LIST_1, SAMPLE_LIST_2)
    transformer.calls.clear()
    second = await service.create(SAMPLE_LIST_1, SAMPLE_LIST_2)
    assert second == CreatedPayload(id=first.id, created=False)
    assert transformer.calls == []


async def test_each_distinct_string_is_transformed_once(service, transformer) -> None:
    created = await service.create(["a", "b", "a"], ["b", "a", "c"])
    assert sorted(transformer.calls) == ["a", "b", "c"]
    assert await service.get(created.id) == "A, B, B, A, A, C"


async def test_new_payload_only_transforms_strings_not_seen_before(service, transformer) -> None:
    await service.create(["a", "b"], ["c", "d"])
    transformer.calls.clear()
    await service.create(["d", "c"], ["b", "e"])
    assert transformer.calls == ["e"]


async def test_get_unknown_id_raises_not_found(service) -> None:
    with pytest.raises(PayloadNotFoundError):
        await service.get(uuid4())


async def test_transformer_failure_caches_successes_and_stores_no_payload(
    service, transformer, cache, payloads
) -> None:
    with pytest.raises(TransformerError):
        await service.create(["ok", "boom"], ["fine", "other"])
    assert cache.data == {"ok": "OK", "fine": "FINE", "other": "OTHER"}
    assert payloads.data == {}

    transformer.calls.clear()
    with pytest.raises(TransformerError):
        await service.create(["ok", "boom"], ["fine", "other"])
    assert transformer.calls == ["boom"]  # a retry only pays for what failed
