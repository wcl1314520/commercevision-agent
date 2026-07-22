"""Domain entities enforcing workflow, lease, and version invariants."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from commercevision_domain.ids import new_uuid7

from .enums import (
    ApprovalDecision,
    ApprovalType,
    AttemptStatus,
    RetentionStatus,
    StepStatus,
    StepType,
    WorkflowStatus,
)
from .errors import ConcurrencyError, LeaseConflictError, RetryExhaustedError
from .transitions import (
    ATTEMPT_TRANSITIONS,
    RETENTION_TRANSITIONS,
    STEP_TRANSITIONS,
    WORKFLOW_TRANSITIONS,
    assert_transition,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class Workflow:
    id: str
    workspace_id: str
    created_by: str
    workflow_type: str
    status: WorkflowStatus
    retention_status: RetentionStatus
    current_node: str | None
    version: int
    input_data: dict[str, Any]
    result_data: dict[str, Any] | None
    expires_at: datetime
    cancellation_requested_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(
        cls,
        *,
        workspace_id: str,
        created_by: str,
        workflow_type: str,
        input_data: dict[str, Any],
        retention: timedelta,
        now: datetime | None = None,
    ) -> Workflow:
        created_at = now or utc_now()
        return cls(
            id=new_uuid7(),
            workspace_id=workspace_id,
            created_by=created_by,
            workflow_type=workflow_type,
            status=WorkflowStatus.DRAFT,
            retention_status=RetentionStatus.ACTIVE,
            current_node=None,
            version=1,
            input_data=input_data,
            result_data=None,
            expires_at=created_at + retention,
            cancellation_requested_at=None,
            created_at=created_at,
            updated_at=created_at,
        )

    def assert_version(self, expected_version: int) -> None:
        if self.version != expected_version:
            raise ConcurrencyError(
                f"workflow {self.id} version is {self.version}, expected {expected_version}"
            )

    def transition(
        self,
        target: WorkflowStatus,
        *,
        current_node: str | None = None,
        expected_version: int | None = None,
        now: datetime | None = None,
    ) -> None:
        if expected_version is not None:
            self.assert_version(expected_version)
        if target == self.status and current_node == self.current_node:
            return
        assert_transition(self.status, target, WORKFLOW_TRANSITIONS)
        self.status = target
        self.current_node = current_node
        self.version += 1
        self.updated_at = now or utc_now()

    def update_current_node(self, current_node: str, *, now: datetime | None = None) -> None:
        if self.current_node == current_node:
            return
        self.current_node = current_node
        self.version += 1
        self.updated_at = now or utc_now()

    def request_cancellation(self, *, expected_version: int, now: datetime | None = None) -> None:
        self.assert_version(expected_version)
        if self.status.terminal:
            raise ConcurrencyError(f"workflow {self.id} is already terminal")
        requested_at = now or utc_now()
        self.cancellation_requested_at = requested_at
        self.transition(WorkflowStatus.CANCELLED, now=requested_at)

    def transition_retention(self, target: RetentionStatus, *, now: datetime | None = None) -> None:
        assert_transition(self.retention_status, target, RETENTION_TRANSITIONS)
        if target == self.retention_status:
            return
        self.retention_status = target
        self.version += 1
        self.updated_at = now or utc_now()


@dataclass(slots=True)
class WorkflowStep:
    id: str
    workflow_id: str
    step_key: str
    step_type: StepType
    status: StepStatus
    sequence: int
    expected_workflow_version: int
    lease_owner: str | None
    lease_token: str | None
    lease_expires_at: datetime | None
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime | None
    input_ref: str | None
    output_ref: str | None
    input_data: dict[str, Any] | None
    output_data: dict[str, Any] | None
    error_class: str | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int = 1

    @classmethod
    def create(
        cls,
        *,
        workflow_id: str,
        step_key: str,
        step_type: StepType,
        sequence: int,
        expected_workflow_version: int,
        max_attempts: int,
        input_ref: str | None = None,
        input_data: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> WorkflowStep:
        created_at = now or utc_now()
        return cls(
            id=new_uuid7(),
            workflow_id=workflow_id,
            step_key=step_key,
            step_type=step_type,
            status=StepStatus.PENDING,
            sequence=sequence,
            expected_workflow_version=expected_workflow_version,
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            attempt_count=0,
            max_attempts=max_attempts,
            next_attempt_at=None,
            input_ref=input_ref,
            output_ref=None,
            input_data=input_data,
            output_data=None,
            error_class=None,
            error_message=None,
            started_at=None,
            completed_at=None,
            created_at=created_at,
            updated_at=created_at,
        )

    def transition(self, target: StepStatus, *, now: datetime | None = None) -> None:
        if target == self.status:
            return
        assert_transition(self.status, target, STEP_TRANSITIONS)
        self.status = target
        self.version += 1
        self.updated_at = now or utc_now()

    def queue(self, *, now: datetime | None = None) -> None:
        self.transition(StepStatus.QUEUED, now=now)
        self.next_attempt_at = None

    def claim(
        self,
        *,
        owner: str,
        lease_duration: timedelta,
        now: datetime | None = None,
    ) -> str:
        claimed_at = now or utc_now()
        if self.status == StepStatus.RETRYABLE_FAILED:
            if self.attempt_count >= self.max_attempts:
                raise RetryExhaustedError(f"step {self.id} exhausted {self.max_attempts} attempts")
            self.queue(now=claimed_at)
        if self.status in {StepStatus.CLAIMED, StepStatus.RUNNING}:
            if self.lease_expires_at and self.lease_expires_at > claimed_at:
                raise LeaseConflictError(f"step {self.id} has an active lease")
            if self.status == StepStatus.RUNNING:
                self.transition(StepStatus.RETRYABLE_FAILED, now=claimed_at)
                self.queue(now=claimed_at)
            else:
                self.transition(StepStatus.QUEUED, now=claimed_at)
        self.transition(StepStatus.CLAIMED, now=claimed_at)
        self.lease_owner = owner
        self.lease_token = new_uuid7()
        self.lease_expires_at = claimed_at + lease_duration
        self.attempt_count += 1
        return self.lease_token

    def start(self, *, lease_token: str, now: datetime | None = None) -> None:
        self.assert_lease(lease_token, now=now)
        self.transition(StepStatus.RUNNING, now=now)
        self.started_at = self.started_at or now or utc_now()

    def wait_for_human(self, *, lease_token: str, now: datetime | None = None) -> None:
        self.assert_lease(lease_token, now=now)
        self.transition(StepStatus.WAITING_HUMAN, now=now)
        self._clear_lease()

    def succeed(
        self,
        *,
        output_ref: str | None = None,
        output_data: dict[str, Any] | None = None,
        lease_token: str | None = None,
        now: datetime | None = None,
    ) -> None:
        if self.status != StepStatus.WAITING_HUMAN:
            if lease_token is None:
                raise LeaseConflictError(f"step {self.id} requires a lease token")
            self.assert_lease(lease_token, now=now)
        self.transition(StepStatus.SUCCEEDED, now=now)
        self.output_ref = output_ref
        self.output_data = output_data
        self.completed_at = now or utc_now()
        self._clear_lease()

    def fail_retryable(
        self,
        *,
        error_class: str,
        error_message: str,
        retry_at: datetime,
        lease_token: str,
        now: datetime | None = None,
    ) -> None:
        self.assert_lease(lease_token, now=now)
        if self.attempt_count >= self.max_attempts:
            self.fail_permanently(
                error_class=error_class,
                error_message=error_message,
                lease_token=lease_token,
                now=now,
            )
            return
        self.transition(StepStatus.RETRYABLE_FAILED, now=now)
        self.error_class = error_class
        self.error_message = error_message
        self.next_attempt_at = retry_at
        self._clear_lease()

    def fail_permanently(
        self,
        *,
        error_class: str,
        error_message: str,
        lease_token: str,
        now: datetime | None = None,
    ) -> None:
        self.assert_lease(lease_token, now=now)
        self.transition(StepStatus.FAILED, now=now)
        self.error_class = error_class
        self.error_message = error_message
        self.completed_at = now or utc_now()
        self._clear_lease()

    def recover_expired_lease(
        self,
        *,
        retry_at: datetime,
        now: datetime | None = None,
    ) -> None:
        recovered_at = now or utc_now()
        if self.status not in {StepStatus.CLAIMED, StepStatus.RUNNING}:
            return
        if self.lease_expires_at is None or self.lease_expires_at > recovered_at:
            raise LeaseConflictError(f"step {self.id} lease has not expired")
        if self.status == StepStatus.CLAIMED:
            self.transition(StepStatus.RETRYABLE_FAILED, now=recovered_at)
        else:
            self.transition(StepStatus.RETRYABLE_FAILED, now=recovered_at)
        self.error_class = "LeaseExpired"
        self.error_message = "worker lease expired before durable completion"
        self.next_attempt_at = retry_at
        self._clear_lease()

    def cancel(self, *, now: datetime | None = None) -> None:
        if self.status.terminal:
            return
        self.transition(StepStatus.CANCELLED, now=now)
        self.completed_at = now or utc_now()
        self._clear_lease()

    def assert_lease(self, lease_token: str, *, now: datetime | None = None) -> None:
        checked_at = now or utc_now()
        if self.lease_token != lease_token:
            raise LeaseConflictError(f"step {self.id} lease token does not match")
        if self.lease_expires_at is not None and self.lease_expires_at <= checked_at:
            raise LeaseConflictError(f"step {self.id} lease has expired")

    def _clear_lease(self) -> None:
        self.lease_owner = None
        self.lease_token = None
        self.lease_expires_at = None


@dataclass(slots=True)
class WorkflowAttempt:
    id: str
    workflow_id: str
    step_id: str
    attempt_number: int
    idempotency_key: str
    status: AttemptStatus
    provider_request_id: str | None
    request_ref: str | None
    result_ref: str | None
    request_data: dict[str, Any] | None
    result_data: dict[str, Any] | None
    error_class: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    version: int = 1

    @classmethod
    def create(
        cls,
        *,
        workflow_id: str,
        step_id: str,
        attempt_number: int,
        idempotency_key: str,
        request_ref: str | None = None,
        request_data: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> WorkflowAttempt:
        created_at = now or utc_now()
        return cls(
            id=new_uuid7(),
            workflow_id=workflow_id,
            step_id=step_id,
            attempt_number=attempt_number,
            idempotency_key=idempotency_key,
            status=AttemptStatus.CREATED,
            provider_request_id=None,
            request_ref=request_ref,
            result_ref=None,
            request_data=request_data,
            result_data=None,
            error_class=None,
            error_message=None,
            created_at=created_at,
            updated_at=created_at,
            started_at=None,
            completed_at=None,
        )

    def transition(self, target: AttemptStatus, *, now: datetime | None = None) -> None:
        if target == self.status:
            return
        assert_transition(self.status, target, ATTEMPT_TRANSITIONS)
        self.status = target
        self.version += 1
        self.updated_at = now or utc_now()

    def mark_submitting(self, *, now: datetime | None = None) -> None:
        self.transition(AttemptStatus.SUBMITTING, now=now)
        self.started_at = self.started_at or now or utc_now()

    def succeed(
        self,
        *,
        result_ref: str | None = None,
        result_data: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        self.transition(AttemptStatus.SUCCEEDED, now=now)
        self.result_ref = result_ref
        self.result_data = result_data
        self.completed_at = now or utc_now()


@dataclass(frozen=True, slots=True)
class Approval:
    id: str
    workflow_id: str
    approval_type: ApprovalType
    subject_id: str
    subject_version: int
    decision: ApprovalDecision
    reason_code: str | None
    comment_ref: str | None
    approved_by: str
    expected_workflow_version: int
    created_at: datetime = field(default_factory=utc_now)

    @classmethod
    def create(
        cls,
        *,
        workflow_id: str,
        approval_type: ApprovalType,
        subject_id: str,
        subject_version: int,
        decision: ApprovalDecision,
        approved_by: str,
        expected_workflow_version: int,
        reason_code: str | None = None,
        comment_ref: str | None = None,
        now: datetime | None = None,
    ) -> Approval:
        return cls(
            id=new_uuid7(),
            workflow_id=workflow_id,
            approval_type=approval_type,
            subject_id=subject_id,
            subject_version=subject_version,
            decision=decision,
            reason_code=reason_code,
            comment_ref=comment_ref,
            approved_by=approved_by,
            expected_workflow_version=expected_workflow_version,
            created_at=now or utc_now(),
        )
