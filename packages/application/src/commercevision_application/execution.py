"""Durable node lifecycle around transaction-free Agent node work."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from commercevision_contracts.events import (
    EventType,
    WorkflowFailedPayload,
    WorkflowHumanInputReceivedPayload,
    WorkflowHumanInputRequiredPayload,
    WorkflowNodeCompletedPayload,
    WorkflowNodeStartedPayload,
    WorkflowRunRequestedPayload,
)
from commercevision_contracts.workflow import product_brief_checkpoint_generation
from commercevision_domain import (
    AttemptStatus,
    LeaseConflictError,
    NotFoundError,
    ProductBriefState,
    StepStatus,
    StepType,
    WorkflowAttempt,
    WorkflowStatus,
    WorkflowStep,
)
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from commercevision_domain.workflow.errors import RetryNotReadyError
from commercevision_tool_runtime import ToolResult

from .ports import UnitOfWorkFactory
from .product_brief_authority import (
    ProductBriefWorkflowAuthorityState,
    ProductBriefWorkflowBindingIssue,
    evaluate_product_brief_workflow_authority,
)


@dataclass(frozen=True, slots=True)
class NodeClaim:
    workflow_id: str
    workflow_version: int
    step_id: str
    step_key: str
    lease_token: str | None
    already_completed: bool
    output_data: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class HumanWait:
    workflow_id: str
    workflow_version: int
    step_id: str
    already_completed: bool
    output_data: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class AttemptClaim:
    attempt_id: str
    attempt_number: int
    already_completed: bool
    result_data: dict[str, Any] | None
    provider_request_id: str | None


@dataclass(frozen=True, slots=True)
class ProductBriefContinuation:
    workspace_id: str
    product_brief_version_id: str
    product_brief_version_number: int
    approval_id: str | None


_PRODUCT_BRIEF_GENERATION_INPUT_KEY = "product_brief_generation"
_PRODUCT_BRIEF_GENERATION_AUTHORITY_KEYS = frozenset(
    {
        "workspace_id",
        "workflow_id",
        "product_id",
        "product_brief_id",
        "product_brief_version_id",
        "product_brief_version_number",
        "approval_id",
        "initial_step_id",
        "checkpoint_generation",
    }
)


@dataclass(frozen=True, slots=True)
class ProductBriefGenerationAuthority:
    workspace_id: str
    workflow_id: str
    product_id: str
    product_brief_id: str
    product_brief_version_id: str
    product_brief_version_number: int
    approval_id: str | None
    initial_step_id: str
    checkpoint_generation: str

    def as_step_input(self) -> dict[str, Any]:
        return {
            _PRODUCT_BRIEF_GENERATION_INPUT_KEY: {
                "workspace_id": self.workspace_id,
                "workflow_id": self.workflow_id,
                "product_id": self.product_id,
                "product_brief_id": self.product_brief_id,
                "product_brief_version_id": self.product_brief_version_id,
                "product_brief_version_number": self.product_brief_version_number,
                "approval_id": self.approval_id,
                "initial_step_id": self.initial_step_id,
                "checkpoint_generation": self.checkpoint_generation,
            }
        }

    @classmethod
    def from_step(cls, step: WorkflowStep) -> ProductBriefGenerationAuthority | None:
        container = step.input_data
        if not isinstance(container, dict):
            return None
        raw = container.get(_PRODUCT_BRIEF_GENERATION_INPUT_KEY)
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ValueError("ProductBrief generation authority is malformed")
        if set(raw) != _PRODUCT_BRIEF_GENERATION_AUTHORITY_KEYS:
            raise ValueError("ProductBrief generation authority fields are invalid")
        required_strings = (
            "workspace_id",
            "workflow_id",
            "product_id",
            "product_brief_id",
            "product_brief_version_id",
            "initial_step_id",
            "checkpoint_generation",
        )
        if any(not isinstance(raw.get(field), str) or not raw[field] for field in required_strings):
            raise ValueError("ProductBrief generation authority is incomplete")
        version_number = raw.get("product_brief_version_number")
        if (
            not isinstance(version_number, int)
            or isinstance(version_number, bool)
            or version_number < 1
        ):
            raise ValueError("ProductBrief generation version number is invalid")
        approval_id = raw.get("approval_id")
        if approval_id is not None and (not isinstance(approval_id, str) or not approval_id):
            raise ValueError("ProductBrief generation approval authority is invalid")
        checkpoint_generation = raw["checkpoint_generation"]
        if not (
            checkpoint_generation.startswith("product-brief:v1:")
            and len(checkpoint_generation) == 81
        ):
            raise ValueError("ProductBrief checkpoint generation is invalid")
        return cls(
            workspace_id=raw["workspace_id"],
            workflow_id=raw["workflow_id"],
            product_id=raw["product_id"],
            product_brief_id=raw["product_brief_id"],
            product_brief_version_id=raw["product_brief_version_id"],
            product_brief_version_number=version_number,
            approval_id=approval_id,
            initial_step_id=raw["initial_step_id"],
            checkpoint_generation=checkpoint_generation,
        )


@dataclass(frozen=True, slots=True)
class ProductBriefContinuationClaim:
    stale_reason: Literal["expired", "superseded"] | None
    workflow_id: str
    workflow_version: int
    workspace_id: str
    actor_id: str
    input_data: dict[str, Any]
    node_claim: NodeClaim | None
    generation_authority: ProductBriefGenerationAuthority | None


@dataclass(frozen=True, slots=True)
class ProductBriefRecoveryClaim:
    stale_reason: Literal["expired", "superseded"] | None
    workflow_id: str
    workflow_version: int
    workspace_id: str
    actor_id: str
    input_data: dict[str, Any]
    current_node: str
    continuation: ProductBriefContinuation | None
    node_claim: NodeClaim | None
    generation_authority: ProductBriefGenerationAuthority | None


class ProductBriefContinuationAuthorityError(ValueError):
    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class StaleProductBriefContinuation(RuntimeError):
    def __init__(self, reason: Literal["expired", "superseded"]) -> None:
        super().__init__(f"ProductBrief continuation is {reason}")
        self.reason = reason


_BINDING_ERROR_REASONS = {
    ProductBriefWorkflowBindingIssue.WORKSPACE: "workspace_mismatch",
    ProductBriefWorkflowBindingIssue.WORKFLOW: "product_brief_workflow_mismatch",
    ProductBriefWorkflowBindingIssue.WORKFLOW_TYPE: "product_brief_workflow_type_mismatch",
    ProductBriefWorkflowBindingIssue.PRODUCT: "product_brief_product_mismatch",
    ProductBriefWorkflowBindingIssue.RETENTION_DEADLINE: (
        "product_brief_retention_binding_mismatch"
    ),
}


def _product_brief_authority_stale_reason(
    *,
    workflow: Any,
    product_brief: Any,
    now: datetime,
) -> Literal["expired"] | None:
    authority = evaluate_product_brief_workflow_authority(
        workflow=workflow,
        product_brief=product_brief,
        now=now,
    )
    if authority.state == ProductBriefWorkflowAuthorityState.BINDING_MISMATCH:
        assert authority.binding_issue is not None
        raise ProductBriefContinuationAuthorityError(
            "ProductBrief continuation Workflow binding is inconsistent",
            reason=_BINDING_ERROR_REASONS[authority.binding_issue],
        )
    if authority.state == ProductBriefWorkflowAuthorityState.EXPIRED:
        return "expired"
    return None


class DurableNodeLifecycle:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        lease_duration: timedelta,
        default_max_attempts: int = 3,
    ) -> None:
        self._uow_factory = uow_factory
        self._lease_duration = lease_duration
        self._default_max_attempts = default_max_attempts

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
        product_brief_continuation: ProductBriefContinuation | None = None,
    ) -> NodeClaim:
        with self._uow_factory() as uow:
            workflow = uow.workflows.get(workflow_id, for_update=True)
            if workflow is None:
                raise NotFoundError(f"workflow {workflow_id} was not found")
            if product_brief_continuation is not None:
                stale_reason = self._product_brief_continuation_stale_reason(
                    uow=uow,
                    workflow=workflow,
                    continuation=product_brief_continuation,
                    now=uow.database_now(),
                    expected_workflow_version=None,
                    require_retrieval_gate=False,
                )
                if stale_reason is not None:
                    raise StaleProductBriefContinuation(stale_reason)
            now = datetime.now(UTC)
            claim = self._begin_node_locked(
                uow=uow,
                workflow=workflow,
                now=now,
                expected_workflow_version=expected_workflow_version,
                step_key=step_key,
                step_type=step_type,
                running_state=running_state,
                node_name=node_name,
                lease_owner=lease_owner,
                trace_id=trace_id,
                input_data=input_data,
            )
            if claim.already_completed:
                return claim
            uow.commit()
        return claim

    def claim_product_brief_continuation(
        self,
        *,
        workflow_id: str,
        expected_workflow_version: int,
        continuation: ProductBriefContinuation,
        lease_owner: str,
        trace_id: str,
    ) -> ProductBriefContinuationClaim:
        with self._uow_factory() as uow:
            workflow = uow.workflows.get(workflow_id, for_update=True)
            if workflow is None:
                raise ProductBriefContinuationAuthorityError(
                    "ProductBrief continuation workflow was not found",
                    reason="product_brief_workflow_mismatch",
                )
            now = uow.database_now()
            stale_reason = self._product_brief_continuation_stale_reason(
                uow=uow,
                workflow=workflow,
                continuation=continuation,
                now=now,
                expected_workflow_version=expected_workflow_version,
                require_retrieval_gate=True,
            )
            if stale_reason is not None:
                stale_step = uow.steps.get_by_key(
                    workflow.id,
                    f"retrieve_references:product-brief:{continuation.product_brief_version_id}",
                    for_update=True,
                )
                if stale_step is not None:
                    self._cancel_stale_step(
                        uow=uow,
                        step=stale_step,
                        lease_token=stale_step.lease_token,
                        now=now,
                    )
                uow.commit()
                return ProductBriefContinuationClaim(
                    stale_reason=stale_reason,
                    workflow_id=workflow.id,
                    workflow_version=workflow.version,
                    workspace_id=workflow.workspace_id,
                    actor_id=workflow.created_by,
                    input_data=dict(workflow.input_data),
                    node_claim=None,
                    generation_authority=None,
                )
            stored_version = uow.product_briefs.get_version(
                workspace_id=continuation.workspace_id,
                product_brief_version_id=continuation.product_brief_version_id,
            )
            product_id = workflow.input_data.get("product_id")
            if stored_version is None or not isinstance(product_id, str) or not product_id:
                raise ProductBriefContinuationAuthorityError(
                    "ProductBrief generation authority is unavailable",
                    reason="product_brief_version_mismatch",
                )

            generation_authority: ProductBriefGenerationAuthority | None = None

            def generation_input(
                step: WorkflowStep,
                lease_token: str,
            ) -> dict[str, Any]:
                nonlocal generation_authority
                generation_authority = ProductBriefGenerationAuthority(
                    workspace_id=continuation.workspace_id,
                    workflow_id=workflow.id,
                    product_id=product_id,
                    product_brief_id=stored_version.version.product_brief_id,
                    product_brief_version_id=continuation.product_brief_version_id,
                    product_brief_version_number=continuation.product_brief_version_number,
                    approval_id=continuation.approval_id,
                    initial_step_id=step.id,
                    checkpoint_generation=product_brief_checkpoint_generation(
                        workspace_id=continuation.workspace_id,
                        product_brief_version_id=continuation.product_brief_version_id,
                        initial_step_id=step.id,
                        initial_step_lease_token=lease_token,
                    ),
                )
                return generation_authority.as_step_input()

            node_claim = self._begin_node_locked(
                uow=uow,
                workflow=workflow,
                now=now,
                expected_workflow_version=expected_workflow_version,
                step_key=(
                    f"retrieve_references:product-brief:{continuation.product_brief_version_id}"
                ),
                step_type=StepType.RETRIEVE_REFERENCES,
                running_state=WorkflowStatus.RETRIEVING,
                node_name="retrieve_references",
                lease_owner=lease_owner,
                trace_id=trace_id,
                input_data=None,
                claimed_input_factory=generation_input,
            )
            if generation_authority is None:
                retrieval_step = uow.steps.get(node_claim.step_id, for_update=True)
                generation_authority = (
                    ProductBriefGenerationAuthority.from_step(retrieval_step)
                    if retrieval_step is not None
                    else None
                )
            if generation_authority is None:
                raise ProductBriefContinuationAuthorityError(
                    "ProductBrief checkpoint generation authority is unavailable",
                    reason="product_brief_resume_mismatch",
                )
            uow.commit()
            return ProductBriefContinuationClaim(
                stale_reason=None,
                workflow_id=workflow.id,
                workflow_version=node_claim.workflow_version,
                workspace_id=workflow.workspace_id,
                actor_id=workflow.created_by,
                input_data=dict(workflow.input_data),
                node_claim=node_claim,
                generation_authority=generation_authority,
            )

    def recover_product_brief_continuation(
        self,
        *,
        workflow_id: str,
        expected_workflow_version: int,
        workspace_id: str,
        product_brief_version_id: str,
        product_brief_version_number: int,
        lease_owner: str,
        trace_id: str,
    ) -> ProductBriefRecoveryClaim:
        with self._uow_factory() as uow:
            workflow = uow.workflows.get(workflow_id, for_update=True)
            if workflow is None:
                raise ProductBriefContinuationAuthorityError(
                    "ProductBrief recovery workflow was not found",
                    reason="product_brief_workflow_mismatch",
                )
            continuation = self._resolve_recovery_continuation(
                uow=uow,
                workflow=workflow,
                workspace_id=workspace_id,
                product_brief_version_id=product_brief_version_id,
                product_brief_version_number=product_brief_version_number,
            )
            now = uow.database_now()
            stale_reason = self._product_brief_continuation_stale_reason(
                uow=uow,
                workflow=workflow,
                continuation=continuation,
                now=now,
                expected_workflow_version=None,
                require_retrieval_gate=False,
            )
            step_key = f"retrieve_references:product-brief:{continuation.product_brief_version_id}"
            retrieval_step = uow.steps.get_by_key(workflow.id, step_key, for_update=True)
            if stale_reason is not None:
                if retrieval_step is not None:
                    self._cancel_stale_step(
                        uow=uow,
                        step=retrieval_step,
                        lease_token=retrieval_step.lease_token,
                        now=now,
                    )
                uow.commit()
                return self._product_brief_recovery_claim(
                    workflow=workflow,
                    continuation=None,
                    stale_reason=stale_reason,
                    node_claim=None,
                    generation_authority=None,
                )
            if workflow.version != expected_workflow_version:
                return self._product_brief_recovery_claim(
                    workflow=workflow,
                    continuation=None,
                    stale_reason="superseded",
                    node_claim=None,
                    generation_authority=None,
                )

            stored_version = uow.product_briefs.get_version(
                workspace_id=continuation.workspace_id,
                product_brief_version_id=continuation.product_brief_version_id,
            )
            product_id = workflow.input_data.get("product_id")
            if stored_version is None or not isinstance(product_id, str) or not product_id:
                raise ProductBriefContinuationAuthorityError(
                    "ProductBrief recovery generation is unavailable",
                    reason="product_brief_resume_mismatch",
                )

            def generation_input(
                step: WorkflowStep,
                lease_token: str,
            ) -> dict[str, Any]:
                return ProductBriefGenerationAuthority(
                    workspace_id=continuation.workspace_id,
                    workflow_id=workflow.id,
                    product_id=product_id,
                    product_brief_id=stored_version.version.product_brief_id,
                    product_brief_version_id=continuation.product_brief_version_id,
                    product_brief_version_number=continuation.product_brief_version_number,
                    approval_id=continuation.approval_id,
                    initial_step_id=step.id,
                    checkpoint_generation=product_brief_checkpoint_generation(
                        workspace_id=continuation.workspace_id,
                        product_brief_version_id=continuation.product_brief_version_id,
                        initial_step_id=step.id,
                        initial_step_lease_token=lease_token,
                    ),
                ).as_step_input()

            if workflow.current_node == "retrieve_references":
                node_claim = self._begin_node_locked(
                    uow=uow,
                    workflow=workflow,
                    now=now,
                    expected_workflow_version=expected_workflow_version,
                    step_key=step_key,
                    step_type=StepType.RETRIEVE_REFERENCES,
                    running_state=WorkflowStatus.RETRIEVING,
                    node_name="retrieve_references",
                    lease_owner=lease_owner,
                    trace_id=trace_id,
                    input_data=None,
                    claimed_input_factory=generation_input,
                )
                if node_claim.lease_token is None:
                    raise ProductBriefContinuationAuthorityError(
                        "ProductBrief retrieval recovery has no live lease",
                        reason="product_brief_resume_mismatch",
                    )
                generation_authority = ProductBriefGenerationAuthority(
                    workspace_id=continuation.workspace_id,
                    workflow_id=workflow.id,
                    product_id=product_id,
                    product_brief_id=stored_version.version.product_brief_id,
                    product_brief_version_id=continuation.product_brief_version_id,
                    product_brief_version_number=continuation.product_brief_version_number,
                    approval_id=continuation.approval_id,
                    initial_step_id=node_claim.step_id,
                    checkpoint_generation=product_brief_checkpoint_generation(
                        workspace_id=continuation.workspace_id,
                        product_brief_version_id=continuation.product_brief_version_id,
                        initial_step_id=node_claim.step_id,
                        initial_step_lease_token=node_claim.lease_token,
                    ),
                )
                uow.commit()
                return self._product_brief_recovery_claim(
                    workflow=workflow,
                    continuation=continuation,
                    stale_reason=None,
                    node_claim=node_claim,
                    generation_authority=generation_authority,
                )

            if retrieval_step is None:
                raise ProductBriefContinuationAuthorityError(
                    "ProductBrief recovery has no retrieval generation",
                    reason="product_brief_resume_mismatch",
                )
            if retrieval_step.status != StepStatus.SUCCEEDED:
                raise ProductBriefContinuationAuthorityError(
                    "ProductBrief recovery generation has no completed retrieval",
                    reason="product_brief_resume_mismatch",
                )
            generation_authority = ProductBriefGenerationAuthority.from_step(retrieval_step)
            if generation_authority is None:
                raise ProductBriefContinuationAuthorityError(
                    "ProductBrief recovery generation authority is unavailable",
                    reason="product_brief_resume_mismatch",
                )
            self._assert_generation_authority(
                authority=generation_authority,
                workflow=workflow,
                continuation=continuation,
                product_brief_id=stored_version.version.product_brief_id,
                product_id=product_id,
                retrieval_step=retrieval_step,
            )
            return self._product_brief_recovery_claim(
                workflow=workflow,
                continuation=continuation,
                stale_reason=None,
                node_claim=None,
                generation_authority=generation_authority,
            )

    def _begin_node_locked(
        self,
        *,
        uow: Any,
        workflow: Any,
        now: Any,
        expected_workflow_version: int,
        step_key: str,
        step_type: StepType,
        running_state: WorkflowStatus,
        node_name: str,
        lease_owner: str,
        trace_id: str,
        input_data: dict[str, Any] | None,
        claimed_input_factory: Callable[[WorkflowStep, str], dict[str, Any]] | None = None,
    ) -> NodeClaim:
        if workflow.status.terminal:
            raise LeaseConflictError(f"workflow {workflow.id} is terminal")
        step = uow.steps.get_by_key(workflow.id, step_key, for_update=True)
        if step is not None and step.status == StepStatus.SUCCEEDED:
            return NodeClaim(
                workflow_id=workflow.id,
                workflow_version=workflow.version,
                step_id=step.id,
                step_key=step.step_key,
                lease_token=None,
                already_completed=True,
                output_data=step.output_data,
            )
        if step is not None and step.status == StepStatus.WAITING_HUMAN:
            raise LeaseConflictError(f"step {step.id} is waiting for human input")
        if step is None:
            workflow.assert_version(expected_workflow_version)
            step = WorkflowStep.create(
                workflow_id=workflow.id,
                step_key=step_key,
                step_type=step_type,
                sequence=uow.steps.next_sequence(workflow.id),
                expected_workflow_version=workflow.version,
                max_attempts=self._default_max_attempts,
                input_data=input_data,
                now=now,
            )
            step.queue(now=now)
            is_new = True
        else:
            if (
                step.status == StepStatus.RETRYABLE_FAILED
                and step.next_attempt_at
                and step.next_attempt_at > now
            ):
                retry_at = step.next_attempt_at.isoformat()
                raise RetryNotReadyError(f"step {step.id} retry is scheduled for {retry_at}")
            is_new = False

        lease_token = step.claim(
            owner=lease_owner,
            lease_duration=self._lease_duration,
            now=now,
        )
        step.start(lease_token=lease_token, now=now)
        if claimed_input_factory is not None:
            step.input_data = claimed_input_factory(step, lease_token)
        if workflow.status != running_state or workflow.current_node != node_name:
            workflow.transition(running_state, current_node=node_name, now=now)
        step.expected_workflow_version = workflow.version
        if is_new:
            uow.steps.add(step)
        else:
            uow.steps.save(step)
        uow.workflows.save(workflow)
        uow.outbox.add(
            self._event(
                workspace_id=workflow.workspace_id,
                workflow_id=workflow.id,
                workflow_version=workflow.version,
                event_type=EventType.WORKFLOW_NODE_STARTED,
                trace_id=trace_id,
                payload=WorkflowNodeStartedPayload(
                    node=node_name,
                    step_id=step.id,
                    step_key=step.step_key,
                ).model_dump(mode="json"),
                now=now,
            )
        )
        return NodeClaim(
            workflow_id=workflow.id,
            workflow_version=workflow.version,
            step_id=step.id,
            step_key=step.step_key,
            lease_token=lease_token,
            already_completed=False,
            output_data=None,
        )

    @staticmethod
    def _resolve_recovery_continuation(
        *,
        uow: Any,
        workflow: Any,
        workspace_id: str,
        product_brief_version_id: str,
        product_brief_version_number: int,
    ) -> ProductBriefContinuation:
        if workflow.workspace_id != workspace_id:
            raise ProductBriefContinuationAuthorityError(
                "ProductBrief recovery workspace does not match its Workflow",
                reason="workspace_mismatch",
            )
        stored_version = uow.product_briefs.get_version(
            workspace_id=workspace_id,
            product_brief_version_id=product_brief_version_id,
        )
        if (
            stored_version is None
            or stored_version.version.version_number != product_brief_version_number
        ):
            raise ProductBriefContinuationAuthorityError(
                "ProductBrief recovery version is inconsistent",
                reason="product_brief_version_mismatch",
            )
        product_brief = uow.product_briefs.get(
            workspace_id=workspace_id,
            product_brief_id=stored_version.version.product_brief_id,
            for_update=True,
        )
        if product_brief is None:
            raise ProductBriefContinuationAuthorityError(
                "ProductBrief recovery aggregate was not found",
                reason="product_brief_workflow_mismatch",
            )
        approval_id: str | None = None
        if stored_version.version.confirmation_required:
            confirmation = uow.product_brief_confirmations.get_confirmation(
                workspace_id=workspace_id,
                product_brief_id=product_brief.id,
                product_brief_version_id=product_brief_version_id,
            )
            if (
                confirmation is None
                or confirmation.workflow_id != workflow.id
                or confirmation.product_brief_version_number != product_brief_version_number
            ):
                raise ProductBriefContinuationAuthorityError(
                    "ProductBrief recovery confirmation is inconsistent",
                    reason="product_brief_approval_mismatch",
                )
            approval_id = confirmation.approval_id
        return ProductBriefContinuation(
            workspace_id=workspace_id,
            product_brief_version_id=product_brief_version_id,
            product_brief_version_number=product_brief_version_number,
            approval_id=approval_id,
        )

    @staticmethod
    def _product_brief_recovery_claim(
        *,
        workflow: Any,
        continuation: ProductBriefContinuation | None,
        stale_reason: Literal["expired", "superseded"] | None,
        node_claim: NodeClaim | None,
        generation_authority: ProductBriefGenerationAuthority | None,
    ) -> ProductBriefRecoveryClaim:
        current_node = workflow.current_node
        if not isinstance(current_node, str) or not current_node:
            raise ProductBriefContinuationAuthorityError(
                "ProductBrief recovery Workflow node is unavailable",
                reason="product_brief_resume_mismatch",
            )
        return ProductBriefRecoveryClaim(
            stale_reason=stale_reason,
            workflow_id=workflow.id,
            workflow_version=workflow.version,
            workspace_id=workflow.workspace_id,
            actor_id=workflow.created_by,
            input_data=dict(workflow.input_data),
            current_node=current_node,
            continuation=continuation,
            node_claim=node_claim,
            generation_authority=generation_authority,
        )

    @staticmethod
    def _assert_generation_authority(
        *,
        authority: ProductBriefGenerationAuthority,
        workflow: Any,
        continuation: ProductBriefContinuation,
        product_brief_id: str,
        product_id: str,
        retrieval_step: WorkflowStep,
    ) -> None:
        expected = (
            workflow.workspace_id,
            workflow.id,
            product_id,
            product_brief_id,
            continuation.product_brief_version_id,
            continuation.product_brief_version_number,
            continuation.approval_id,
            retrieval_step.id,
        )
        actual = (
            authority.workspace_id,
            authority.workflow_id,
            authority.product_id,
            authority.product_brief_id,
            authority.product_brief_version_id,
            authority.product_brief_version_number,
            authority.approval_id,
            authority.initial_step_id,
        )
        if actual != expected:
            raise ProductBriefContinuationAuthorityError(
                "ProductBrief checkpoint generation authority is inconsistent",
                reason="product_brief_resume_mismatch",
            )

    @staticmethod
    def _product_brief_continuation_stale_reason(
        *,
        uow: Any,
        workflow: Any,
        continuation: ProductBriefContinuation,
        now: Any,
        expected_workflow_version: int | None,
        require_retrieval_gate: bool,
    ) -> Literal["expired", "superseded"] | None:
        if workflow.workspace_id != continuation.workspace_id:
            raise ProductBriefContinuationAuthorityError(
                "ProductBrief continuation workspace does not match its Workflow",
                reason="workspace_mismatch",
            )
        stored_version = uow.product_briefs.get_version(
            workspace_id=continuation.workspace_id,
            product_brief_version_id=continuation.product_brief_version_id,
        )
        if stored_version is None:
            raise ProductBriefContinuationAuthorityError(
                "ProductBrief continuation version was not found",
                reason="product_brief_version_mismatch",
            )
        product_brief = uow.product_briefs.get(
            workspace_id=continuation.workspace_id,
            product_brief_id=stored_version.version.product_brief_id,
            for_update=True,
        )
        if product_brief is None:
            raise ProductBriefContinuationAuthorityError(
                "ProductBrief continuation aggregate was not found",
                reason="product_brief_workflow_mismatch",
            )
        if stored_version.version.version_number != continuation.product_brief_version_number:
            raise ProductBriefContinuationAuthorityError(
                "ProductBrief continuation version number is inconsistent",
                reason="product_brief_version_mismatch",
            )
        if stored_version.version.confirmation_required:
            if continuation.approval_id is None:
                raise ProductBriefContinuationAuthorityError(
                    "Human-confirmed ProductBrief continuation has no approval authority",
                    reason="product_brief_approval_mismatch",
                )
            confirmation = uow.product_brief_confirmations.get_confirmation(
                workspace_id=continuation.workspace_id,
                product_brief_id=product_brief.id,
                product_brief_version_id=continuation.product_brief_version_id,
            )
            if (
                confirmation is None
                or confirmation.approval_id != continuation.approval_id
                or confirmation.workflow_id != workflow.id
                or confirmation.product_brief_version_number
                != continuation.product_brief_version_number
            ):
                raise ProductBriefContinuationAuthorityError(
                    "Human-confirmed ProductBrief continuation approval is inconsistent",
                    reason="product_brief_approval_mismatch",
                )
        elif continuation.approval_id is not None:
            raise ProductBriefContinuationAuthorityError(
                "Policy-confirmed ProductBrief continuation cannot claim human approval",
                reason="product_brief_approval_mismatch",
            )
        authority_stale_reason = _product_brief_authority_stale_reason(
            workflow=workflow,
            product_brief=product_brief,
            now=now,
        )
        if authority_stale_reason is not None:
            return authority_stale_reason
        if (
            product_brief.state != ProductBriefState.CONFIRMED
            or product_brief.current_version_id != continuation.product_brief_version_id
            or product_brief.confirmed_version_id != continuation.product_brief_version_id
            or (
                expected_workflow_version is not None
                and workflow.version != expected_workflow_version
            )
            or (
                require_retrieval_gate
                and (
                    workflow.status != WorkflowStatus.RETRIEVING
                    or workflow.current_node != "retrieve_references"
                )
            )
        ):
            return "superseded"
        return None

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
        product_brief_continuation: ProductBriefContinuation | None = None,
    ) -> int:
        now = datetime.now(UTC)
        stale_reason: Literal["expired", "superseded"] | None = None
        with self._uow_factory() as uow:
            workflow = uow.workflows.get(workflow_id, for_update=True)
            if workflow is None:
                raise NotFoundError(f"workflow {workflow_id} was not found")
            step = uow.steps.get(step_id, for_update=True)
            if step is None or step.workflow_id != workflow_id:
                raise NotFoundError("workflow step was not found")
            if product_brief_continuation is not None:
                stale_reason = (
                    self._product_brief_continuation_stale_reason(
                        uow=uow,
                        workflow=workflow,
                        continuation=product_brief_continuation,
                        now=uow.database_now(),
                        expected_workflow_version=None,
                        require_retrieval_gate=False,
                    )
                    if step.status == StepStatus.SUCCEEDED
                    else self._completion_stale_reason(
                        uow=uow,
                        workflow=workflow,
                        continuation=product_brief_continuation,
                        expected_workflow_version=expected_workflow_version,
                    )
                )
            if stale_reason is not None:
                self._cancel_stale_step(
                    uow=uow,
                    step=step,
                    lease_token=lease_token,
                    now=now,
                )
                uow.commit()
            if stale_reason is not None:
                completed_version = workflow.version
            elif step.status == StepStatus.SUCCEEDED:
                return workflow.version
            elif lease_token is None:
                raise LeaseConflictError(f"workflow step {step.id} has no live completion lease")
            else:
                step.succeed(
                    output_ref=output_ref,
                    output_data=output_data,
                    lease_token=lease_token,
                    now=now,
                )
                workflow.transition(target_state, current_node=next_node, now=now)
                if workflow_result is not None:
                    workflow.result_data = workflow_result
                uow.steps.save(step)
                uow.workflows.save(workflow)
                uow.outbox.add(
                    self._event(
                        workspace_id=workflow.workspace_id,
                        workflow_id=workflow.id,
                        workflow_version=workflow.version,
                        event_type=EventType.WORKFLOW_NODE_COMPLETED,
                        trace_id=trace_id,
                        payload=WorkflowNodeCompletedPayload(
                            node=next_node,
                            completed_step_id=step.id,
                            status=workflow.status,
                        ).model_dump(mode="json"),
                        now=now,
                    )
                )
                uow.commit()
                completed_version = workflow.version
        if stale_reason is not None:
            raise StaleProductBriefContinuation(stale_reason)
        return completed_version

    def begin_human_wait(
        self,
        *,
        workflow_id: str,
        expected_workflow_version: int,
        step_key: str,
        step_type: StepType,
        lease_owner: str,
        trace_id: str,
        product_brief_continuation: ProductBriefContinuation | None = None,
    ) -> HumanWait:
        with self._uow_factory() as uow:
            workflow = uow.workflows.get(workflow_id, for_update=True)
            if workflow is None:
                raise NotFoundError(f"workflow {workflow_id} was not found")
            if product_brief_continuation is not None:
                stale_reason = self._product_brief_continuation_stale_reason(
                    uow=uow,
                    workflow=workflow,
                    continuation=product_brief_continuation,
                    now=uow.database_now(),
                    expected_workflow_version=None,
                    require_retrieval_gate=False,
                )
                if stale_reason is not None:
                    raise StaleProductBriefContinuation(stale_reason)
            now = datetime.now(UTC)
            step = uow.steps.get_by_key(workflow_id, step_key, for_update=True)
            if step is not None:
                if step.status == StepStatus.SUCCEEDED:
                    return HumanWait(
                        workflow.id,
                        step.expected_workflow_version,
                        step.id,
                        True,
                        step.output_data,
                    )
                if step.status == StepStatus.WAITING_HUMAN:
                    return HumanWait(
                        workflow.id,
                        step.expected_workflow_version,
                        step.id,
                        False,
                        None,
                    )
                raise LeaseConflictError(f"human step {step.id} is in {step.status.value}")
            workflow.assert_version(expected_workflow_version)
            step = WorkflowStep.create(
                workflow_id=workflow.id,
                step_key=step_key,
                step_type=step_type,
                sequence=uow.steps.next_sequence(workflow.id),
                expected_workflow_version=workflow.version,
                max_attempts=1,
                now=now,
            )
            step.queue(now=now)
            token = step.claim(
                owner=lease_owner,
                lease_duration=self._lease_duration,
                now=now,
            )
            step.start(lease_token=token, now=now)
            step.wait_for_human(lease_token=token, now=now)
            uow.steps.add(step)
            uow.outbox.add(
                self._event(
                    workspace_id=workflow.workspace_id,
                    workflow_id=workflow.id,
                    workflow_version=workflow.version,
                    event_type=EventType.WORKFLOW_HUMAN_INPUT_REQUIRED,
                    trace_id=trace_id,
                    payload=WorkflowHumanInputRequiredPayload(
                        step_id=step.id,
                        step_key=step.step_key,
                    ).model_dump(mode="json"),
                    now=now,
                )
            )
            uow.commit()
        return HumanWait(workflow.id, workflow.version, step.id, False, None)

    def complete_human_wait(
        self,
        *,
        workflow_id: str,
        step_id: str,
        output_data: dict[str, Any],
        trace_id: str,
        expected_workflow_version: int | None = None,
        product_brief_continuation: ProductBriefContinuation | None = None,
    ) -> int:
        now = datetime.now(UTC)
        stale_reason: Literal["expired", "superseded"] | None = None
        with self._uow_factory() as uow:
            workflow = uow.workflows.get(workflow_id, for_update=True)
            if workflow is None:
                raise NotFoundError(f"workflow {workflow_id} was not found")
            if product_brief_continuation is not None:
                stale_reason = self._completion_stale_reason(
                    uow=uow,
                    workflow=workflow,
                    continuation=product_brief_continuation,
                    expected_workflow_version=expected_workflow_version,
                )
            step = uow.steps.get(step_id, for_update=True)
            if step is None or step.workflow_id != workflow_id:
                raise NotFoundError("human workflow step was not found")
            if stale_reason is not None:
                self._cancel_stale_step(
                    uow=uow,
                    step=step,
                    lease_token=None,
                    now=now,
                )
                uow.commit()
            if stale_reason is None and step.status != StepStatus.SUCCEEDED:
                step.succeed(output_data=output_data, now=now)
                uow.steps.save(step)
                uow.outbox.add(
                    self._event(
                        workspace_id=workflow.workspace_id,
                        workflow_id=workflow.id,
                        workflow_version=workflow.version,
                        event_type=EventType.WORKFLOW_HUMAN_INPUT_RECEIVED,
                        trace_id=trace_id,
                        payload=WorkflowHumanInputReceivedPayload(
                            step_id=step.id,
                            decision=output_data.get("decision"),
                        ).model_dump(mode="json"),
                        now=now,
                    )
                )
                uow.commit()
            completed_version = workflow.version
        if stale_reason is not None:
            raise StaleProductBriefContinuation(stale_reason)
        return completed_version

    def begin_attempt(
        self,
        *,
        workflow_id: str,
        step_id: str,
        idempotency_key: str,
        request_data: dict[str, Any],
        lease_token: str | None = None,
        expected_workflow_version: int | None = None,
        product_brief_continuation: ProductBriefContinuation | None = None,
    ) -> AttemptClaim:
        now = datetime.now(UTC)
        stale_reason: Literal["expired", "superseded"] | None = None
        with self._uow_factory() as uow:
            if product_brief_continuation is not None:
                workflow = uow.workflows.get(workflow_id, for_update=True)
                if workflow is None:
                    raise NotFoundError(f"workflow {workflow_id} was not found")
                stale_reason = self._completion_stale_reason(
                    uow=uow,
                    workflow=workflow,
                    continuation=product_brief_continuation,
                    expected_workflow_version=expected_workflow_version,
                )
                step = uow.steps.get(step_id, for_update=True)
                if step is None or step.workflow_id != workflow_id:
                    raise NotFoundError(f"step {step_id} was not found")
                if stale_reason is not None:
                    self._cancel_stale_step(
                        uow=uow,
                        step=step,
                        lease_token=lease_token,
                        now=now,
                    )
                    uow.commit()
                elif lease_token is None:
                    raise LeaseConflictError(
                        f"step {step_id} requires a lease token before tool submission"
                    )
                else:
                    step.assert_lease(lease_token, now=now)
            if stale_reason is not None:
                attempt_claim = None
            else:
                attempt_claim = self._begin_attempt_locked(
                    uow=uow,
                    workflow_id=workflow_id,
                    step_id=step_id,
                    idempotency_key=idempotency_key,
                    request_data=request_data,
                    now=now,
                )
                if not attempt_claim.already_completed:
                    uow.commit()
        if stale_reason is not None:
            raise StaleProductBriefContinuation(stale_reason)
        if attempt_claim is None:
            raise RuntimeError("workflow attempt claim was not produced")
        return attempt_claim

    @staticmethod
    def _begin_attempt_locked(
        *,
        uow: Any,
        workflow_id: str,
        step_id: str,
        idempotency_key: str,
        request_data: dict[str, Any],
        now: datetime,
    ) -> AttemptClaim:
        existing = uow.attempts.get_by_idempotency(idempotency_key, for_update=True)
        if existing is not None and existing.status == AttemptStatus.SUCCEEDED:
            return AttemptClaim(
                existing.id,
                existing.attempt_number,
                True,
                existing.result_data,
                existing.provider_request_id,
            )
        if existing is None:
            step = uow.steps.get(step_id, for_update=True)
            if step is None or step.workflow_id != workflow_id:
                raise NotFoundError(f"step {step_id} was not found")
            attempt = WorkflowAttempt.create(
                workflow_id=workflow_id,
                step_id=step_id,
                attempt_number=step.attempt_count,
                idempotency_key=idempotency_key,
                request_data=request_data,
                now=now,
            )
            attempt.mark_submitting(now=now)
            uow.attempts.add(attempt)
        else:
            attempt = existing
            if attempt.status == AttemptStatus.SUBMITTING:
                attempt.transition(AttemptStatus.UNKNOWN, now=now)
            if attempt.status in {
                AttemptStatus.UNKNOWN,
                AttemptStatus.RETRYABLE_FAILED,
            }:
                attempt.transition(AttemptStatus.SUBMITTING, now=now)
            elif attempt.status.terminal:
                raise LeaseConflictError(
                    f"attempt {attempt.id} is terminal in {attempt.status.value}"
                )
            uow.attempts.save(attempt)
        return AttemptClaim(
            attempt.id,
            attempt.attempt_number,
            False,
            None,
            attempt.provider_request_id,
        )

    def complete_attempt(
        self,
        *,
        workflow_id: str,
        step_id: str,
        lease_token: str,
        idempotency_key: str,
        result: ToolResult,
        expected_workflow_version: int | None = None,
        product_brief_continuation: ProductBriefContinuation | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        stale_reason: Literal["expired", "superseded"] | None = None
        with self._uow_factory() as uow:
            workflow = uow.workflows.get(workflow_id, for_update=True)
            if workflow is None:
                raise NotFoundError(f"workflow {workflow_id} was not found")
            if product_brief_continuation is not None:
                stale_reason = self._completion_stale_reason(
                    uow=uow,
                    workflow=workflow,
                    continuation=product_brief_continuation,
                    expected_workflow_version=expected_workflow_version,
                )
            attempt = uow.attempts.get_by_idempotency(idempotency_key, for_update=True)
            step = uow.steps.get(step_id, for_update=True)
            if (
                attempt is None
                or attempt.workflow_id != workflow_id
                or attempt.step_id != step_id
                or step is None
                or step.workflow_id != workflow_id
            ):
                raise NotFoundError("workflow attempt was not found")
            if stale_reason is not None:
                if not attempt.status.terminal:
                    attempt.transition(AttemptStatus.CANCELLED, now=now)
                    attempt.completed_at = now
                    uow.attempts.save(attempt)
                self._cancel_stale_step(
                    uow=uow,
                    step=step,
                    lease_token=lease_token,
                    now=now,
                )
                uow.commit()
            if stale_reason is not None:
                result_data = {}
            elif attempt.status == AttemptStatus.SUCCEEDED:
                return attempt.result_data or {}
            else:
                step.assert_lease(lease_token, now=now)
                attempt.provider_request_id = result.provider_request_id
                attempt.succeed(result_data=result.output, now=now)
                uow.attempts.save(attempt)
                uow.commit()
                result_data = result.output
        if stale_reason is not None:
            raise StaleProductBriefContinuation(stale_reason)
        return result_data

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
        product_brief_continuation: ProductBriefContinuation | None = None,
    ) -> None:
        now = datetime.now(UTC)
        stale_reason: Literal["expired", "superseded"] | None = None
        with self._uow_factory() as uow:
            workflow = uow.workflows.get(workflow_id, for_update=True)
            if workflow is None:
                raise NotFoundError(f"workflow {workflow_id} was not found")
            if product_brief_continuation is not None:
                stale_reason = self._completion_stale_reason(
                    uow=uow,
                    workflow=workflow,
                    continuation=product_brief_continuation,
                    expected_workflow_version=expected_workflow_version,
                )
            attempt = (
                uow.attempts.get(attempt_id, for_update=True) if attempt_id is not None else None
            )
            step = uow.steps.get(step_id, for_update=True)
            if step is None or step.workflow_id != workflow_id:
                raise NotFoundError("workflow step was not found")
            if attempt_id is not None and (
                attempt is None or attempt.workflow_id != workflow_id or attempt.step_id != step_id
            ):
                raise NotFoundError("workflow attempt was not found")
            should_retry = (
                stale_reason is None and retryable and step.attempt_count < step.max_attempts
            )
            if stale_reason is not None:
                self._settle_failed_attempt(
                    uow=uow,
                    attempt=attempt,
                    target=AttemptStatus.CANCELLED,
                    error=error,
                    now=now,
                )
                self._cancel_stale_step(
                    uow=uow,
                    step=step,
                    lease_token=lease_token,
                    now=now,
                )
                uow.commit()
            if stale_reason is not None:
                pass
            elif should_retry:
                self._settle_failed_attempt(
                    uow=uow,
                    attempt=attempt,
                    target=AttemptStatus.RETRYABLE_FAILED,
                    error=error,
                    now=now,
                )
                retry_at = now + retry_delay
                step.fail_retryable(
                    error_class=type(error).__name__,
                    error_message=str(error),
                    retry_at=retry_at,
                    lease_token=lease_token,
                    now=now,
                )
                uow.outbox.add(
                    self._event(
                        workspace_id=workflow.workspace_id,
                        workflow_id=workflow.id,
                        workflow_version=workflow.version,
                        event_type=EventType.WORKFLOW_RUN_REQUESTED,
                        trace_id=trace_id,
                        payload=WorkflowRunRequestedPayload(
                            workflow_id=workflow.id,
                            action="retry",
                            reason=(
                                "product-brief-generation-retry"
                                if product_brief_continuation is not None
                                else None
                            ),
                            product_brief_version_id=(
                                product_brief_continuation.product_brief_version_id
                                if product_brief_continuation is not None
                                else None
                            ),
                            product_brief_version_number=(
                                product_brief_continuation.product_brief_version_number
                                if product_brief_continuation is not None
                                else None
                            ),
                        ).model_dump(mode="json", exclude_none=True),
                        now=retry_at,
                    )
                )
            elif stale_reason is None:
                self._settle_failed_attempt(
                    uow=uow,
                    attempt=attempt,
                    target=AttemptStatus.PERMANENT_FAILED,
                    error=error,
                    now=now,
                )
                step.fail_permanently(
                    error_class=type(error).__name__,
                    error_message=str(error),
                    lease_token=lease_token,
                    now=now,
                )
                if not workflow.status.terminal:
                    workflow.transition(
                        WorkflowStatus.FAILED,
                        current_node=workflow.current_node,
                        now=now,
                    )
                    uow.workflows.save(workflow)
                uow.outbox.add(
                    self._event(
                        workspace_id=workflow.workspace_id,
                        workflow_id=workflow.id,
                        workflow_version=workflow.version,
                        event_type=EventType.WORKFLOW_FAILED,
                        trace_id=trace_id,
                        payload=WorkflowFailedPayload(
                            workflow_id=workflow.id,
                            step_id=step.id,
                            error_class=type(error).__name__,
                        ).model_dump(mode="json"),
                        now=now,
                    )
                )
            if stale_reason is None:
                uow.steps.save(step)
                uow.commit()
        if stale_reason is not None:
            raise StaleProductBriefContinuation(stale_reason)

    @staticmethod
    def _settle_failed_attempt(
        *,
        uow: Any,
        attempt: WorkflowAttempt | None,
        target: AttemptStatus,
        error: Exception,
        now: datetime,
    ) -> None:
        if attempt is None or attempt.status.terminal:
            return
        attempt.transition(target, now=now)
        if target != AttemptStatus.CANCELLED:
            attempt.error_class = type(error).__name__
            attempt.error_message = str(error)
        if target.terminal:
            attempt.completed_at = now
        uow.attempts.save(attempt)

    @classmethod
    def _completion_stale_reason(
        cls,
        *,
        uow: Any,
        workflow: Any,
        continuation: ProductBriefContinuation,
        expected_workflow_version: int | None,
    ) -> Literal["expired", "superseded"] | None:
        if expected_workflow_version is None:
            raise ValueError("ProductBrief completion requires its claim-time Workflow version")
        return cls._product_brief_continuation_stale_reason(
            uow=uow,
            workflow=workflow,
            continuation=continuation,
            now=uow.database_now(),
            expected_workflow_version=expected_workflow_version,
            require_retrieval_gate=False,
        )

    @staticmethod
    def _cancel_stale_step(
        *,
        uow: Any,
        step: WorkflowStep,
        lease_token: str | None,
        now: datetime,
    ) -> None:
        if step.status.terminal:
            return
        if (
            step.status in {StepStatus.CLAIMED, StepStatus.RUNNING}
            and step.lease_token != lease_token
        ):
            return
        step.cancel(now=now)
        uow.steps.save(step)

    @staticmethod
    def _event(
        *,
        workspace_id: str,
        workflow_id: str,
        workflow_version: int,
        event_type: EventType,
        trace_id: str,
        payload: dict[str, Any],
        now: datetime,
    ) -> OutboxEvent:
        return OutboxEvent(
            envelope=EventEnvelope.create(
                event_type=event_type.value,
                aggregate_type="workflow",
                aggregate_id=workflow_id,
                aggregate_version=workflow_version,
                trace_id=trace_id,
                payload=payload,
                now=now,
            ),
            available_at=now,
            workspace_id=workspace_id,
        )
