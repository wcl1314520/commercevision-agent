"""Structural ports required by application services."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Protocol

from commercevision_domain.messaging import DeadLetterMessage, OutboxEvent
from commercevision_domain.workflow.entities import (
    Approval,
    Workflow,
    WorkflowAttempt,
    WorkflowStep,
)


class WorkflowRepositoryPort(Protocol):
    def add(self, workflow: Workflow) -> None: ...
    def get(
        self,
        workflow_id: str,
        *,
        workspace_id: str | None = None,
        for_update: bool = False,
    ) -> Workflow | None: ...
    def save(self, workflow: Workflow) -> None: ...
    def list(
        self,
        *,
        workspace_id: str,
        limit: int,
        cursor: tuple[datetime, str] | None = None,
    ) -> list[Workflow]: ...
    def list_recoverable(self, *, stale_before: datetime, limit: int) -> list[Workflow]: ...


class StepRepositoryPort(Protocol):
    def add(self, step: WorkflowStep) -> None: ...
    def get_by_key(
        self, workflow_id: str, step_key: str, *, for_update: bool = False
    ) -> WorkflowStep | None: ...
    def get(self, step_id: str, *, for_update: bool = False) -> WorkflowStep | None: ...
    def save(self, step: WorkflowStep) -> None: ...
    def next_sequence(self, workflow_id: str) -> int: ...
    def list_for_workflow(self, workflow_id: str) -> list[WorkflowStep]: ...
    def list_expired_leases(self, *, now: datetime, limit: int) -> list[WorkflowStep]: ...


class AttemptRepositoryPort(Protocol):
    def add(self, attempt: WorkflowAttempt) -> None: ...
    def get_by_idempotency(
        self, idempotency_key: str, *, for_update: bool = False
    ) -> WorkflowAttempt | None: ...
    def save(self, attempt: WorkflowAttempt) -> None: ...
    def list_for_workflow(self, workflow_id: str) -> list[WorkflowAttempt]: ...


class ApprovalRepositoryPort(Protocol):
    def add(self, approval: Approval) -> None: ...
    def list_for_workflow(self, workflow_id: str) -> list[Approval]: ...


class IdempotencyRepositoryPort(Protocol):
    def get(self, scope: str, key_hash: str, *, for_update: bool = False) -> Any | None: ...
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
    ) -> None: ...


class OutboxRepositoryPort(Protocol):
    def add(self, event: OutboxEvent) -> None: ...
    def get(self, event_id: str, *, for_update: bool = False) -> OutboxEvent | None: ...
    def claim_ready(
        self,
        *,
        now: datetime,
        owner: str,
        lease_duration: timedelta,
        limit: int,
    ) -> list[OutboxEvent]: ...
    def mark_published(self, event_id: str, lock_token: str, *, now: datetime) -> None: ...
    def mark_publish_failed(
        self,
        event_id: str,
        lock_token: str,
        *,
        available_at: datetime,
        error_message: str,
    ) -> None: ...
    def list_for_aggregate(self, aggregate_id: str, *, limit: int = 200) -> list[OutboxEvent]: ...
    def has_unpublished(self, *, aggregate_id: str, event_type: str) -> bool: ...


class InboxRepositoryPort(Protocol):
    def claim(self, **kwargs: Any) -> Any: ...
    def mark_processed(self, **kwargs: Any) -> None: ...
    def mark_failed(self, **kwargs: Any) -> None: ...


class DeadLetterRepositoryPort(Protocol):
    def add(self, message: DeadLetterMessage) -> None: ...


class AuditRepositoryPort(Protocol):
    def add(self, **kwargs: Any) -> None: ...


class UnitOfWorkPort(Protocol):
    workflows: WorkflowRepositoryPort
    steps: StepRepositoryPort
    attempts: AttemptRepositoryPort
    approvals: ApprovalRepositoryPort
    idempotency: IdempotencyRepositoryPort
    outbox: OutboxRepositoryPort
    inbox: InboxRepositoryPort
    dead_letters: DeadLetterRepositoryPort
    audit: AuditRepositoryPort

    def __enter__(self) -> UnitOfWorkPort: ...
    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None: ...
    def commit(self) -> None: ...


UnitOfWorkFactory = Callable[[], UnitOfWorkPort]


class MessagePublisher(Protocol):
    def publish(self, event_id: str) -> None: ...
