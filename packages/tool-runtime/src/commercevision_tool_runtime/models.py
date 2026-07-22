"""Stable tool invocation and result models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    workflow_id: str
    workspace_id: str
    actor_id: str
    trace_id: str
    idempotency_key: str
    policy_version: str
    started_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    tool_name: str
    tool_version: str
    arguments: dict[str, Any]
    idempotency_key: str
    policy_version: str
    reason: str


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_name: str
    tool_version: str
    idempotency_key: str
    output: dict[str, Any]
    provider_request_id: str | None = None
    cost_amount_minor: int = 0
    currency: str = "CNY"
    completed_at: datetime = field(default_factory=utc_now)
