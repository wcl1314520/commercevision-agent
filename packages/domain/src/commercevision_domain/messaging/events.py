"""Transport-independent event and reliable-delivery entities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from commercevision_domain.ids import new_uuid7
from commercevision_domain.workflow.entities import utc_now
from commercevision_domain.workflow.enums import InboxStatus
from commercevision_domain.workspace_identity import validate_workspace_id


def _require_aware_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware UTC")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must use UTC")


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_id: str
    event_type: str
    schema_version: int
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    occurred_at: datetime
    trace_id: str
    payload: dict[str, Any]

    @classmethod
    def create(
        cls,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        trace_id: str,
        payload: dict[str, Any],
        schema_version: int = 1,
        now: datetime | None = None,
    ) -> EventEnvelope:
        return cls(
            event_id=new_uuid7(),
            event_type=event_type,
            schema_version=schema_version,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            occurred_at=now or utc_now(),
            trace_id=trace_id,
            payload=payload,
        )


@dataclass(slots=True)
class OutboxEvent:
    envelope: EventEnvelope
    available_at: datetime
    published_at: datetime | None = None
    publish_attempts: int = 0
    lock_owner: str | None = None
    lock_token: str | None = None
    locked_until: datetime | None = None
    last_error: str | None = None
    workspace_id: str | None = None
    source_dead_letter_id: str | None = None
    replay_attempt: int = 0

    def __post_init__(self) -> None:
        if self.workspace_id is not None:
            validate_workspace_id(self.workspace_id)


@dataclass(slots=True)
class InboxMessage:
    consumer: str
    message_id: str
    status: InboxStatus
    lease_owner: str | None
    lease_token: str | None
    lease_expires_at: datetime | None
    delivery_attempts: int
    processed_at: datetime | None
    error_class: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DeadLetterMessage:
    id: str
    consumer: str
    message_id: str
    event_type: str
    payload: dict[str, Any]
    reason: str
    error_class: str | None
    error_message: str | None
    attempt_count: int
    original_created_at: datetime
    created_at: datetime
    workspace_id: str | None = None
    source_dead_letter_id: str | None = None
    replay_attempt: int = 0

    def __post_init__(self) -> None:
        if self.workspace_id is not None:
            validate_workspace_id(self.workspace_id)

    @classmethod
    def create(
        cls,
        *,
        consumer: str,
        message_id: str,
        event_type: str,
        payload: dict[str, Any],
        reason: str,
        attempt_count: int,
        original_created_at: datetime,
        error_class: str | None = None,
        error_message: str | None = None,
        workspace_id: str | None = None,
        source_dead_letter_id: str | None = None,
        replay_attempt: int = 0,
        now: datetime | None = None,
    ) -> DeadLetterMessage:
        return cls(
            id=new_uuid7(),
            consumer=consumer,
            message_id=message_id,
            event_type=event_type,
            payload=payload,
            reason=reason,
            error_class=error_class,
            error_message=error_message,
            attempt_count=attempt_count,
            original_created_at=original_created_at,
            created_at=now or utc_now(),
            workspace_id=workspace_id,
            source_dead_letter_id=source_dead_letter_id,
            replay_attempt=replay_attempt,
        )


@dataclass(frozen=True, slots=True)
class DeadLetterReplay:
    id: str
    source_dead_letter_id: str
    workspace_id: str
    actor_id: str
    reason: str
    replayed_at: datetime
    replay_attempt: int
    replay_event_id: str

    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)

    @classmethod
    def create(
        cls,
        *,
        source_dead_letter_id: str,
        workspace_id: str,
        actor_id: str,
        reason: str,
        replay_attempt: int,
        replay_event_id: str,
        now: datetime | None = None,
    ) -> DeadLetterReplay:
        if not reason or len(reason) > 512:
            raise ValueError("dead-letter replay reason must contain 1-512 characters")
        if replay_attempt < 1:
            raise ValueError("dead-letter replay attempt must be positive")
        replayed_at = now or datetime.now(UTC)
        _require_aware_utc(replayed_at, "replayed_at")
        return cls(
            id=new_uuid7(),
            source_dead_letter_id=source_dead_letter_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            reason=reason,
            replayed_at=replayed_at,
            replay_attempt=replay_attempt,
            replay_event_id=replay_event_id,
        )


class ReplayLifecycleState(StrEnum):
    RECORDED = "RECORDED"
    PREPARED = "PREPARED"
    CLAIMED = "CLAIMED"
    COMPLETED = "COMPLETED"


class ReplayPreparationKind(StrEnum):
    TERMINAL_OPERATION = "TERMINAL_OPERATION"
    TRANSPORT = "TRANSPORT"


class ReplayWorkKind(StrEnum):
    EXECUTION = "EXECUTION"
    RECONCILIATION = "RECONCILIATION"


@dataclass(frozen=True, slots=True)
class OperationReplayLifecycle:
    source_dead_letter_id: str
    replay_attempt: int
    replay_event_id: str
    workspace_id: str
    state: ReplayLifecycleState
    operation_id: str | None
    preparation_kind: ReplayPreparationKind | None
    work_kind: ReplayWorkKind | None
    prepared_at: datetime | None
    prepared_operation_version: int | None
    claim_token: str | None
    claimed_at: datetime | None
    claimed_operation_version: int | None
    completed_at: datetime | None
    completed_operation_version: int | None

    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)
