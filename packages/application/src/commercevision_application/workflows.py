"""Workflow command and query use cases."""

from __future__ import annotations

import base64
import hashlib
import json
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
    NotFoundError,
    Workflow,
    WorkflowStatus,
)
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from commercevision_domain.workflow.errors import (
    ApprovalConflictError,
    IdempotencyConflictError,
)

from .ports import UnitOfWorkFactory
from .projections import workflow_response


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
        existing = self._load_idempotent(scope, key_hash, request_hash, workspace_id)
        if existing is not None:
            return existing
        target = self._approval_target(approval_type, request.decision)
        now = datetime.now(UTC)
        with self._uow_factory() as uow:
            workflow = uow.workflows.get(workflow_id, workspace_id=workspace_id, for_update=True)
            if workflow is None:
                raise NotFoundError(f"workflow {workflow_id} was not found")
            self._validate_approval_state(workflow.status, approval_type)
            workflow.assert_version(request.expected_workflow_version)
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
            uow.idempotency.add(
                scope=scope,
                key_hash=key_hash,
                request_hash=request_hash,
                resource_type="workflow",
                resource_id=workflow.id,
                response_data={"workflow_id": workflow.id, "approval_id": approval.id},
                expires_at=workflow.expires_at,
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
        return workflow_response(workflow, approvals=[approval])

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
