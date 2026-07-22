"""Stable health and metadata response contracts."""

from typing import Literal

from pydantic import BaseModel, Field

CheckStatus = Literal["ok", "skipped", "failed"]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    service: str
    version: str
    checks: dict[str, CheckStatus] = Field(default_factory=dict)


class ServiceMetadata(BaseModel):
    service: str
    version: str
    environment: str
    phase: str = "phase-0"
