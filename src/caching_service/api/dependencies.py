"""Wiring: routes receive the application's single PayloadService, built in main.create_app()."""

from typing import Annotated

from fastapi import Depends, Request

from caching_service.application.service import PayloadService


def get_payload_service(request: Request) -> PayloadService:
    # Safe to share across requests: the service holds no state, and its SQL adapters open a
    # short session per call instead of keeping a connection for the whole request.
    service: PayloadService = request.app.state.service
    return service


PayloadServiceDep = Annotated[PayloadService, Depends(get_payload_service)]
