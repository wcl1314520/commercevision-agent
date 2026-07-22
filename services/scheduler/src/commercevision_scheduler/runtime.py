"""Scheduler composition for Outbox publication and recovery scans."""

from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from celery import Celery
from commercevision_application import (
    EventRoutingError,
    OutboxDispatcher,
    RecoveryService,
    build_event_routing_registry,
)
from commercevision_contracts import Settings
from commercevision_contracts.events import EventQueue
from commercevision_domain.messaging import OutboxEvent
from commercevision_persistence import Database, SqlAlchemyUnitOfWork, create_database


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
class SchedulerState:
    last_dispatch_at: datetime | None = None
    last_recovery_at: datetime | None = None
    last_error: str | None = None
    published_total: int = 0
    publish_failed_total: int = 0
    recovered_steps_total: int = 0
    recovered_workflows_total: int = 0


class SchedulerRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database: Database = create_database(settings)

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(self.database.session_factory)

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
        self.state = SchedulerState()

    async def run(self) -> None:
        next_recovery = 0.0
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(self.settings.scheduler_poll_seconds)
            try:
                published, failed = await asyncio.to_thread(self.dispatcher.dispatch_once)
                self.state.last_dispatch_at = datetime.now(UTC)
                self.state.published_total += published
                self.state.publish_failed_total += failed
                now = loop.time()
                if now >= next_recovery:
                    recovered_steps, recovered_workflows = await asyncio.to_thread(
                        self.recovery.recover_once
                    )
                    self.state.last_recovery_at = datetime.now(UTC)
                    self.state.recovered_steps_total += recovered_steps
                    self.state.recovered_workflows_total += recovered_workflows
                    next_recovery = now + self.settings.scheduler_recovery_interval_seconds
                self.state.last_error = None
            except Exception as exc:
                self.state.last_error = f"{type(exc).__name__}: {exc}"

    def close(self) -> None:
        self.database.dispose()
