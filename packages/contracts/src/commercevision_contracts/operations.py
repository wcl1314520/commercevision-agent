"""Versioned HTTP contracts for durable operations and dead-letter administration."""

from datetime import datetime

from commercevision_domain import OperationKind, OperationState, ReconciliationOutcome
from pydantic import BaseModel, Field, JsonValue, field_validator

from .workspace_identity import WorkspaceId


class OperationErrorResponseV1(BaseModel):
    code: str
    category: str
    message: str
    retryable: bool
    provider_request_id: str | None


class OperationResponseV1(BaseModel):
    id: str
    workspace_id: WorkspaceId
    kind: OperationKind
    target_type: str
    target_id: str
    target_version: int
    input_hash: str
    input_ref: str | None
    output_ref: str | None
    provider_request_id: str | None
    state: OperationState
    lease_owner: str | None
    lease_expires_at: datetime | None
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime | None
    execution_deadline_at: datetime
    reconciliation_attempt_count: int
    max_reconciliation_attempts: int
    next_reconciliation_at: datetime | None
    reconciliation_started_at: datetime | None
    reconciliation_deadline_at: datetime | None
    reconciliation_required: bool
    reconciliation_outcome: ReconciliationOutcome
    dead_letter_id: str | None
    replay_source_dead_letter_id: str | None
    replay_attempt: int
    recovery_generation: int
    recovery_consumed_generation: int
    error: OperationErrorResponseV1 | None
    created_at: datetime
    updated_at: datetime
    last_attempt_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    version: int


class OperationListResponseV1(BaseModel):
    items: list[OperationResponseV1]
    next_cursor: str | None


class DeadLetterResponseV1(BaseModel):
    id: str
    consumer: str
    message_id: str
    event_type: str
    payload: dict[str, JsonValue]
    reason: str
    error_class: str | None
    error_message: str | None
    attempt_count: int
    original_created_at: datetime
    created_at: datetime
    source_dead_letter_id: str | None
    replay_attempt: int


class DeadLetterReplayResponseV1(BaseModel):
    id: str
    source_dead_letter_id: str
    actor_id: str
    reason: str
    replayed_at: datetime
    replay_attempt: int
    replay_event_id: str


class DeadLetterDetailResponseV1(BaseModel):
    dead_letter: DeadLetterResponseV1
    replays: list[DeadLetterReplayResponseV1]
    replays_next_cursor: str | None
    child_dead_letters: list[DeadLetterResponseV1]
    child_dead_letters_next_cursor: str | None


class DeadLetterListResponseV1(BaseModel):
    items: list[DeadLetterResponseV1]
    next_cursor: str | None


class DeadLetterReplayRequestV1(BaseModel):
    reason: str = Field(min_length=1, max_length=512)

    @field_validator("reason", mode="before")
    @classmethod
    def _trim_reason(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value
