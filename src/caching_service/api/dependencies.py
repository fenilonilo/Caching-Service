"""Request-scoped wiring: one database session per request, shared by both repositories."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request

from caching_service.application.service import PayloadService
from caching_service.infrastructure.repositories import (
    SqlPayloadRepository,
    SqlTransformationCache,
)


async def get_payload_service(request: Request) -> AsyncIterator[PayloadService]:
    async with request.app.state.sessionmaker() as session:
        yield PayloadService(
            transformer=request.app.state.transformer,
            cache=SqlTransformationCache(session),
            payloads=SqlPayloadRepository(session),
        )


PayloadServiceDep = Annotated[PayloadService, Depends(get_payload_service)]
