"""Scheduler composition for Outbox publication and recovery scans."""

from __future__ import annotations

import asyncio
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import Event, Thread

from celery import Celery
from commercevision_application import (
    EventRoutingError,
    OperationRecoveryService,
    OutboxDispatcher,
    RecoveryService,
    build_event_routing_registry,
)
from commercevision_contracts import Settings
from commercevision_contracts.events import EventQueue
from commercevision_domain.messaging import OutboxEvent
from commercevision_persistence import (
    Database,
    SqlAlchemyOperationUnitOfWork,
    SqlAlchemyUnitOfWork,
    create_database,
)


class CeleryMessagePublisher:
    def __init__(self, settings: Settings) -> None:
        self._client = Celery("commercevision-scheduler", broker=settings.rabbitmq_url)
        self._routing = build_event_routing_registry(
            {
                EventQueue.WORKFLOW: settings.workflow_queue_name,
                EventQueue.ASSET: settings.asset_queue_name,
                EventQueue.INDEX: settings.index_queue_name,
                EventQueue.MAINTENANCE: settings.maintenance_queue_name,
            }
        )
        self._fallback_queue = settings.maintenance_queue_name
        self._client.conf.update(
            broker_connection_retry_on_startup=True,
            broker_transport_options={"confirm_publish": True},
            task_default_delivery_mode="persistent",
            task_default_queue=settings.workflow_queue_name,
            task_publish_retry=True,
            task_publish_retry_policy={
                "max_retries": 5,
                "interval_start": 0,
                "interval_step": 1,
                "interval_max": 5,
            },
        )

    def publish_event(self, event: OutboxEvent) -> None:
        try:
            queue = self._routing.queue_for(event.envelope)
        except EventRoutingError:
            queue = self._fallback_queue
        self._send(event.envelope.event_id, queue)

    def _send(self, event_id: str, queue: str) -> None:
        self._client.send_task(
            "commercevision.process_outbox_event",
            args=[event_id],
            task_id=event_id,
            queue=queue,
        )


@dataclass(slots=True)
class ScannerStatus:
    last_started_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    last_duration_ms: float | None = None
    last_count: int = 0
    total_count: int = 0
    in_progress: bool = False
    timed_out: bool = False
    timeout_count: int = 0


@dataclass(frozen=True, slots=True)
class ScannerDefinition:
    name: str
    interval_seconds: float
    run_once: Callable[[], int]


@dataclass(slots=True)
class _ScannerRun:
    scanner: ScannerDefinition
    started_counter: float
    completed: Event = field(default_factory=Event)
    thread: Thread | None = None
    result: int | None = None
    error: Exception | None = None
    timed_out: bool = False
    completed_counter: float | None = None


class IndependentScannerOrchestrator:
    def __init__(
        self,
        *,
        scanners: tuple[ScannerDefinition, ...],
        timeout_seconds: float = 30.0,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        names = [scanner.name for scanner in scanners]
        if len(set(names)) != len(names):
            raise ValueError("scanner names must be unique")
        if any(scanner.interval_seconds <= 0 for scanner in scanners):
            raise ValueError("scanner intervals must be positive")
        if timeout_seconds <= 0:
            raise ValueError("scanner timeout must be positive")
        self._scanners = scanners
        self._timeout_seconds = timeout_seconds
        self._monotonic_clock = monotonic_clock
        self._wall_clock = wall_clock or (lambda: datetime.now(UTC))
        self._next_due = {scanner.name: 0.0 for scanner in scanners}
        self.statuses = {scanner.name: ScannerStatus() for scanner in scanners}
        self._active: dict[str, _ScannerRun] = {}

    def run_due(self) -> None:
        tick = self._monotonic_clock()
        self._collect_completed_and_timeouts()
        for scanner in self._scanners:
            if tick < self._next_due[scanner.name] or scanner.name in self._active:
                continue
            self._start(scanner, tick=tick)

    def _start(self, scanner: ScannerDefinition, *, tick: float) -> None:
        status = self.statuses[scanner.name]
        status.last_started_at = self._wall_clock()
        status.in_progress = True
        status.timed_out = False
        run = _ScannerRun(
            scanner=scanner,
            started_counter=time.perf_counter(),
        )
        run.thread = Thread(
            target=self._run_scanner,
            args=(run,),
            name=f"scanner-{scanner.name}",
            daemon=True,
        )
        self._active[scanner.name] = run
        self._next_due[scanner.name] = tick + scanner.interval_seconds
        run.thread.start()

    @staticmethod
    def _run_scanner(run: _ScannerRun) -> None:
        try:
            run.result = run.scanner.run_once()
        except Exception as exc:
            run.error = exc
        finally:
            run.completed_counter = time.perf_counter()
            run.completed.set()

    def _collect_completed_and_timeouts(self) -> None:
        now = time.perf_counter()
        for name, run in tuple(self._active.items()):
            elapsed = (run.completed_counter or now) - run.started_counter
            if not run.timed_out and elapsed >= self._timeout_seconds:
                self._mark_timed_out(run)
            if not run.completed.is_set():
                continue
            status = self.statuses[name]
            status.in_progress = False
            del self._active[name]
            if run.timed_out:
                continue
            status.last_duration_ms = elapsed * 1000
            if run.error is not None:
                status.last_error = f"{type(run.error).__name__}: {run.error}"
            else:
                count = run.result or 0
                status.last_success_at = self._wall_clock()
                status.last_error = None
                status.last_count = count
                status.total_count += count

    def _mark_timed_out(self, run: _ScannerRun) -> None:
        run.timed_out = True
        status = self.statuses[run.scanner.name]
        status.timed_out = True
        status.timeout_count += 1
        status.last_duration_ms = self._timeout_seconds * 1000
        status.last_error = f"TimeoutError: scanner exceeded {self._timeout_seconds:.3f} seconds"


@dataclass(slots=True)
class SchedulerState:
    last_dispatch_at: datetime | None = None
    last_recovery_at: datetime | None = None
    published_total: int = 0
    publish_failed_total: int = 0
    recovered_steps_total: int = 0
    recovered_workflows_total: int = 0
    recovered_operations_total: int = 0
    scanners: dict[str, ScannerStatus] | None = None

    @property
    def last_error(self) -> str | None:
        errors = [
            f"{name}: {status.last_error}"
            for name, status in (self.scanners or {}).items()
            if status.last_error
        ]
        return "; ".join(errors) or None


class SchedulerRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database: Database = create_database(settings)

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(self.database.session_factory)

        def operation_uow_factory() -> SqlAlchemyOperationUnitOfWork:
            return SqlAlchemyOperationUnitOfWork(self.database.session_factory)

        owner = f"{socket.gethostname()}:{settings.service_name}"
        self.dispatcher = OutboxDispatcher(
            uow_factory=uow_factory,
            publisher=CeleryMessagePublisher(settings),
            owner=owner,
            lease_duration=timedelta(seconds=settings.scheduler_lease_seconds),
            batch_size=settings.scheduler_batch_size,
        )
        self.recovery = RecoveryService(
            uow_factory=uow_factory,
            batch_size=settings.scheduler_batch_size,
            stale_after=timedelta(
                seconds=max(
                    settings.workflow_step_lease_seconds * 2,
                    settings.scheduler_recovery_interval_seconds * 2,
                )
            ),
        )
        self.operation_recovery = OperationRecoveryService(
            uow_factory=operation_uow_factory,
            batch_size=settings.scheduler_batch_size,
            reconciliation_max_elapsed=timedelta(
                seconds=settings.operation_reconciliation_max_elapsed_seconds
            ),
        )
        self.state = SchedulerState()
        self.orchestrator = IndependentScannerOrchestrator(
            scanners=(
                ScannerDefinition(
                    "outbox_dispatch",
                    settings.scheduler_poll_seconds,
                    self._dispatch_once,
                ),
                ScannerDefinition(
                    "workflow_recovery",
                    settings.scheduler_recovery_interval_seconds,
                    self._recover_workflows_once,
                ),
                ScannerDefinition(
                    "operation_recovery",
                    settings.scheduler_operation_recovery_interval_seconds,
                    self._recover_operations_once,
                ),
            ),
            timeout_seconds=settings.scheduler_scanner_timeout_seconds,
        )
        self.state.scanners = self.orchestrator.statuses

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.settings.scheduler_poll_seconds)
            await asyncio.to_thread(self.orchestrator.run_due)

    def _dispatch_once(self) -> int:
        published, failed = self.dispatcher.dispatch_once()
        self.state.last_dispatch_at = datetime.now(UTC)
        self.state.published_total += published
        self.state.publish_failed_total += failed
        return published

    def _recover_workflows_once(self) -> int:
        recovered_steps, recovered_workflows = self.recovery.recover_once()
        self.state.last_recovery_at = datetime.now(UTC)
        self.state.recovered_steps_total += recovered_steps
        self.state.recovered_workflows_total += recovered_workflows
        return recovered_steps + recovered_workflows

    def _recover_operations_once(self) -> int:
        recovered = self.operation_recovery.recover_once()
        self.state.recovered_operations_total += recovered
        return recovered

    def close(self) -> None:
        self.database.dispose()
