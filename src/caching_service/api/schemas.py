"""HTTP contract. The limits bound how much work one request can trigger."""

from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

MAX_ITEMS = 1_000
MAX_STRING_LENGTH = 1_000


Item = Annotated[str, StringConstraints(max_length=MAX_STRING_LENGTH)]


class PayloadRequest(BaseModel):
    # Rejecting unknown fields turns typos such as "list1" into a 422 instead of silent data loss.
    model_config = ConfigDict(extra="forbid")

    list_1: list[Item] = Field(min_length=1, max_length=MAX_ITEMS)
    list_2: list[Item] = Field(min_length=1, max_length=MAX_ITEMS)

    @model_validator(mode="after")
    def _lists_have_same_length(self) -> Self:
        if len(self.list_1) != len(self.list_2):
            raise ValueError("list_1 and list_2 must have the same length")
        return self


class PayloadCreated(BaseModel):
    id: UUID
    message: str


class PayloadOut(BaseModel):
    output: str
