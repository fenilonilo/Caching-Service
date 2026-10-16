import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from caching_service.api.dependencies import PayloadServiceDep
from caching_service.api.schemas import PayloadCreated, PayloadOut, PayloadRequest
from caching_service.application.service import PayloadNotFoundError, TransformerError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payload", tags=["payload"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {"model": PayloadCreated, "description": "Payload already existed"},
        status.HTTP_502_BAD_GATEWAY: {"description": "Transformer service failed"},
    },
)
async def create_payload(
    body: PayloadRequest, response: Response, service: PayloadServiceDep
) -> PayloadCreated:
    try:
        result = await service.create(body.list_1, body.list_2)
    except TransformerError:
        logger.exception("Transformer failed while creating a payload")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Transformer service failed, please retry"
        ) from None
    if not result.created:
        response.status_code = status.HTTP_200_OK
        return PayloadCreated(id=result.id, message="Payload already exists")
    return PayloadCreated(id=result.id, message="Payload created")


@router.get("/{payload_id}", responses={status.HTTP_404_NOT_FOUND: {"description": "Unknown id"}})
async def read_payload(payload_id: UUID, service: PayloadServiceDep) -> PayloadOut:
    try:
        return PayloadOut(output=await service.get(payload_id))
    except PayloadNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payload not found") from None
