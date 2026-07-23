from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from commercevision_application import OperationApplicationService, OperationCreateCommand
from commercevision_domain import NormalizedOperationError, OperationKind
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from commercevision_persistence import (
    SqlAlchemyOperationUnitOfWork,
    SqlAlchemyUnitOfWork,
)
from commercevision_scheduler.runtime import SchedulerRuntime

pytestmark = pytest.mark.integration


class NoopDispatcher:
    def dispatch_once(self) -> tuple[int, int]:
        return 0, 0


class BlockingRollbackRecovery:
    def __init__(self, runtime: SchedulerRuntime) -> None:
        self._runtime = runtime
        self.started = Event()
        self.release = Event()

    def recover_once(self) -> tuple[int, int]:
        marker = OutboxEvent(
            envelope=EventEnvelope.create(
                event_type="integration.scheduler-rollback-marker",
                aggregate_type="scheduler_test",
                aggregate_id="scheduler-rollback-marker",
                aggregate_version=1,
                trace_id="scheduler-rollback-marker",
                payload={"workspace_id": "workspace-scheduler"},
                now=datetime.now(UTC),
            ),
            available_at=datetime.now(UTC),
            workspace_id="workspace-scheduler",
        )
        with SqlAlchemyUnitOfWork(self._runtime.database.session_factory) as uow:
            uow.outbox.add(marker)
            uow.session.flush()
            self.started.set()
            if not self.release.wait(timeout=5):
                raise TimeoutError("test did not release blocked scanner")
            raise RuntimeError("workflow recovery transaction failed")


def test_production_scheduler_scanners_isolate_real_transactions_and_rollback(
    integration_settings,
    integration_database,
) -> None:
    settings = integration_settings.model_copy(
        update={
            "service_name": "scheduler-isolation-integration",
            "scheduler_poll_seconds": 60.0,
            "scheduler_recovery_interval_seconds": 60.0,
            "scheduler_operation_recovery_interval_seconds": 60.0,
            "scheduler_scanner_timeout_seconds": 4.0,
            "scheduler_batch_size": 1,
        }
    )
    runtime = SchedulerRuntime(settings)
    assert runtime.database.engine.url.database == integration_database.engine.url.database
    blocking_recovery = BlockingRollbackRecovery(runtime)
    runtime.dispatcher = NoopDispatcher()
    runtime.recovery = blocking_recovery
    operations = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(runtime.database.session_factory)
    )
    operation = operations.create(
        OperationCreateCommand(
            workspace_id="workspace-scheduler",
            kind=OperationKind.ASSET_INDEXING,
            target_type="asset_version",
            target_id="scheduler-isolation-operation",
            target_version=1,
            input_hash="8" * 64,
            input_ref=None,
            max_attempts=3,
        )
    )
    claimed_at = datetime.now(UTC)
    token = operations.claim(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        owner="scheduler-isolation-worker",
        lease_duration=timedelta(seconds=30),
        now=claimed_at,
    )
    operations.start(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        lease_token=token,
        now=claimed_at,
    )
    operations.fail(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        lease_token=token,
        error=NormalizedOperationError(
            code="TRANSIENT_PROVIDER_FAILURE",
            category="provider",
            message="provider is temporarily unavailable",
            retryable=True,
        ),
        retry_at=claimed_at + timedelta(microseconds=2),
        now=claimed_at + timedelta(microseconds=1),
    )

    try:
        runtime.orchestrator.run_due()
        if not blocking_recovery.started.wait(timeout=1):
            runtime.orchestrator.run_due()
            raise AssertionError(f"workflow scanner did not start: {runtime.orchestrator.statuses}")

        deadline = time.monotonic() + 2
        recovery_events: list[OutboxEvent] = []
        while time.monotonic() < deadline:
            runtime.orchestrator.run_due()
            with SqlAlchemyUnitOfWork(runtime.database.session_factory) as uow:
                recovery_events = uow.outbox.list_for_aggregate(operation.id)
            if recovery_events:
                break
            time.sleep(0.01)

        assert len(recovery_events) == 1
        assert runtime.state.recovered_operations_total == 1
        assert runtime.orchestrator.statuses["workflow_recovery"].in_progress is True

        blocking_recovery.release.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            runtime.orchestrator.run_due()
            if not runtime.orchestrator.statuses["workflow_recovery"].in_progress:
                break
            time.sleep(0.01)

        with SqlAlchemyUnitOfWork(runtime.database.session_factory) as uow:
            rollback_markers = uow.outbox.list_for_aggregate("scheduler-rollback-marker")
        assert rollback_markers == []
        assert runtime.orchestrator.statuses["workflow_recovery"].last_error == (
            "RuntimeError: workflow recovery transaction failed"
        )
        assert runtime.orchestrator.statuses["operation_recovery"].last_error is None
        assert runtime.orchestrator.statuses["operation_recovery"].last_count == 1
    finally:
        blocking_recovery.release.set()
        runtime.close()
