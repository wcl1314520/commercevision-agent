"""Bounded MySQL recovery scanner for generic Durable Operations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from commercevision_contracts.events import (
    EventType,
    OperationRecoveryReason,
    OperationRecoveryRequestedPayload,
)
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from commercevision_domain.operations import (
    DurableOperation,
    NormalizedOperationError,
    OperationState,
)

from .operation_ports import OperationUnitOfWorkFactory
from .operations import record_terminal_operation_failure


class OperationRecoveryService:
    def __init__(
        self,
        *,
        uow_factory: OperationUnitOfWorkFactory,
        batch_size: int,
        reconciliation_max_elapsed: timedelta = timedelta(hours=24),
    ) -> None:
        if batch_size < 1:
            raise ValueError("operation recovery batch_size must be positive")
        if reconciliation_max_elapsed <= timedelta(0):
            raise ValueError("reconciliation maximum elapsed time must be positive")
        self._uow_factory = uow_factory
        self._batch_size = batch_size
        self._reconciliation_max_elapsed = reconciliation_max_elapsed

    def recover_once(self, *, now: datetime | None = None) -> int:
        scanned_at = now or datetime.now(UTC)
        emitted = 0
        with self._uow_factory() as uow:
            operations = uow.operations.claim_recoverable(
                now=scanned_at,
                limit=self._batch_size,
                pending_event_type=EventType.OPERATION_RECOVERY_REQUESTED.value,
            )
            for operation in operations:
                original_version = operation.version
                expired_claim_token = (
                    operation.lease_token
                    if (
                        operation.lease_token is not None
                        and operation.lease_expires_at is not None
                        and operation.lease_expires_at <= scanned_at
                    )
                    else None
                )
                reason = self._prepare_recovery(
                    operation,
                    now=scanned_at,
                    reconciliation_max_elapsed=self._reconciliation_max_elapsed,
                )
                record_terminal_operation_failure(uow, operation, now=scanned_at)
                if (
                    reason is not None
                    and operation.state
                    in {
                        OperationState.RETRYABLE_FAILED,
                        OperationState.RECONCILING,
                    }
                    and operation.recovery_generation == operation.recovery_consumed_generation
                    and not uow.outbox.has_unpublished(
                        aggregate_id=operation.id,
                        event_type=EventType.OPERATION_RECOVERY_REQUESTED.value,
                    )
                ):
                    generation = operation.reserve_recovery_generation(now=scanned_at)
                    uow.outbox.add(
                        self._event(
                            operation,
                            reason=reason,
                            generation=generation,
                            now=scanned_at,
                        )
                    )
                    emitted += 1
                if operation.version != original_version:
                    uow.operations.save(operation)
                if expired_claim_token is not None:
                    uow.dead_letters.complete_claimed_replays(
                        operation_id=operation.id,
                        claim_token=expired_claim_token,
                        completed_operation_version=operation.version,
                        completed_at=scanned_at,
                    )
            uow.commit()
        return emitted

    @staticmethod
    def _prepare_recovery(
        operation: DurableOperation,
        *,
        now: datetime,
        reconciliation_max_elapsed: timedelta,
    ) -> OperationRecoveryReason | None:
        if operation.state == OperationState.CLAIMED:
            operation.recover_expired_lease(
                retry_at=now,
                reconciliation_deadline_at=now + reconciliation_max_elapsed,
                now=now,
            )
            return (
                OperationRecoveryReason.EXPIRED_CLAIM
                if operation.state == OperationState.RETRYABLE_FAILED
                else None
            )
        if operation.state == OperationState.RUNNING:
            operation.recover_expired_lease(
                retry_at=now,
                reconciliation_deadline_at=now + reconciliation_max_elapsed,
                now=now,
            )
            return OperationRecoveryReason.UNKNOWN_EXTERNAL_OUTCOME
        if operation.state == OperationState.RETRYABLE_FAILED:
            return OperationRecoveryReason.READY_RETRY
        if operation.state == OperationState.RECONCILING:
            if operation.reconciliation_exhausted(now=now):
                operation.exhaust_reconciliation(
                    error=operation.error or _reconciliation_exhausted_error(),
                    now=now,
                )
                return None
            return OperationRecoveryReason.RECONCILIATION_PENDING
        return None

    @staticmethod
    def _event(
        operation: DurableOperation,
        *,
        reason: OperationRecoveryReason,
        generation: int,
        now: datetime,
    ) -> OutboxEvent:
        payload = OperationRecoveryRequestedPayload(
            operation_id=operation.id,
            workspace_id=operation.workspace_id,
            operation_kind=operation.kind,
            recovery_reason=reason,
            recovery_generation=generation,
        )
        return OutboxEvent(
            envelope=EventEnvelope.create(
                event_type=EventType.OPERATION_RECOVERY_REQUESTED.value,
                aggregate_type="durable_operation",
                aggregate_id=operation.id,
                aggregate_version=operation.version,
                trace_id=f"operation-recovery:{operation.id}",
                payload=payload.model_dump(mode="json"),
                now=now,
            ),
            available_at=now,
            workspace_id=operation.workspace_id,
        )


def _reconciliation_exhausted_error() -> NormalizedOperationError:
    return NormalizedOperationError(
        code="RECONCILIATION_EXHAUSTED",
        category="recovery",
        message="external outcome could not be confirmed within the reconciliation budget",
        retryable=False,
    )
