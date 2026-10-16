"""Stand-in for the external transformation service the task asks us to simulate."""

import asyncio


class UppercaseTransformer:
    """Uppercases after a configurable delay, so the cost of a remote call is visible."""

    def __init__(self, latency_seconds: float = 0.0) -> None:
        self._latency_seconds = latency_seconds

    async def transform(self, value: str) -> str:
        await asyncio.sleep(self._latency_seconds)
        return value.upper()
