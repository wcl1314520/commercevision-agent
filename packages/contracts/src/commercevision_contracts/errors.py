"""Public error envelope shared by HTTP and event boundaries."""

from typing import Any

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    code: str
    message: str
    category: str
    retryable: bool
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str
    trace_id: str
