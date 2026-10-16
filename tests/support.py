"""Shared test doubles for the application ports, plus the example from the task brief."""

from collections.abc import Collection, Mapping
from uuid import UUID

SAMPLE_LIST_1 = ["first string", "second string", "third string"]
SAMPLE_LIST_2 = ["other string", "another string", "last string"]
SAMPLE_BODY = {"list_1": SAMPLE_LIST_1, "list_2": SAMPLE_LIST_2}
SAMPLE_OUTPUT = (
    "FIRST STRING, OTHER STRING, SECOND STRING, ANOTHER STRING, THIRD STRING, LAST STRING"
)


class CountingTransformer:
    """Uppercases like the real adapter, records every call, fails on chosen inputs."""

    def __init__(self, fail_on: Collection[str] = ()) -> None:
        self.calls: list[str] = []
        self._fail_on = set(fail_on)

    async def transform(self, value: str) -> str:
        self.calls.append(value)
        if value in self._fail_on:
            raise RuntimeError(f"transformer unavailable for {value!r}")
        return value.upper()


class InMemoryCache:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get_many(self, values: Collection[str]) -> dict[str, str]:
        return {value: self.data[value] for value in values if value in self.data}

    async def put_many(self, results: Mapping[str, str]) -> None:
        for value, output in results.items():
            self.data.setdefault(value, output)


class InMemoryPayloads:
    def __init__(self) -> None:
        self.data: dict[UUID, str] = {}

    async def get(self, payload_id: UUID) -> str | None:
        return self.data.get(payload_id)

    async def add(self, payload_id: UUID, output: str) -> None:
        self.data.setdefault(payload_id, output)
