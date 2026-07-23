"""Typed seams for the Durable Operation application module."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from commercevision_domain.messaging import (
    DeadLetterMessage,
    OperationReplayLifecycle,
    OutboxEvent,
    ReplayLifecycleState,
    ReplayPreparationKind,
    ReplayWorkKind,
)
from commercevision_domain.operations import DurableOperation, OperationKind

OperationLogicalKey = tuple[str, OperationKind, str, str, int, str]
OperationCursor = tuple[datetime, str]


class OperationRepositoryPort(Protocol):
    def add(self, operation: DurableOperation) -> None: ...
    def get(
        self,
        operation_id: str,
        *,
        workspace_id: str | None = None,
        for_update: bool = False,
    ) -> DurableOperation | None: ...
    def get_by_logical_key(
        self,
        logical_key: OperationLogicalKey,
        *,
        for_update: bool = False,
    ) -> DurableOperation | None: ...
    def list(
        self,
        *,
        workspace_id: str,
        limit: int,
        cursor: OperationCursor | None,
    ) -> list[DurableOperation]: ...
    def save(self, operation: DurableOperation) -> None: ...
    def claim_recoverable(
        self,
        *,
        now: datetime,
        limit: int,
        pending_event_type: str,
    ) -> list[DurableOperation]: ...


class OperationOutboxPort(Protocol):
    def add(self, event: OutboxEvent) -> None: ...
    def has_unpublished(
        self,
        *,
        aggregate_id: str,
        event_type: str,
        exclude_event_id: str | None = None,
    ) -> bool: ...


class OperationDeadLetterPort(Protocol):
    def add(self, message: DeadLetterMessage) -> None: ...
    def get_replay_lifecycle(
        self,
        *,
        source_dead_letter_id: str,
        replay_attempt: int,
        replay_event_id: str,
        workspace_id: str,
        for_update: bool = False,
    ) -> OperationReplayLifecycle | None: ...
    def mark_replay_prepared(
        self,
        *,
        replay_event_id: str,
        operation_id: str,
        preparation_kind: ReplayPreparationKind,
        work_kind: ReplayWorkKind,
        prepared_operation_version: int,
        prepared_at: datetime,
        completed: bool,
    ) -> None: ...
    def mark_replay_claimed(
        self,
        *,
        replay_event_id: str,
        operation_id: str,
        claim_token: str,
        claimed_operation_version: int,
        claimed_at: datetime,
    ) -> None: ...
    def mark_replay_completed(
        self,
        *,
        replay_event_id: str,
        operation_id: str,
        completed_operation_version: int,
        completed_at: datetime,
        expected_state: ReplayLifecycleState,
        claim_token: str | None = None,
    ) -> None: ...
    def complete_claimed_replays(
        self,
        *,
        operation_id: str,
        claim_token: str,
        completed_operation_version: int,
        completed_at: datetime,
    ) -> int: ...


class OperationUnitOfWorkPort(Protocol):
    operations: OperationRepositoryPort
    outbox: OperationOutboxPort
    dead_letters: OperationDeadLetterPort

    def __enter__(self) -> OperationUnitOfWorkPort: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None: ...
    def flush(self) -> None: ...
    def commit(self) -> None: ...


OperationUnitOfWorkFactory = Callable[[], OperationUnitOfWorkPort]
