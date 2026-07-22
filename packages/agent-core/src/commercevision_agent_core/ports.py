"""Runtime ports implemented by the application layer."""

from __future__ import annotations

from typing import Any, Protocol

from commercevision_domain import StepType, WorkflowStatus
from commercevision_tool_runtime import ToolResult


class NodeClaimLike(Protocol):
    workflow_version: int
    step_id: str
    lease_token: str | None
    already_completed: bool
    output_data: dict[str, Any] | None


class HumanWaitLike(Protocol):
    workflow_version: int
    step_id: str
    already_completed: bool
    output_data: dict[str, Any] | None


class AttemptClaimLike(Protocol):
    attempt_id: str
    already_completed: bool
    result_data: dict[str, Any] | None


class NodeLifecyclePort(Protocol):
    def begin_node(
        self,
        *,
        workflow_id: str,
        expected_workflow_version: int,
        step_key: str,
        step_type: StepType,
        running_state: WorkflowStatus,
        node_name: str,
        lease_owner: str,
        trace_id: str,
        input_data: dict[str, Any] | None = None,
    ) -> NodeClaimLike: ...

    def complete_node(
        self,
        *,
        workflow_id: str,
        step_id: str,
        lease_token: str,
        target_state: WorkflowStatus,
        next_node: str,
        trace_id: str,
        output_data: dict[str, Any] | None = None,
        output_ref: str | None = None,
        workflow_result: dict[str, Any] | None = None,
    ) -> int: ...

    def begin_human_wait(
        self,
        *,
        workflow_id: str,
        expected_workflow_version: int,
        step_key: str,
        step_type: StepType,
        lease_owner: str,
        trace_id: str,
    ) -> HumanWaitLike: ...

    def complete_human_wait(
        self,
        *,
        workflow_id: str,
        step_id: str,
        output_data: dict[str, Any],
        trace_id: str,
    ) -> int: ...

    def begin_attempt(
        self,
        *,
        workflow_id: str,
        step_id: str,
        idempotency_key: str,
        request_data: dict[str, Any],
    ) -> AttemptClaimLike: ...

    def complete_attempt(
        self,
        *,
        idempotency_key: str,
        result: ToolResult,
    ) -> dict[str, Any]: ...

    def fail_node(self, **kwargs: Any) -> None: ...
