"""Typed use cases for generic durable operation lifecycle management."""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Protocol

from commercevision_contracts.events import (
    OPERATION_RECOVERY_REQUESTED_V1,
    EventType,
    OperationRecoveryReason,
    OperationRecoveryRequestedPayload,
)
from commercevision_domain import (
    ConcurrencyError,
    InvalidTransitionError,
    LeaseConflictError,
    NotFoundError,
    RetryExhaustedError,
    UniqueConstraintError,
    validate_workspace_id,
)
from commercevision_domain.messaging import (
    DeadLetterMessage,
    EventEnvelope,
    OperationReplayLifecycle,
    OutboxEvent,
    ReplayLifecycleState,
    ReplayPreparationKind,
    ReplayWorkKind,
)
from commercevision_domain.operations import (
    DurableOperation,
    NormalizedOperationError,
    OperationKind,
    OperationState,
    ReconciliationOutcome,
    normalize_provider_request_id,
)

from .operation_ports import (
    OperationLogicalKey,
    OperationUnitOfWorkFactory,
    OperationUnitOfWorkPort,
)


@dataclass(frozen=True, slots=True)
class OperationCreateCommand:
    workspace_id: str
    kind: OperationKind
    target_type: str
    target_id: str
    target_version: int
    input_hash: str
    max_attempts: int
    max_reconciliation_attempts: int = 8
    input_ref: str | None = None

    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)

    @property
    def logical_key(self) -> OperationLogicalKey:
        return (
            self.workspace_id,
            self.kind,
            self.target_type,
            self.target_id,
            self.target_version,
            self.input_hash,
        )


@dataclass(frozen=True, slots=True)
class _OperationReplayClaim:
    operation: DurableOperation
    work_kind: ReplayWorkKind
    lease_token: str | None
    provider_claimed: bool


class OperationApplicationService:
    def __init__(
        self,
        *,
        uow_factory: OperationUnitOfWorkFactory,
        execution_max_elapsed: timedelta = timedelta(hours=24),
    ) -> None:
        if execution_max_elapsed <= timedelta(0):
            raise ValueError("execution maximum elapsed time must be positive")
        self._uow_factory = uow_factory
        self._execution_max_elapsed = execution_max_elapsed

    def create(self, command: OperationCreateCommand) -> DurableOperation:
        conflict: UniqueConstraintError | None = None
        with self._uow_factory() as uow:
            existing = uow.operations.get_by_logical_key(command.logical_key)
            if existing is not None:
                return existing
            operation = DurableOperation.create(
                workspace_id=command.workspace_id,
                kind=command.kind,
                target_type=command.target_type,
                target_id=command.target_id,
                target_version=command.target_version,
                input_hash=command.input_hash,
                input_ref=command.input_ref,
                max_attempts=command.max_attempts,
                max_reconciliation_attempts=command.max_reconciliation_attempts,
                execution_max_elapsed=self._execution_max_elapsed,
                now=datetime.now(UTC),
            )
            uow.operations.add(operation)
            try:
                uow.commit()
            except UniqueConstraintError as exc:
                conflict = exc
            else:
                return operation

        with self._uow_factory() as uow:
            existing = uow.operations.get_by_logical_key(command.logical_key)
            if existing is None:
                assert conflict is not None
                raise conflict
            return existing

    def get(self, *, workspace_id: str, operation_id: str) -> DurableOperation:
        with self._uow_factory() as uow:
            operation = uow.operations.get(
                operation_id,
                workspace_id=workspace_id,
            )
        if operation is None:
            raise NotFoundError(f"operation {operation_id} was not found")
        return operation

    def claim(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        owner: str,
        lease_duration: timedelta,
        now: datetime | None = None,
    ) -> str:
        claimed_at = now or datetime.now(UTC)
        exhausted = False
        with self._uow_factory() as uow:
            operation = self._get_for_update(uow, workspace_id, operation_id)
            if operation.execution_deadline_elapsed(now=claimed_at):
                operation.exhaust_execution_deadline(
                    error=_execution_deadline_error(),
                    now=claimed_at,
                )
                record_terminal_operation_failure(uow, operation, now=claimed_at)
                exhausted = True
                token = None
            else:
                token = operation.claim(
                    owner=owner,
                    lease_duration=lease_duration,
                    now=claimed_at,
                )
            uow.operations.save(operation)
            uow.commit()
        if exhausted:
            raise RetryExhaustedError(f"operation {operation_id} execution deadline elapsed")
        assert token is not None
        return token

    def retry(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        owner: str,
        lease_duration: timedelta,
        now: datetime | None = None,
    ) -> str:
        retry_started_at = now or datetime.now(UTC)
        exhausted = False
        with self._uow_factory() as uow:
            operation = self._get_for_update(uow, workspace_id, operation_id)
            if operation.execution_deadline_elapsed(now=retry_started_at):
                operation.exhaust_execution_deadline(
                    error=_execution_deadline_error(),
                    now=retry_started_at,
                )
                record_terminal_operation_failure(uow, operation, now=retry_started_at)
                exhausted = True
                token = None
            else:
                token = operation.retry(
                    owner=owner,
                    lease_duration=lease_duration,
                    now=retry_started_at,
                )
            uow.operations.save(operation)
            uow.commit()
        if exhausted:
            raise RetryExhaustedError(f"operation {operation_id} execution deadline elapsed")
        assert token is not None
        return token

    def start(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        lease_token: str,
        now: datetime | None = None,
    ) -> DurableOperation:
        started_at = now or datetime.now(UTC)
        with self._uow_factory() as uow:
            operation = self._get_for_update(uow, workspace_id, operation_id)
            operation.start(lease_token=lease_token, now=started_at)
            uow.operations.save(operation)
            uow.commit()
        return operation

    def succeed(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        lease_token: str,
        output_ref: str | None,
        provider_request_id: str | None = None,
        expected_execution_version: int | None = None,
        expected_attempt_count: int | None = None,
        now: datetime | None = None,
    ) -> DurableOperation:
        completed_at = now or datetime.now(UTC)
        with self._uow_factory() as uow:
            operation = self._get_for_update(uow, workspace_id, operation_id)
            if self._retain_late_execution_identity(
                uow,
                operation,
                provider_request_id=provider_request_id,
                expected_execution_version=expected_execution_version,
                expected_attempt_count=expected_attempt_count,
                now=completed_at,
            ):
                return operation
            operation.succeed(
                lease_token=lease_token,
                output_ref=output_ref,
                provider_request_id=provider_request_id,
                expected_execution_version=expected_execution_version,
                expected_attempt_count=expected_attempt_count,
                now=completed_at,
            )
            uow.operations.save(operation)
            self._complete_claimed_replays(
                uow,
                operation,
                claim_token=lease_token,
                completed_at=completed_at,
            )
            uow.commit()
        return operation

    def fail(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        lease_token: str,
        error: NormalizedOperationError,
        retry_at: datetime | None,
        expected_execution_version: int | None = None,
        expected_attempt_count: int | None = None,
        now: datetime | None = None,
    ) -> DurableOperation:
        failed_at = now or datetime.now(UTC)
        with self._uow_factory() as uow:
            operation = self._get_for_update(uow, workspace_id, operation_id)
            if self._retain_late_execution_identity(
                uow,
                operation,
                provider_request_id=error.provider_request_id,
                expected_execution_version=expected_execution_version,
                expected_attempt_count=expected_attempt_count,
                now=failed_at,
            ):
                return operation
            operation.fail(
                lease_token=lease_token,
                error=error,
                retry_at=retry_at,
                expected_execution_version=expected_execution_version,
                expected_attempt_count=expected_attempt_count,
                now=failed_at,
            )
            record_terminal_operation_failure(uow, operation, now=failed_at)
            uow.operations.save(operation)
            self._complete_claimed_replays(
                uow,
                operation,
                claim_token=lease_token,
                completed_at=failed_at,
            )
            uow.commit()
        return operation

    def require_reconciliation(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        lease_token: str,
        error: NormalizedOperationError,
        provider_request_id: str | None = None,
        expected_execution_version: int | None = None,
        expected_attempt_count: int | None = None,
        reconciliation_deadline_at: datetime | None = None,
        now: datetime | None = None,
    ) -> DurableOperation:
        required_at = now or datetime.now(UTC)
        with self._uow_factory() as uow:
            operation = self._get_for_update(uow, workspace_id, operation_id)
            if self._retain_late_execution_identity(
                uow,
                operation,
                provider_request_id=provider_request_id or error.provider_request_id,
                expected_execution_version=expected_execution_version,
                expected_attempt_count=expected_attempt_count,
                now=required_at,
            ):
                return operation
            operation.require_reconciliation(
                lease_token=lease_token,
                error=error,
                provider_request_id=provider_request_id,
                expected_execution_version=expected_execution_version,
                expected_attempt_count=expected_attempt_count,
                deadline_at=reconciliation_deadline_at,
                now=required_at,
            )
            uow.operations.save(operation)
            self._complete_claimed_replays(
                uow,
                operation,
                claim_token=lease_token,
                completed_at=required_at,
            )
            uow.commit()
        return operation

    def claim_reconciliation(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        owner: str,
        lease_duration: timedelta,
        now: datetime | None = None,
    ) -> str:
        claimed_at = now or datetime.now(UTC)
        exhausted = False
        with self._uow_factory() as uow:
            operation = self._get_for_update(uow, workspace_id, operation_id)
            expired_claim_token = (
                operation.lease_token
                if (
                    operation.lease_token is not None
                    and operation.lease_expires_at is not None
                    and operation.lease_expires_at <= claimed_at
                )
                else None
            )
            if operation.reconciliation_exhausted(now=claimed_at):
                operation.exhaust_reconciliation(
                    error=_reconciliation_deadline_error(),
                    now=claimed_at,
                )
                record_terminal_operation_failure(uow, operation, now=claimed_at)
                exhausted = True
                token = None
            else:
                token = operation.claim_reconciliation(
                    owner=owner,
                    lease_duration=lease_duration,
                    now=claimed_at,
                )
            uow.operations.save(operation)
            if expired_claim_token is not None:
                self._complete_claimed_replays(
                    uow,
                    operation,
                    claim_token=expired_claim_token,
                    completed_at=claimed_at,
                )
            uow.commit()
        if exhausted:
            raise RetryExhaustedError(f"operation {operation_id} reconciliation deadline elapsed")
        assert token is not None
        return token

    def resolve_reconciliation(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        lease_token: str,
        outcome: ReconciliationOutcome,
        error: NormalizedOperationError | None = None,
        retry_at: datetime | None = None,
        output_ref: str | None = None,
        provider_request_id: str | None = None,
        expected_reconciliation_version: int | None = None,
        expected_reconciliation_attempt_count: int | None = None,
        now: datetime | None = None,
    ) -> DurableOperation:
        resolved_at = now or datetime.now(UTC)
        with self._uow_factory() as uow:
            operation = self._get_for_update(uow, workspace_id, operation_id)
            if self._retain_late_reconciliation_identity(
                uow,
                operation,
                provider_request_id=(
                    provider_request_id
                    or (error.provider_request_id if error is not None else None)
                ),
                expected_reconciliation_version=expected_reconciliation_version,
                expected_reconciliation_attempt_count=(expected_reconciliation_attempt_count),
                now=resolved_at,
            ):
                return operation
            operation.resolve_reconciliation(
                lease_token=lease_token,
                outcome=outcome,
                error=error,
                retry_at=retry_at,
                output_ref=output_ref,
                provider_request_id=provider_request_id,
                expected_reconciliation_version=expected_reconciliation_version,
                expected_reconciliation_attempt_count=(expected_reconciliation_attempt_count),
                now=resolved_at,
            )
            record_terminal_operation_failure(uow, operation, now=resolved_at)
            uow.operations.save(operation)
            self._complete_claimed_replays(
                uow,
                operation,
                claim_token=lease_token,
                completed_at=resolved_at,
            )
            uow.commit()
        return operation

    def defer_reconciliation(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        lease_token: str,
        error: NormalizedOperationError,
        provider_request_id: str | None = None,
        next_reconciliation_at: datetime | None,
        expected_reconciliation_version: int | None = None,
        expected_reconciliation_attempt_count: int | None = None,
        now: datetime | None = None,
    ) -> DurableOperation:
        deferred_at = now or datetime.now(UTC)
        with self._uow_factory() as uow:
            operation = self._get_for_update(uow, workspace_id, operation_id)
            if self._retain_late_reconciliation_identity(
                uow,
                operation,
                provider_request_id=provider_request_id or error.provider_request_id,
                expected_reconciliation_version=expected_reconciliation_version,
                expected_reconciliation_attempt_count=(expected_reconciliation_attempt_count),
                now=deferred_at,
            ):
                return operation
            operation.defer_reconciliation(
                lease_token=lease_token,
                error=error,
                provider_request_id=provider_request_id,
                next_reconciliation_at=next_reconciliation_at,
                expected_reconciliation_version=expected_reconciliation_version,
                expected_reconciliation_attempt_count=(expected_reconciliation_attempt_count),
                now=deferred_at,
            )
            record_terminal_operation_failure(uow, operation, now=deferred_at)
            uow.operations.save(operation)
            self._complete_claimed_replays(
                uow,
                operation,
                claim_token=lease_token,
                completed_at=deferred_at,
            )
            uow.commit()
        return operation

    def exhaust_reconciliation(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        error: NormalizedOperationError,
        now: datetime | None = None,
    ) -> DurableOperation:
        exhausted_at = now or datetime.now(UTC)
        with self._uow_factory() as uow:
            operation = self._get_for_update(uow, workspace_id, operation_id)
            claim_token = operation.lease_token
            operation.exhaust_reconciliation(error=error, now=exhausted_at)
            record_terminal_operation_failure(uow, operation, now=exhausted_at)
            uow.operations.save(operation)
            if claim_token is not None:
                self._complete_claimed_replays(
                    uow,
                    operation,
                    claim_token=claim_token,
                    completed_at=exhausted_at,
                )
            uow.commit()
        return operation

    def apply_recovery_replay(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        source_dead_letter_id: str,
        replay_attempt: int,
        replay_event_id: str,
        recovery_generation: int,
        reconcile_only: bool,
        execution_deadline_at: datetime,
        reconciliation_deadline_at: datetime,
        now: datetime | None = None,
    ) -> tuple[DurableOperation, bool, bool]:
        replayed_at = now or datetime.now(UTC)
        with self._uow_factory() as uow:
            operation = self._get_for_update(uow, workspace_id, operation_id)
            lifecycle = uow.dead_letters.get_replay_lifecycle(
                source_dead_letter_id=source_dead_letter_id,
                replay_attempt=replay_attempt,
                replay_event_id=replay_event_id,
                workspace_id=workspace_id,
                for_update=True,
            )
            if lifecycle is None:
                raise NotFoundError(
                    f"replay event {replay_event_id} is not registered for "
                    f"dead letter {source_dead_letter_id}"
                )
            preparation_kind = (
                ReplayPreparationKind.TRANSPORT
                if recovery_generation > 0
                else ReplayPreparationKind.TERMINAL_OPERATION
            )
            work_kind = (
                ReplayWorkKind.RECONCILIATION if reconcile_only else ReplayWorkKind.EXECUTION
            )
            if lifecycle.state != ReplayLifecycleState.RECORDED:
                self._validate_replay_lifecycle(
                    lifecycle,
                    operation_id=operation_id,
                    preparation_kind=preparation_kind,
                    work_kind=work_kind,
                )
                return (
                    operation,
                    False,
                    lifecycle.state != ReplayLifecycleState.COMPLETED,
                )

            should_process = False
            if (
                preparation_kind == ReplayPreparationKind.TERMINAL_OPERATION
                and operation.state == OperationState.FAILED
                and operation.dead_letter_id == source_dead_letter_id
            ):
                operation.replay_failure(
                    source_dead_letter_id=source_dead_letter_id,
                    replay_attempt=replay_attempt,
                    reconcile_only=reconcile_only,
                    execution_deadline_at=execution_deadline_at,
                    reconciliation_deadline_at=reconciliation_deadline_at,
                    now=replayed_at,
                )
                uow.operations.save(operation)
                should_process = True
            elif (
                preparation_kind == ReplayPreparationKind.TRANSPORT
                and operation.apply_transport_replay(
                    source_dead_letter_id=source_dead_letter_id,
                    replay_attempt=replay_attempt,
                    recovery_generation=recovery_generation,
                    now=replayed_at,
                )
            ):
                uow.operations.save(operation)
                should_process = True
            uow.dead_letters.mark_replay_prepared(
                replay_event_id=replay_event_id,
                operation_id=operation_id,
                preparation_kind=preparation_kind,
                work_kind=work_kind,
                prepared_operation_version=operation.version,
                prepared_at=replayed_at,
                completed=not should_process,
            )
            uow.commit()
        return operation, True, should_process

    def claim_recovery_replay(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        source_dead_letter_id: str,
        replay_attempt: int,
        replay_event_id: str,
        recovery_generation: int,
        reconcile_only: bool,
        owner: str,
        lease_duration: timedelta,
        now: datetime | None = None,
    ) -> _OperationReplayClaim:
        claimed_at = now or datetime.now(UTC)
        preparation_kind = (
            ReplayPreparationKind.TRANSPORT
            if recovery_generation > 0
            else ReplayPreparationKind.TERMINAL_OPERATION
        )
        work_kind = ReplayWorkKind.RECONCILIATION if reconcile_only else ReplayWorkKind.EXECUTION
        with self._uow_factory() as uow:
            operation = self._get_for_update(uow, workspace_id, operation_id)
            lifecycle = uow.dead_letters.get_replay_lifecycle(
                source_dead_letter_id=source_dead_letter_id,
                replay_attempt=replay_attempt,
                replay_event_id=replay_event_id,
                workspace_id=workspace_id,
                for_update=True,
            )
            if lifecycle is None:
                raise NotFoundError(
                    f"replay event {replay_event_id} is not registered for "
                    f"dead letter {source_dead_letter_id}"
                )
            self._validate_replay_lifecycle(
                lifecycle,
                operation_id=operation_id,
                preparation_kind=preparation_kind,
                work_kind=work_kind,
            )
            if lifecycle.state == ReplayLifecycleState.RECORDED:
                raise ConcurrencyError(f"replay event {replay_event_id} is not prepared")
            if lifecycle.state == ReplayLifecycleState.COMPLETED:
                return _OperationReplayClaim(operation, work_kind, None, False)
            if lifecycle.state == ReplayLifecycleState.CLAIMED:
                if self._replay_claim_is_in_flight(operation, lifecycle):
                    return _OperationReplayClaim(
                        operation,
                        work_kind,
                        lifecycle.claim_token,
                        False,
                    )
                assert lifecycle.claim_token is not None
                uow.dead_letters.mark_replay_completed(
                    replay_event_id=replay_event_id,
                    operation_id=operation_id,
                    completed_operation_version=operation.version,
                    completed_at=claimed_at,
                    expected_state=ReplayLifecycleState.CLAIMED,
                    claim_token=lifecycle.claim_token,
                )
                uow.commit()
                return _OperationReplayClaim(operation, work_kind, None, False)

            if (
                operation.replay_source_dead_letter_id != source_dead_letter_id
                or operation.replay_attempt != replay_attempt
            ):
                self._complete_unclaimed_replay(
                    uow,
                    replay_event_id=replay_event_id,
                    operation=operation,
                    completed_at=claimed_at,
                )
                return _OperationReplayClaim(operation, work_kind, None, False)

            lease_token: str | None = None
            provider_claimed = False
            operation_changed = False
            if work_kind == ReplayWorkKind.EXECUTION:
                if operation.state == OperationState.RETRYABLE_FAILED:
                    if operation.execution_deadline_elapsed(now=claimed_at):
                        operation.exhaust_execution_deadline(
                            error=_execution_deadline_error(),
                            now=claimed_at,
                        )
                        record_terminal_operation_failure(uow, operation, now=claimed_at)
                        operation_changed = True
                    else:
                        try:
                            lease_token = operation.retry(
                                owner=owner,
                                lease_duration=lease_duration,
                                now=claimed_at,
                            )
                        except RetryExhaustedError:
                            lease_token = None
                        else:
                            operation.start(lease_token=lease_token, now=claimed_at)
                            provider_claimed = True
                            operation_changed = True
                elif (
                    operation.state in {OperationState.CLAIMED, OperationState.RUNNING}
                    and operation.lease_token is not None
                ):
                    lease_token = operation.lease_token
            elif operation.state == OperationState.RECONCILING:
                active_lease = (
                    operation.lease_token is not None
                    and operation.lease_expires_at is not None
                    and operation.lease_expires_at > claimed_at
                )
                if active_lease:
                    lease_token = operation.lease_token
                elif operation.reconciliation_exhausted(now=claimed_at):
                    operation.exhaust_reconciliation(
                        error=_reconciliation_deadline_error(),
                        now=claimed_at,
                    )
                    record_terminal_operation_failure(uow, operation, now=claimed_at)
                    operation_changed = True
                else:
                    try:
                        lease_token = operation.claim_reconciliation(
                            owner=owner,
                            lease_duration=lease_duration,
                            now=claimed_at,
                        )
                    except RetryExhaustedError:
                        lease_token = None
                    else:
                        provider_claimed = True
                        operation_changed = True

            if lease_token is None:
                if operation_changed:
                    uow.operations.save(operation)
                self._complete_unclaimed_replay(
                    uow,
                    replay_event_id=replay_event_id,
                    operation=operation,
                    completed_at=claimed_at,
                )
                return _OperationReplayClaim(operation, work_kind, None, False)

            if operation_changed:
                uow.operations.save(operation)
            uow.dead_letters.mark_replay_claimed(
                replay_event_id=replay_event_id,
                operation_id=operation_id,
                claim_token=lease_token,
                claimed_operation_version=operation.version,
                claimed_at=claimed_at,
            )
            uow.commit()
            return _OperationReplayClaim(
                operation,
                work_kind,
                lease_token,
                provider_claimed,
            )

    def complete_recovery_replay(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        source_dead_letter_id: str,
        replay_attempt: int,
        replay_event_id: str,
        claim_token: str,
        now: datetime | None = None,
    ) -> DurableOperation:
        completed_at = now or datetime.now(UTC)
        with self._uow_factory() as uow:
            operation = self._get_for_update(uow, workspace_id, operation_id)
            lifecycle = uow.dead_letters.get_replay_lifecycle(
                source_dead_letter_id=source_dead_letter_id,
                replay_attempt=replay_attempt,
                replay_event_id=replay_event_id,
                workspace_id=workspace_id,
                for_update=True,
            )
            if lifecycle is None:
                raise NotFoundError(
                    f"replay event {replay_event_id} is not registered for "
                    f"dead letter {source_dead_letter_id}"
                )
            if lifecycle.state == ReplayLifecycleState.COMPLETED:
                return operation
            if (
                lifecycle.state != ReplayLifecycleState.CLAIMED
                or lifecycle.claim_token != claim_token
            ):
                raise LeaseConflictError(
                    f"replay event {replay_event_id} claim token does not match"
                )
            if self._replay_claim_is_in_flight(operation, lifecycle):
                raise LeaseConflictError(
                    f"replay event {replay_event_id} provider work is in flight"
                )
            uow.dead_letters.mark_replay_completed(
                replay_event_id=replay_event_id,
                operation_id=operation_id,
                completed_operation_version=operation.version,
                completed_at=completed_at,
                expected_state=ReplayLifecycleState.CLAIMED,
                claim_token=claim_token,
            )
            uow.commit()
        return operation

    @staticmethod
    def _validate_replay_lifecycle(
        lifecycle: OperationReplayLifecycle,
        *,
        operation_id: str,
        preparation_kind: ReplayPreparationKind,
        work_kind: ReplayWorkKind,
    ) -> None:
        if lifecycle.operation_id != operation_id:
            raise ConcurrencyError(
                f"replay event {lifecycle.replay_event_id} targets another operation"
            )
        if lifecycle.preparation_kind != preparation_kind or lifecycle.work_kind != work_kind:
            raise ConcurrencyError(
                f"replay event {lifecycle.replay_event_id} lifecycle does not match its payload"
            )
        if lifecycle.prepared_at is None or lifecycle.prepared_operation_version is None:
            raise ConcurrencyError(
                f"replay event {lifecycle.replay_event_id} has incomplete preparation state"
            )
        if lifecycle.state == ReplayLifecycleState.CLAIMED and (
            lifecycle.claim_token is None
            or lifecycle.claimed_at is None
            or lifecycle.claimed_operation_version is None
        ):
            raise ConcurrencyError(
                f"replay event {lifecycle.replay_event_id} has incomplete claim state"
            )
        if lifecycle.state == ReplayLifecycleState.COMPLETED and (
            lifecycle.completed_at is None or lifecycle.completed_operation_version is None
        ):
            raise ConcurrencyError(
                f"replay event {lifecycle.replay_event_id} has incomplete completion state"
            )

    @staticmethod
    def _replay_claim_is_in_flight(
        operation: DurableOperation,
        lifecycle: OperationReplayLifecycle,
    ) -> bool:
        if lifecycle.claim_token is None or operation.lease_token != lifecycle.claim_token:
            return False
        if lifecycle.work_kind == ReplayWorkKind.EXECUTION:
            return operation.state in {OperationState.CLAIMED, OperationState.RUNNING}
        return operation.state == OperationState.RECONCILING

    @staticmethod
    def _complete_unclaimed_replay(
        uow: OperationUnitOfWorkPort,
        *,
        replay_event_id: str,
        operation: DurableOperation,
        completed_at: datetime,
    ) -> None:
        uow.dead_letters.mark_replay_completed(
            replay_event_id=replay_event_id,
            operation_id=operation.id,
            completed_operation_version=operation.version,
            completed_at=completed_at,
            expected_state=ReplayLifecycleState.PREPARED,
        )
        uow.commit()

    @staticmethod
    def _complete_claimed_replays(
        uow: OperationUnitOfWorkPort,
        operation: DurableOperation,
        *,
        claim_token: str,
        completed_at: datetime,
    ) -> None:
        uow.dead_letters.complete_claimed_replays(
            operation_id=operation.id,
            claim_token=claim_token,
            completed_operation_version=operation.version,
            completed_at=completed_at,
        )

    def cancel(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        expected_version: int,
        now: datetime | None = None,
    ) -> DurableOperation:
        cancelled_at = now or datetime.now(UTC)
        with self._uow_factory() as uow:
            operation = self._get_for_update(uow, workspace_id, operation_id)
            operation.cancel(expected_version=expected_version, now=cancelled_at)
            uow.operations.save(operation)
            uow.commit()
        return operation

    def consume_recovery_generation(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        generation: int,
        now: datetime | None = None,
    ) -> DurableOperation:
        consumed_at = now or datetime.now(UTC)
        with self._uow_factory() as uow:
            operation = self._get_for_update(uow, workspace_id, operation_id)
            if operation.consume_recovery_generation(generation, now=consumed_at):
                uow.operations.save(operation)
                uow.commit()
        return operation

    def list(
        self,
        *,
        workspace_id: str,
        limit: int,
        cursor: tuple[datetime, str] | None,
    ) -> list[DurableOperation]:
        with self._uow_factory() as uow:
            return uow.operations.list(
                workspace_id=workspace_id,
                limit=limit,
                cursor=cursor,
            )

    @staticmethod
    def _get_for_update(
        uow: OperationUnitOfWorkPort,
        workspace_id: str,
        operation_id: str,
    ) -> DurableOperation:
        operation = uow.operations.get(
            operation_id,
            workspace_id=workspace_id,
            for_update=True,
        )
        if operation is None:
            raise NotFoundError(f"operation {operation_id} was not found")
        return operation

    @staticmethod
    def _retain_late_execution_identity(
        uow: OperationUnitOfWorkPort,
        operation: DurableOperation,
        *,
        provider_request_id: str | None,
        expected_execution_version: int | None,
        expected_attempt_count: int | None,
        now: datetime,
    ) -> bool:
        if expected_execution_version is None and expected_attempt_count is None:
            return False
        if expected_execution_version is None or expected_attempt_count is None:
            raise ValueError("execution version and attempt count must be supplied together")
        if operation.version == expected_execution_version:
            return False
        changed = operation.record_late_execution_provider_identity(
            provider_request_id=provider_request_id,
            expected_execution_version=expected_execution_version,
            expected_attempt_count=expected_attempt_count,
            now=now,
        )
        if changed:
            uow.operations.save(operation)
            uow.commit()
        return True

    @staticmethod
    def _retain_late_reconciliation_identity(
        uow: OperationUnitOfWorkPort,
        operation: DurableOperation,
        *,
        provider_request_id: str | None,
        expected_reconciliation_version: int | None,
        expected_reconciliation_attempt_count: int | None,
        now: datetime,
    ) -> bool:
        if (
            expected_reconciliation_version is None
            and expected_reconciliation_attempt_count is None
        ):
            return False
        if expected_reconciliation_version is None or expected_reconciliation_attempt_count is None:
            raise ValueError("reconciliation version and attempt count must be supplied together")
        if operation.version == expected_reconciliation_version:
            return False
        changed = operation.record_late_reconciliation_provider_identity(
            provider_request_id=provider_request_id,
            expected_reconciliation_version=expected_reconciliation_version,
            expected_reconciliation_attempt_count=(expected_reconciliation_attempt_count),
            now=now,
        )
        if changed:
            uow.operations.save(operation)
            uow.commit()
        return True


def record_terminal_operation_failure(
    uow: OperationUnitOfWorkPort,
    operation: DurableOperation,
    *,
    now: datetime,
) -> None:
    if operation.state != OperationState.FAILED or operation.dead_letter_id is not None:
        return
    reason = (
        OperationRecoveryReason.RECONCILIATION_PENDING
        if (
            operation.reconciliation_required
            and operation.reconciliation_outcome == ReconciliationOutcome.PENDING
        )
        else OperationRecoveryReason.READY_RETRY
    )
    payload = OperationRecoveryRequestedPayload(
        operation_id=operation.id,
        workspace_id=operation.workspace_id,
        operation_kind=operation.kind,
        recovery_reason=reason,
    )
    event = OutboxEvent(
        envelope=EventEnvelope.create(
            event_type=EventType.OPERATION_RECOVERY_REQUESTED.value,
            aggregate_type="durable_operation",
            aggregate_id=operation.id,
            aggregate_version=operation.version,
            trace_id=f"operation-terminal:{operation.id}",
            payload=payload.model_dump(mode="json"),
            now=now,
        ),
        available_at=now,
        published_at=now,
        workspace_id=operation.workspace_id,
        source_dead_letter_id=operation.replay_source_dead_letter_id,
        replay_attempt=operation.replay_attempt,
    )
    error = operation.error or NormalizedOperationError(
        code="OPERATION_FAILED",
        category="operation",
        message="operation reached a terminal failed state",
        retryable=False,
    )
    dead_letter = DeadLetterMessage.create(
        consumer="durable-operation-worker",
        message_id=event.envelope.event_id,
        event_type=event.envelope.event_type,
        payload=event.envelope.payload,
        reason="operation_terminal_failure",
        error_class=error.code,
        error_message=error.message,
        attempt_count=operation.attempt_count + operation.reconciliation_attempt_count,
        original_created_at=event.envelope.occurred_at,
        workspace_id=operation.workspace_id,
        source_dead_letter_id=operation.replay_source_dead_letter_id,
        replay_attempt=operation.replay_attempt,
        now=now,
    )
    uow.outbox.add(event)
    uow.dead_letters.add(dead_letter)
    uow.flush()
    operation.mark_dead_lettered(dead_letter.id, now=now)


def _execution_deadline_error() -> NormalizedOperationError:
    return NormalizedOperationError(
        code="OPERATION_MAXIMUM_ELAPSED",
        category="recovery",
        message="operation execution did not start within the maximum elapsed budget",
        retryable=False,
    )


def _reconciliation_deadline_error() -> NormalizedOperationError:
    return NormalizedOperationError(
        code="RECONCILIATION_EXHAUSTED",
        category="recovery",
        message="external outcome could not be confirmed within the reconciliation budget",
        retryable=False,
    )


@dataclass(frozen=True, slots=True)
class OperationExecutionRequest:
    operation_id: str
    workspace_id: str
    kind: OperationKind
    target_type: str
    target_id: str
    target_version: int
    input_hash: str
    input_ref: str | None
    provider_request_id: str | None
    attempt_count: int
    idempotency_key: str

    @classmethod
    def from_operation(
        cls,
        operation: DurableOperation,
    ) -> OperationExecutionRequest:
        return cls(
            operation_id=operation.id,
            workspace_id=operation.workspace_id,
            kind=operation.kind,
            target_type=operation.target_type,
            target_id=operation.target_id,
            target_version=operation.target_version,
            input_hash=operation.input_hash,
            input_ref=operation.input_ref,
            provider_request_id=operation.provider_request_id,
            attempt_count=operation.attempt_count,
            idempotency_key=f"durable-operation:{operation.id}",
        )


@dataclass(frozen=True, slots=True)
class OperationExecutionResult:
    operation_id: str
    output_ref: str | None
    provider_request_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider_request_id",
            normalize_provider_request_id(self.provider_request_id),
        )


@dataclass(frozen=True, slots=True)
class OperationReconciliationResult:
    operation_id: str
    outcome: ReconciliationOutcome
    output_ref: str | None = None
    provider_request_id: str | None = None
    error: NormalizedOperationError | None = None
    retry_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider_request_id",
            normalize_provider_request_id(self.provider_request_id),
        )


class OperationExecutionFailure(Exception):
    def __init__(
        self,
        error: NormalizedOperationError,
        *,
        retry_at: datetime | None = None,
    ) -> None:
        super().__init__(error.message)
        self.error = error
        self.retry_at = retry_at


class UnknownOperationOutcome(Exception):
    def __init__(self, error: NormalizedOperationError) -> None:
        super().__init__(error.message)
        self.error = error


def _jittered_backoff_seconds(
    *,
    attempt_count: int,
    initial_delay: timedelta,
    maximum_delay: timedelta,
    jitter: Callable[[float, float], float],
    invalid_jitter_message: str,
) -> float:
    exponent = max(attempt_count - 1, 0)
    base_seconds = min(
        initial_delay.total_seconds() * (2**exponent),
        maximum_delay.total_seconds(),
    )
    lower = min(base_seconds * 0.5, maximum_delay.total_seconds())
    upper = min(base_seconds * 1.5, maximum_delay.total_seconds())
    delay_seconds = jitter(lower, upper)
    if not lower <= delay_seconds <= upper:
        raise ValueError(invalid_jitter_message)
    return delay_seconds


class OperationRetryPolicy:
    def __init__(
        self,
        *,
        initial_delay: timedelta,
        maximum_delay: timedelta,
        maximum_elapsed: timedelta,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        if initial_delay <= timedelta(0):
            raise ValueError("operation retry initial delay must be positive")
        if maximum_delay < initial_delay:
            raise ValueError("operation retry maximum delay must not be below the initial delay")
        if maximum_elapsed <= timedelta(0):
            raise ValueError("operation retry maximum elapsed time must be positive")
        self._initial_delay = initial_delay
        self._maximum_delay = maximum_delay
        self._maximum_elapsed = maximum_elapsed
        self._jitter = jitter

    def deadline_for_replay(self, now: datetime) -> datetime:
        return now + self._maximum_elapsed

    def decide(
        self,
        *,
        operation: DurableOperation,
        failure: OperationExecutionFailure,
        now: datetime,
    ) -> tuple[NormalizedOperationError, datetime | None]:
        error = failure.error
        if not error.retryable:
            return error, None
        retry_at = failure.retry_at
        if retry_at is None:
            delay_seconds = _jittered_backoff_seconds(
                attempt_count=operation.attempt_count,
                initial_delay=self._initial_delay,
                maximum_delay=self._maximum_delay,
                jitter=self._jitter,
                invalid_jitter_message=(
                    "operation retry jitter returned a value outside its bounds"
                ),
            )
            retry_at = now + timedelta(seconds=delay_seconds)
        else:
            if retry_at.tzinfo is None or retry_at.utcoffset() != timedelta(0):
                raise ValueError("provider retry_at must be timezone-aware UTC")
            retry_at = min(
                max(retry_at, now),
                now + self._maximum_delay,
            )
        if retry_at >= operation.execution_deadline_at:
            return replace(error, retryable=False), None
        return error, retry_at


class OperationReconciliationPolicy:
    def __init__(
        self,
        *,
        initial_delay: timedelta,
        maximum_delay: timedelta,
        maximum_elapsed: timedelta,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        if initial_delay <= timedelta(0):
            raise ValueError("reconciliation retry initial delay must be positive")
        if maximum_delay < initial_delay:
            raise ValueError(
                "reconciliation retry maximum delay must not be below the initial delay"
            )
        if maximum_elapsed <= timedelta(0):
            raise ValueError("reconciliation maximum elapsed time must be positive")
        self._initial_delay = initial_delay
        self._maximum_delay = maximum_delay
        self._maximum_elapsed = maximum_elapsed
        self._jitter = jitter

    def deadline_for(self, now: datetime) -> datetime:
        return now + self._maximum_elapsed

    def decide(
        self,
        *,
        operation: DurableOperation,
        failure: OperationExecutionFailure,
        now: datetime,
    ) -> tuple[NormalizedOperationError, datetime | None]:
        error = failure.error
        if (
            not error.retryable
            or operation.reconciliation_attempt_count >= operation.max_reconciliation_attempts
            or (
                operation.reconciliation_deadline_at is not None
                and operation.reconciliation_deadline_at <= now
            )
        ):
            return replace(error, retryable=False), None

        retry_at = failure.retry_at
        if retry_at is not None and (
            retry_at.tzinfo is None or retry_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("provider reconciliation retry_at must be timezone-aware UTC")
        if retry_at is None or retry_at <= now:
            retry_at = self._backoff_at(operation=operation, now=now)
        else:
            retry_at = min(max(retry_at, now), now + self._maximum_delay)

        deadline = operation.reconciliation_deadline_at
        if deadline is None:
            started_at = operation.reconciliation_started_at or now
            deadline = started_at + self._maximum_elapsed
        if retry_at >= deadline:
            return replace(error, retryable=False), None
        return error, retry_at

    def _backoff_at(
        self,
        *,
        operation: DurableOperation,
        now: datetime,
    ) -> datetime:
        delay_seconds = _jittered_backoff_seconds(
            attempt_count=operation.reconciliation_attempt_count,
            initial_delay=self._initial_delay,
            maximum_delay=self._maximum_delay,
            jitter=self._jitter,
            invalid_jitter_message=(
                "reconciliation retry jitter returned a value outside its bounds"
            ),
        )
        return max(
            now + timedelta(microseconds=1),
            now + timedelta(seconds=delay_seconds),
        )


class OperationExecutor(Protocol):
    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult: ...
    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult: ...


class OperationExecutorRegistry:
    def __init__(self) -> None:
        self._executors: dict[OperationKind, OperationExecutor] = {}

    def register(self, *, kind: OperationKind, executor: OperationExecutor) -> None:
        if kind in self._executors:
            raise ValueError(f"operation executor for {kind.value} is already registered")
        self._executors[kind] = executor

    @property
    def registered_kinds(self) -> frozenset[OperationKind]:
        return frozenset(self._executors)

    def missing(
        self,
        required_kinds: Iterable[OperationKind],
    ) -> frozenset[OperationKind]:
        return frozenset(required_kinds).difference(self._executors)

    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        return self._resolve(request.kind).execute(request)

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        return self._resolve(request.kind).reconcile(request)

    def _resolve(self, kind: OperationKind) -> OperationExecutor:
        executor = self._executors.get(kind)
        if executor is None:
            raise OperationExecutionFailure(
                NormalizedOperationError(
                    code="OPERATION_EXECUTOR_UNAVAILABLE",
                    category="configuration",
                    message=f"no executor is registered for {kind.value}",
                    retryable=False,
                )
            )
        return executor


class OperationExecutionBoundary:
    def __init__(
        self,
        *,
        executor: OperationExecutor,
        transaction_active: Callable[[], bool],
    ) -> None:
        self._executor = executor
        self._transaction_active = transaction_active

    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        self._assert_transaction_free()
        return self._executor.execute(request)

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        self._assert_transaction_free()
        return self._executor.reconcile(request)

    def _assert_transaction_free(self) -> None:
        if self._transaction_active():
            raise RuntimeError(
                "external operation execution is forbidden while a unit of work "
                "has an active transaction"
            )


class DurableOperationWorker:
    def __init__(
        self,
        *,
        operations: OperationApplicationService,
        execution: OperationExecutionBoundary,
        owner: str,
        lease_duration: timedelta,
        retry_policy: OperationRetryPolicy | None = None,
        reconciliation_policy: OperationReconciliationPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._operations = operations
        self._execution = execution
        self._owner = owner
        self._lease_duration = lease_duration
        self._retry_policy = retry_policy or OperationRetryPolicy(
            initial_delay=timedelta(seconds=1),
            maximum_delay=timedelta(minutes=5),
            maximum_elapsed=timedelta(hours=24),
        )
        self._reconciliation_policy = reconciliation_policy or OperationReconciliationPolicy(
            initial_delay=timedelta(seconds=2),
            maximum_delay=timedelta(minutes=5),
            maximum_elapsed=timedelta(hours=24),
        )
        self._clock = clock or (lambda: datetime.now(UTC))

    def execute(self, *, workspace_id: str, operation_id: str) -> DurableOperation:
        operation = self._operations.get(
            workspace_id=workspace_id,
            operation_id=operation_id,
        )
        claim_at = self._clock()
        try:
            if operation.state == OperationState.PENDING:
                lease_token = self._operations.claim(
                    workspace_id=workspace_id,
                    operation_id=operation_id,
                    owner=self._owner,
                    lease_duration=self._lease_duration,
                    now=claim_at,
                )
            elif operation.state == OperationState.RETRYABLE_FAILED:
                lease_token = self._operations.retry(
                    workspace_id=workspace_id,
                    operation_id=operation_id,
                    owner=self._owner,
                    lease_duration=self._lease_duration,
                    now=claim_at,
                )
            elif operation.state == OperationState.RECONCILING:
                return self._reconcile(operation)
            elif operation.state.terminal:
                return operation
            else:
                raise InvalidTransitionError(
                    f"operation {operation.id} cannot execute from {operation.state.value}"
                )
        except RetryExhaustedError:
            return self._operations.get(
                workspace_id=workspace_id,
                operation_id=operation_id,
            )
        running = self._operations.start(
            workspace_id=workspace_id,
            operation_id=operation_id,
            lease_token=lease_token,
            now=claim_at,
        )
        return self._execute_claimed(running, lease_token)

    def _execute_claimed(
        self,
        running: DurableOperation,
        lease_token: str,
    ) -> DurableOperation:
        try:
            result = self._execution.execute(OperationExecutionRequest.from_operation(running))
        except UnknownOperationOutcome as exc:
            required_at = self._clock()
            return self._operations.require_reconciliation(
                workspace_id=running.workspace_id,
                operation_id=running.id,
                lease_token=lease_token,
                error=exc.error,
                expected_execution_version=running.version,
                expected_attempt_count=running.attempt_count,
                reconciliation_deadline_at=self._reconciliation_policy.deadline_for(required_at),
                now=required_at,
            )
        except OperationExecutionFailure as exc:
            failed_at = self._clock()
            error, retry_at = self._retry_policy.decide(
                operation=running,
                failure=exc,
                now=failed_at,
            )
            return self._operations.fail(
                workspace_id=running.workspace_id,
                operation_id=running.id,
                lease_token=lease_token,
                error=error,
                retry_at=retry_at,
                expected_execution_version=running.version,
                expected_attempt_count=running.attempt_count,
                now=failed_at,
            )
        if result.operation_id != running.id:
            required_at = self._clock()
            return self._operations.require_reconciliation(
                workspace_id=running.workspace_id,
                operation_id=running.id,
                lease_token=lease_token,
                error=self._result_mismatch_error(),
                provider_request_id=result.provider_request_id,
                expected_execution_version=running.version,
                expected_attempt_count=running.attempt_count,
                reconciliation_deadline_at=self._reconciliation_policy.deadline_for(required_at),
                now=required_at,
            )
        return self._operations.succeed(
            workspace_id=running.workspace_id,
            operation_id=running.id,
            lease_token=lease_token,
            output_ref=result.output_ref,
            provider_request_id=result.provider_request_id,
            expected_execution_version=running.version,
            expected_attempt_count=running.attempt_count,
            now=self._clock(),
        )

    def handle_recovery_event(self, event: OutboxEvent) -> DurableOperation:
        payload = OPERATION_RECOVERY_REQUESTED_V1.validate_payload(event.envelope.payload)
        if not isinstance(payload, OperationRecoveryRequestedPayload):
            raise TypeError("operation recovery contract returned an unexpected payload")
        operation = self._operations.get(
            workspace_id=payload.workspace_id,
            operation_id=payload.operation_id,
        )
        if event.source_dead_letter_id is None:
            operation = self.execute(
                workspace_id=operation.workspace_id,
                operation_id=operation.id,
            )
        else:
            replayed_at = self._clock()
            reconcile_only = (
                payload.recovery_reason == OperationRecoveryReason.RECONCILIATION_PENDING
            )
            operation, _, should_handle_replay = self._operations.apply_recovery_replay(
                workspace_id=payload.workspace_id,
                operation_id=payload.operation_id,
                source_dead_letter_id=event.source_dead_letter_id,
                replay_attempt=event.replay_attempt,
                replay_event_id=event.envelope.event_id,
                recovery_generation=payload.recovery_generation,
                reconcile_only=reconcile_only,
                execution_deadline_at=self._retry_policy.deadline_for_replay(replayed_at),
                reconciliation_deadline_at=self._reconciliation_policy.deadline_for(replayed_at),
                now=replayed_at,
            )
            if should_handle_replay:
                claim = self._operations.claim_recovery_replay(
                    workspace_id=payload.workspace_id,
                    operation_id=payload.operation_id,
                    source_dead_letter_id=event.source_dead_letter_id,
                    replay_attempt=event.replay_attempt,
                    replay_event_id=event.envelope.event_id,
                    recovery_generation=payload.recovery_generation,
                    reconcile_only=reconcile_only,
                    owner=self._owner,
                    lease_duration=self._lease_duration,
                    now=self._clock(),
                )
                operation = claim.operation
                if claim.provider_claimed:
                    assert claim.lease_token is not None
                    if claim.work_kind == ReplayWorkKind.RECONCILIATION:
                        operation = self._reconcile_claimed(operation, claim.lease_token)
                    else:
                        operation = self._execute_claimed(operation, claim.lease_token)
                    operation = self._operations.complete_recovery_replay(
                        workspace_id=payload.workspace_id,
                        operation_id=payload.operation_id,
                        source_dead_letter_id=event.source_dead_letter_id,
                        replay_attempt=event.replay_attempt,
                        replay_event_id=event.envelope.event_id,
                        claim_token=claim.lease_token,
                        now=self._clock(),
                    )
        return self._operations.consume_recovery_generation(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
            generation=payload.recovery_generation,
            now=self._clock(),
        )

    def _reconcile(self, operation: DurableOperation) -> DurableOperation:
        reconciliation_at = self._clock()
        if operation.reconciliation_exhausted(now=reconciliation_at):
            return self._operations.exhaust_reconciliation(
                workspace_id=operation.workspace_id,
                operation_id=operation.id,
                error=self._reconciliation_exhausted_error(),
                now=reconciliation_at,
            )
        try:
            lease_token = self._operations.claim_reconciliation(
                workspace_id=operation.workspace_id,
                operation_id=operation.id,
                owner=self._owner,
                lease_duration=self._lease_duration,
                now=reconciliation_at,
            )
        except RetryExhaustedError:
            return self._operations.get(
                workspace_id=operation.workspace_id,
                operation_id=operation.id,
            )
        current = self._operations.get(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
        )
        return self._reconcile_claimed(current, lease_token)

    def _reconcile_claimed(
        self,
        current: DurableOperation,
        lease_token: str,
    ) -> DurableOperation:
        try:
            result = self._execution.reconcile(OperationExecutionRequest.from_operation(current))
        except UnknownOperationOutcome as exc:
            return self._defer_reconciliation(
                operation=current,
                lease_token=lease_token,
                failure=OperationExecutionFailure(
                    replace(exc.error, retryable=True),
                ),
            )
        except OperationExecutionFailure as exc:
            return self._defer_reconciliation(
                operation=current,
                lease_token=lease_token,
                failure=OperationExecutionFailure(
                    replace(exc.error, retryable=True),
                    retry_at=exc.retry_at,
                ),
            )
        if result.operation_id != current.id:
            return self._defer_reconciliation(
                operation=current,
                lease_token=lease_token,
                failure=OperationExecutionFailure(self._result_mismatch_error()),
                provider_request_id=result.provider_request_id,
            )
        if result.outcome in {
            ReconciliationOutcome.PENDING,
            ReconciliationOutcome.NOT_FOUND,
        }:
            error = result.error or NormalizedOperationError(
                code=f"RECONCILIATION_{result.outcome.value}",
                category="provider",
                message="provider outcome remains uncertain",
                retryable=True,
            )
            return self._defer_reconciliation(
                operation=current,
                lease_token=lease_token,
                failure=OperationExecutionFailure(
                    replace(error, retryable=True),
                    retry_at=result.retry_at,
                ),
                provider_request_id=result.provider_request_id,
            )
        if result.outcome == ReconciliationOutcome.CONFIRMED_FAILURE:
            if result.error is None:
                raise ValueError("confirmed reconciliation failure requires an error")
            resolved_at = self._clock()
            error, retry_at = self._retry_policy.decide(
                operation=current,
                failure=OperationExecutionFailure(
                    result.error,
                    retry_at=result.retry_at,
                ),
                now=resolved_at,
            )
            return self._operations.resolve_reconciliation(
                workspace_id=current.workspace_id,
                operation_id=current.id,
                lease_token=lease_token,
                outcome=result.outcome,
                error=error,
                retry_at=retry_at,
                provider_request_id=result.provider_request_id,
                expected_reconciliation_version=current.version,
                expected_reconciliation_attempt_count=(current.reconciliation_attempt_count),
                now=resolved_at,
            )
        resolved_at = self._clock()
        return self._operations.resolve_reconciliation(
            workspace_id=current.workspace_id,
            operation_id=current.id,
            lease_token=lease_token,
            outcome=result.outcome,
            error=result.error,
            retry_at=result.retry_at,
            output_ref=result.output_ref,
            provider_request_id=result.provider_request_id,
            expected_reconciliation_version=current.version,
            expected_reconciliation_attempt_count=current.reconciliation_attempt_count,
            now=resolved_at,
        )

    def _defer_reconciliation(
        self,
        *,
        operation: DurableOperation,
        lease_token: str,
        failure: OperationExecutionFailure,
        provider_request_id: str | None = None,
    ) -> DurableOperation:
        deferred_at = self._clock()
        error, next_reconciliation_at = self._reconciliation_policy.decide(
            operation=operation,
            failure=failure,
            now=deferred_at,
        )
        return self._operations.defer_reconciliation(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
            lease_token=lease_token,
            error=error,
            provider_request_id=provider_request_id,
            next_reconciliation_at=next_reconciliation_at,
            expected_reconciliation_version=operation.version,
            expected_reconciliation_attempt_count=(operation.reconciliation_attempt_count),
            now=deferred_at,
        )

    @staticmethod
    def _result_mismatch_error() -> NormalizedOperationError:
        return NormalizedOperationError(
            code="OPERATION_RESULT_MISMATCH",
            category="provider",
            message="operation executor returned a result for a different operation",
            retryable=True,
        )

    @staticmethod
    def _reconciliation_exhausted_error() -> NormalizedOperationError:
        return NormalizedOperationError(
            code="RECONCILIATION_EXHAUSTED",
            category="recovery",
            message="external outcome could not be confirmed within the reconciliation budget",
            retryable=False,
        )
