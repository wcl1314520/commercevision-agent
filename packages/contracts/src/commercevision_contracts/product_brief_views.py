"""Least-privilege browser projections used by the ProductBrief workbench."""

from datetime import datetime, timedelta

from commercevision_domain import OperationState, WorkflowStatus
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProductBriefWorkflowContextResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    status: WorkflowStatus
    version: int = Field(ge=1)
    retention_deadline: datetime

    @field_validator("retention_deadline")
    @classmethod
    def require_utc_deadline(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("ProductBrief Workflow deadline must be timezone-aware UTC")
        return value


class ProductBriefOperationErrorResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=128)
    category: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=512)
    retryable: bool


class ProductBriefOperationStatusResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    state: OperationState
    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    error: ProductBriefOperationErrorResponseV1 | None
    version: int = Field(ge=1)
