"""Transactional repositories and reliable-delivery claim operations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from commercevision_domain import ConcurrencyError, LeaseConflictError, new_uuid7
from commercevision_domain.messaging import DeadLetterMessage, OutboxEvent
from commercevision_domain.workflow.entities import (
    Approval,
    Workflow,
    WorkflowAttempt,
    WorkflowStep,
)
from commercevision_domain.workflow.enums import InboxStatus, StepStatus
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from .mappers import (
    approval_from_model,
    approval_to_model,
    attempt_from_model,
    attempt_to_model,
    dead_letter_from_model,
    dead_letter_to_model,
    outbox_from_model,
    outbox_to_model,
    step_from_model,
    step_to_model,
    workflow_from_model,
    workflow_to_model,
)
from .models import (
    ApprovalModel,
    AuditEventModel,
    DeadLetterMessageModel,
    IdempotencyKeyModel,
    InboxMessageModel,
    OutboxEventModel,
    WorkflowAttemptModel,
    WorkflowModel,
    WorkflowStepModel,
)


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    scope: str
    key_hash: str
    request_hash: str
    resource_type: str
    resource_id: str
    response_data: dict[str, Any] | None
    status: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class InboxClaim:
    should_process: bool
    already_processed: bool
    dead: bool
    lease_token: str | None
    delivery_attempt: int


class WorkflowRepository:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._loaded_versions: dict[str, int] = {}

    def add(self, workflow: Workflow) -> None:
        self.session.add(workflow_to_model(workflow))
        self._loaded_versions[workflow.id] = workflow.version

    def get(
        self,
        workflow_id: str,
        *,
        workspace_id: str | None = None,
        for_update: bool = False,
    ) -> Workflow | None:
        statement = select(WorkflowModel).where(WorkflowModel.id == workflow_id)
        if workspace_id is not None:
            statement = statement.where(WorkflowModel.workspace_id == workspace_id)
        if for_update:
            statement = statement.with_for_update()
        model = self.session.scalar(statement)
        if model is None:
            return None
        self._loaded_versions[model.id] = model.version
        return workflow_from_model(model)

    def save(self, workflow: Workflow) -> None:
        original_version = self._loaded_versions.get(workflow.id)
        if original_version is None:
            raise ConcurrencyError(f"workflow {workflow.id} was not loaded by this unit of work")
        values = workflow_to_model(workflow)
        result = self.session.execute(
            update(WorkflowModel)
            .where(
                WorkflowModel.id == workflow.id,
                WorkflowModel.version == original_version,
            )
            .values(
                status=values.status,
                retention_status=values.retention_status,
                current_node=values.current_node,
                version=values.version,
                input_json=values.input_json,
                result_json=values.result_json,
                expires_at=values.expires_at,
                cancellation_requested_at=values.cancellation_requested_at,
                updated_at=values.updated_at,
            )
        )
        if result.rowcount != 1:
            raise ConcurrencyError(f"workflow {workflow.id} was concurrently modified")
        self._loaded_versions[workflow.id] = workflow.version

    def list(
        self,
        *,
        workspace_id: str,
        limit: int,
        cursor: tuple[datetime, str] | None = None,
    ) -> list[Workflow]:
        statement = (
            select(WorkflowModel)
            .where(WorkflowModel.workspace_id == workspace_id)
            .order_by(WorkflowModel.created_at.desc(), WorkflowModel.id.desc())
            .limit(limit)
        )
        if cursor:
            created_at, workflow_id = cursor
            statement = statement.where(
                or_(
                    WorkflowModel.created_at < created_at,
                    and_(
                        WorkflowModel.created_at == created_at,
                        WorkflowModel.id < workflow_id,
                    ),
                )
            )
        models = list(self.session.scalars(statement))
        for model in models:
            self._loaded_versions[model.id] = model.version
        return [workflow_from_model(model) for model in models]

    def list_recoverable(self, *, stale_before: datetime, limit: int) -> list[Workflow]:
        terminal = ("COMPLETED", "FAILED", "CANCELLED")
        waiting = (
            "AWAITING_PRODUCT_CONFIRMATION",
            "AWAITING_PLAN_APPROVAL",
            "AWAITING_RESULT_APPROVAL",
        )
        models = list(
            self.session.scalars(
                select(WorkflowModel)
                .where(
                    WorkflowModel.status.not_in((*terminal, *waiting)),
                    WorkflowModel.updated_at < stale_before,
                    WorkflowModel.cancellation_requested_at.is_(None),
                )
                .order_by(WorkflowModel.updated_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for model in models:
            self._loaded_versions[model.id] = model.version
        return [workflow_from_model(model) for model in models]


class StepRepository:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._loaded_versions: dict[str, int] = {}

    def add(self, step: WorkflowStep) -> None:
        self.session.add(step_to_model(step))
        self._loaded_versions[step.id] = step.version

    def get_by_key(
        self, workflow_id: str, step_key: str, *, for_update: bool = False
    ) -> WorkflowStep | None:
        statement = select(WorkflowStepModel).where(
            WorkflowStepModel.workflow_id == workflow_id,
            WorkflowStepModel.step_key == step_key,
        )
        if for_update:
            statement = statement.with_for_update()
        model = self.session.scalar(statement)
        if model is None:
            return None
        self._loaded_versions[model.id] = model.version
        return step_from_model(model)

    def get(self, step_id: str, *, for_update: bool = False) -> WorkflowStep | None:
        statement = select(WorkflowStepModel).where(WorkflowStepModel.id == step_id)
        if for_update:
            statement = statement.with_for_update()
        model = self.session.scalar(statement)
        if model is None:
            return None
        self._loaded_versions[model.id] = model.version
        return step_from_model(model)

    def save(self, step: WorkflowStep) -> None:
        original_version = self._loaded_versions.get(step.id)
        if original_version is None:
            raise ConcurrencyError(f"step {step.id} was not loaded by this unit of work")
        values = step_to_model(step)
        result = self.session.execute(
            update(WorkflowStepModel)
            .where(
                WorkflowStepModel.id == step.id,
                WorkflowStepModel.version == original_version,
            )
            .values(
                status=values.status,
                expected_workflow_version=values.expected_workflow_version,
                lease_owner=values.lease_owner,
                lease_token=values.lease_token,
                lease_expires_at=values.lease_expires_at,
                attempt_count=values.attempt_count,
                max_attempts=values.max_attempts,
                next_attempt_at=values.next_attempt_at,
                input_ref=values.input_ref,
                output_ref=values.output_ref,
                input_json=values.input_json,
                output_json=values.output_json,
                error_class=values.error_class,
                error_message=values.error_message,
                started_at=values.started_at,
                completed_at=values.completed_at,
                updated_at=values.updated_at,
                version=values.version,
            )
        )
        if result.rowcount != 1:
            raise ConcurrencyError(f"step {step.id} was concurrently modified")
        self._loaded_versions[step.id] = step.version

    def next_sequence(self, workflow_id: str) -> int:
        current = self.session.scalar(
            select(func.coalesce(func.max(WorkflowStepModel.sequence), 0)).where(
                WorkflowStepModel.workflow_id == workflow_id
            )
        )
        return int(current or 0) + 1

    def list_for_workflow(self, workflow_id: str) -> list[WorkflowStep]:
        models = list(
            self.session.scalars(
                select(WorkflowStepModel)
                .where(WorkflowStepModel.workflow_id == workflow_id)
                .order_by(WorkflowStepModel.sequence)
            )
        )
        for model in models:
            self._loaded_versions[model.id] = model.version
        return [step_from_model(model) for model in models]

    def list_expired_leases(self, *, now: datetime, limit: int) -> list[WorkflowStep]:
        models = list(
            self.session.scalars(
                select(WorkflowStepModel)
                .where(
                    WorkflowStepModel.status.in_(
                        (StepStatus.CLAIMED.value, StepStatus.RUNNING.value)
                    ),
                    WorkflowStepModel.lease_expires_at <= now,
                )
                .order_by(WorkflowStepModel.lease_expires_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for model in models:
            self._loaded_versions[model.id] = model.version
        return [step_from_model(model) for model in models]


class AttemptRepository:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._loaded_versions: dict[str, int] = {}

    def add(self, attempt: WorkflowAttempt) -> None:
        self.session.add(attempt_to_model(attempt))
        self._loaded_versions[attempt.id] = attempt.version

    def get_by_idempotency(
        self, idempotency_key: str, *, for_update: bool = False
    ) -> WorkflowAttempt | None:
        statement = select(WorkflowAttemptModel).where(
            WorkflowAttemptModel.idempotency_key == idempotency_key
        )
        if for_update:
            statement = statement.with_for_update()
        model = self.session.scalar(statement)
        if model is None:
            return None
        self._loaded_versions[model.id] = model.version
        return attempt_from_model(model)

    def save(self, attempt: WorkflowAttempt) -> None:
        original_version = self._loaded_versions.get(attempt.id)
        if original_version is None:
            raise ConcurrencyError(f"attempt {attempt.id} was not loaded by this unit of work")
        values = attempt_to_model(attempt)
        result = self.session.execute(
            update(WorkflowAttemptModel)
            .where(
                WorkflowAttemptModel.id == attempt.id,
                WorkflowAttemptModel.version == original_version,
            )
            .values(
                status=values.status,
                provider_request_id=values.provider_request_id,
                result_ref=values.result_ref,
                result_json=values.result_json,
                error_class=values.error_class,
                error_message=values.error_message,
                updated_at=values.updated_at,
                started_at=values.started_at,
                completed_at=values.completed_at,
                version=values.version,
            )
        )
        if result.rowcount != 1:
            raise ConcurrencyError(f"attempt {attempt.id} was concurrently modified")
        self._loaded_versions[attempt.id] = attempt.version

    def list_for_workflow(self, workflow_id: str) -> list[WorkflowAttempt]:
        models = list(
            self.session.scalars(
                select(WorkflowAttemptModel)
                .where(WorkflowAttemptModel.workflow_id == workflow_id)
                .order_by(WorkflowAttemptModel.created_at)
            )
        )
        for model in models:
            self._loaded_versions[model.id] = model.version
        return [attempt_from_model(model) for model in models]


class ApprovalRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, approval: Approval) -> None:
        self.session.add(approval_to_model(approval))

    def list_for_workflow(self, workflow_id: str) -> list[Approval]:
        models = self.session.scalars(
            select(ApprovalModel)
            .where(ApprovalModel.workflow_id == workflow_id)
            .order_by(ApprovalModel.created_at)
        )
        return [approval_from_model(model) for model in models]


class IdempotencyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def hash_key(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    def get(
        self, scope: str, key_hash: str, *, for_update: bool = False
    ) -> IdempotencyRecord | None:
        statement = select(IdempotencyKeyModel).where(
            IdempotencyKeyModel.scope == scope,
            IdempotencyKeyModel.key_hash == key_hash,
        )
        if for_update:
            statement = statement.with_for_update()
        model = self.session.scalar(statement)
        if model is None:
            return None
        return IdempotencyRecord(
            scope=model.scope,
            key_hash=model.key_hash,
            request_hash=model.request_hash,
            resource_type=model.resource_type,
            resource_id=model.resource_id,
            response_data=model.response_json,
            status=model.status,
            expires_at=model.expires_at,
        )

    def add(
        self,
        *,
        scope: str,
        key_hash: str,
        request_hash: str,
        resource_type: str,
        resource_id: str,
        response_data: dict[str, Any] | None,
        expires_at: datetime,
    ) -> None:
        self.session.add(
            IdempotencyKeyModel(
                id=new_uuid7(),
                scope=scope,
                key_hash=key_hash,
                request_hash=request_hash,
                resource_type=resource_type,
                resource_id=resource_id,
                response_json=response_data,
                status="COMPLETED",
                created_at=datetime.now(expires_at.tzinfo),
                expires_at=expires_at,
            )
        )


class OutboxRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, event: OutboxEvent) -> None:
        self.session.add(outbox_to_model(event))

    def get(self, event_id: str, *, for_update: bool = False) -> OutboxEvent | None:
        statement = select(OutboxEventModel).where(OutboxEventModel.id == event_id)
        if for_update:
            statement = statement.with_for_update()
        model = self.session.scalar(statement)
        return outbox_from_model(model) if model else None

    def claim_ready(
        self,
        *,
        now: datetime,
        owner: str,
        lease_duration: timedelta,
        limit: int,
    ) -> list[OutboxEvent]:
        models = list(
            self.session.scalars(
                select(OutboxEventModel)
                .where(
                    OutboxEventModel.published_at.is_(None),
                    OutboxEventModel.available_at <= now,
                    or_(
                        OutboxEventModel.locked_until.is_(None),
                        OutboxEventModel.locked_until <= now,
                    ),
                )
                .order_by(OutboxEventModel.available_at, OutboxEventModel.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for model in models:
            model.lock_owner = owner
            model.lock_token = new_uuid7()
            model.locked_until = now + lease_duration
            model.publish_attempts += 1
        self.session.flush()
        return [outbox_from_model(model) for model in models]

    def mark_published(self, event_id: str, lock_token: str, *, now: datetime) -> None:
        result = self.session.execute(
            update(OutboxEventModel)
            .where(
                OutboxEventModel.id == event_id,
                OutboxEventModel.lock_token == lock_token,
                OutboxEventModel.published_at.is_(None),
            )
            .values(
                published_at=now,
                lock_owner=None,
                lock_token=None,
                locked_until=None,
                last_error=None,
            )
        )
        if result.rowcount != 1:
            raise LeaseConflictError(f"outbox event {event_id} lease does not match")

    def mark_publish_failed(
        self,
        event_id: str,
        lock_token: str,
        *,
        available_at: datetime,
        error_message: str,
    ) -> None:
        result = self.session.execute(
            update(OutboxEventModel)
            .where(
                OutboxEventModel.id == event_id,
                OutboxEventModel.lock_token == lock_token,
                OutboxEventModel.published_at.is_(None),
            )
            .values(
                available_at=available_at,
                lock_owner=None,
                lock_token=None,
                locked_until=None,
                last_error=error_message[:4000],
            )
        )
        if result.rowcount != 1:
            raise LeaseConflictError(f"outbox event {event_id} lease does not match")

    def schedule_retry(
        self,
        event_id: str,
        *,
        available_at: datetime,
        error_message: str,
    ) -> None:
        result = self.session.execute(
            update(OutboxEventModel)
            .where(OutboxEventModel.id == event_id)
            .values(
                available_at=available_at,
                published_at=None,
                lock_owner=None,
                lock_token=None,
                locked_until=None,
                last_error=error_message[:4000],
            )
        )
        if result.rowcount != 1:
            raise LeaseConflictError(f"outbox event {event_id} was not available for retry")

    def list_for_aggregate(self, aggregate_id: str, *, limit: int = 200) -> list[OutboxEvent]:
        models = self.session.scalars(
            select(OutboxEventModel)
            .where(OutboxEventModel.aggregate_id == aggregate_id)
            .order_by(OutboxEventModel.occurred_at, OutboxEventModel.id)
            .limit(limit)
        )
        return [outbox_from_model(model) for model in models]

    def has_unpublished(
        self,
        *,
        aggregate_id: str,
        event_type: str,
        exclude_event_id: str | None = None,
    ) -> bool:
        statement = select(OutboxEventModel.id).where(
            OutboxEventModel.aggregate_id == aggregate_id,
            OutboxEventModel.event_type == event_type,
            OutboxEventModel.published_at.is_(None),
        )
        if exclude_event_id is not None:
            statement = statement.where(OutboxEventModel.id != exclude_event_id)
        event_id = self.session.scalar(statement.limit(1))
        return event_id is not None


class InboxRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def claim(
        self,
        *,
        consumer: str,
        message_id: str,
        owner: str,
        now: datetime,
        lease_duration: timedelta,
        max_attempts: int,
    ) -> InboxClaim:
        model = self.session.scalar(
            select(InboxMessageModel)
            .where(
                InboxMessageModel.consumer == consumer,
                InboxMessageModel.message_id == message_id,
            )
            .with_for_update()
        )
        if model is None:
            token = new_uuid7()
            self.session.add(
                InboxMessageModel(
                    consumer=consumer,
                    message_id=message_id,
                    status=InboxStatus.PROCESSING.value,
                    lease_owner=owner,
                    lease_token=token,
                    lease_expires_at=now + lease_duration,
                    delivery_attempts=1,
                    processed_at=None,
                    error_class=None,
                    error_message=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            return InboxClaim(True, False, False, token, 1)

        if model.status == InboxStatus.PROCESSED.value:
            return InboxClaim(False, True, False, None, model.delivery_attempts)
        if model.status == InboxStatus.DEAD.value:
            return InboxClaim(False, False, True, None, model.delivery_attempts)
        if (
            model.status == InboxStatus.PROCESSING.value
            and model.lease_expires_at
            and model.lease_expires_at > now
        ):
            return InboxClaim(False, False, False, None, model.delivery_attempts)
        if model.delivery_attempts >= max_attempts:
            model.status = InboxStatus.DEAD.value
            model.lease_owner = None
            model.lease_token = None
            model.lease_expires_at = None
            model.updated_at = now
            return InboxClaim(False, False, True, None, model.delivery_attempts)

        token = new_uuid7()
        model.status = InboxStatus.PROCESSING.value
        model.lease_owner = owner
        model.lease_token = token
        model.lease_expires_at = now + lease_duration
        model.delivery_attempts += 1
        model.updated_at = now
        return InboxClaim(True, False, False, token, model.delivery_attempts)

    def mark_processed(
        self,
        *,
        consumer: str,
        message_id: str,
        lease_token: str,
        now: datetime,
    ) -> None:
        self._finish(
            consumer=consumer,
            message_id=message_id,
            lease_token=lease_token,
            status=InboxStatus.PROCESSED,
            now=now,
            error_class=None,
            error_message=None,
        )

    def mark_failed(
        self,
        *,
        consumer: str,
        message_id: str,
        lease_token: str,
        now: datetime,
        error_class: str,
        error_message: str,
    ) -> None:
        self._finish(
            consumer=consumer,
            message_id=message_id,
            lease_token=lease_token,
            status=InboxStatus.FAILED,
            now=now,
            error_class=error_class,
            error_message=error_message,
        )

    def mark_dead(
        self,
        *,
        consumer: str,
        message_id: str,
        lease_token: str,
        now: datetime,
        error_class: str,
        error_message: str,
    ) -> None:
        self._finish(
            consumer=consumer,
            message_id=message_id,
            lease_token=lease_token,
            status=InboxStatus.DEAD,
            now=now,
            error_class=error_class,
            error_message=error_message,
        )

    def _finish(
        self,
        *,
        consumer: str,
        message_id: str,
        lease_token: str,
        status: InboxStatus,
        now: datetime,
        error_class: str | None,
        error_message: str | None,
    ) -> None:
        result = self.session.execute(
            update(InboxMessageModel)
            .where(
                InboxMessageModel.consumer == consumer,
                InboxMessageModel.message_id == message_id,
                InboxMessageModel.lease_token == lease_token,
                InboxMessageModel.status == InboxStatus.PROCESSING.value,
            )
            .values(
                status=status.value,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                processed_at=now if status == InboxStatus.PROCESSED else None,
                error_class=error_class,
                error_message=(error_message[:4000] if error_message else None),
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            raise LeaseConflictError(f"inbox message {consumer}/{message_id} lease does not match")


class DeadLetterRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, message: DeadLetterMessage) -> None:
        existing = self.session.scalar(
            select(DeadLetterMessageModel.id).where(
                DeadLetterMessageModel.consumer == message.consumer,
                DeadLetterMessageModel.message_id == message.message_id,
            )
        )
        if existing is None:
            self.session.add(dead_letter_to_model(message))

    def get(self, *, consumer: str, message_id: str) -> DeadLetterMessage | None:
        model = self.session.scalar(
            select(DeadLetterMessageModel).where(
                DeadLetterMessageModel.consumer == consumer,
                DeadLetterMessageModel.message_id == message_id,
            )
        )
        return dead_letter_from_model(model) if model else None


class AuditRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(
        self,
        *,
        workspace_id: str,
        actor_type: str,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        trace_id: str,
        metadata: dict[str, Any],
        created_at: datetime,
        expires_at: datetime,
    ) -> None:
        self.session.add(
            AuditEventModel(
                id=new_uuid7(),
                workspace_id=workspace_id,
                actor_type=actor_type,
                actor_id=actor_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                trace_id=trace_id,
                metadata_json=metadata,
                created_at=created_at,
                expires_at=expires_at,
            )
        )
