"""Transport-independent event and reliable-delivery entities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from commercevision_domain.ids import new_uuid7
from commercevision_domain.workflow.entities import utc_now
from commercevision_domain.workflow.enums import InboxStatus


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
        )
