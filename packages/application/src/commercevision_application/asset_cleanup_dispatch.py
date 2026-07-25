"""Atomic scheduling for durable cleanup of terminal uploads."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from commercevision_contracts.events import (
    AssetDeleteRequestedPayload,
    EventType,
    UploadCleanupReason,
)
from commercevision_domain import (
    DurableOperation,
    OperationKind,
    UploadSession,
    UploadSessionState,
)
from commercevision_domain.messaging import EventEnvelope, OutboxEvent

from .asset_idempotency import canonical_hash
from .asset_ports import AssetUnitOfWorkPort


@dataclass(frozen=True, slots=True)
class UploadCleanupPolicy:
    max_attempts: int
    max_reconciliation_attempts: int
    execution_max_elapsed: timedelta
    presign_replay_grace: timedelta
    reconciliation_horizon: timedelta

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("upload cleanup max_attempts must be positive")
        if self.max_reconciliation_attempts < 1:
            raise ValueError("upload cleanup reconciliation attempts must be positive")
        if self.execution_max_elapsed <= timedelta(0):
            raise ValueError("upload cleanup execution budget must be positive")
        if self.presign_replay_grace <= timedelta(0):
            raise ValueError("presigned upload cleanup grace must be positive")
        if self.reconciliation_horizon <= timedelta(0):
            raise ValueError("upload cleanup reconciliation horizon must be positive")


def upload_cleanup_input_hash(
    upload_session: UploadSession,
    *,
    reconcile_until: datetime | None = None,
) -> str:
    cleanup_reconcile_until = reconcile_until or upload_session.cleanup_reconcile_until
    if cleanup_reconcile_until is None:
        raise ValueError("cleanup reconciliation window is not attached")
    return canonical_hash(
        {
            "cleanup_contract": "upload-session-cleanup-v1",
            "workspace_id": upload_session.workspace_id,
            "upload_session_id": upload_session.id,
            "expected_byte_length": upload_session.expected_byte_length,
            "expected_sha256": upload_session.expected_sha256,
            "storage_backend": upload_session.storage_backend.value,
            "source_location": upload_session.storage_location.value,
            "destination_location": upload_session.destination_location.value,
            "cleanup_reconcile_until": cleanup_reconcile_until.isoformat(),
        }
    )


def schedule_upload_cleanup(
    *,
    uow: AssetUnitOfWorkPort,
    upload_session: UploadSession,
    trace_id: str,
    policy: UploadCleanupPolicy,
    now: datetime,
) -> DurableOperation | None:
    """Persist one cleanup Operation, ownership pointer, and command atomically."""

    if upload_session.cleanup_operation_id is not None:
        return None
    reason = _cleanup_reason(upload_session)

    available_at = max(now, upload_session.expires_at + policy.presign_replay_grace)
    reconcile_until = available_at + policy.reconciliation_horizon
    target_version = upload_session.version + 1
    operation = DurableOperation.create(
        workspace_id=upload_session.workspace_id,
        kind=OperationKind.ASSET_DELETION,
        target_type="UPLOAD_SESSION",
        target_id=upload_session.id,
        target_version=target_version,
        input_hash=upload_cleanup_input_hash(
            upload_session,
            reconcile_until=reconcile_until,
        ),
        input_ref=f"mysql://upload-sessions/{upload_session.id}",
        max_attempts=policy.max_attempts,
        max_reconciliation_attempts=policy.max_reconciliation_attempts,
        execution_max_elapsed=(available_at - now) + policy.execution_max_elapsed,
        now=now,
    )
    upload_session.schedule_cleanup(
        operation_id=operation.id,
        reconcile_until=reconcile_until,
        now=now,
    )
    if upload_session.version != target_version:
        raise RuntimeError("cleanup Operation target version does not match its Upload Session")

    payload = AssetDeleteRequestedPayload(
        operation_id=operation.id,
        workspace_id=operation.workspace_id,
        target_type="UPLOAD_SESSION",
        target_id=upload_session.id,
        target_version=upload_session.version,
        reason=reason,
    )
    event = OutboxEvent(
        envelope=EventEnvelope.create(
            event_type=EventType.ASSET_DELETE_REQUESTED.value,
            aggregate_type="DurableOperation",
            aggregate_id=operation.id,
            aggregate_version=operation.version,
            trace_id=trace_id,
            payload=payload.model_dump(mode="json"),
            now=now,
        ),
        available_at=available_at,
        workspace_id=operation.workspace_id,
    )
    uow.operations.add(operation)
    uow.upload_sessions.save(upload_session)
    uow.outbox.add(event)
    return operation


def schedule_abandoned_upload_cleanup(
    *,
    uow: AssetUnitOfWorkPort,
    upload_session: UploadSession,
    trace_id: str,
    policy: UploadCleanupPolicy,
    now: datetime,
) -> bool:
    """Schedule cleanup only when an upload ended without a registered Asset."""

    if upload_session.state not in {
        UploadSessionState.EXPIRED,
        UploadSessionState.ABORTED,
    }:
        return False
    return (
        schedule_upload_cleanup(
            uow=uow,
            upload_session=upload_session,
            trace_id=trace_id,
            policy=policy,
            now=now,
        )
        is not None
    )


def _cleanup_reason(upload_session: UploadSession) -> UploadCleanupReason:
    if upload_session.state == UploadSessionState.EXPIRED:
        return UploadCleanupReason.UPLOAD_EXPIRED
    if upload_session.state == UploadSessionState.ABORTED:
        return UploadCleanupReason.UPLOAD_ABORTED
    if upload_session.state == UploadSessionState.FINALIZED:
        return UploadCleanupReason.UPLOAD_PROMOTED
    raise ValueError("cleanup can only be scheduled for a terminal upload")
