"""Workflow command and query use cases."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from commercevision_contracts.events import (
    EventType,
    WorkflowCancelledPayload,
    WorkflowResumeRequestedPayload,
    WorkflowRunRequestedPayload,
)
from commercevision_contracts.workflow import (
    ApprovalRequest,
    EventResponse,
    WorkflowCreateRequest,
    WorkflowListResponse,
    WorkflowResponse,
)
from commercevision_domain import (
    Approval,
    ApprovalDecision,
    ApprovalType,
    ConcurrencyError,
    CreativePlanVersion,
    NotFoundError,
    RetentionStatus,
    Workflow,
    WorkflowCancellationRefusedError,
    WorkflowStatus,
    validate_workspace_id,
)
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from commercevision_domain.workflow.errors import (
    ApprovalConflictError,
    CreativePlanApprovalRejectedVersionError,
    CreativePlanApprovalRetentionExpiredError,
    CreativePlanApprovalSubjectConflictError,
    CreativePlanApprovalVersionConflictError,
    IdempotencyConflictError,
)

from .ports import UnitOfWorkFactory, UnitOfWorkPort
from .projections import workflow_response

_MAX_CREATIVE_PLAN_REJECTIONS = 10


@dataclass(frozen=True, slots=True)
class CreativePlanExecutionClaim:
    """MySQL-revalidated authority for one approved Creative Plan version."""

    workflow_version: int
    plan: CreativePlanVersion
    approval: Approval
    retain_until: datetime


@dataclass(frozen=True, slots=True)
class CreativePlanResumeClaim:
    """MySQL-revalidated continuation for one exact Plan Approval."""

    workflow_version: int
    plan: CreativePlanVersion
    approval: Approval
    retain_until: datetime


def _canonical_hash(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _key_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _encode_cursor(created_at: datetime, workflow_id: str) -> str:
    payload = json.dumps(
        {"created_at": created_at.isoformat(), "id": workflow_id},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    if not cursor:
        return None
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(padded).decode())
        return datetime.fromisoformat(data["created_at"]), str(data["id"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid workflow cursor") from exc


class WorkflowApplicationService:
    def __init__(self, *, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def create(
        self,
        *,
        request: WorkflowCreateRequest,
        workspace_id: str,
        actor_id: str,
        idempotency_key: str,
        trace_id: str,
    ) -> WorkflowResponse:
        validate_workspace_id(workspace_id)
        scope = f"workflow:create:{workspace_id}"
        key_hash = _key_hash(idempotency_key)
        request_hash = _canonical_hash(request.model_dump(mode="json"))
        existing = self._load_idempotent(scope, key_hash, request_hash, workspace_id)
        if existing is not None:
            return existing

        now = datetime.now(UTC)
        workflow = Workflow.create(
            workspace_id=workspace_id,
            created_by=actor_id,
            workflow_type=request.workflow_type,
            input_data=request.input_data,
            retention=timedelta(hours=request.retention_hours),
            now=now,
        )
        workflow.transition(
            WorkflowStatus.INGESTING,
            current_node="validate_input",
            expected_version=1,
            now=now,
        )
        event = None
        if request.workflow_type == "COMMERCE_IMAGE_GENERATION":
            workflow.transition(
                WorkflowStatus.UNDERSTANDING,
                current_node="understand_product",
                expected_version=workflow.version,
                now=now,
            )
        else:
            event = self._workflow_event(
                workflow=workflow,
                event_type=EventType.WORKFLOW_RUN_REQUESTED,
                trace_id=trace_id,
                payload=WorkflowRunRequestedPayload(
                    action="start",
                    workflow_id=workflow.id,
                ).model_dump(mode="json", exclude_none=True),
                now=now,
            )
        try:
            with self._uow_factory() as uow:
                uow.workflows.add(workflow)
                if event is not None:
                    uow.outbox.add(event)
                uow.idempotency.add(
                    scope=scope,
                    key_hash=key_hash,
                    request_hash=request_hash,
                    resource_type="workflow",
                    resource_id=workflow.id,
                    response_data={"workflow_id": workflow.id},
                    expires_at=workflow.expires_at,
                )
                self._audit(
                    uow=uow,
                    workflow=workflow,
                    actor_id=actor_id,
                    trace_id=trace_id,
                    action="workflow.created",
                    metadata={"workflow_type": workflow.workflow_type},
                    now=now,
                )
                uow.commit()
        except ConcurrencyError:
            existing = self._load_idempotent(scope, key_hash, request_hash, workspace_id)
            if existing is not None:
                return existing
            raise
        return workflow_response(workflow)

    def get(self, *, workflow_id: str, workspace_id: str) -> WorkflowResponse:
        with self._uow_factory() as uow:
            workflow = uow.workflows.get(workflow_id, workspace_id=workspace_id)
            if workflow is None:
                raise NotFoundError(f"workflow {workflow_id} was not found")
            response = workflow_response(
                workflow,
                steps=uow.steps.list_for_workflow(workflow.id),
                attempts=uow.attempts.list_for_workflow(workflow.id),
                approvals=uow.approvals.list_for_workflow(workflow.id),
            )
        return response

    def list(
        self,
        *,
        workspace_id: str,
        limit: int,
        cursor: str | None,
    ) -> WorkflowListResponse:
        bounded_limit = min(max(limit, 1), 100)
        with self._uow_factory() as uow:
            workflows = uow.workflows.list(
                workspace_id=workspace_id,
                limit=bounded_limit + 1,
                cursor=_decode_cursor(cursor),
            )
        has_more = len(workflows) > bounded_limit
        workflows = workflows[:bounded_limit]
        next_cursor = (
            _encode_cursor(workflows[-1].created_at, workflows[-1].id)
            if has_more and workflows
            else None
        )
        return WorkflowListResponse(
            items=[workflow_response(workflow) for workflow in workflows],
            next_cursor=next_cursor,
        )

    def cancel(
        self,
        *,
        workflow_id: str,
        workspace_id: str,
        actor_id: str,
        expected_version: int,
        idempotency_key: str,
        trace_id: str,
    ) -> WorkflowResponse:
        scope = f"workflow:cancel:{workflow_id}"
        key_hash = _key_hash(idempotency_key)
        request_hash = _canonical_hash({"expected_workflow_version": expected_version})
        existing = self._load_idempotent(scope, key_hash, request_hash, workspace_id)
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        with self._uow_factory() as uow:
            workflow = uow.workflows.get(workflow_id, workspace_id=workspace_id, for_update=True)
            if workflow is None:
                raise NotFoundError(f"workflow {workflow_id} was not found")
            if uow.workflows.has_irreversible_provider_submission(
                workspace_id=workspace_id,
                workflow_id=workflow_id,
            ):
                raise WorkflowCancellationRefusedError(
                    "workflow cancellation is refused after an external provider "
                    "submission has started"
                )
            workflow.request_cancellation(expected_version=expected_version, now=now)
            for step in uow.steps.list_for_workflow(workflow.id):
                if not step.status.terminal:
                    step.cancel(now=now)
                    uow.steps.save(step)
            uow.workflows.save(workflow)
            uow.outbox.add(
                self._workflow_event(
                    workflow=workflow,
                    event_type=EventType.WORKFLOW_CANCELLED,
                    trace_id=trace_id,
                    payload=WorkflowCancelledPayload(
                        workflow_id=workflow.id,
                    ).model_dump(mode="json"),
                    now=now,
                )
            )
            uow.idempotency.add(
                scope=scope,
                key_hash=key_hash,
                request_hash=request_hash,
                resource_type="workflow",
                resource_id=workflow.id,
                response_data={"workflow_id": workflow.id},
                expires_at=workflow.expires_at,
            )
            self._audit(
                uow=uow,
                workflow=workflow,
                actor_id=actor_id,
                trace_id=trace_id,
                action="workflow.cancelled",
                metadata={},
                now=now,
            )
            uow.commit()
        return workflow_response(workflow)

    def approve(
        self,
        *,
        workflow_id: str,
        workspace_id: str,
        actor_id: str,
        approval_type: ApprovalType,
        request: ApprovalRequest,
        idempotency_key: str,
        trace_id: str,
    ) -> WorkflowResponse:
        scope = f"workflow:approval:{workflow_id}:{approval_type.value}"
        key_hash = _key_hash(idempotency_key)
        request_hash = _canonical_hash(
            {
                **request.model_dump(mode="json"),
                "approval_type": approval_type.value,
                "actor_id": actor_id,
            }
        )
        target = self._approval_target(approval_type, request.decision)
        with self._uow_factory() as uow:
            workflow = uow.workflows.get(workflow_id, workspace_id=workspace_id, for_update=True)
            if workflow is None:
                raise NotFoundError(f"workflow {workflow_id} was not found")
            now = uow.database_now()
            replay = self._claim_approval_idempotency(
                uow=uow,
                scope=scope,
                key_hash=key_hash,
                request_hash=request_hash,
                workflow=workflow,
                approval_type=approval_type,
            )
            if replay is not None:
                return replay
            self._validate_approval_state(workflow.status, approval_type)
            workflow.assert_version(request.expected_workflow_version)
            if approval_type is ApprovalType.CREATIVE_PLAN:
                self._validate_creative_plan_subject(
                    uow=uow,
                    workflow=workflow,
                    subject_id=request.subject_id,
                    subject_version=request.subject_version,
                    decision=request.decision,
                    now=now,
                )
            approval = Approval.create(
                workflow_id=workflow.id,
                approval_type=approval_type,
                subject_id=request.subject_id,
                subject_version=request.subject_version,
                decision=request.decision,
                approved_by=actor_id,
                expected_workflow_version=request.expected_workflow_version,
                reason_code=request.reason_code,
                comment_ref=request.comment_ref,
                now=now,
            )
            workflow.transition(target, current_node=workflow.current_node, now=now)
            uow.approvals.add(approval)
            uow.workflows.save(workflow)
            uow.outbox.add(
                self._workflow_event(
                    workflow=workflow,
                    event_type=EventType.WORKFLOW_RESUME_REQUESTED,
                    trace_id=trace_id,
                    payload=WorkflowResumeRequestedPayload(
                        workflow_id=workflow.id,
                        approval_id=approval.id,
                        approval_type=approval_type,
                        decision=request.decision,
                        expected_workflow_version=request.expected_workflow_version,
                        resulting_workflow_version=workflow.version,
                        subject_id=request.subject_id,
                        subject_version=request.subject_version,
                    ).model_dump(mode="json"),
                    now=now,
                )
            )
            response = workflow_response(workflow, approvals=[approval])
            uow.idempotency.complete(
                scope=scope,
                key_hash=key_hash,
                request_hash=request_hash,
                resource_type="workflow-approval",
                resource_id=approval.id,
                response_data=response.model_dump(mode="json"),
            )
            self._audit(
                uow=uow,
                workflow=workflow,
                actor_id=actor_id,
                trace_id=trace_id,
                action=f"workflow.approval.{request.decision.value.lower()}",
                metadata={
                    "approval_type": approval_type.value,
                    "subject_id": request.subject_id,
                    "subject_version": request.subject_version,
                },
                now=now,
            )
            uow.commit()
        return response

    @staticmethod
    def _claim_approval_idempotency(
        *,
        uow: UnitOfWorkPort,
        scope: str,
        key_hash: str,
        request_hash: str,
        workflow: Workflow,
        approval_type: ApprovalType,
    ) -> WorkflowResponse | None:
        record = uow.idempotency.claim(
            scope=scope,
            key_hash=key_hash,
            request_hash=request_hash,
            expires_at=workflow.expires_at,
        )
        if record.request_hash != request_hash:
            raise IdempotencyConflictError(
                "idempotency key was already used with a different request"
            )
        if record.status == "PENDING":
            return None
        if record.status != "COMPLETED":
            raise ConcurrencyError("approval idempotency record has an unsupported status")
        if record.resource_type != "workflow-approval" or not isinstance(
            record.response_data, dict
        ):
            raise ConcurrencyError("idempotency record does not contain an approval response")
        try:
            response = WorkflowResponse.model_validate(record.response_data)
        except ValueError as exc:
            raise ConcurrencyError("idempotent approval response is invalid") from exc
        if (
            response.id != workflow.id
            or response.workspace_id != workflow.workspace_id
            or len(response.approvals) != 1
            or response.approvals[0].id != record.resource_id
            or response.approvals[0].approval_type is not approval_type
        ):
            raise ConcurrencyError("idempotent approval response has the wrong authority")
        return response

    @staticmethod
    def _validate_creative_plan_subject(
        *,
        uow: UnitOfWorkPort,
        workflow: Workflow,
        subject_id: str,
        subject_version: int,
        decision: ApprovalDecision,
        now: datetime,
    ) -> None:
        current = uow.creative_plans.get_current(
            workspace_id=workflow.workspace_id,
            workflow_id=workflow.id,
            creative_plan_id=subject_id,
        )
        if current is None:
            raise CreativePlanApprovalSubjectConflictError(
                "approval subject is not the authoritative current Creative Plan"
            )
        approvals = uow.approvals.list_for_workflow(workflow.id)
        if decision is ApprovalDecision.APPROVE and any(
            approval.approval_type is ApprovalType.CREATIVE_PLAN
            and approval.subject_id == subject_id
            and approval.subject_version == subject_version
            and approval.decision is ApprovalDecision.REJECT
            for approval in approvals
        ):
            raise CreativePlanApprovalRejectedVersionError(
                "rejected Creative Plan requires a later Creative Plan version"
            )
        if (
            decision is ApprovalDecision.REJECT
            and sum(
                approval.approval_type is ApprovalType.CREATIVE_PLAN
                and approval.subject_id == subject_id
                and approval.decision is ApprovalDecision.REJECT
                for approval in approvals
            )
            >= _MAX_CREATIVE_PLAN_REJECTIONS
        ):
            raise ApprovalConflictError("Creative Plan rejection limit has been reached")
        head, version = current
        if (
            version.version_number != subject_version
            or head.current_version_number != subject_version
            or head.current_version_id != version.id
        ):
            raise CreativePlanApprovalVersionConflictError(
                "approval subject is not the authoritative current Creative Plan"
            )
        if (
            workflow.retention_status is not RetentionStatus.ACTIVE
            or now >= head.retain_until
            or now >= workflow.expires_at
        ):
            raise CreativePlanApprovalRetentionExpiredError(
                "approval subject retention has expired"
            )

    def validate_creative_plan_execution_claim(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
        creative_plan_version: int,
        approval_id: str,
    ) -> CreativePlanExecutionClaim:
        """Load execution authority from current MySQL facts, never checkpoint state."""

        claim = self._validate_creative_plan_approval_claim(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            creative_plan_id=creative_plan_id,
            creative_plan_version=creative_plan_version,
            approval_id=approval_id,
            expected_workflow_version=None,
            resulting_workflow_version=None,
            decision=ApprovalDecision.APPROVE,
            conflict_message="execution claim does not match the exact approved Creative Plan",
        )
        return CreativePlanExecutionClaim(
            workflow_version=claim.workflow_version,
            plan=claim.plan,
            approval=claim.approval,
            retain_until=claim.retain_until,
        )

    def validate_creative_plan_resume_claim(
        self,
        *,
        workspace_id: str,
        payload: WorkflowResumeRequestedPayload,
    ) -> CreativePlanResumeClaim:
        """Revalidate an untrusted graph continuation against current MySQL facts."""

        if payload.approval_type is not ApprovalType.CREATIVE_PLAN:
            raise ApprovalConflictError(
                "resume claim does not match the exact Creative Plan approval"
            )
        return self._validate_creative_plan_approval_claim(
            workspace_id=workspace_id,
            workflow_id=payload.workflow_id,
            creative_plan_id=payload.subject_id,
            creative_plan_version=payload.subject_version,
            approval_id=payload.approval_id,
            expected_workflow_version=payload.expected_workflow_version,
            resulting_workflow_version=payload.resulting_workflow_version,
            decision=payload.decision,
            conflict_message="resume claim does not match the exact Creative Plan approval",
        )

    def _validate_creative_plan_approval_claim(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
        creative_plan_version: int,
        approval_id: str,
        expected_workflow_version: int | None,
        resulting_workflow_version: int | None,
        decision: ApprovalDecision,
        conflict_message: str,
    ) -> CreativePlanResumeClaim:
        validate_workspace_id(workspace_id)
        with self._uow_factory() as uow:
            workflow = uow.workflows.get(
                workflow_id,
                workspace_id=workspace_id,
                for_update=True,
            )
            if workflow is None:
                raise NotFoundError(f"workflow {workflow_id} was not found")
            now = uow.database_now()
            current = uow.creative_plans.get_current(
                workspace_id=workspace_id,
                workflow_id=workflow_id,
                creative_plan_id=creative_plan_id,
            )
            approval = uow.approvals.get(approval_id, workflow_id=workflow_id)
            if current is None or approval is None:
                raise ApprovalConflictError(conflict_message)
            head, plan = current
            expected_version = (
                approval.expected_workflow_version
                if expected_workflow_version is None
                else expected_workflow_version
            )
            resulting_version = (
                workflow.version
                if resulting_workflow_version is None
                else resulting_workflow_version
            )
            expected_status = (
                WorkflowStatus.GENERATING
                if decision is ApprovalDecision.APPROVE
                else WorkflowStatus.PLANNING
            )
            if (
                workflow.status is not expected_status
                or workflow.retention_status is not RetentionStatus.ACTIVE
                or workflow.current_node != "approve_plan"
                or workflow.version != resulting_version
                or resulting_version != expected_version + 1
                or approval.expected_workflow_version != expected_version
                or approval.approval_type is not ApprovalType.CREATIVE_PLAN
                or approval.decision is not decision
                or approval.subject_id != creative_plan_id
                or approval.subject_version != creative_plan_version
                or plan.version_number != creative_plan_version
                or head.current_version_number != creative_plan_version
                or head.current_version_id != plan.id
                or now >= workflow.expires_at
                or now >= head.retain_until
            ):
                raise ApprovalConflictError(conflict_message)
            return CreativePlanResumeClaim(
                workflow_version=workflow.version,
                plan=plan,
                approval=approval,
                retain_until=min(workflow.expires_at, head.retain_until),
            )

    def events(self, *, workflow_id: str, workspace_id: str) -> list[EventResponse]:
        with self._uow_factory() as uow:
            workflow = uow.workflows.get(workflow_id, workspace_id=workspace_id)
            if workflow is None:
                raise NotFoundError(f"workflow {workflow_id} was not found")
            events = uow.outbox.list_for_aggregate(workflow_id)
        return [
            EventResponse(
                event_id=event.envelope.event_id,
                event_type=event.envelope.event_type,
                schema_version=event.envelope.schema_version,
                aggregate_type=event.envelope.aggregate_type,
                aggregate_id=event.envelope.aggregate_id,
                aggregate_version=event.envelope.aggregate_version,
                occurred_at=event.envelope.occurred_at,
                trace_id=event.envelope.trace_id,
                payload=event.envelope.payload,
            )
            for event in events
        ]

    def _load_idempotent(
        self,
        scope: str,
        key_hash: str,
        request_hash: str,
        workspace_id: str,
    ) -> WorkflowResponse | None:
        with self._uow_factory() as uow:
            record = uow.idempotency.get(scope, key_hash)
            if record is None:
                return None
            if record.request_hash != request_hash:
                raise IdempotencyConflictError(
                    "idempotency key was already used with a different request"
                )
            workflow = uow.workflows.get(record.resource_id, workspace_id=workspace_id)
            if workflow is None:
                raise ConcurrencyError("idempotency record references a missing workflow")
            return workflow_response(workflow)

    @staticmethod
    def _approval_target(approval_type: ApprovalType, decision: ApprovalDecision) -> WorkflowStatus:
        targets = {
            (ApprovalType.PRODUCT_BRIEF, ApprovalDecision.APPROVE): WorkflowStatus.RETRIEVING,
            (ApprovalType.CREATIVE_PLAN, ApprovalDecision.APPROVE): WorkflowStatus.GENERATING,
            (ApprovalType.CREATIVE_PLAN, ApprovalDecision.REJECT): WorkflowStatus.PLANNING,
            (ApprovalType.RESULTS, ApprovalDecision.APPROVE): WorkflowStatus.EXPORTING,
            (ApprovalType.RESULTS, ApprovalDecision.REGENERATE): WorkflowStatus.GENERATING,
        }
        try:
            return targets[(approval_type, decision)]
        except KeyError as exc:
            raise ApprovalConflictError(
                f"{decision.value} is not allowed for {approval_type.value}"
            ) from exc

    @staticmethod
    def _validate_approval_state(status: WorkflowStatus, approval_type: ApprovalType) -> None:
        required = {
            ApprovalType.PRODUCT_BRIEF: WorkflowStatus.AWAITING_PRODUCT_CONFIRMATION,
            ApprovalType.CREATIVE_PLAN: WorkflowStatus.AWAITING_PLAN_APPROVAL,
            ApprovalType.RESULTS: WorkflowStatus.AWAITING_RESULT_APPROVAL,
        }[approval_type]
        if status != required:
            raise ApprovalConflictError(
                f"{approval_type.value} approval requires {required.value}, got {status.value}"
            )

    @staticmethod
    def _workflow_event(
        *,
        workflow: Workflow,
        event_type: EventType,
        trace_id: str,
        payload: dict[str, Any],
        now: datetime,
    ) -> OutboxEvent:
        return OutboxEvent(
            envelope=EventEnvelope.create(
                event_type=event_type.value,
                aggregate_type="workflow",
                aggregate_id=workflow.id,
                aggregate_version=workflow.version,
                trace_id=trace_id,
                payload=payload,
                now=now,
            ),
            available_at=now,
            workspace_id=workflow.workspace_id,
        )

    @staticmethod
    def _audit(
        *,
        uow: Any,
        workflow: Workflow,
        actor_id: str,
        trace_id: str,
        action: str,
        metadata: dict[str, Any],
        now: datetime,
    ) -> None:
        uow.audit.add(
            workspace_id=workflow.workspace_id,
            actor_type="USER",
            actor_id=actor_id,
            action=action,
            resource_type="workflow",
            resource_id=workflow.id,
            trace_id=trace_id,
            metadata=metadata,
            created_at=now,
            expires_at=now + timedelta(days=180),
        )
