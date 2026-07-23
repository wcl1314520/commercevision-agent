"""Aggregate enforcing durable operation, lease, retry, and reconciliation invariants."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from commercevision_domain.ids import new_uuid7
from commercevision_domain.workflow.errors import (
    ConcurrencyError,
    InvalidTransitionError,
    LeaseConflictError,
    RetryExhaustedError,
    RetryNotReadyError,
)
from commercevision_domain.workspace_identity import validate_workspace_id

from .enums import OperationKind, OperationState, ReconciliationOutcome


def _require_aware_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must use UTC")


def normalize_provider_request_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > 256:
        raise ValueError("provider request id must contain 1-256 characters")
    return normalized


@dataclass(frozen=True, slots=True)
class NormalizedOperationError:
    code: str
    category: str
    message: str
    retryable: bool
    provider_request_id: str | None = None

    def __post_init__(self) -> None:
        if not self.code or len(self.code) > 128:
            raise ValueError("operation error code must contain 1-128 characters")
        if not self.category or len(self.category) > 64:
            raise ValueError("operation error category must contain 1-64 characters")
        if not self.message or len(self.message) > 4000:
            raise ValueError("operation error message must contain 1-4000 characters")
        object.__setattr__(
            self,
            "provider_request_id",
            normalize_provider_request_id(self.provider_request_id),
        )


@dataclass(slots=True)
class DurableOperation:
    id: str
    workspace_id: str
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
    lease_token: str | None
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
    error: NormalizedOperationError | None
    created_at: datetime
    updated_at: datetime
    last_attempt_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    version: int

    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)

    @classmethod
    def create(
        cls,
        *,
        workspace_id: str,
        kind: OperationKind,
        target_type: str,
        target_id: str,
        target_version: int,
        input_hash: str,
        max_attempts: int,
        max_reconciliation_attempts: int = 8,
        execution_max_elapsed: timedelta = timedelta(hours=24),
        input_ref: str | None = None,
        now: datetime | None = None,
    ) -> DurableOperation:
        created_at = now or datetime.now(UTC)
        _require_aware_utc(created_at, "now")
        if not target_type or len(target_type) > 64:
            raise ValueError("target_type must contain 1-64 characters")
        if not target_id or len(target_id) > 128:
            raise ValueError("target_id must contain 1-128 characters")
        if target_version < 1:
            raise ValueError("target_version must be positive")
        if len(input_hash) != 64 or any(
            character not in "0123456789abcdef" for character in input_hash
        ):
            raise ValueError("input_hash must be a 64-character SHA-256 digest")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if max_reconciliation_attempts < 1:
            raise ValueError("max_reconciliation_attempts must be positive")
        if execution_max_elapsed <= timedelta(0):
            raise ValueError("execution maximum elapsed time must be positive")
        return cls(
            id=new_uuid7(),
            workspace_id=workspace_id,
            kind=kind,
            target_type=target_type,
            target_id=target_id,
            target_version=target_version,
            input_hash=input_hash,
            input_ref=input_ref,
            output_ref=None,
            provider_request_id=None,
            state=OperationState.PENDING,
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            attempt_count=0,
            max_attempts=max_attempts,
            next_attempt_at=None,
            execution_deadline_at=created_at + execution_max_elapsed,
            reconciliation_attempt_count=0,
            max_reconciliation_attempts=max_reconciliation_attempts,
            next_reconciliation_at=None,
            reconciliation_started_at=None,
            reconciliation_deadline_at=None,
            reconciliation_required=False,
            reconciliation_outcome=ReconciliationOutcome.NOT_REQUIRED,
            dead_letter_id=None,
            replay_source_dead_letter_id=None,
            replay_attempt=0,
            recovery_generation=0,
            recovery_consumed_generation=0,
            error=None,
            created_at=created_at,
            updated_at=created_at,
            last_attempt_at=None,
            started_at=None,
            completed_at=None,
            version=1,
        )

    @property
    def logical_key(self) -> tuple[str, OperationKind, str, str, int, str]:
        return (
            self.workspace_id,
            self.kind,
            self.target_type,
            self.target_id,
            self.target_version,
            self.input_hash,
        )

    def assert_version(self, expected_version: int) -> None:
        if self.version != expected_version:
            raise ConcurrencyError(
                f"operation {self.id} version is {self.version}, expected {expected_version}"
            )

    def claim(
        self,
        *,
        owner: str,
        lease_duration: timedelta,
        now: datetime | None = None,
    ) -> str:
        claimed_at = now or datetime.now(UTC)
        _require_aware_utc(claimed_at, "now")
        if self.state != OperationState.PENDING:
            raise InvalidTransitionError(
                f"operation {self.id} cannot be claimed from {self.state.value}"
            )
        return self._claim(owner=owner, lease_duration=lease_duration, now=claimed_at)

    def retry(
        self,
        *,
        owner: str,
        lease_duration: timedelta,
        now: datetime | None = None,
    ) -> str:
        claimed_at = now or datetime.now(UTC)
        _require_aware_utc(claimed_at, "now")
        if self.state != OperationState.RETRYABLE_FAILED:
            raise InvalidTransitionError(
                f"operation {self.id} cannot retry from {self.state.value}"
            )
        if self.next_attempt_at is None or self.next_attempt_at > claimed_at:
            raise RetryNotReadyError(f"operation {self.id} retry is not ready")
        return self._claim(owner=owner, lease_duration=lease_duration, now=claimed_at)

    def _claim(
        self,
        *,
        owner: str,
        lease_duration: timedelta,
        now: datetime,
    ) -> str:
        if not owner:
            raise ValueError("lease owner must not be blank")
        if lease_duration <= timedelta(0):
            raise ValueError("lease duration must be positive")
        if self.execution_deadline_at <= now:
            raise RetryExhaustedError(f"operation {self.id} execution deadline elapsed")
        if self.attempt_count >= self.max_attempts:
            raise RetryExhaustedError(f"operation {self.id} exhausted {self.max_attempts} attempts")
        self.state = OperationState.CLAIMED
        self.lease_owner = owner
        self.lease_token = new_uuid7()
        self.lease_expires_at = now + lease_duration
        self.attempt_count += 1
        self.last_attempt_at = now
        self.next_attempt_at = None
        self.error = None
        self._touch(now)
        return self.lease_token

    def execution_deadline_elapsed(self, *, now: datetime) -> bool:
        _require_aware_utc(now, "now")
        return self.execution_deadline_at <= now

    def exhaust_execution_deadline(
        self,
        *,
        error: NormalizedOperationError,
        now: datetime,
    ) -> None:
        _require_aware_utc(now, "now")
        if self.state not in {
            OperationState.PENDING,
            OperationState.RETRYABLE_FAILED,
        }:
            raise InvalidTransitionError(
                f"operation {self.id} cannot exhaust execution from {self.state.value}"
            )
        if not self.execution_deadline_elapsed(now=now):
            raise InvalidTransitionError(f"operation {self.id} execution deadline has not elapsed")
        self.state = OperationState.FAILED
        self.completed_at = now
        self.next_attempt_at = None
        self.error = replace(error, retryable=False)
        self._clear_lease()
        self._touch(now)

    def require_reconciliation(
        self,
        *,
        lease_token: str,
        error: NormalizedOperationError,
        provider_request_id: str | None = None,
        expected_execution_version: int | None = None,
        expected_attempt_count: int | None = None,
        deadline_at: datetime | None = None,
        now: datetime | None = None,
    ) -> None:
        required_at = now or datetime.now(UTC)
        reconciliation_deadline = deadline_at or required_at + timedelta(hours=24)
        _require_aware_utc(reconciliation_deadline, "deadline_at")
        if reconciliation_deadline <= required_at:
            raise ValueError("reconciliation deadline must be after the start time")
        self._assert_execution_result_authority(
            lease_token=lease_token,
            expected_execution_version=expected_execution_version,
            expected_attempt_count=expected_attempt_count,
            now=required_at,
        )
        self._assert_state(OperationState.RUNNING, "require reconciliation")
        self.state = OperationState.RECONCILING
        self.reconciliation_required = True
        self.reconciliation_outcome = ReconciliationOutcome.PENDING
        self.reconciliation_started_at = self.reconciliation_started_at or required_at
        self.reconciliation_deadline_at = reconciliation_deadline
        self.next_reconciliation_at = required_at
        self._capture_provider_request_id(provider_request_id)
        self._capture_provider_request_id(error)
        self.error = error
        self.next_attempt_at = None
        self._clear_lease()
        self._touch(required_at)

    def start(self, *, lease_token: str, now: datetime | None = None) -> None:
        started_at = now or datetime.now(UTC)
        self._assert_lease(lease_token, started_at)
        self._assert_state(OperationState.CLAIMED, "start")
        self.state = OperationState.RUNNING
        self.started_at = self.started_at or started_at
        self._touch(started_at)

    def succeed(
        self,
        *,
        lease_token: str,
        output_ref: str | None = None,
        provider_request_id: str | None = None,
        expected_execution_version: int | None = None,
        expected_attempt_count: int | None = None,
        now: datetime | None = None,
    ) -> None:
        completed_at = now or datetime.now(UTC)
        self._assert_execution_result_authority(
            lease_token=lease_token,
            expected_execution_version=expected_execution_version,
            expected_attempt_count=expected_attempt_count,
            now=completed_at,
        )
        self._apply_success(
            output_ref=output_ref,
            provider_request_id=provider_request_id,
            now=completed_at,
        )

    def _apply_success(
        self,
        *,
        output_ref: str | None,
        provider_request_id: str | None,
        now: datetime,
    ) -> None:
        if self.state not in {OperationState.RUNNING, OperationState.RECONCILING}:
            raise InvalidTransitionError(
                f"operation {self.id} cannot succeed from {self.state.value}"
            )
        self.state = OperationState.SUCCEEDED
        self.output_ref = output_ref
        self._capture_provider_request_id(provider_request_id)
        self.completed_at = now
        if self.reconciliation_required:
            self.reconciliation_outcome = ReconciliationOutcome.CONFIRMED_SUCCESS
        self._clear_lease()
        self._touch(now)

    def fail(
        self,
        *,
        lease_token: str,
        error: NormalizedOperationError,
        retry_at: datetime | None,
        expected_execution_version: int | None = None,
        expected_attempt_count: int | None = None,
        now: datetime | None = None,
    ) -> None:
        failed_at = now or datetime.now(UTC)
        self._assert_execution_result_authority(
            lease_token=lease_token,
            expected_execution_version=expected_execution_version,
            expected_attempt_count=expected_attempt_count,
            now=failed_at,
        )
        self._apply_failure(error=error, retry_at=retry_at, now=failed_at)

    def _apply_failure(
        self,
        *,
        error: NormalizedOperationError,
        retry_at: datetime | None,
        now: datetime,
    ) -> None:
        if self.state not in {OperationState.RUNNING, OperationState.RECONCILING}:
            raise InvalidTransitionError(f"operation {self.id} cannot fail from {self.state.value}")
        self._capture_provider_request_id(error)
        self.error = error
        if error.retryable and self.attempt_count < self.max_attempts:
            if retry_at is None:
                raise ValueError("retry_at is required for a retryable operation failure")
            _require_aware_utc(retry_at, "retry_at")
            if retry_at < now:
                raise ValueError("retry_at must not be before the failure time")
            self.state = OperationState.RETRYABLE_FAILED
            self.next_attempt_at = retry_at
        else:
            self.state = OperationState.FAILED
            self.completed_at = now
            self.next_attempt_at = None
        self._clear_lease()
        self._touch(now)

    def recover_expired_lease(
        self,
        *,
        retry_at: datetime,
        reconciliation_deadline_at: datetime | None = None,
        now: datetime | None = None,
    ) -> None:
        recovered_at = now or datetime.now(UTC)
        _require_aware_utc(recovered_at, "now")
        _require_aware_utc(retry_at, "retry_at")
        if self.state not in {OperationState.CLAIMED, OperationState.RUNNING}:
            raise InvalidTransitionError(f"operation {self.id} has no execution lease to recover")
        if self.lease_expires_at is None or self.lease_expires_at > recovered_at:
            raise LeaseConflictError(f"operation {self.id} lease has not expired")
        if self.state == OperationState.RUNNING:
            deadline_at = reconciliation_deadline_at or recovered_at + timedelta(hours=24)
            _require_aware_utc(deadline_at, "reconciliation_deadline_at")
            if deadline_at <= recovered_at:
                raise ValueError("reconciliation deadline must be after the recovery time")
            self.state = OperationState.RECONCILING
            self.reconciliation_required = True
            self.reconciliation_outcome = ReconciliationOutcome.PENDING
            self.reconciliation_started_at = self.reconciliation_started_at or recovered_at
            self.reconciliation_deadline_at = deadline_at
            self.next_reconciliation_at = recovered_at
            self.error = NormalizedOperationError(
                code="EXTERNAL_OUTCOME_UNKNOWN",
                category="recovery",
                message="worker lease expired after external execution started",
                retryable=True,
            )
            self.next_attempt_at = None
        elif self.attempt_count < self.max_attempts:
            self.state = OperationState.RETRYABLE_FAILED
            self.next_attempt_at = retry_at
            self.error = NormalizedOperationError(
                code="LEASE_EXPIRED",
                category="recovery",
                message="worker lease expired before external execution started",
                retryable=True,
            )
        else:
            self.state = OperationState.FAILED
            self.completed_at = recovered_at
            self.error = NormalizedOperationError(
                code="LEASE_EXPIRED",
                category="recovery",
                message="worker lease expired and the attempt budget is exhausted",
                retryable=False,
            )
        self._clear_lease()
        self._touch(recovered_at)

    def claim_reconciliation(
        self,
        *,
        owner: str,
        lease_duration: timedelta,
        now: datetime | None = None,
    ) -> str:
        claimed_at = now or datetime.now(UTC)
        _require_aware_utc(claimed_at, "now")
        self._assert_state(OperationState.RECONCILING, "reconcile")
        if not owner:
            raise ValueError("lease owner must not be blank")
        if lease_duration <= timedelta(0):
            raise ValueError("lease duration must be positive")
        if self.lease_expires_at is not None and self.lease_expires_at > claimed_at:
            raise LeaseConflictError(f"operation {self.id} has an active reconciliation lease")
        if (
            self.reconciliation_deadline_at is not None
            and self.reconciliation_deadline_at <= claimed_at
        ):
            raise RetryExhaustedError(f"operation {self.id} reconciliation deadline elapsed")
        if self.reconciliation_attempt_count >= self.max_reconciliation_attempts:
            raise RetryExhaustedError(
                f"operation {self.id} exhausted "
                f"{self.max_reconciliation_attempts} reconciliation attempts"
            )
        if self.next_reconciliation_at is not None and self.next_reconciliation_at > claimed_at:
            raise RetryNotReadyError(f"operation {self.id} reconciliation is not ready")
        self.lease_owner = owner
        self.lease_token = new_uuid7()
        self.lease_expires_at = claimed_at + lease_duration
        self.reconciliation_attempt_count += 1
        self.next_reconciliation_at = None
        self._touch(claimed_at)
        return self.lease_token

    def defer_reconciliation(
        self,
        *,
        lease_token: str,
        error: NormalizedOperationError,
        provider_request_id: str | None = None,
        next_reconciliation_at: datetime | None,
        expected_reconciliation_version: int | None = None,
        expected_reconciliation_attempt_count: int | None = None,
        now: datetime | None = None,
    ) -> None:
        deferred_at = now or datetime.now(UTC)
        if next_reconciliation_at is not None:
            _require_aware_utc(next_reconciliation_at, "next_reconciliation_at")
            if next_reconciliation_at <= deferred_at:
                raise ValueError("next reconciliation must be after the current time")
        elif error.retryable:
            raise ValueError("retryable reconciliation uncertainty requires a next attempt")
        self._assert_reconciliation_result_authority(
            lease_token=lease_token,
            expected_reconciliation_version=expected_reconciliation_version,
            expected_reconciliation_attempt_count=expected_reconciliation_attempt_count,
            now=deferred_at,
        )
        self._assert_state(OperationState.RECONCILING, "defer reconciliation")
        self.reconciliation_required = True
        self.reconciliation_outcome = ReconciliationOutcome.PENDING
        self._capture_provider_request_id(provider_request_id)
        self._capture_provider_request_id(error)
        self.error = error
        deadline_elapsed = (
            self.reconciliation_deadline_at is not None
            and next_reconciliation_at is not None
            and next_reconciliation_at >= self.reconciliation_deadline_at
        )
        attempts_exhausted = self.reconciliation_attempt_count >= self.max_reconciliation_attempts
        if not error.retryable or deadline_elapsed or attempts_exhausted:
            self.state = OperationState.FAILED
            self.completed_at = deferred_at
            self.next_reconciliation_at = None
            self.error = replace(error, retryable=False)
        else:
            self.next_reconciliation_at = next_reconciliation_at
        self._clear_lease()
        self._touch(deferred_at)

    def exhaust_reconciliation(
        self,
        *,
        error: NormalizedOperationError,
        now: datetime | None = None,
    ) -> None:
        exhausted_at = now or datetime.now(UTC)
        _require_aware_utc(exhausted_at, "now")
        self._assert_state(OperationState.RECONCILING, "exhaust reconciliation")
        if self.lease_expires_at is not None and self.lease_expires_at > exhausted_at:
            raise LeaseConflictError(f"operation {self.id} has an active reconciliation lease")
        self.state = OperationState.FAILED
        self.completed_at = exhausted_at
        self.next_reconciliation_at = None
        self.error = replace(error, retryable=False)
        self._clear_lease()
        self._touch(exhausted_at)

    def reconciliation_exhausted(self, *, now: datetime) -> bool:
        _require_aware_utc(now, "now")
        return self.reconciliation_attempt_count >= self.max_reconciliation_attempts or (
            self.reconciliation_deadline_at is not None and self.reconciliation_deadline_at <= now
        )

    def resolve_reconciliation(
        self,
        *,
        lease_token: str,
        outcome: ReconciliationOutcome,
        error: NormalizedOperationError | None = None,
        retry_at: datetime | None = None,
        output_ref: str | None = None,
        provider_request_id: str | None = None,
        expected_reconciliation_version: int | None = None,
        expected_reconciliation_attempt_count: int | None = None,
        now: datetime | None = None,
    ) -> None:
        resolved_at = now or datetime.now(UTC)
        self._assert_reconciliation_result_authority(
            lease_token=lease_token,
            expected_reconciliation_version=expected_reconciliation_version,
            expected_reconciliation_attempt_count=expected_reconciliation_attempt_count,
            now=resolved_at,
        )
        self._assert_state(OperationState.RECONCILING, "resolve reconciliation")
        self._capture_provider_request_id(provider_request_id)
        if outcome == ReconciliationOutcome.CONFIRMED_SUCCESS:
            self._apply_success(
                output_ref=output_ref,
                provider_request_id=provider_request_id,
                now=resolved_at,
            )
            return
        if outcome != ReconciliationOutcome.CONFIRMED_FAILURE:
            raise InvalidTransitionError(f"reconciliation outcome {outcome.value} is not final")
        if error is None:
            raise ValueError("a failed reconciliation requires a normalized error")
        self.reconciliation_outcome = outcome
        self._apply_failure(
            error=error,
            retry_at=retry_at,
            now=resolved_at,
        )

    def wait_for_human(self, *, lease_token: str, now: datetime | None = None) -> None:
        waiting_at = now or datetime.now(UTC)
        self._assert_lease(lease_token, waiting_at)
        self._assert_state(OperationState.RUNNING, "wait for human")
        self.state = OperationState.WAITING_HUMAN
        self._clear_lease()
        self._touch(waiting_at)

    def complete_human_wait(
        self,
        *,
        output_ref: str | None = None,
        now: datetime | None = None,
    ) -> None:
        completed_at = now or datetime.now(UTC)
        _require_aware_utc(completed_at, "now")
        self._assert_state(OperationState.WAITING_HUMAN, "complete human wait")
        self.state = OperationState.SUCCEEDED
        self.output_ref = output_ref
        self.completed_at = completed_at
        self._touch(completed_at)

    def cancel(
        self,
        *,
        expected_version: int | None = None,
        now: datetime | None = None,
    ) -> None:
        cancelled_at = now or datetime.now(UTC)
        _require_aware_utc(cancelled_at, "now")
        if expected_version is not None:
            self.assert_version(expected_version)
        if self.state.terminal:
            raise InvalidTransitionError(
                f"operation {self.id} cannot be cancelled from {self.state.value}"
            )
        self.state = OperationState.CANCELLED
        self.completed_at = cancelled_at
        self.next_attempt_at = None
        self._clear_lease()
        self._touch(cancelled_at)

    def replay_failure(
        self,
        *,
        source_dead_letter_id: str,
        replay_attempt: int,
        reconcile_only: bool,
        execution_deadline_at: datetime,
        reconciliation_deadline_at: datetime,
        now: datetime | None = None,
    ) -> None:
        replayed_at = now or datetime.now(UTC)
        _require_aware_utc(replayed_at, "now")
        _require_aware_utc(execution_deadline_at, "execution_deadline_at")
        _require_aware_utc(reconciliation_deadline_at, "reconciliation_deadline_at")
        self._assert_state(OperationState.FAILED, "replay")
        if not source_dead_letter_id:
            raise ValueError("source_dead_letter_id must not be blank")
        if replay_attempt < 1:
            raise ValueError("replay_attempt must be positive")
        self.dead_letter_id = None
        self.replay_source_dead_letter_id = source_dead_letter_id
        self.replay_attempt = replay_attempt
        self.completed_at = None
        if reconcile_only:
            if reconciliation_deadline_at <= replayed_at:
                raise ValueError("reconciliation deadline must be after replay")
            self.state = OperationState.RECONCILING
            self.reconciliation_required = True
            self.reconciliation_outcome = ReconciliationOutcome.PENDING
            self.max_reconciliation_attempts = self.reconciliation_attempt_count + 1
            self.reconciliation_started_at = replayed_at
            self.reconciliation_deadline_at = reconciliation_deadline_at
            self.next_reconciliation_at = replayed_at
        else:
            if execution_deadline_at <= replayed_at:
                raise ValueError("execution deadline must be after replay")
            self.state = OperationState.RETRYABLE_FAILED
            self.max_attempts = self.attempt_count + 1
            self.next_attempt_at = replayed_at
            self.execution_deadline_at = execution_deadline_at
        self._clear_lease()
        self._touch(replayed_at)

    def apply_transport_replay(
        self,
        *,
        source_dead_letter_id: str,
        replay_attempt: int,
        recovery_generation: int,
        now: datetime,
    ) -> bool:
        _require_aware_utc(now, "now")
        if self.state not in {
            OperationState.RETRYABLE_FAILED,
            OperationState.RECONCILING,
        }:
            return False
        if not source_dead_letter_id:
            raise ValueError("source_dead_letter_id must not be blank")
        if replay_attempt < 1:
            raise ValueError("replay_attempt must be positive")
        if not self.consume_recovery_generation(recovery_generation, now=now):
            return False
        self.replay_source_dead_letter_id = source_dead_letter_id
        self.replay_attempt = replay_attempt
        return True

    def mark_dead_lettered(self, dead_letter_id: str, *, now: datetime) -> None:
        _require_aware_utc(now, "now")
        self._assert_state(OperationState.FAILED, "record a dead letter")
        if self.dead_letter_id is not None:
            raise InvalidTransitionError(f"operation {self.id} is already dead-lettered")
        if not dead_letter_id:
            raise ValueError("dead_letter_id must not be blank")
        self.dead_letter_id = dead_letter_id
        self._touch(now)

    def reserve_recovery_generation(self, *, now: datetime) -> int:
        _require_aware_utc(now, "now")
        if self.recovery_generation != self.recovery_consumed_generation:
            raise InvalidTransitionError(
                f"operation {self.id} already has an outstanding recovery generation"
            )
        self.recovery_generation += 1
        self._touch(now)
        return self.recovery_generation

    def consume_recovery_generation(self, generation: int, *, now: datetime) -> bool:
        _require_aware_utc(now, "now")
        if generation == 0:
            return False
        if generation < 0:
            raise ValueError("recovery generation must not be negative")
        if generation <= self.recovery_consumed_generation:
            return False
        if generation != self.recovery_generation:
            raise InvalidTransitionError(
                f"operation {self.id} recovery generation {generation} is not outstanding"
            )
        self.recovery_consumed_generation = generation
        self._touch(now)
        return True

    def record_late_execution_provider_identity(
        self,
        *,
        provider_request_id: str | None,
        expected_execution_version: int,
        expected_attempt_count: int,
        now: datetime,
    ) -> bool:
        _require_aware_utc(now, "now")
        if self.version <= expected_execution_version:
            raise ConcurrencyError(
                f"operation {self.id} has no persisted recovery after execution "
                f"version {expected_execution_version}"
            )
        if self.attempt_count != expected_attempt_count:
            raise ConcurrencyError(
                f"operation {self.id} execution attempt is {self.attempt_count}, "
                f"expected {expected_attempt_count}"
            )
        if (
            self.state
            not in {
                OperationState.RECONCILING,
                OperationState.RETRYABLE_FAILED,
            }
            and not self.state.terminal
        ):
            raise LeaseConflictError(
                f"operation {self.id} cannot retain a stale execution result "
                f"from {self.state.value}"
            )
        previous = self.provider_request_id
        self._capture_provider_request_id(provider_request_id)
        if self.provider_request_id == previous:
            return False
        self._touch(now)
        return True

    def record_late_reconciliation_provider_identity(
        self,
        *,
        provider_request_id: str | None,
        expected_reconciliation_version: int,
        expected_reconciliation_attempt_count: int,
        now: datetime,
    ) -> bool:
        _require_aware_utc(now, "now")
        if self.version <= expected_reconciliation_version:
            raise ConcurrencyError(
                f"operation {self.id} has no persisted recovery after reconciliation "
                f"version {expected_reconciliation_version}"
            )
        if self.reconciliation_attempt_count < expected_reconciliation_attempt_count:
            raise ConcurrencyError(
                f"operation {self.id} reconciliation attempt is "
                f"{self.reconciliation_attempt_count}, expected at least "
                f"{expected_reconciliation_attempt_count}"
            )
        if self.state != OperationState.RECONCILING and not self.state.terminal:
            raise LeaseConflictError(
                f"operation {self.id} cannot retain a stale reconciliation result "
                f"from {self.state.value}"
            )
        previous = self.provider_request_id
        self._capture_provider_request_id(provider_request_id)
        if self.provider_request_id == previous:
            return False
        self._touch(now)
        return True

    def _assert_lease(self, lease_token: str, now: datetime) -> None:
        _require_aware_utc(now, "now")
        if self.lease_token != lease_token:
            raise LeaseConflictError(f"operation {self.id} lease token does not match")
        if self.lease_expires_at is None or self.lease_expires_at <= now:
            raise LeaseConflictError(f"operation {self.id} lease has expired")

    def _assert_execution_result_authority(
        self,
        *,
        lease_token: str,
        expected_execution_version: int | None,
        expected_attempt_count: int | None,
        now: datetime,
    ) -> None:
        if expected_execution_version is None and expected_attempt_count is None:
            self._assert_lease(lease_token, now)
            return
        if expected_execution_version is None or expected_attempt_count is None:
            raise ValueError("execution version and attempt count must be supplied together")
        _require_aware_utc(now, "now")
        # CAS authority survives lease expiry until a scanner advances the persisted version.
        self.assert_version(expected_execution_version)
        self._assert_state(OperationState.RUNNING, "settle execution")
        if self.attempt_count != expected_attempt_count:
            raise ConcurrencyError(
                f"operation {self.id} execution attempt is {self.attempt_count}, "
                f"expected {expected_attempt_count}"
            )
        if self.lease_token != lease_token:
            raise LeaseConflictError(f"operation {self.id} lease token does not match")

    def _assert_reconciliation_result_authority(
        self,
        *,
        lease_token: str,
        expected_reconciliation_version: int | None,
        expected_reconciliation_attempt_count: int | None,
        now: datetime,
    ) -> None:
        if (
            expected_reconciliation_version is None
            and expected_reconciliation_attempt_count is None
        ):
            self._assert_lease(lease_token, now)
            return
        if expected_reconciliation_version is None or expected_reconciliation_attempt_count is None:
            raise ValueError("reconciliation version and attempt count must be supplied together")
        _require_aware_utc(now, "now")
        # CAS authority survives lease expiry until another transaction advances the claim.
        self.assert_version(expected_reconciliation_version)
        self._assert_state(OperationState.RECONCILING, "settle reconciliation")
        if self.reconciliation_attempt_count != expected_reconciliation_attempt_count:
            raise ConcurrencyError(
                f"operation {self.id} reconciliation attempt is "
                f"{self.reconciliation_attempt_count}, expected "
                f"{expected_reconciliation_attempt_count}"
            )
        if self.lease_token != lease_token:
            raise LeaseConflictError(f"operation {self.id} lease token does not match")

    def _assert_state(self, expected: OperationState, action: str) -> None:
        if self.state != expected:
            raise InvalidTransitionError(
                f"operation {self.id} cannot {action} from {self.state.value}"
            )

    def _clear_lease(self) -> None:
        self.lease_owner = None
        self.lease_token = None
        self.lease_expires_at = None

    def _capture_provider_request_id(
        self,
        value: NormalizedOperationError | str | None,
    ) -> None:
        provider_request_id = (
            value.provider_request_id
            if isinstance(value, NormalizedOperationError)
            else normalize_provider_request_id(value)
        )
        if self.provider_request_id is None and provider_request_id is not None:
            self.provider_request_id = provider_request_id

    def _touch(self, now: datetime) -> None:
        self.version += 1
        self.updated_at = now
