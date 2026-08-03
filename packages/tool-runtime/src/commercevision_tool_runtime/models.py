"""Stable tool invocation and result models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from commercevision_contracts import validate_workspace_id


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
    scopes: frozenset[str] = frozenset()
    purpose: str = ""
    provider: str = ""
    requires_derivative: bool = False
    maximum_result_count: int = 50
    maximum_candidate_count: int = 1_000
    maximum_output_bytes: int = 256 * 1024
    started_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)
        if self.maximum_result_count < 1:
            raise ValueError("tool result budget must be positive")
        if self.maximum_candidate_count < self.maximum_result_count:
            raise ValueError("tool candidate budget must cover the result budget")
        if self.maximum_output_bytes < 1:
            raise ValueError("tool output byte budget must be positive")


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
