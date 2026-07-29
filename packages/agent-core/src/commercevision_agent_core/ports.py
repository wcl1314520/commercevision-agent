"""Runtime ports implemented by the application layer."""

from __future__ import annotations

from datetime import timedelta
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


class ProductBriefContinuationLike(Protocol):
    workspace_id: str
    product_brief_version_id: str
    product_brief_version_number: int
    approval_id: str | None


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
        product_brief_continuation: ProductBriefContinuationLike | None = None,
    ) -> NodeClaimLike: ...

    def complete_node(
        self,
        *,
        workflow_id: str,
        step_id: str,
        lease_token: str | None,
        target_state: WorkflowStatus,
        next_node: str,
        trace_id: str,
        output_data: dict[str, Any] | None = None,
        output_ref: str | None = None,
        workflow_result: dict[str, Any] | None = None,
        expected_workflow_version: int | None = None,
        product_brief_continuation: ProductBriefContinuationLike | None = None,
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
        product_brief_continuation: ProductBriefContinuationLike | None = None,
    ) -> HumanWaitLike: ...

    def complete_human_wait(
        self,
        *,
        workflow_id: str,
        step_id: str,
        output_data: dict[str, Any],
        trace_id: str,
        expected_workflow_version: int | None = None,
        product_brief_continuation: ProductBriefContinuationLike | None = None,
    ) -> int: ...

    def begin_attempt(
        self,
        *,
        workflow_id: str,
        step_id: str,
        idempotency_key: str,
        request_data: dict[str, Any],
        lease_token: str | None = None,
        expected_workflow_version: int | None = None,
        product_brief_continuation: ProductBriefContinuationLike | None = None,
    ) -> AttemptClaimLike: ...

    def complete_attempt(
        self,
        *,
        workflow_id: str,
        step_id: str,
        lease_token: str,
        idempotency_key: str,
        result: ToolResult,
        expected_workflow_version: int | None = None,
        product_brief_continuation: ProductBriefContinuationLike | None = None,
    ) -> dict[str, Any]: ...

    def fail_node(
        self,
        *,
        workflow_id: str,
        step_id: str,
        attempt_id: str | None = None,
        lease_token: str,
        trace_id: str,
        error: Exception,
        retryable: bool,
        retry_delay: timedelta,
        expected_workflow_version: int | None = None,
        product_brief_continuation: ProductBriefContinuationLike | None = None,
    ) -> None: ...
