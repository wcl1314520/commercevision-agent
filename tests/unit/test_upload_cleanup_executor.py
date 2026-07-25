from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from commercevision_application import (
    OperationExecutionRequest,
    OperationReconciliationRequired,
)
from commercevision_application.asset_cleanup_dispatch import upload_cleanup_input_hash
from commercevision_domain import (
    AssetKind,
    OperationKind,
    ReconciliationOutcome,
    RetentionClass,
    StorageBackend,
    StorageLocationClass,
    UploadSession,
    new_uuid7,
)
from commercevision_worker.asset_cleanup import UploadSessionCleanupExecutor

NOW = datetime(2026, 7, 26, 1, 0, tzinfo=UTC)
RECONCILE_UNTIL = NOW + timedelta(hours=3)


class UploadSessionRepository:
    def __init__(self, upload_session: UploadSession) -> None:
        self._upload_session = upload_session

    def get(self, **_kwargs: object) -> UploadSession:
        return self._upload_session


class AssetUnitOfWork:
    def __init__(self, upload_session: UploadSession) -> None:
        self.upload_sessions = UploadSessionRepository(upload_session)

    def __enter__(self) -> AssetUnitOfWork:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class RecordingCleaner:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.object_present = False
        self.deleted_count = 0

    def cleanup(self, upload_session: UploadSession) -> None:
        self.calls.append(upload_session.id)
        if self.object_present:
            self.object_present = False
            self.deleted_count += 1


def _session_and_request() -> tuple[UploadSession, OperationExecutionRequest]:
    asset_version_id = new_uuid7()
    upload_session = UploadSession.create(
        workspace_id="cleanup-executor",
        actor_id="cleanup-user",
        reserved_asset_id=new_uuid7(),
        reserved_asset_version_id=asset_version_id,
        retention_class=RetentionClass.FOUNDATION,
        asset_kind=AssetKind.IMAGE,
        filename="pixel.png",
        declared_mime="image/png",
        expected_byte_length=68,
        expected_sha256="a" * 64,
        workflow_id=None,
        product_id=None,
        sku_id=None,
        category="beauty",
        role="primary",
        upload_policy_version="direct-put-v1",
        integrity_policy_version="image-integrity-v1",
        storage_backend=StorageBackend.MINIO,
        storage_bucket="quarantine",
        storage_key=f"quarantine/{new_uuid7()}",
        destination_location=StorageLocationClass.FOUNDATION,
        destination_bucket="foundation",
        destination_key=f"foundation/{asset_version_id}",
        expires_at=NOW + timedelta(minutes=15),
        now=NOW,
    )
    upload_session.abort(expected_version=1, now=NOW + timedelta(seconds=1))
    operation_id = new_uuid7()
    upload_session.schedule_cleanup(
        operation_id=operation_id,
        reconcile_until=RECONCILE_UNTIL,
        now=NOW + timedelta(seconds=2),
    )
    return upload_session, OperationExecutionRequest(
        operation_id=operation_id,
        workspace_id=upload_session.workspace_id,
        kind=OperationKind.ASSET_DELETION,
        target_type="UPLOAD_SESSION",
        target_id=upload_session.id,
        target_version=upload_session.version,
        input_hash=upload_cleanup_input_hash(upload_session),
        input_ref=f"mysql://upload-sessions/{upload_session.id}",
        provider_request_id=None,
        attempt_count=1,
        idempotency_key=f"durable-operation:{operation_id}",
    )


def test_cleanup_stays_in_reconciliation_until_a_final_boundary_pass() -> None:
    upload_session, request = _session_and_request()
    cleaner = RecordingCleaner()
    clock_value = [NOW + timedelta(hours=1)]
    executor = UploadSessionCleanupExecutor(
        uow_factory=lambda: AssetUnitOfWork(upload_session),
        cleaner=cleaner,  # type: ignore[arg-type]
        reconciliation_interval=timedelta(hours=1),
        final_cleanup_budget=timedelta(hours=1),
        clock=lambda: clock_value[0],
    )

    with pytest.raises(OperationReconciliationRequired) as required:
        executor.execute(request)
    assert required.value.deadline_at == RECONCILE_UNTIL + timedelta(hours=1)

    # A PUT accepted before URL expiry becomes visible after the first cleanup pass.
    cleaner.object_present = True
    pending = executor.reconcile(request)
    assert pending.outcome == ReconciliationOutcome.PENDING
    assert pending.retry_at == NOW + timedelta(hours=2)
    assert cleaner.object_present is False
    assert cleaner.deleted_count == 1

    clock_value[0] = RECONCILE_UNTIL
    completed = executor.reconcile(request)
    assert completed.outcome == ReconciliationOutcome.CONFIRMED_SUCCESS
    assert cleaner.calls == [upload_session.id, upload_session.id, upload_session.id]


def test_cleanup_reconciliation_reports_a_definitive_fact_mismatch() -> None:
    upload_session, request = _session_and_request()
    request = OperationExecutionRequest(
        operation_id=request.operation_id,
        workspace_id=request.workspace_id,
        kind=request.kind,
        target_type=request.target_type,
        target_id=request.target_id,
        target_version=request.target_version + 1,
        input_hash=request.input_hash,
        input_ref=request.input_ref,
        provider_request_id=request.provider_request_id,
        attempt_count=request.attempt_count,
        idempotency_key=request.idempotency_key,
    )
    executor = UploadSessionCleanupExecutor(
        uow_factory=lambda: AssetUnitOfWork(upload_session),
        cleaner=RecordingCleaner(),  # type: ignore[arg-type]
        reconciliation_interval=timedelta(hours=1),
        final_cleanup_budget=timedelta(hours=1),
        clock=lambda: NOW + timedelta(hours=1),
    )

    result = executor.reconcile(request)

    assert result.outcome == ReconciliationOutcome.CONFIRMED_FAILURE
    assert result.error is not None
    assert result.error.code == "UPLOAD_CLEANUP_FACT_MISMATCH"
