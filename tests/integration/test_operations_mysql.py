from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event

import pytest
from commercevision_application import (
    AuthenticatedPrincipal,
    DeadLetterOperatorService,
    DurableOperationWorker,
    OperationApplicationService,
    OperationCreateCommand,
    OperationExecutionBoundary,
    OperationExecutionFailure,
    OperationExecutionRequest,
    OperationExecutionResult,
    OperationReconciliationPolicy,
    OperationReconciliationResult,
    OperationRecoveryService,
    OperationRetryPolicy,
    UnknownOperationOutcome,
)
from commercevision_contracts.events import (
    EventType,
    OperationRecoveryReason,
    OperationRecoveryRequestedPayload,
)
from commercevision_domain import (
    InvalidTransitionError,
    NormalizedOperationError,
    OperationKind,
    OperationState,
    ReconciliationOutcome,
    RetryExhaustedError,
    RetryNotReadyError,
)
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from commercevision_persistence import (
    SqlAlchemyOperationUnitOfWork,
    SqlAlchemyOperatorUnitOfWork,
    SqlAlchemyUnitOfWork,
    is_unit_of_work_active,
)
from commercevision_worker.runtime import WorkerRuntime

pytestmark = pytest.mark.integration


class AllowWorkspaceAdminPolicy:
    def require_admin(
        self,
        *,
        workspace_id: str,
        principal: AuthenticatedPrincipal,
    ) -> None:
        assert workspace_id in principal.admin_workspace_ids


def test_concurrent_operation_creation_returns_one_logical_operation(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    command = OperationCreateCommand(
        workspace_id="workspace-operation-race",
        kind=OperationKind.ASSET_INDEXING,
        target_type="asset_version",
        target_id="asset-version-1",
        target_version=4,
        input_hash="b" * 64,
        input_ref="mysql://asset-versions/asset-version-1",
        max_attempts=3,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        operations = list(pool.map(lambda _: service.create(command), range(2)))

    assert len({operation.id for operation in operations}) == 1
    assert operations[0].logical_key == operations[1].logical_key


def test_terminal_operation_is_reused_for_same_logical_identity(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    command = OperationCreateCommand(
        workspace_id="workspace-operation-terminal",
        kind=OperationKind.COLLECTION_REBUILD,
        target_type="collection",
        target_id="collection-terminal",
        target_version=1,
        input_hash="2" * 64,
        input_ref=None,
        max_attempts=3,
    )
    operation = service.create(command)
    cancelled = service.cancel(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        expected_version=operation.version,
    )

    repeated = service.create(command)

    assert repeated.id == cancelled.id
    assert repeated.state == OperationState.CANCELLED


def test_concurrent_operation_claim_allows_one_lease_owner(integration_database) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-operation-claim",
            kind=OperationKind.ASSET_VALIDATION,
            target_type="asset_version",
            target_id="asset-version-claim",
            target_version=1,
            input_hash="c" * 64,
            input_ref=None,
            max_attempts=3,
        )
    )
    barrier = Barrier(2)

    def claim(owner: str) -> str:
        barrier.wait()
        return service.claim(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
            owner=owner,
            lease_duration=timedelta(seconds=30),
            now=datetime(2026, 7, 23, 9, 0, tzinfo=UTC),
        )

    successes: list[str] = []
    failures: list[Exception] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim, owner) for owner in ("worker-a", "worker-b")]
        for future in futures:
            try:
                successes.append(future.result())
            except Exception as exc:
                failures.append(exc)

    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], InvalidTransitionError)


class RecordingExecutor:
    def __init__(self) -> None:
        self.transaction_states: list[bool] = []

    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        self.transaction_states.append(is_unit_of_work_active())
        return OperationExecutionResult(
            operation_id=request.operation_id,
            output_ref=f"mysql://operation-results/{request.operation_id}",
        )

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        self.transaction_states.append(is_unit_of_work_active())
        return OperationReconciliationResult(
            operation_id=request.operation_id,
            outcome=ReconciliationOutcome.CONFIRMED_SUCCESS,
            output_ref=f"mysql://operation-results/{request.operation_id}",
        )


class SuccessfulProviderIdentityExecutor(RecordingExecutor):
    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        self.transaction_states.append(is_unit_of_work_active())
        return OperationExecutionResult(
            operation_id=request.operation_id,
            output_ref=f"mysql://operation-results/{request.operation_id}",
            provider_request_id=f"  provider-success-{request.operation_id}  ",
        )


class BranchCompleteProviderIdentityExecutor(RecordingExecutor):
    def __init__(self, branch: str) -> None:
        super().__init__()
        self.branch = branch
        self.execute_calls = 0
        self.reconcile_calls = 0

    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        self.execute_calls += 1
        self.transaction_states.append(is_unit_of_work_active())
        if self.branch == "execution_success":
            return self._execution_result(request.operation_id)
        if self.branch == "execution_mismatch":
            return self._execution_result("different-operation")
        raise UnknownOperationOutcome(
            NormalizedOperationError(
                code="PROVIDER_TIMEOUT_UNKNOWN",
                category="provider",
                message="provider outcome is unknown",
                retryable=True,
            )
        )

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        self.reconcile_calls += 1
        self.transaction_states.append(is_unit_of_work_active())
        operation_id = (
            "different-operation"
            if self.branch == "reconciliation_mismatch"
            else request.operation_id
        )
        outcome = {
            "reconciliation_mismatch": ReconciliationOutcome.PENDING,
            "reconciliation_pending": ReconciliationOutcome.PENDING,
            "reconciliation_not_found": ReconciliationOutcome.NOT_FOUND,
            "reconciliation_failure": ReconciliationOutcome.CONFIRMED_FAILURE,
            "reconciliation_success": ReconciliationOutcome.CONFIRMED_SUCCESS,
        }[self.branch]
        error = (
            NormalizedOperationError(
                code=f"PROVIDER_{outcome.value}",
                category="provider",
                message=f"provider returned {outcome.value}",
                retryable=outcome != ReconciliationOutcome.CONFIRMED_FAILURE,
                provider_request_id=f"latest-error-{self.branch}",
            )
            if outcome != ReconciliationOutcome.CONFIRMED_SUCCESS
            else None
        )
        return OperationReconciliationResult(
            operation_id=operation_id,
            outcome=outcome,
            output_ref=(
                f"mysql://operation-results/{request.operation_id}"
                if outcome == ReconciliationOutcome.CONFIRMED_SUCCESS
                else None
            ),
            provider_request_id=f"  result-{self.branch}  ",
            error=error,
        )

    def _execution_result(self, operation_id: str) -> OperationExecutionResult:
        return OperationExecutionResult(
            operation_id=operation_id,
            output_ref=f"mysql://operation-results/{operation_id}",
            provider_request_id=f"  result-{self.branch}  ",
        )


class UnexpectedExecutionCrashExecutor:
    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        raise RuntimeError(f"worker crashed while executing {request.operation_id}")

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        raise AssertionError(f"crashed executor must not reconcile {request.operation_id}")


def operation_recovery_event(
    operation,
    *,
    reason: OperationRecoveryReason,
    now: datetime,
    source_dead_letter_id: str | None = None,
    replay_attempt: int = 0,
) -> OutboxEvent:
    payload = OperationRecoveryRequestedPayload(
        operation_id=operation.id,
        workspace_id=operation.workspace_id,
        operation_kind=operation.kind,
        recovery_reason=reason,
    )
    return OutboxEvent(
        envelope=EventEnvelope.create(
            event_type=EventType.OPERATION_RECOVERY_REQUESTED.value,
            aggregate_type="durable_operation",
            aggregate_id=operation.id,
            aggregate_version=operation.version,
            trace_id=f"operation-test:{operation.id}",
            payload=payload.model_dump(mode="json"),
            now=now,
        ),
        available_at=now,
        workspace_id=operation.workspace_id,
        source_dead_letter_id=source_dead_letter_id,
        replay_attempt=replay_attempt,
    )


def test_stale_delivery_at_execution_deadline_dead_letters_before_external_work(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-stale-operation-delivery",
            kind=OperationKind.ASSET_VALIDATION,
            target_type="asset_version",
            target_id="asset-version-stale-delivery",
            target_version=1,
            input_hash="4" * 64,
            input_ref=None,
            max_attempts=3,
        )
    )
    deadline = operation.created_at + timedelta(days=1)
    clock = MutableClock(deadline)
    executor = RecordingExecutor()
    worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-stale-delivery",
        lease_duration=timedelta(seconds=30),
        retry_policy=OperationRetryPolicy(
            initial_delay=timedelta(seconds=1),
            maximum_delay=timedelta(minutes=5),
            maximum_elapsed=timedelta(days=1),
        ),
        clock=clock,
    )
    event = operation_recovery_event(
        operation,
        reason=OperationRecoveryReason.READY_RETRY,
        now=deadline,
    )

    failed = worker.handle_recovery_event(event)

    assert failed.state == OperationState.FAILED
    assert failed.execution_deadline_at == deadline
    assert failed.dead_letter_id is not None
    assert failed.error is not None
    assert failed.error.code == "OPERATION_MAXIMUM_ELAPSED"
    assert failed.attempt_count == 0
    assert executor.transaction_states == []


def test_execution_claim_is_ready_one_microsecond_before_elapsed_deadline(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-operation-deadline-before",
            kind=OperationKind.ASSET_VALIDATION,
            target_type="asset_version",
            target_id="asset-version-deadline-before",
            target_version=1,
            input_hash="3" * 64,
            input_ref=None,
            max_attempts=1,
        )
    )
    executor = RecordingExecutor()
    worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-deadline-before",
        lease_duration=timedelta(seconds=30),
        retry_policy=OperationRetryPolicy(
            initial_delay=timedelta(seconds=1),
            maximum_delay=timedelta(minutes=5),
            maximum_elapsed=timedelta(days=1),
        ),
        clock=MutableClock(operation.created_at + timedelta(days=1, microseconds=-1)),
    )

    succeeded = worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )

    assert succeeded.state == OperationState.SUCCEEDED
    assert succeeded.execution_deadline_at == operation.created_at + timedelta(days=1)
    assert executor.transaction_states == [False]


def test_reconciliation_claim_at_deadline_atomically_dead_letters(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-reconciliation-claim-deadline",
            kind=OperationKind.RECONCILIATION,
            target_type="provider_request",
            target_id="provider-request-claim-deadline",
            target_version=1,
            input_hash="2" * 64,
            input_ref=None,
            max_attempts=2,
        )
    )
    worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=UnknownOutcomeExecutor(),
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-reconciliation-claim-deadline",
        lease_duration=timedelta(seconds=30),
        reconciliation_policy=OperationReconciliationPolicy(
            initial_delay=timedelta(seconds=1),
            maximum_delay=timedelta(minutes=1),
            maximum_elapsed=timedelta(hours=1),
        ),
    )
    uncertain = worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )
    assert uncertain.reconciliation_deadline_at is not None

    with pytest.raises(RetryExhaustedError):
        service.claim_reconciliation(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
            owner="worker-reconciliation-after-outage",
            lease_duration=timedelta(seconds=30),
            now=uncertain.reconciliation_deadline_at,
        )

    failed = service.get(workspace_id=operation.workspace_id, operation_id=operation.id)
    assert failed.state == OperationState.FAILED
    assert failed.dead_letter_id is not None
    assert failed.error is not None
    assert failed.error.code == "RECONCILIATION_EXHAUSTED"


@pytest.mark.parametrize("kind", list(OperationKind))
def test_worker_executes_every_durable_operation_kind(
    integration_database,
    kind: OperationKind,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id=f"workspace-kind-{kind.value.lower()}",
            kind=kind,
            target_type="proof_target",
            target_id=f"target-{kind.value.lower()}",
            target_version=1,
            input_hash=f"{list(OperationKind).index(kind):064x}",
            input_ref=None,
            max_attempts=3,
        )
    )
    executor = RecordingExecutor()
    worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-kind-proof",
        lease_duration=timedelta(seconds=30),
    )

    result = worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )

    assert result.state == OperationState.SUCCEEDED
    assert executor.transaction_states == [False]


def test_worker_runtime_registers_required_operation_executor(
    integration_settings,
) -> None:
    settings = integration_settings.model_copy(
        update={"worker_required_operation_kinds": [OperationKind.ASSET_VALIDATION]}
    )
    runtime = WorkerRuntime.build(
        settings,
        operation_executors={
            OperationKind.ASSET_VALIDATION: RecordingExecutor(),
        },
    )
    try:
        assert runtime.operation_executor_readiness() == {
            "ready": True,
            "required_kinds": ["ASSET_VALIDATION"],
            "registered_kinds": ["ASSET_VALIDATION"],
            "missing_kinds": [],
        }
    finally:
        runtime.close()


def test_worker_executes_external_boundary_without_active_transaction(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-operation-worker",
            kind=OperationKind.ASSET_VALIDATION,
            target_type="asset_version",
            target_id="asset-version-worker",
            target_version=1,
            input_hash="d" * 64,
            input_ref="mysql://asset-versions/asset-version-worker",
            max_attempts=3,
        )
    )
    executor = RecordingExecutor()
    boundary = OperationExecutionBoundary(
        executor=executor,
        transaction_active=is_unit_of_work_active,
    )
    worker = DurableOperationWorker(
        operations=service,
        execution=boundary,
        owner="worker-a",
        lease_duration=timedelta(seconds=30),
    )

    worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )

    restored = service.get(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )
    assert executor.transaction_states == [False]
    assert restored.state == OperationState.SUCCEEDED

    with (
        SqlAlchemyOperationUnitOfWork(integration_database.session_factory),
        pytest.raises(RuntimeError, match="active transaction"),
    ):
        boundary.execute(OperationExecutionRequest.from_operation(restored))


def test_recovery_moves_expired_running_operation_to_reconciliation_at_boundary(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-operation-recovery",
            kind=OperationKind.PRODUCT_BRIEF_ANALYSIS,
            target_type="product",
            target_id="product-1",
            target_version=3,
            input_hash="e" * 64,
            input_ref="mysql://products/product-1",
            max_attempts=3,
        )
    )
    claimed_at = datetime(2026, 7, 23, 10, 0, 0, 123000, tzinfo=UTC)
    expires_at = claimed_at + timedelta(microseconds=400)
    token = service.claim(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        owner="worker-a",
        lease_duration=timedelta(microseconds=400),
        now=claimed_at,
    )
    service.start(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        lease_token=token,
        now=claimed_at,
    )
    recovery = OperationRecoveryService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory),
        batch_size=10,
    )

    assert recovery.recover_once(now=expires_at - timedelta(microseconds=1)) == 0
    assert recovery.recover_once(now=expires_at) == 1
    assert recovery.recover_once(now=expires_at) == 0

    restored = service.get(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )
    assert restored.state == OperationState.RECONCILING

    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        recovery_events = [
            event
            for event in uow.outbox.list_for_aggregate(operation.id)
            if event.envelope.event_type == EventType.OPERATION_RECOVERY_REQUESTED
        ]
    assert len(recovery_events) == 1
    assert recovery_events[0].workspace_id == operation.workspace_id
    assert (
        recovery_events[0].envelope.payload["recovery_reason"]
        == OperationRecoveryReason.UNKNOWN_EXTERNAL_OUTCOME
    )


def test_concurrent_recovery_scanners_emit_one_unpublished_event(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-operation-recovery-race",
            kind=OperationKind.ASSET_DELETION,
            target_type="asset",
            target_id="asset-1",
            target_version=2,
            input_hash="f" * 64,
            input_ref=None,
            max_attempts=3,
        )
    )
    claimed_at = datetime(2026, 7, 23, 10, 30, tzinfo=UTC)
    service.claim(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        owner="worker-a",
        lease_duration=timedelta(microseconds=400),
        now=claimed_at,
    )
    scan_at = claimed_at + timedelta(microseconds=400)

    def scan() -> int:
        return OperationRecoveryService(
            uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory),
            batch_size=1,
        ).recover_once(now=scan_at)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(lambda _: scan(), range(2))) == 1

    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        events = [
            event
            for event in uow.outbox.list_for_aggregate(operation.id)
            if event.envelope.event_type == EventType.OPERATION_RECOVERY_REQUESTED
        ]
    assert len(events) == 1


def test_pending_oldest_recovery_event_does_not_starve_newer_backlog(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    claimed_at = datetime.now(UTC) - timedelta(seconds=1)
    operations = []
    for index in range(3):
        operation = service.create(
            OperationCreateCommand(
                workspace_id="workspace-operation-backlog",
                kind=OperationKind.ASSET_DELETION,
                target_type="asset",
                target_id=f"asset-backlog-{index}",
                target_version=1,
                input_hash=f"{index + 10:064x}",
                input_ref=None,
                max_attempts=3,
            )
        )
        service.claim(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
            owner="worker-backlog",
            lease_duration=timedelta(microseconds=1),
            now=claimed_at + timedelta(microseconds=index),
        )
        operations.append(operation)
    recovery = OperationRecoveryService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory),
        batch_size=1,
    )
    scan_at = datetime.now(UTC)

    assert recovery.recover_once(now=scan_at) == 1
    assert recovery.recover_once(now=scan_at) == 1

    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        event_operation_ids = {
            event.envelope.aggregate_id
            for operation in operations
            for event in uow.outbox.list_for_aggregate(operation.id)
            if event.envelope.event_type == EventType.OPERATION_RECOVERY_REQUESTED
        }
    assert len(event_operation_ids) == 2


def test_published_unconsumed_recovery_generation_does_not_starve_backlog(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    claimed_at = datetime.now(UTC) - timedelta(seconds=1)
    operations = []
    for index in range(3):
        operation = service.create(
            OperationCreateCommand(
                workspace_id="workspace-operation-published-backlog",
                kind=OperationKind.ASSET_DELETION,
                target_type="asset",
                target_id=f"asset-published-backlog-{index}",
                target_version=1,
                input_hash=f"{index + 40:064x}",
                input_ref=None,
                max_attempts=3,
            )
        )
        service.claim(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
            owner="worker-published-backlog",
            lease_duration=timedelta(microseconds=1),
            now=claimed_at + timedelta(microseconds=index),
        )
        operations.append(operation)

    recovery = OperationRecoveryService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory),
        batch_size=1,
    )
    scan_at = datetime.now(UTC)
    assert recovery.recover_once(now=scan_at) == 1

    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        claimed_events = uow.outbox.claim_ready(
            now=scan_at + timedelta(seconds=1),
            owner="publisher-published-backlog",
            lease_duration=timedelta(seconds=30),
            limit=10,
        )
        uow.commit()
    assert len(claimed_events) == 1
    published_event = claimed_events[0]
    assert published_event.lock_token is not None
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        uow.outbox.mark_published(
            published_event.envelope.event_id,
            published_event.lock_token,
            now=scan_at + timedelta(seconds=1),
        )
        uow.commit()

    assert recovery.recover_once(now=scan_at + timedelta(seconds=2)) == 1
    assert recovery.recover_once(now=scan_at + timedelta(seconds=3)) == 1
    assert recovery.recover_once(now=scan_at + timedelta(seconds=4)) == 0

    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        recovery_events = [
            event
            for operation in operations
            for event in uow.outbox.list_for_aggregate(operation.id)
            if event.envelope.event_type == EventType.OPERATION_RECOVERY_REQUESTED
        ]
    assert {event.envelope.aggregate_id for event in recovery_events} == {
        operation.id for operation in operations
    }
    assert (
        sum(
            event.envelope.aggregate_id == published_event.envelope.aggregate_id
            for event in recovery_events
        )
        == 1
    )
    assert published_event.envelope.payload["recovery_generation"] == 1

    consumer = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=RecordingExecutor(),
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-published-backlog-consumer",
        lease_duration=timedelta(seconds=30),
    )
    consumed = consumer.handle_recovery_event(published_event)
    reloaded = service.get(
        workspace_id=consumed.workspace_id,
        operation_id=consumed.id,
    )
    assert reloaded.recovery_generation == 1
    assert reloaded.recovery_consumed_generation == 1


def test_failed_recovery_delivery_keeps_generation_until_reconciliation_succeeds(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    claimed_at = datetime.now(UTC)
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-operation-recovery-redelivery",
            kind=OperationKind.ASSET_INDEXING,
            target_type="asset_version",
            target_id="asset-version-recovery-redelivery",
            target_version=1,
            input_hash="6" * 64,
            input_ref=None,
            max_attempts=3,
        )
    )
    service.claim(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        owner="worker-before-recovery-redelivery",
        lease_duration=timedelta(microseconds=1),
        now=claimed_at,
    )
    recovery = OperationRecoveryService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory),
        batch_size=1,
    )
    first_scan_at = claimed_at + timedelta(microseconds=1)
    assert recovery.recover_once(now=first_scan_at) == 1

    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        events = uow.outbox.claim_ready(
            now=first_scan_at,
            owner="publisher-recovery-redelivery",
            lease_duration=timedelta(seconds=30),
            limit=1,
        )
        uow.commit()
    assert len(events) == 1
    event = events[0]
    assert event.lock_token is not None
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        uow.outbox.mark_published(
            event.envelope.event_id,
            event.lock_token,
            now=first_scan_at,
        )
        uow.commit()
    clock = MutableClock(first_scan_at + timedelta(seconds=1))
    crashing_worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=UnexpectedExecutionCrashExecutor(),
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-recovery-redelivery-crash",
        lease_duration=timedelta(seconds=1),
        clock=clock,
    )

    with pytest.raises(RuntimeError, match="worker crashed"):
        crashing_worker.handle_recovery_event(event)

    running = service.get(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )
    assert running.state == OperationState.RUNNING
    assert running.recovery_generation == 1
    assert running.recovery_consumed_generation == 0

    clock.now += timedelta(seconds=1)
    assert recovery.recover_once(now=clock.now) == 0
    reconciling = service.get(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )
    assert reconciling.state == OperationState.RECONCILING
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        recovery_events = [
            candidate
            for candidate in uow.outbox.list_for_aggregate(operation.id)
            if candidate.envelope.event_type == EventType.OPERATION_RECOVERY_REQUESTED
        ]
    assert len(recovery_events) == 1

    successful_worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=RecordingExecutor(),
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-recovery-redelivery-success",
        lease_duration=timedelta(seconds=1),
        clock=clock,
    )
    succeeded = successful_worker.handle_recovery_event(event)

    assert succeeded.state == OperationState.SUCCEEDED
    assert succeeded.recovery_generation == 1
    assert succeeded.recovery_consumed_generation == 1


def test_recovery_selection_uses_skip_locked_under_forced_row_overlap(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    claimed_at = datetime.now(UTC) - timedelta(seconds=1)
    operations = []
    for index in range(2):
        operation = service.create(
            OperationCreateCommand(
                workspace_id="workspace-operation-skip-locked",
                kind=OperationKind.COLLECTION_REBUILD,
                target_type="collection",
                target_id=f"collection-lock-{index}",
                target_version=1,
                input_hash=f"{index + 20:064x}",
                input_ref=None,
                max_attempts=3,
            )
        )
        service.claim(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
            owner="worker-lock",
            lease_duration=timedelta(microseconds=1),
            now=claimed_at + timedelta(microseconds=index),
        )
        operations.append(operation)
    scan_at = datetime.now(UTC)

    with SqlAlchemyOperationUnitOfWork(integration_database.session_factory) as locked_uow:
        locked = locked_uow.operations.get(
            operations[0].id,
            for_update=True,
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            emitted = pool.submit(
                OperationRecoveryService(
                    uow_factory=lambda: SqlAlchemyOperationUnitOfWork(
                        integration_database.session_factory
                    ),
                    batch_size=1,
                ).recover_once,
                now=scan_at,
            ).result(timeout=5)

        assert emitted == 1
        assert locked is not None

    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        emitted_ids = {
            event.envelope.aggregate_id
            for operation in operations
            for event in uow.outbox.list_for_aggregate(operation.id)
            if event.envelope.event_type == EventType.OPERATION_RECOVERY_REQUESTED
        }
    assert len(emitted_ids) == 1
    assert locked.id not in emitted_ids


def test_retry_becomes_recoverable_at_exact_mysql_microsecond(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-operation-retry-boundary",
            kind=OperationKind.ASSET_VALIDATION,
            target_type="asset_version",
            target_id="asset-version-retry-boundary",
            target_version=1,
            input_hash="3" * 64,
            input_ref=None,
            max_attempts=3,
        )
    )
    failed_at = datetime(2026, 7, 23, 10, 45, 0, 123000, tzinfo=UTC)
    retry_at = failed_at + timedelta(microseconds=400)
    token = service.claim(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        owner="worker-a",
        lease_duration=timedelta(seconds=30),
        now=failed_at,
    )
    service.start(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        lease_token=token,
        now=failed_at,
    )
    service.fail(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        lease_token=token,
        error=NormalizedOperationError(
            code="PROVIDER_TEMPORARY",
            category="provider",
            message="provider is temporarily unavailable",
            retryable=True,
        ),
        retry_at=retry_at,
        now=failed_at,
    )
    recovery = OperationRecoveryService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory),
        batch_size=1,
    )

    assert recovery.recover_once(now=retry_at - timedelta(microseconds=1)) == 0
    assert recovery.recover_once(now=retry_at) == 1


class UnknownOutcomeExecutor(RecordingExecutor):
    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        self.transaction_states.append(is_unit_of_work_active())
        raise UnknownOperationOutcome(
            NormalizedOperationError(
                code="PROVIDER_TIMEOUT_UNKNOWN",
                category="provider",
                message="provider timed out after accepting the request",
                retryable=True,
                provider_request_id=f"provider-{request.operation_id}",
            )
        )


class TransientReconciliationExecutor(RecordingExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.execute_calls = 0
        self.reconcile_calls = 0

    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        self.execute_calls += 1
        raise UnknownOperationOutcome(
            NormalizedOperationError(
                code="PROVIDER_TIMEOUT_UNKNOWN",
                category="provider",
                message="provider outcome is unknown",
                retryable=True,
            )
        )

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        self.reconcile_calls += 1
        raise OperationExecutionFailure(
            NormalizedOperationError(
                code="PROVIDER_QUERY_TEMPORARY",
                category="provider",
                message="provider status query is temporarily unavailable",
                retryable=True,
            )
        )


class ProviderStatusQueryFailureExecutor(RecordingExecutor):
    def __init__(self, *, error_request_id: str) -> None:
        super().__init__()
        self.error_request_id = error_request_id
        self.provider_request_ids: list[str | None] = []

    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        raise AssertionError(f"operation {request.operation_id} must not be re-executed")

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        self.provider_request_ids.append(request.provider_request_id)
        raise OperationExecutionFailure(
            NormalizedOperationError(
                code="PROVIDER_QUERY_TEMPORARY",
                category="provider",
                message="provider status query is temporarily unavailable",
                retryable=True,
                provider_request_id=self.error_request_id,
            )
        )


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class ExpiringSuccessExecutor:
    def __init__(self, *, clock: MutableClock, completed_at: datetime) -> None:
        self.clock = clock
        self.completed_at = completed_at
        self.execute_calls = 0

    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        self.execute_calls += 1
        self.clock.now = self.completed_at
        return OperationExecutionResult(
            operation_id=request.operation_id,
            output_ref=f"mysql://operation-results/{request.operation_id}",
            provider_request_id=f"provider-expiry-{request.operation_id}",
        )

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        raise AssertionError(f"operation {request.operation_id} must not reconcile")


class BlockingLateSuccessExecutor:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.execute_calls = 0

    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        self.execute_calls += 1
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test did not release provider execution")
        return OperationExecutionResult(
            operation_id=request.operation_id,
            output_ref=f"mysql://operation-results/{request.operation_id}",
            provider_request_id=f"provider-late-{request.operation_id}",
        )

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        raise AssertionError(f"stale worker must not reconcile {request.operation_id}")


class RestartedLateResultReconciler:
    def __init__(self) -> None:
        self.execute_calls = 0
        self.reconcile_calls = 0
        self.provider_request_ids: list[str | None] = []

    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        self.execute_calls += 1
        raise AssertionError(f"operation {request.operation_id} must not be re-executed")

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        self.reconcile_calls += 1
        self.provider_request_ids.append(request.provider_request_id)
        return OperationReconciliationResult(
            operation_id=request.operation_id,
            outcome=ReconciliationOutcome.CONFIRMED_SUCCESS,
            output_ref=f"mysql://operation-results/{request.operation_id}",
            provider_request_id=request.provider_request_id,
        )


class StaleRetryReconciliationExecutor:
    def __init__(
        self,
        *,
        mode: str,
        clock: MutableClock,
        retry_offset: timedelta,
    ) -> None:
        self.mode = mode
        self.clock = clock
        self.retry_offset = retry_offset
        self.execute_calls = 0
        self.reconcile_calls = 0

    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        self.execute_calls += 1
        raise UnknownOperationOutcome(
            NormalizedOperationError(
                code="EXTERNAL_OUTCOME_UNKNOWN",
                category="provider",
                message="provider outcome is unknown",
                retryable=True,
                provider_request_id=f"provider-stale-retry-{request.operation_id}",
            )
        )

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        self.reconcile_calls += 1
        error = NormalizedOperationError(
            code=f"PROVIDER_{self.mode.upper()}",
            category="provider",
            message="provider outcome remains uncertain",
            retryable=True,
        )
        retry_at = self.clock.now + self.retry_offset
        if self.mode == "transient":
            raise OperationExecutionFailure(error, retry_at=retry_at)
        outcome = (
            ReconciliationOutcome.PENDING
            if self.mode == "pending"
            else ReconciliationOutcome.NOT_FOUND
        )
        return OperationReconciliationResult(
            operation_id=request.operation_id,
            outcome=outcome,
            provider_request_id=request.provider_request_id,
            error=error,
            retry_at=retry_at,
        )


def test_provider_request_identity_survives_restart_and_query_failures(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-provider-request-persistence",
            kind=OperationKind.RECONCILIATION,
            target_type="provider_request",
            target_id="provider-request-persistence",
            target_version=1,
            input_hash="f" * 64,
            input_ref=None,
            max_attempts=3,
        )
    )
    execution_worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=UnknownOutcomeExecutor(),
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-provider-request-execute",
        lease_duration=timedelta(seconds=30),
    )

    uncertain = execution_worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )

    expected_provider_request_id = f"provider-{operation.id}"
    assert uncertain.provider_request_id == expected_provider_request_id
    reloaded = service.get(workspace_id=operation.workspace_id, operation_id=operation.id)
    assert reloaded.provider_request_id == expected_provider_request_id

    first_clock = MutableClock(datetime.now(UTC) + timedelta(seconds=1))
    first_query_executor = ProviderStatusQueryFailureExecutor(
        error_request_id="status-query-error-1"
    )
    first_query_worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=first_query_executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-provider-request-query-1",
        lease_duration=timedelta(seconds=30),
        reconciliation_policy=OperationReconciliationPolicy(
            initial_delay=timedelta(seconds=1),
            maximum_delay=timedelta(seconds=1),
            maximum_elapsed=timedelta(minutes=5),
            jitter=lambda lower, upper: lower,
        ),
        clock=first_clock,
    )
    deferred = first_query_worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )
    assert deferred.error is not None
    assert deferred.error.provider_request_id == "status-query-error-1"
    assert deferred.provider_request_id == expected_provider_request_id
    assert first_query_executor.provider_request_ids == [expected_provider_request_id]

    assert deferred.next_reconciliation_at is not None
    second_clock = MutableClock(deferred.next_reconciliation_at)
    second_query_executor = ProviderStatusQueryFailureExecutor(
        error_request_id="status-query-error-2"
    )
    restarted_query_worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=second_query_executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-provider-request-query-2",
        lease_duration=timedelta(seconds=30),
        reconciliation_policy=OperationReconciliationPolicy(
            initial_delay=timedelta(seconds=1),
            maximum_delay=timedelta(seconds=1),
            maximum_elapsed=timedelta(minutes=5),
            jitter=lambda lower, upper: lower,
        ),
        clock=second_clock,
    )
    deferred_again = restarted_query_worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )

    assert deferred_again.error is not None
    assert deferred_again.error.provider_request_id == "status-query-error-2"
    assert deferred_again.provider_request_id == expected_provider_request_id
    assert second_query_executor.provider_request_ids == [expected_provider_request_id]


def test_successful_provider_request_identity_round_trips_after_restart(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-provider-success-persistence",
            kind=OperationKind.ASSET_INDEXING,
            target_type="asset_version",
            target_id="asset-version-provider-success",
            target_version=1,
            input_hash="1" * 64,
            input_ref=None,
            max_attempts=3,
        )
    )
    executor = SuccessfulProviderIdentityExecutor()
    worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-provider-success",
        lease_duration=timedelta(seconds=30),
    )

    succeeded = worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )
    restarted_service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    reloaded = restarted_service.get(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )

    expected_request_id = f"provider-success-{operation.id}"
    assert succeeded.state == OperationState.SUCCEEDED
    assert succeeded.provider_request_id == expected_request_id
    assert succeeded.error is None
    assert reloaded.provider_request_id == expected_request_id
    assert reloaded.output_ref == f"mysql://operation-results/{operation.id}"
    assert executor.transaction_states == [False]


@pytest.mark.parametrize("completion_offset", [timedelta(0), timedelta(microseconds=1)])
def test_current_execution_cas_persists_result_at_or_after_exact_lease_expiry(
    integration_database,
    completion_offset: timedelta,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id=f"workspace-exact-expiry-{completion_offset.microseconds}",
            kind=OperationKind.ASSET_VALIDATION,
            target_type="asset_version",
            target_id=f"asset-exact-expiry-{completion_offset.microseconds}",
            target_version=1,
            input_hash=("9" if completion_offset == timedelta(0) else "a") * 64,
            input_ref=None,
            max_attempts=3,
        )
    )
    started_at = operation.created_at + timedelta(microseconds=1)
    lease_duration = timedelta(seconds=30)
    clock = MutableClock(started_at)
    executor = ExpiringSuccessExecutor(
        clock=clock,
        completed_at=started_at + lease_duration + completion_offset,
    )
    worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-exact-expiry",
        lease_duration=lease_duration,
        clock=clock,
    )

    completed = worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )

    assert completed.state == OperationState.SUCCEEDED
    assert completed.provider_request_id == f"provider-expiry-{operation.id}"
    assert completed.completed_at == clock.now
    assert executor.execute_calls == 1


def test_scanner_reclaim_wins_late_result_race_then_restart_reconciles_without_reexecution(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-late-result-reclaim",
            kind=OperationKind.RECONCILIATION,
            target_type="provider_request",
            target_id="late-result-reclaim",
            target_version=1,
            input_hash="c" * 64,
            input_ref=None,
            max_attempts=3,
        )
    )
    started_at = operation.created_at + timedelta(microseconds=1)
    clock = MutableClock(started_at)
    executor = BlockingLateSuccessExecutor()
    worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-late-result",
        lease_duration=timedelta(seconds=30),
        clock=clock,
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            worker.execute,
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
        )
        assert executor.started.wait(timeout=1)
        running = service.get(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
        )
        assert running.lease_expires_at is not None
        reclaimed_at = running.lease_expires_at
        recovery = OperationRecoveryService(
            uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory),
            batch_size=1,
            reconciliation_max_elapsed=timedelta(minutes=5),
        )

        assert recovery.recover_once(now=reclaimed_at) == 1
        clock.now = reclaimed_at + timedelta(microseconds=1)
        executor.release.set()
        late_result = future.result(timeout=2)

    assert late_result.state == OperationState.RECONCILING
    assert late_result.output_ref is None
    assert late_result.provider_request_id == f"provider-late-{operation.id}"
    assert late_result.error is not None
    assert late_result.error.code == "EXTERNAL_OUTCOME_UNKNOWN"
    assert executor.execute_calls == 1

    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        recovery_events = uow.outbox.list_for_aggregate(operation.id)
    assert len(recovery_events) == 1
    restarted_service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    restarted_executor = RestartedLateResultReconciler()
    restarted_worker = DurableOperationWorker(
        operations=restarted_service,
        execution=OperationExecutionBoundary(
            executor=restarted_executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-late-result-restarted",
        lease_duration=timedelta(seconds=30),
        clock=clock,
    )

    reconciled = restarted_worker.handle_recovery_event(recovery_events[0])
    reloaded = restarted_service.get(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )

    assert reconciled.state == OperationState.SUCCEEDED
    assert reloaded.state == OperationState.SUCCEEDED
    assert reloaded.provider_request_id == f"provider-late-{operation.id}"
    assert reloaded.recovery_generation == reloaded.recovery_consumed_generation
    assert restarted_executor.execute_calls == 0
    assert restarted_executor.reconcile_calls == 1
    assert restarted_executor.provider_request_ids == [f"provider-late-{operation.id}"]


@pytest.mark.parametrize("mode", ["pending", "not_found", "transient"])
@pytest.mark.parametrize("retry_offset", [timedelta(microseconds=-1), timedelta(0)])
def test_stale_reconciliation_retry_releases_lease_and_consumes_recovery_generation(
    integration_database,
    mode: str,
    retry_offset: timedelta,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id=f"workspace-stale-reconcile-{mode}-{retry_offset.microseconds}",
            kind=OperationKind.RECONCILIATION,
            target_type="provider_request",
            target_id=f"stale-reconcile-{mode}-{retry_offset.microseconds}",
            target_version=1,
            input_hash={
                ("pending", 999999): "1",
                ("pending", 0): "2",
                ("not_found", 999999): "3",
                ("not_found", 0): "4",
                ("transient", 999999): "5",
                ("transient", 0): "6",
            }[(mode, retry_offset.microseconds)]
            * 64,
            input_ref=None,
            max_attempts=3,
        )
    )
    clock = MutableClock(operation.created_at + timedelta(microseconds=1))
    executor = StaleRetryReconciliationExecutor(
        mode=mode,
        clock=clock,
        retry_offset=retry_offset,
    )
    worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner=f"worker-stale-reconcile-{mode}",
        lease_duration=timedelta(seconds=30),
        reconciliation_policy=OperationReconciliationPolicy(
            initial_delay=timedelta(seconds=2),
            maximum_delay=timedelta(seconds=4),
            maximum_elapsed=timedelta(minutes=5),
            jitter=lambda lower, upper: lower,
        ),
        clock=clock,
    )
    uncertain = worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )
    assert uncertain.state == OperationState.RECONCILING
    recovery = OperationRecoveryService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory),
        batch_size=1,
        reconciliation_max_elapsed=timedelta(minutes=5),
    )
    assert recovery.recover_once(now=clock.now) == 1
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        recovery_events = uow.outbox.list_for_aggregate(operation.id)
    assert len(recovery_events) == 1

    deferred = worker.handle_recovery_event(recovery_events[0])

    assert deferred.state == OperationState.RECONCILING
    assert deferred.lease_token is None
    assert deferred.lease_expires_at is None
    assert deferred.next_reconciliation_at == clock.now + timedelta(seconds=1)
    assert deferred.recovery_generation == deferred.recovery_consumed_generation == 1
    assert deferred.dead_letter_id is None
    assert executor.execute_calls == 1
    assert executor.reconcile_calls == 1
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        assert (
            uow.dead_letters.get(
                consumer="durable-operation-worker",
                message_id=recovery_events[0].envelope.event_id,
            )
            is None
        )
    with pytest.raises(RetryNotReadyError):
        service.claim_reconciliation(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
            owner="reconciler-before-boundary",
            lease_duration=timedelta(seconds=30),
            now=deferred.next_reconciliation_at - timedelta(microseconds=1),
        )
    assert (
        service.claim_reconciliation(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
            owner="reconciler-at-boundary",
            lease_duration=timedelta(seconds=30),
            now=deferred.next_reconciliation_at,
        )
        is not None
    )


def test_worker_runtime_marks_stale_reconciliation_retry_event_processed_without_dlq(
    integration_settings,
    integration_database,
) -> None:
    executor = StaleRetryReconciliationExecutor(
        mode="transient",
        clock=MutableClock(datetime(2000, 1, 1, tzinfo=UTC)),
        retry_offset=timedelta(0),
    )
    settings = integration_settings.model_copy(
        update={
            "service_name": "worker-stale-reconciliation-inbox",
            "worker_required_operation_kinds": [OperationKind.RECONCILIATION],
            "operation_reconciliation_initial_seconds": 2.0,
            "operation_reconciliation_max_seconds": 4.0,
        }
    )
    runtime = WorkerRuntime.build(
        settings,
        operation_executors={OperationKind.RECONCILIATION: executor},
    )
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(runtime.database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-stale-reconciliation-inbox",
            kind=OperationKind.RECONCILIATION,
            target_type="provider_request",
            target_id="stale-reconciliation-inbox",
            target_version=1,
            input_hash="7" * 64,
            input_ref=None,
            max_attempts=3,
        )
    )

    try:
        uncertain = runtime.operation_worker.execute(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
        )
        assert uncertain.state == OperationState.RECONCILING
        scanner = OperationRecoveryService(
            uow_factory=lambda: SqlAlchemyOperationUnitOfWork(runtime.database.session_factory),
            batch_size=1,
            reconciliation_max_elapsed=timedelta(minutes=5),
        )
        assert scanner.recover_once(now=datetime.now(UTC)) == 1
        with SqlAlchemyUnitOfWork(runtime.database.session_factory) as uow:
            events = uow.outbox.list_for_aggregate(operation.id)
        assert len(events) == 1

        assert runtime.process_event(events[0].envelope.event_id) == "processed"
        assert runtime.process_event(events[0].envelope.event_id) == "duplicate"

        reloaded = service.get(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
        )
        assert reloaded.state == OperationState.RECONCILING
        assert reloaded.next_reconciliation_at is not None
        assert reloaded.next_reconciliation_at > reloaded.updated_at
        assert reloaded.recovery_generation == reloaded.recovery_consumed_generation == 1
        with SqlAlchemyUnitOfWork(runtime.database.session_factory) as uow:
            assert (
                uow.dead_letters.get(
                    consumer=settings.worker_consumer_name,
                    message_id=events[0].envelope.event_id,
                )
                is None
            )
    finally:
        runtime.close()


@pytest.mark.parametrize(
    ("branch", "expected_state", "expected_error_request_id"),
    [
        ("execution_success", OperationState.SUCCEEDED, None),
        ("execution_mismatch", OperationState.RECONCILING, None),
        ("reconciliation_mismatch", OperationState.RECONCILING, None),
        (
            "reconciliation_pending",
            OperationState.RECONCILING,
            "latest-error-reconciliation_pending",
        ),
        (
            "reconciliation_not_found",
            OperationState.RECONCILING,
            "latest-error-reconciliation_not_found",
        ),
        (
            "reconciliation_failure",
            OperationState.FAILED,
            "latest-error-reconciliation_failure",
        ),
        ("reconciliation_success", OperationState.SUCCEEDED, None),
    ],
)
def test_every_result_branch_persists_provider_identity_independently(
    integration_database,
    branch: str,
    expected_state: OperationState,
    expected_error_request_id: str | None,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id=f"workspace-result-provenance-{branch}",
            kind=OperationKind.RECONCILIATION,
            target_type="provider_request",
            target_id=f"provider-result-{branch}",
            target_version=1,
            input_hash={
                "execution_success": "1",
                "execution_mismatch": "2",
                "reconciliation_mismatch": "3",
                "reconciliation_pending": "4",
                "reconciliation_not_found": "5",
                "reconciliation_failure": "6",
                "reconciliation_success": "7",
            }[branch]
            * 64,
            input_ref=None,
            max_attempts=3,
        )
    )
    executor = BranchCompleteProviderIdentityExecutor(branch)
    clock = MutableClock(datetime.now(UTC))
    worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner=f"worker-result-provenance-{branch}",
        lease_duration=timedelta(seconds=30),
        reconciliation_policy=OperationReconciliationPolicy(
            initial_delay=timedelta(seconds=1),
            maximum_delay=timedelta(seconds=1),
            maximum_elapsed=timedelta(minutes=5),
            jitter=lambda lower, upper: lower,
        ),
        clock=clock,
    )

    result = worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )
    if branch.startswith("reconciliation_"):
        clock.now += timedelta(microseconds=1)
        result = worker.execute(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
        )

    restarted_service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    reloaded = restarted_service.get(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )

    assert result.state == expected_state
    assert result.provider_request_id == f"result-{branch}"
    assert reloaded.provider_request_id == f"result-{branch}"
    assert (
        reloaded.error.provider_request_id if reloaded.error is not None else None
    ) == expected_error_request_id
    assert executor.execute_calls == 1
    assert executor.reconcile_calls == int(branch.startswith("reconciliation_"))
    assert executor.transaction_states == [False] * (
        executor.execute_calls + executor.reconcile_calls
    )


def test_transient_reconciliation_failure_stays_uncertain_without_reexecution(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-operation-reconcile-transient",
            kind=OperationKind.RECONCILIATION,
            target_type="provider_request",
            target_id="provider-request-transient",
            target_version=1,
            input_hash="5" * 64,
            input_ref=None,
            max_attempts=3,
        )
    )
    executor = TransientReconciliationExecutor()
    worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-reconcile",
        lease_duration=timedelta(seconds=30),
        retry_policy=OperationRetryPolicy(
            initial_delay=timedelta(seconds=1),
            maximum_delay=timedelta(seconds=10),
            maximum_elapsed=timedelta(minutes=5),
            jitter=lambda lower, upper: lower,
        ),
    )

    first = worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )
    second = worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )

    assert first.state == OperationState.RECONCILING
    assert second.state == OperationState.RECONCILING
    assert second.reconciliation_outcome == ReconciliationOutcome.PENDING
    assert second.reconciliation_attempt_count == 1
    assert second.next_reconciliation_at is not None
    assert executor.execute_calls == 1
    assert executor.reconcile_calls == 1


def test_reconciliation_uncertainty_exhaustion_dead_letters_without_hot_loop(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-operation-reconcile-exhausted",
            kind=OperationKind.RECONCILIATION,
            target_type="provider_request",
            target_id="provider-request-exhausted",
            target_version=1,
            input_hash="a" * 64,
            input_ref=None,
            max_attempts=3,
            max_reconciliation_attempts=1,
        )
    )
    executor = TransientReconciliationExecutor()
    worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-reconcile-exhausted",
        lease_duration=timedelta(seconds=30),
    )

    worker.execute(workspace_id=operation.workspace_id, operation_id=operation.id)
    failed = worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )
    emitted = OperationRecoveryService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory),
        batch_size=10,
    ).recover_once(now=datetime.now(UTC) + timedelta(hours=1))

    assert failed.state == OperationState.FAILED
    assert failed.dead_letter_id is not None
    assert failed.error is not None
    assert failed.error.retryable is False
    assert failed.next_reconciliation_at is None
    assert emitted == 0
    assert executor.execute_calls == 1
    assert executor.reconcile_calls == 1


class RetryThenSuccessExecutor(RecordingExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        self.calls += 1
        if self.calls == 1:
            raise OperationExecutionFailure(
                NormalizedOperationError(
                    code="PROVIDER_TEMPORARY",
                    category="provider",
                    message="provider is temporarily unavailable",
                    retryable=True,
                ),
                retry_at=datetime.now(UTC),
            )
        return OperationExecutionResult(
            operation_id=request.operation_id,
            output_ref=f"mysql://operation-results/{request.operation_id}",
        )


def test_worker_retries_through_success_with_real_mysql(integration_database) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-operation-retry-success",
            kind=OperationKind.ASSET_VALIDATION,
            target_type="asset_version",
            target_id="asset-version-retry-success",
            target_version=1,
            input_hash="6" * 64,
            input_ref=None,
            max_attempts=3,
        )
    )
    executor = RetryThenSuccessExecutor()
    worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-retry-success",
        lease_duration=timedelta(seconds=30),
    )

    first = worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )
    second = worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )

    assert first.state == OperationState.RETRYABLE_FAILED
    assert second.state == OperationState.SUCCEEDED
    assert second.attempt_count == 2
    assert executor.calls == 2


class ReconciliationSuccessExecutor(UnknownOutcomeExecutor):
    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        self.transaction_states.append(is_unit_of_work_active())
        return OperationReconciliationResult(
            operation_id=request.operation_id,
            outcome=ReconciliationOutcome.CONFIRMED_SUCCESS,
            output_ref=f"mysql://operation-results/{request.operation_id}",
        )


class ReconciliationResultIdentityExecutor(RecordingExecutor):
    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        self.transaction_states.append(is_unit_of_work_active())
        raise UnknownOperationOutcome(
            NormalizedOperationError(
                code="PROVIDER_TIMEOUT_UNKNOWN",
                category="provider",
                message="provider outcome is unknown",
                retryable=True,
            )
        )

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        self.transaction_states.append(is_unit_of_work_active())
        assert request.provider_request_id is None
        return OperationReconciliationResult(
            operation_id=request.operation_id,
            outcome=ReconciliationOutcome.CONFIRMED_SUCCESS,
            output_ref=f"mysql://operation-results/{request.operation_id}",
            provider_request_id=f"  provider-reconciled-{request.operation_id}  ",
        )


def test_reconciliation_dead_letter_replay_queries_outcome_without_reexecution(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-reconcile-dlq-replay",
            kind=OperationKind.RECONCILIATION,
            target_type="provider_request",
            target_id="provider-request-dlq-replay",
            target_version=1,
            input_hash="c" * 64,
            input_ref=None,
            max_attempts=3,
            max_reconciliation_attempts=1,
        )
    )
    transient_executor = TransientReconciliationExecutor()
    transient_worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=transient_executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-reconcile-dlq",
        lease_duration=timedelta(seconds=30),
    )
    transient_worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )
    failed = transient_worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )
    assert failed.dead_letter_id is not None
    principal = AuthenticatedPrincipal(
        actor_id="reconcile-admin",
        workspace_ids=frozenset({operation.workspace_id}),
        admin_workspace_ids=frozenset({operation.workspace_id}),
    )
    dead_letters = DeadLetterOperatorService(
        uow_factory=lambda: SqlAlchemyOperatorUnitOfWork(integration_database.session_factory),
        access_policy=AllowWorkspaceAdminPolicy(),
    )
    replay = dead_letters.replay(
        workspace_id=operation.workspace_id,
        dead_letter_id=failed.dead_letter_id,
        principal=principal,
        reason="query provider outcome after incident recovery",
        idempotency_key="reconcile-dlq-replay-0001",
        trace_id="reconcile-dlq-replay-trace",
    )
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        replay_event = uow.outbox.get(replay.replay_event_id)
    assert replay_event is not None
    success_executor = ReconciliationSuccessExecutor()
    success_worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=success_executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-reconcile-dlq-replay",
        lease_duration=timedelta(seconds=30),
    )

    result = success_worker.handle_recovery_event(replay_event)

    assert result.state == OperationState.SUCCEEDED
    assert result.attempt_count == 1
    assert result.reconciliation_outcome == ReconciliationOutcome.CONFIRMED_SUCCESS
    assert success_executor.transaction_states == [False]


def test_reconciliation_completes_without_reexecuting_provider_work(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-operation-reconcile-success",
            kind=OperationKind.PRODUCT_BRIEF_ANALYSIS,
            target_type="product",
            target_id="product-reconcile-success",
            target_version=1,
            input_hash="7" * 64,
            input_ref=None,
            max_attempts=3,
        )
    )
    executor = ReconciliationSuccessExecutor()
    worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-reconcile-success",
        lease_duration=timedelta(seconds=30),
    )

    worker.execute(workspace_id=operation.workspace_id, operation_id=operation.id)
    result = worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )

    assert result.state == OperationState.SUCCEEDED
    assert result.reconciliation_outcome == ReconciliationOutcome.CONFIRMED_SUCCESS
    assert result.attempt_count == 1
    assert executor.transaction_states == [False, False]


def test_reconciled_success_provider_identity_round_trips_after_worker_restart(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-reconciled-provider-success",
            kind=OperationKind.RECONCILIATION,
            target_type="provider_request",
            target_id="provider-request-reconciled-success",
            target_version=1,
            input_hash="2" * 64,
            input_ref=None,
            max_attempts=3,
        )
    )
    first_executor = ReconciliationResultIdentityExecutor()
    first_worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=first_executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-provider-reconcile-start",
        lease_duration=timedelta(seconds=30),
    )

    uncertain = first_worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )
    assert uncertain.state == OperationState.RECONCILING
    assert uncertain.provider_request_id is None

    restarted_service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    restarted_executor = ReconciliationResultIdentityExecutor()
    restarted_worker = DurableOperationWorker(
        operations=restarted_service,
        execution=OperationExecutionBoundary(
            executor=restarted_executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-provider-reconcile-finish",
        lease_duration=timedelta(seconds=30),
    )
    succeeded = restarted_worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )
    audit_service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    audited = audit_service.get(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )

    expected_request_id = f"provider-reconciled-{operation.id}"
    assert succeeded.state == OperationState.SUCCEEDED
    assert succeeded.reconciliation_outcome == ReconciliationOutcome.CONFIRMED_SUCCESS
    assert succeeded.provider_request_id == expected_request_id
    assert audited.provider_request_id == expected_request_id
    assert audited.error is not None
    assert audited.error.provider_request_id is None
    assert first_executor.transaction_states == [False]
    assert restarted_executor.transaction_states == [False]


class PermanentFailureExecutor(RecordingExecutor):
    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        raise OperationExecutionFailure(
            NormalizedOperationError(
                code="PROVIDER_REJECTED",
                category="provider",
                message="provider rejected the operation",
                retryable=False,
            )
        )


class RetryableReplayFailureExecutor(RecordingExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.execute_calls = 0

    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        self.execute_calls += 1
        raise OperationExecutionFailure(
            NormalizedOperationError(
                code="PROVIDER_TEMPORARY",
                category="provider",
                message="provider remained unavailable during explicit replay",
                retryable=True,
            )
        )

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        raise AssertionError(f"execution replay must not reconcile {request.operation_id}")


def test_terminal_execution_failure_atomically_creates_operation_dead_letter(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-operation-terminal-dlq",
            kind=OperationKind.ASSET_INDEXING,
            target_type="asset_version",
            target_id="asset-version-terminal-dlq",
            target_version=1,
            input_hash="8" * 64,
            input_ref=None,
            max_attempts=1,
        )
    )
    worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=PermanentFailureExecutor(),
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-terminal-dlq",
        lease_duration=timedelta(seconds=30),
    )

    failed = worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )

    assert failed.state == OperationState.FAILED
    assert failed.dead_letter_id is not None
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        dead_letter = uow.dead_letters.get_by_id(
            workspace_id=operation.workspace_id,
            dead_letter_id=failed.dead_letter_id,
        )
        source_event = uow.outbox.get(dead_letter.message_id) if dead_letter else None
    assert dead_letter is not None
    assert dead_letter.reason == "operation_terminal_failure"
    assert source_event is not None
    assert source_event.published_at is not None


def test_operation_dead_letter_replay_requeues_one_attempt_and_preserves_source(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-operation-dlq-replay",
            kind=OperationKind.COLLECTION_REBUILD,
            target_type="collection",
            target_id="collection-dlq-replay",
            target_version=1,
            input_hash="b" * 64,
            input_ref=None,
            max_attempts=1,
        )
    )
    failing_worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=PermanentFailureExecutor(),
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-dlq-failure",
        lease_duration=timedelta(seconds=30),
    )
    failed = failing_worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )
    assert failed.dead_letter_id is not None
    principal = AuthenticatedPrincipal(
        actor_id="operation-admin",
        workspace_ids=frozenset({operation.workspace_id}),
        admin_workspace_ids=frozenset({operation.workspace_id}),
    )
    dead_letters = DeadLetterOperatorService(
        uow_factory=lambda: SqlAlchemyOperatorUnitOfWork(integration_database.session_factory),
        access_policy=AllowWorkspaceAdminPolicy(),
    )
    replay = dead_letters.replay(
        workspace_id=operation.workspace_id,
        dead_letter_id=failed.dead_letter_id,
        principal=principal,
        reason="operator confirmed safe replay",
        idempotency_key="operation-dlq-replay-0001",
        trace_id="operation-dlq-replay-trace",
    )
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        replay_event = uow.outbox.get(replay.replay_event_id)
    assert replay_event is not None
    replayed_at = failed.execution_deadline_at + timedelta(seconds=1)
    success_worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=RecordingExecutor(),
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-dlq-replay",
        lease_duration=timedelta(seconds=30),
        clock=MutableClock(replayed_at),
    )

    succeeded = success_worker.handle_recovery_event(replay_event)
    detail = dead_letters.get(
        workspace_id=operation.workspace_id,
        dead_letter_id=failed.dead_letter_id,
        principal=principal,
    )

    assert succeeded.state == OperationState.SUCCEEDED
    assert succeeded.attempt_count == 2
    assert succeeded.execution_deadline_at == replayed_at + timedelta(hours=24)
    assert succeeded.replay_source_dead_letter_id == failed.dead_letter_id
    assert detail.dead_letter.id == failed.dead_letter_id
    assert detail.dead_letter.reason == "operation_terminal_failure"
    assert [item.id for item in detail.replays] == [replay.id]


def test_early_terminal_failure_replay_grants_exactly_one_execution_attempt(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-operation-early-terminal-replay",
            kind=OperationKind.ASSET_DELETION,
            target_type="asset",
            target_id="asset-early-terminal-replay",
            target_version=1,
            input_hash="3" * 64,
            input_ref=None,
            max_attempts=5,
        )
    )
    initial_worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=PermanentFailureExecutor(),
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-early-terminal",
        lease_duration=timedelta(seconds=30),
    )
    failed = initial_worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )
    assert failed.attempt_count == 1
    assert failed.max_attempts == 5
    assert failed.dead_letter_id is not None

    principal = AuthenticatedPrincipal(
        actor_id="early-terminal-admin",
        workspace_ids=frozenset({operation.workspace_id}),
        admin_workspace_ids=frozenset({operation.workspace_id}),
    )
    dead_letters = DeadLetterOperatorService(
        uow_factory=lambda: SqlAlchemyOperatorUnitOfWork(integration_database.session_factory),
        access_policy=AllowWorkspaceAdminPolicy(),
    )
    replay = dead_letters.replay(
        workspace_id=operation.workspace_id,
        dead_letter_id=failed.dead_letter_id,
        principal=principal,
        reason="one explicit provider execution after terminal classification review",
        idempotency_key="early-terminal-replay-0001",
        trace_id="early-terminal-replay-trace",
    )
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        replay_event = uow.outbox.get(replay.replay_event_id)
    assert replay_event is not None

    replay_executor = RetryableReplayFailureExecutor()
    replay_worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=replay_executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-early-terminal-replay",
        lease_duration=timedelta(seconds=30),
    )
    replay_failed = replay_worker.handle_recovery_event(replay_event)
    emitted = OperationRecoveryService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory),
        batch_size=10,
    ).recover_once(now=datetime.now(UTC) + timedelta(hours=1))

    assert replay_failed.state == OperationState.FAILED
    assert replay_failed.attempt_count == 2
    assert replay_failed.max_attempts == 2
    assert replay_failed.dead_letter_id is not None
    assert replay_failed.dead_letter_id != failed.dead_letter_id
    assert replay_failed.next_attempt_at is None
    assert replay_executor.execute_calls == 1
    assert emitted == 0
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        child_dead_letter = uow.dead_letters.get_by_id(
            workspace_id=operation.workspace_id,
            dead_letter_id=replay_failed.dead_letter_id,
        )
    assert child_dead_letter is not None
    assert child_dead_letter.source_dead_letter_id == failed.dead_letter_id


def test_each_reconciliation_replay_grants_exactly_one_additional_query(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-reconciliation-replay-budget",
            kind=OperationKind.RECONCILIATION,
            target_type="provider_request",
            target_id="provider-request-replay-budget",
            target_version=1,
            input_hash="4" * 64,
            input_ref=None,
            max_attempts=3,
            max_reconciliation_attempts=1,
        )
    )
    initial_executor = TransientReconciliationExecutor()
    initial_worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=initial_executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-reconciliation-budget-initial",
        lease_duration=timedelta(seconds=30),
    )
    initial_worker.execute(workspace_id=operation.workspace_id, operation_id=operation.id)
    failed = initial_worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )
    assert failed.state == OperationState.FAILED
    assert failed.reconciliation_attempt_count == 1
    assert failed.max_reconciliation_attempts == 1
    assert failed.dead_letter_id is not None

    principal = AuthenticatedPrincipal(
        actor_id="reconciliation-budget-admin",
        workspace_ids=frozenset({operation.workspace_id}),
        admin_workspace_ids=frozenset({operation.workspace_id}),
    )
    dead_letters = DeadLetterOperatorService(
        uow_factory=lambda: SqlAlchemyOperatorUnitOfWork(integration_database.session_factory),
        access_policy=AllowWorkspaceAdminPolicy(),
    )

    previous_dead_letter_id = failed.dead_letter_id
    expected_provider_request_id = None
    for replay_number, expected_attempt_count in ((1, 2), (2, 3)):
        replay = dead_letters.replay(
            workspace_id=operation.workspace_id,
            dead_letter_id=previous_dead_letter_id,
            principal=principal,
            reason=f"one explicit reconciliation query #{replay_number}",
            idempotency_key=f"reconciliation-budget-replay-{replay_number:04d}",
            trace_id=f"reconciliation-budget-replay-trace-{replay_number}",
        )
        with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
            replay_event = uow.outbox.get(replay.replay_event_id)
        assert replay_event is not None

        query_executor = ProviderStatusQueryFailureExecutor(
            error_request_id=f"reconciliation-query-error-{replay_number}"
        )
        replay_worker = DurableOperationWorker(
            operations=service,
            execution=OperationExecutionBoundary(
                executor=query_executor,
                transaction_active=is_unit_of_work_active,
            ),
            owner=f"worker-reconciliation-budget-replay-{replay_number}",
            lease_duration=timedelta(seconds=30),
        )
        replay_failed = replay_worker.handle_recovery_event(replay_event)

        assert replay_failed.state == OperationState.FAILED
        assert replay_failed.reconciliation_attempt_count == expected_attempt_count
        assert replay_failed.max_reconciliation_attempts == expected_attempt_count
        assert replay_failed.next_reconciliation_at is None
        assert replay_failed.dead_letter_id is not None
        assert replay_failed.dead_letter_id != previous_dead_letter_id
        assert query_executor.provider_request_ids == [expected_provider_request_id]
        expected_provider_request_id = (
            expected_provider_request_id or f"reconciliation-query-error-{replay_number}"
        )
        assert replay_failed.provider_request_id == expected_provider_request_id
        previous_dead_letter_id = replay_failed.dead_letter_id

    emitted = OperationRecoveryService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory),
        batch_size=10,
    ).recover_once(now=datetime.now(UTC) + timedelta(hours=1))
    assert emitted == 0


def test_expired_claim_at_max_attempts_atomically_creates_dead_letter(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-operation-expired-dlq",
            kind=OperationKind.ASSET_DELETION,
            target_type="asset",
            target_id="asset-expired-dlq",
            target_version=1,
            input_hash="9" * 64,
            input_ref=None,
            max_attempts=1,
        )
    )
    claimed_at = datetime.now(UTC) - timedelta(seconds=1)
    service.claim(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        owner="worker-expired-dlq",
        lease_duration=timedelta(microseconds=1),
        now=claimed_at,
    )

    emitted = OperationRecoveryService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory),
        batch_size=1,
    ).recover_once(now=datetime.now(UTC))
    failed = service.get(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )

    assert emitted == 0
    assert failed.state == OperationState.FAILED
    assert failed.dead_letter_id is not None


class MismatchedResultExecutor(RecordingExecutor):
    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        self.transaction_states.append(is_unit_of_work_active())
        return OperationExecutionResult(
            operation_id="different-operation",
            output_ref="mysql://operation-results/different-operation",
        )


def test_mismatched_external_result_enters_reconciliation_immediately(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-operation-mismatch",
            kind=OperationKind.ASSET_INDEXING,
            target_type="asset_version",
            target_id="asset-version-mismatch",
            target_version=1,
            input_hash="4" * 64,
            input_ref=None,
            max_attempts=3,
        )
    )
    worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=MismatchedResultExecutor(),
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-a",
        lease_duration=timedelta(seconds=30),
    )

    result = worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )

    assert result.state == OperationState.RECONCILING
    assert result.error is not None
    assert result.error.code == "OPERATION_RESULT_MISMATCH"


def test_unknown_external_outcome_enters_reconciliation_without_blind_retry(
    integration_database,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-operation-unknown",
            kind=OperationKind.ASSET_INDEXING,
            target_type="asset_version",
            target_id="asset-version-unknown",
            target_version=1,
            input_hash="0" * 64,
            input_ref=None,
            max_attempts=3,
        )
    )
    executor = UnknownOutcomeExecutor()
    worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="worker-a",
        lease_duration=timedelta(seconds=30),
    )

    result = worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )

    assert executor.transaction_states == [False]
    assert result.state == OperationState.RECONCILING
    assert result.next_attempt_at is None
    assert result.reconciliation_required is True
    assert result.error is not None
    assert result.error.code == "PROVIDER_TIMEOUT_UNKNOWN"
