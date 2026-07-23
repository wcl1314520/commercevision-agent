from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event, Lock

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
    UnknownOperationOutcome,
)
from commercevision_domain import (
    DurableOperation,
    LeaseConflictError,
    NormalizedOperationError,
    NotFoundError,
    OperationKind,
    OperationState,
    ReconciliationOutcome,
)
from commercevision_domain.messaging import OutboxEvent
from commercevision_persistence import (
    SqlAlchemyOperationUnitOfWork,
    SqlAlchemyOperatorUnitOfWork,
    SqlAlchemyUnitOfWork,
    is_unit_of_work_active,
)
from commercevision_persistence.models import InboxMessageModel
from commercevision_worker.runtime import WorkerRuntime
from sqlalchemy import update

pytestmark = pytest.mark.integration


class AllowWorkspaceAdminPolicy:
    def require_admin(
        self,
        *,
        workspace_id: str,
        principal: AuthenticatedPrincipal,
    ) -> None:
        assert workspace_id in principal.admin_workspace_ids


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class PermanentFailureExecutor:
    def __init__(self) -> None:
        self.execute_calls = 0

    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        self.execute_calls += 1
        raise OperationExecutionFailure(
            NormalizedOperationError(
                code="PERMANENT_PROVIDER_FAILURE",
                category="provider",
                message=f"provider rejected {request.operation_id}",
                retryable=False,
            )
        )

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        raise AssertionError(f"operation {request.operation_id} must not reconcile")


class BlockingPermanentFailureExecutor(PermanentFailureExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.started = Event()
        self.release = Event()
        self._counter_lock = Lock()

    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        with self._counter_lock:
            self.execute_calls += 1
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test did not release replay execution")
        raise OperationExecutionFailure(
            NormalizedOperationError(
                code="PERMANENT_PROVIDER_FAILURE",
                category="provider",
                message=f"provider rejected {request.operation_id}",
                retryable=False,
            )
        )


class QueryExceptionExecutor:
    def __init__(
        self,
        *,
        retryable: bool,
        exception_kind: str,
        clock: MutableClock,
        completed_at: datetime | None = None,
    ) -> None:
        self.retryable = retryable
        self.exception_kind = exception_kind
        self.clock = clock
        self.completed_at = completed_at
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
            )
        )

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        self.reconcile_calls += 1
        if self.completed_at is not None:
            self.clock.now = self.completed_at
        error = NormalizedOperationError(
            code="PROVIDER_QUERY_UNAVAILABLE",
            category="provider",
            message="provider status query could not complete",
            retryable=self.retryable,
            provider_request_id=f"query-{request.operation_id}",
        )
        if self.exception_kind == "unknown_outcome":
            raise UnknownOperationOutcome(error)
        raise OperationExecutionFailure(error)


class TimedReconciliationResultExecutor:
    def __init__(
        self,
        *,
        outcome: ReconciliationOutcome,
        clock: MutableClock,
        completed_at: datetime,
        error_retryable: bool | None = None,
    ) -> None:
        self.outcome = outcome
        self.clock = clock
        self.completed_at = completed_at
        self.error_retryable = error_retryable
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
            )
        )

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        self.reconcile_calls += 1
        self.clock.now = self.completed_at
        return _reconciliation_result(
            request=request,
            outcome=self.outcome,
            provider_request_id=f"late-{request.operation_id}",
            error_retryable=self.error_retryable,
        )


class BlockingReconciliationResultExecutor:
    def __init__(self, *, outcome: ReconciliationOutcome) -> None:
        self.outcome = outcome
        self.started = Event()
        self.release = Event()
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
            )
        )

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        self.reconcile_calls += 1
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test did not release reconciliation query")
        return _reconciliation_result(
            request=request,
            outcome=self.outcome,
            provider_request_id=f"late-{request.operation_id}",
        )


class RestartedReconciliationSuccessExecutor:
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


def _reconciliation_result(
    *,
    request: OperationExecutionRequest,
    outcome: ReconciliationOutcome,
    provider_request_id: str,
    error_retryable: bool | None = None,
) -> OperationReconciliationResult:
    error = None
    if outcome != ReconciliationOutcome.CONFIRMED_SUCCESS:
        error = NormalizedOperationError(
            code=f"PROVIDER_{outcome.value}",
            category="provider",
            message=f"provider returned {outcome.value}",
            retryable=(
                error_retryable
                if error_retryable is not None
                else outcome
                in {
                    ReconciliationOutcome.PENDING,
                    ReconciliationOutcome.NOT_FOUND,
                }
            ),
        )
    return OperationReconciliationResult(
        operation_id=request.operation_id,
        outcome=outcome,
        output_ref=(
            f"mysql://operation-results/{request.operation_id}"
            if outcome == ReconciliationOutcome.CONFIRMED_SUCCESS
            else None
        ),
        provider_request_id=provider_request_id,
        error=error,
    )


def _operation_service(integration_database) -> OperationApplicationService:
    return OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )


def _create_operation(
    integration_database,
    *,
    workspace_id: str,
    target_id: str,
    kind: OperationKind = OperationKind.RECONCILIATION,
    max_attempts: int = 3,
    max_reconciliation_attempts: int = 4,
) -> tuple[OperationApplicationService, DurableOperation]:
    service = _operation_service(integration_database)
    operation = service.create(
        OperationCreateCommand(
            workspace_id=workspace_id,
            kind=kind,
            target_type="provider_request",
            target_id=target_id,
            target_version=1,
            input_hash=(target_id.encode().hex() + ("0" * 64))[:64],
            input_ref=None,
            max_attempts=max_attempts,
            max_reconciliation_attempts=max_reconciliation_attempts,
        )
    )
    return service, operation


def _create_failed_operation(
    integration_database,
    *,
    workspace_id: str,
    target_id: str,
) -> tuple[OperationApplicationService, DurableOperation]:
    service, operation = _create_operation(
        integration_database,
        workspace_id=workspace_id,
        target_id=target_id,
        kind=OperationKind.ASSET_DELETION,
        max_attempts=1,
    )
    worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=PermanentFailureExecutor(),
            transaction_active=is_unit_of_work_active,
        ),
        owner="initial-failure-worker",
        lease_duration=timedelta(seconds=30),
    )
    failed = worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )
    assert failed.state == OperationState.FAILED
    assert failed.dead_letter_id is not None
    return service, failed


def _create_replay_event(
    integration_database,
    *,
    failed,
    idempotency_key: str,
) -> tuple[OutboxEvent, DeadLetterOperatorService]:
    principal = AuthenticatedPrincipal(
        actor_id="replay-acceptance-admin",
        workspace_ids=frozenset({failed.workspace_id}),
        admin_workspace_ids=frozenset({failed.workspace_id}),
    )
    dead_letters = DeadLetterOperatorService(
        uow_factory=lambda: SqlAlchemyOperatorUnitOfWork(integration_database.session_factory),
        access_policy=AllowWorkspaceAdminPolicy(),
    )
    replay = dead_letters.replay(
        workspace_id=failed.workspace_id,
        dead_letter_id=failed.dead_letter_id,
        principal=principal,
        reason="acceptance replay idempotency proof",
        idempotency_key=idempotency_key,
        trace_id=f"{idempotency_key}-trace",
    )
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        replay_event = uow.outbox.get(replay.replay_event_id)
    assert replay_event is not None
    return replay_event, dead_letters


def _children_for_source(integration_database, failed) -> list:
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        return uow.dead_letters.list_children(
            source_dead_letter_id=failed.dead_letter_id,
            workspace_id=failed.workspace_id,
            limit=10,
            cursor=None,
        )


def _recovery_event_for_operation(
    integration_database,
    operation,
    *,
    now: datetime,
) -> OutboxEvent:
    scanner = OperationRecoveryService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory),
        batch_size=1,
        reconciliation_max_elapsed=timedelta(minutes=5),
    )
    assert scanner.recover_once(now=now) == 1
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        events = uow.outbox.list_for_aggregate(operation.id)
    assert len(events) == 1
    return events[0]


def test_replay_redelivery_after_terminal_commit_does_not_reopen_or_duplicate_child_dlq(
    integration_settings,
    integration_database,
) -> None:
    _service, failed = _create_failed_operation(
        integration_database,
        workspace_id="workspace-replay-crash-window",
        target_id="replay-crash-window",
    )
    replay_event, _dead_letters = _create_replay_event(
        integration_database,
        failed=failed,
        idempotency_key="replay-crash-window-0001",
    )
    replay_executor = PermanentFailureExecutor()
    settings = integration_settings.model_copy(
        update={
            "service_name": "worker-replay-crash-window",
            "worker_required_operation_kinds": [OperationKind.ASSET_DELETION],
        }
    )
    runtime = WorkerRuntime.build(
        settings,
        operation_executors={OperationKind.ASSET_DELETION: replay_executor},
    )
    mark_processed = runtime.inbox.mark_processed

    def crash_before_inbox_completion(_event_id: str, _lease_token: str) -> None:
        raise RuntimeError("simulated crash before Inbox completion")

    runtime.inbox.mark_processed = crash_before_inbox_completion
    try:
        with pytest.raises(RuntimeError, match="simulated crash"):
            runtime.process_event(replay_event.envelope.event_id)

        first_children = _children_for_source(integration_database, failed)
        assert replay_executor.execute_calls == 1
        assert len(first_children) == 1
        first_child_id = first_children[0].id

        expired_at = datetime.now(UTC) - timedelta(microseconds=1)
        with integration_database.engine.begin() as connection:
            connection.execute(
                update(InboxMessageModel)
                .where(
                    InboxMessageModel.consumer == settings.worker_consumer_name,
                    InboxMessageModel.message_id == replay_event.envelope.event_id,
                )
                .values(lease_expires_at=expired_at, updated_at=expired_at)
            )
        runtime.inbox.mark_processed = mark_processed

        assert runtime.process_event(replay_event.envelope.event_id) == "processed"
        reloaded = _operation_service(integration_database).get(
            workspace_id=failed.workspace_id,
            operation_id=failed.id,
        )
        children = _children_for_source(integration_database, failed)

        assert replay_executor.execute_calls == 1
        assert [child.id for child in children] == [first_child_id]
        assert reloaded.state == OperationState.FAILED
        assert reloaded.dead_letter_id == first_child_id
        assert reloaded.attempt_count == 2
        assert reloaded.max_attempts == 2
    finally:
        runtime.inbox.mark_processed = mark_processed
        runtime.close()


def test_concurrent_replay_delivery_has_one_provider_call_and_one_child_dlq(
    integration_database,
) -> None:
    service, failed = _create_failed_operation(
        integration_database,
        workspace_id="workspace-replay-concurrent",
        target_id="replay-concurrent",
    )
    replay_event, _dead_letters = _create_replay_event(
        integration_database,
        failed=failed,
        idempotency_key="replay-concurrent-0001",
    )
    executor = BlockingPermanentFailureExecutor()
    workers = [
        DurableOperationWorker(
            operations=service,
            execution=OperationExecutionBoundary(
                executor=executor,
                transaction_active=is_unit_of_work_active,
            ),
            owner=f"replay-concurrent-worker-{index}",
            lease_duration=timedelta(seconds=30),
        )
        for index in range(2)
    ]
    barrier = Barrier(2)

    def deliver(worker: DurableOperationWorker):
        barrier.wait()
        return worker.handle_recovery_event(replay_event)

    errors: list[Exception] = []
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(deliver, worker) for worker in workers]
            assert executor.started.wait(timeout=2)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not any(future.done() for future in futures):
                time.sleep(0.01)
            executor.release.set()
            for future in futures:
                try:
                    future.result(timeout=2)
                except Exception as exc:
                    errors.append(exc)
    finally:
        executor.release.set()

    children = _children_for_source(integration_database, failed)
    reloaded = service.get(
        workspace_id=failed.workspace_id,
        operation_id=failed.id,
    )
    assert errors == []
    assert executor.execute_calls == 1
    assert len(children) == 1
    assert reloaded.state == OperationState.FAILED
    assert reloaded.dead_letter_id == children[0].id


def test_unregistered_replay_event_cannot_consume_replay_identity(
    integration_database,
) -> None:
    service, failed = _create_failed_operation(
        integration_database,
        workspace_id="workspace-replay-identity",
        target_id="replay-identity",
    )
    replay_event, _dead_letters = _create_replay_event(
        integration_database,
        failed=failed,
        idempotency_key="replay-identity-0001",
    )
    spoofed_event = replace(
        replay_event,
        envelope=replace(
            replay_event.envelope,
            event_id="00000000-0000-0000-0000-000000000099",
        ),
    )
    executor = PermanentFailureExecutor()
    worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="replay-identity-worker",
        lease_duration=timedelta(seconds=30),
    )

    with pytest.raises(NotFoundError, match="replay event"):
        worker.handle_recovery_event(spoofed_event)

    reloaded = service.get(
        workspace_id=failed.workspace_id,
        operation_id=failed.id,
    )
    assert executor.execute_calls == 0
    assert reloaded.state == OperationState.FAILED
    assert reloaded.dead_letter_id == failed.dead_letter_id
    assert reloaded.attempt_count == failed.attempt_count
    assert reloaded.max_attempts == failed.max_attempts


@pytest.mark.parametrize("retryable", [True, False])
@pytest.mark.parametrize("exception_kind", ["execution_failure", "unknown_outcome"])
def test_query_exception_remains_uncertain_until_reconciliation_budget(
    integration_database,
    retryable: bool,
    exception_kind: str,
) -> None:
    service, operation = _create_operation(
        integration_database,
        workspace_id=f"workspace-query-{retryable}-{exception_kind}",
        target_id=f"query-{retryable}-{exception_kind}",
        max_reconciliation_attempts=3,
    )
    clock = MutableClock(operation.created_at + timedelta(microseconds=1))
    executor = QueryExceptionExecutor(
        retryable=retryable,
        exception_kind=exception_kind,
        clock=clock,
    )
    worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner=f"query-worker-{retryable}-{exception_kind}",
        lease_duration=timedelta(seconds=30),
        reconciliation_policy=OperationReconciliationPolicy(
            initial_delay=timedelta(seconds=2),
            maximum_delay=timedelta(seconds=2),
            maximum_elapsed=timedelta(minutes=5),
            jitter=lambda lower, upper: lower,
        ),
        clock=clock,
    )
    uncertain = worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )
    recovery_event = _recovery_event_for_operation(
        integration_database,
        uncertain,
        now=clock.now,
    )

    deferred = worker.handle_recovery_event(recovery_event)

    assert deferred.state == OperationState.RECONCILING
    assert deferred.next_reconciliation_at == clock.now + timedelta(seconds=1)
    assert deferred.lease_token is None
    assert deferred.error is not None
    assert deferred.error.retryable is True
    assert deferred.provider_request_id == f"query-{operation.id}"
    assert deferred.recovery_generation == deferred.recovery_consumed_generation == 1
    assert deferred.dead_letter_id is None
    assert executor.execute_calls == 1
    assert executor.reconcile_calls == 1


@pytest.mark.parametrize(
    ("exhaustion", "completed_offset"),
    [
        ("attempt_budget", None),
        ("exact_deadline", timedelta(seconds=5)),
    ],
)
def test_query_exception_terminates_at_exact_reconciliation_limit(
    integration_database,
    exhaustion: str,
    completed_offset: timedelta | None,
) -> None:
    max_reconciliation_attempts = 1 if exhaustion == "attempt_budget" else 3
    service, operation = _create_operation(
        integration_database,
        workspace_id=f"workspace-query-limit-{exhaustion}",
        target_id=f"query-limit-{exhaustion}",
        max_reconciliation_attempts=max_reconciliation_attempts,
    )
    clock = MutableClock(operation.created_at + timedelta(microseconds=1))
    completed_at = clock.now + completed_offset if completed_offset is not None else None
    executor = QueryExceptionExecutor(
        retryable=False,
        exception_kind="execution_failure",
        clock=clock,
        completed_at=completed_at,
    )
    worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner=f"query-limit-worker-{exhaustion}",
        lease_duration=timedelta(seconds=30),
        reconciliation_policy=OperationReconciliationPolicy(
            initial_delay=timedelta(seconds=2),
            maximum_delay=timedelta(seconds=2),
            maximum_elapsed=timedelta(seconds=5),
            jitter=lambda lower, upper: lower,
        ),
        clock=clock,
    )
    uncertain = worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )
    recovery_event = _recovery_event_for_operation(
        integration_database,
        uncertain,
        now=clock.now,
    )

    exhausted = worker.handle_recovery_event(recovery_event)

    assert exhausted.state == OperationState.FAILED
    assert exhausted.next_reconciliation_at is None
    assert exhausted.lease_token is None
    assert exhausted.error is not None
    assert exhausted.error.retryable is False
    assert exhausted.recovery_generation == exhausted.recovery_consumed_generation == 1
    assert exhausted.dead_letter_id is not None
    assert executor.reconcile_calls == 1


@pytest.mark.parametrize(
    "outcome",
    [
        ReconciliationOutcome.PENDING,
        ReconciliationOutcome.NOT_FOUND,
    ],
)
def test_uncertain_query_result_ignores_nonretryable_diagnostic(
    integration_database,
    outcome: ReconciliationOutcome,
) -> None:
    service, operation = _create_operation(
        integration_database,
        workspace_id=f"workspace-query-result-{outcome}",
        target_id=f"query-result-{outcome}",
        max_reconciliation_attempts=3,
    )
    clock = MutableClock(operation.created_at + timedelta(microseconds=1))
    executor = TimedReconciliationResultExecutor(
        outcome=outcome,
        clock=clock,
        completed_at=clock.now,
        error_retryable=False,
    )
    worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner=f"query-result-worker-{outcome}",
        lease_duration=timedelta(seconds=30),
        reconciliation_policy=OperationReconciliationPolicy(
            initial_delay=timedelta(seconds=2),
            maximum_delay=timedelta(seconds=2),
            maximum_elapsed=timedelta(minutes=5),
            jitter=lambda lower, upper: lower,
        ),
        clock=clock,
    )
    worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )

    deferred = worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )

    assert deferred.state == OperationState.RECONCILING
    assert deferred.error is not None
    assert deferred.error.retryable is True
    assert deferred.next_reconciliation_at == clock.now + timedelta(seconds=1)
    assert deferred.provider_request_id == f"late-{operation.id}"
    assert deferred.lease_token is None


@pytest.mark.parametrize(
    "outcome",
    [
        ReconciliationOutcome.CONFIRMED_SUCCESS,
        ReconciliationOutcome.PENDING,
        ReconciliationOutcome.NOT_FOUND,
        ReconciliationOutcome.CONFIRMED_FAILURE,
    ],
)
@pytest.mark.parametrize("completion_offset", [timedelta(0), timedelta(microseconds=1)])
def test_reconciliation_result_cas_settles_at_or_after_exact_lease_expiry(
    integration_database,
    outcome: ReconciliationOutcome,
    completion_offset: timedelta,
) -> None:
    service, operation = _create_operation(
        integration_database,
        workspace_id=f"workspace-reconcile-expiry-{outcome}-{completion_offset.microseconds}",
        target_id=f"reconcile-expiry-{outcome}-{completion_offset.microseconds}",
    )
    clock = MutableClock(operation.created_at + timedelta(microseconds=1))
    lease_duration = timedelta(seconds=30)
    completed_at = clock.now + lease_duration + completion_offset
    executor = TimedReconciliationResultExecutor(
        outcome=outcome,
        clock=clock,
        completed_at=completed_at,
    )
    worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner=f"reconcile-expiry-worker-{outcome}",
        lease_duration=lease_duration,
        reconciliation_policy=OperationReconciliationPolicy(
            initial_delay=timedelta(seconds=2),
            maximum_delay=timedelta(seconds=2),
            maximum_elapsed=timedelta(minutes=5),
            jitter=lambda lower, upper: lower,
        ),
        clock=clock,
    )
    worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )

    settled = worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )

    expected_state = {
        ReconciliationOutcome.CONFIRMED_SUCCESS: OperationState.SUCCEEDED,
        ReconciliationOutcome.PENDING: OperationState.RECONCILING,
        ReconciliationOutcome.NOT_FOUND: OperationState.RECONCILING,
        ReconciliationOutcome.CONFIRMED_FAILURE: OperationState.FAILED,
    }[outcome]
    assert settled.state == expected_state
    assert settled.provider_request_id == f"late-{operation.id}"
    assert settled.lease_token is None
    assert settled.lease_expires_at is None
    assert settled.updated_at == completed_at
    if outcome in {
        ReconciliationOutcome.CONFIRMED_SUCCESS,
        ReconciliationOutcome.CONFIRMED_FAILURE,
    }:
        assert settled.completed_at == completed_at
    if outcome in {ReconciliationOutcome.PENDING, ReconciliationOutcome.NOT_FOUND}:
        assert settled.next_reconciliation_at == completed_at + timedelta(seconds=1)
    assert executor.execute_calls == 1
    assert executor.reconcile_calls == 1


def test_reconciliation_cas_still_rejects_a_changed_lease_token(
    integration_database,
) -> None:
    service, operation = _create_operation(
        integration_database,
        workspace_id="workspace-reconcile-token",
        target_id="reconcile-token",
    )
    now = operation.created_at + timedelta(microseconds=1)
    execution_token = service.claim(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        owner="execution-token-worker",
        lease_duration=timedelta(seconds=30),
        now=now,
    )
    running = service.start(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        lease_token=execution_token,
        now=now,
    )
    reconciling = service.require_reconciliation(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        lease_token=execution_token,
        error=NormalizedOperationError(
            code="EXTERNAL_OUTCOME_UNKNOWN",
            category="provider",
            message="provider outcome is unknown",
            retryable=True,
        ),
        expected_execution_version=running.version,
        expected_attempt_count=running.attempt_count,
        now=now,
    )
    reconciliation_token = service.claim_reconciliation(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        owner="reconciliation-token-worker",
        lease_duration=timedelta(seconds=30),
        now=now,
    )
    claimed = service.get(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )

    with pytest.raises(LeaseConflictError, match="token does not match"):
        service.resolve_reconciliation(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
            lease_token=f"{reconciliation_token}-changed",
            outcome=ReconciliationOutcome.CONFIRMED_SUCCESS,
            output_ref="mysql://result/changed-token",
            provider_request_id="provider-changed-token",
            expected_reconciliation_version=claimed.version,
            expected_reconciliation_attempt_count=claimed.reconciliation_attempt_count,
            now=claimed.lease_expires_at,
        )
    assert reconciling.state == OperationState.RECONCILING


@pytest.mark.parametrize(
    "outcome",
    [
        ReconciliationOutcome.CONFIRMED_SUCCESS,
        ReconciliationOutcome.PENDING,
        ReconciliationOutcome.NOT_FOUND,
        ReconciliationOutcome.CONFIRMED_FAILURE,
    ],
)
def test_competing_reconciliation_reclaim_retains_identity_without_stale_overwrite(
    integration_database,
    outcome: ReconciliationOutcome,
) -> None:
    service, operation = _create_operation(
        integration_database,
        workspace_id=f"workspace-reconcile-reclaim-{outcome}",
        target_id=f"reconcile-reclaim-{outcome}",
        max_reconciliation_attempts=4,
    )
    clock = MutableClock(operation.created_at + timedelta(microseconds=1))
    lease_duration = timedelta(seconds=30)
    stale_executor = BlockingReconciliationResultExecutor(outcome=outcome)
    stale_worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=stale_executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner=f"stale-reconciliation-worker-{outcome}",
        lease_duration=lease_duration,
        reconciliation_policy=OperationReconciliationPolicy(
            initial_delay=timedelta(seconds=2),
            maximum_delay=timedelta(seconds=2),
            maximum_elapsed=timedelta(minutes=5),
            jitter=lambda lower, upper: lower,
        ),
        clock=clock,
    )
    stale_worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            stale_worker.execute,
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
        )
        assert stale_executor.started.wait(timeout=2)
        stale_claim = service.get(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
        )
        assert stale_claim.lease_expires_at is not None
        reclaimed_at = stale_claim.lease_expires_at
        competing_token = service.claim_reconciliation(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
            owner="competing-reconciliation-worker",
            lease_duration=lease_duration,
            now=reclaimed_at,
        )
        clock.now = reclaimed_at + timedelta(microseconds=1)
        stale_executor.release.set()
        stale_result = future.result(timeout=2)

    assert stale_result.state == OperationState.RECONCILING
    assert stale_result.lease_token == competing_token
    assert stale_result.reconciliation_attempt_count == 2
    assert stale_result.output_ref is None
    assert stale_result.provider_request_id == f"late-{operation.id}"
    assert stale_result.dead_letter_id is None

    restarted_executor = RestartedReconciliationSuccessExecutor()
    restarted_worker = DurableOperationWorker(
        operations=_operation_service(integration_database),
        execution=OperationExecutionBoundary(
            executor=restarted_executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="restarted-reconciliation-worker",
        lease_duration=lease_duration,
        clock=MutableClock(reclaimed_at + lease_duration),
    )
    restarted = restarted_worker.execute(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )

    assert restarted.state == OperationState.SUCCEEDED
    assert restarted.provider_request_id == f"late-{operation.id}"
    assert restarted_executor.execute_calls == 0
    assert restarted_executor.reconcile_calls == 1
    assert restarted_executor.provider_request_ids == [f"late-{operation.id}"]
