"""Composition root: the only place that picks concrete adapters for the ports."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from caching_service.api.routes import router
from caching_service.application.ports import Transformer
from caching_service.application.service import PayloadService
from caching_service.config import Settings
from caching_service.infrastructure.database import create_schema
from caching_service.infrastructure.repositories import (
    SqlPayloadRepository,
    SqlTransformationCache,
)
from caching_service.infrastructure.transformer import UppercaseTransformer


async def _reject_invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
    # The default handler echoes each offending value back. JSON allows lone surrogate escapes
    # such as "\ud800"; pydantic rejects them, but they cannot be UTF-8 encoded, so echoing
    # one turns the 422 into a 500.
    errors = [{key: value for key, value in e.items() if key != "input"} for e in exc.errors()]
    return JSONResponse(
        {"detail": jsonable_encoder(errors)}, status_code=status.HTTP_422_UNPROCESSABLE_CONTENT
    )


def create_app(settings: Settings | None = None, transformer: Transformer | None = None) -> FastAPI:
    """Build the app. Tests pass their own settings and transformer instead of patching."""
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_async_engine(config.database_url)
        await create_schema(engine)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        app.state.service = PayloadService(
            transformer=transformer or UppercaseTransformer(config.transformer_latency_seconds),
            cache=SqlTransformationCache(sessions),
            payloads=SqlPayloadRepository(sessions),
        )
        yield
        await engine.dispose()

    app = FastAPI(title="Caching Service", lifespan=lifespan)
    app.exception_handler(RequestValidationError)(_reject_invalid_request)
    app.include_router(router)
    return app
