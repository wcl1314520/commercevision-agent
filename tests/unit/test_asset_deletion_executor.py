from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from commercevision_application import (
    OperationExecutionFailure,
    OperationExecutionRequest,
)
from commercevision_application.asset_deletion import AssetDeletionConvergenceResult
from commercevision_domain import OperationKind, ReconciliationOutcome
from commercevision_worker.asset_deletion import AssetDeletionExecutor

NOW = datetime(2026, 8, 4, 2, 0, tzinfo=UTC)


class RecordingCoordinator:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.failure: Exception | None = None

    def converge(self, request: OperationExecutionRequest) -> AssetDeletionConvergenceResult:
        self.calls.append(request.operation_id)
        if self.failure is not None:
            raise self.failure
        return AssetDeletionConvergenceResult(
            output_ref=f"mysql://assets/{request.target_id}/deletions/{request.target_version}",
        )


def _request() -> OperationExecutionRequest:
    return OperationExecutionRequest(
        operation_id="019c2a90-54b0-7000-8000-000000000001",
        workspace_id="asset-delete-executor",
        kind=OperationKind.ASSET_DELETION,
        target_type="ASSET",
        target_id="019c2a90-54b0-7000-8000-000000000002",
        target_version=1,
        input_hash="a" * 64,
        input_ref=("mysql://assets/019c2a90-54b0-7000-8000-000000000002/deletions/1"),
        provider_request_id=None,
        attempt_count=1,
        idempotency_key="durable-operation:asset-delete",
    )


def test_asset_deletion_executor_rejects_stale_or_malformed_target_identity() -> None:
    coordinator = RecordingCoordinator()
    executor = AssetDeletionExecutor(coordinator=coordinator)

    for request in (
        replace(_request(), target_type="UPLOAD_SESSION"),
        replace(_request(), target_version=2),
        replace(_request(), input_ref="mysql://assets/other/deletions/1"),
    ):
        with pytest.raises(OperationExecutionFailure) as failure:
            executor.execute(request)
        assert failure.value.error.code == "ASSET_DELETION_TARGET_MISMATCH"

    assert coordinator.calls == []


def test_asset_deletion_executor_normalizes_retryable_dependency_outage() -> None:
    coordinator = RecordingCoordinator()
    coordinator.failure = ConnectionError("object storage unavailable")
    executor = AssetDeletionExecutor(coordinator=coordinator)

    with pytest.raises(OperationExecutionFailure) as failure:
        executor.execute(_request())

    assert failure.value.error.code == "ASSET_DELETION_DEPENDENCY_UNAVAILABLE"
    assert failure.value.error.retryable is True


def test_asset_deletion_executor_is_idempotent_across_execute_and_reconcile() -> None:
    coordinator = RecordingCoordinator()
    executor = AssetDeletionExecutor(coordinator=coordinator)

    completed = executor.execute(_request())
    reconciled = executor.reconcile(_request())

    assert completed.output_ref.endswith("/deletions/1")
    assert reconciled.outcome == ReconciliationOutcome.CONFIRMED_SUCCESS
    assert reconciled.output_ref == completed.output_ref
    assert coordinator.calls == [_request().operation_id, _request().operation_id]
