from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Lock

import pytest
from commercevision_application import (
    DurableNodeLifecycle,
    OutboxDispatcher,
    RecoveryService,
    WorkflowApplicationService,
)
from commercevision_application import execution as execution_module
from commercevision_contracts.workflow import WorkflowCreateRequest
from commercevision_domain import StepType, WorkflowStatus
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from commercevision_domain.workflow.errors import RetryNotReadyError
from commercevision_persistence import SqlAlchemyUnitOfWork
from commercevision_persistence.models import MYSQL_DATETIME_FSP, Base, UTCDateTime
from sqlalchemy import text, update

pytestmark = pytest.mark.integration


class CollectingPublisher:
    def __init__(self) -> None:
        self.ids: list[str] = []
        self._lock = Lock()

    def publish_event(self, event: OutboxEvent) -> None:
        with self._lock:
            self.ids.append(event.envelope.event_id)


def test_all_runtime_datetime_columns_use_microsecond_precision(integration_database) -> None:
    expected = {
        (table.name, column.name)
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if isinstance(column.type, UTCDateTime)
    }
    with integration_database.engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT TABLE_NAME, COLUMN_NAME, DATETIME_PRECISION
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND DATA_TYPE = 'datetime'
                """
            )
        ).all()
    actual = {(table_name, column_name) for table_name, column_name, _ in rows}

    assert actual == expected
    assert {precision for _, _, precision in rows} == {MYSQL_DATETIME_FSP}


def test_datetime_round_trip_preserves_microseconds(integration_database) -> None:
    now = datetime(2026, 7, 22, 3, 15, 56, 516123, tzinfo=UTC)
    event = OutboxEvent(
        envelope=EventEnvelope.create(
            event_type="integration.datetime-round-trip",
            aggregate_type="integration",
            aggregate_id="datetime-round-trip",
            aggregate_version=1,
            trace_id="datetime-round-trip",
            payload={},
            now=now,
        ),
        available_at=now,
    )
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        uow.outbox.add(event)
        uow.commit()
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        restored = uow.outbox.get(event.envelope.event_id)

    assert restored is not None
    assert restored.envelope.occurred_at == now
    assert restored.available_at == now


def test_outbox_is_claimable_at_the_exact_available_microsecond(integration_database) -> None:
    now = datetime(2026, 7, 22, 3, 15, 56, 516000, tzinfo=UTC)
    event = OutboxEvent(
        envelope=EventEnvelope.create(
            event_type="integration.outbox-boundary",
            aggregate_type="integration",
            aggregate_id="outbox-boundary",
            aggregate_version=1,
            trace_id="outbox-boundary",
            payload={},
            now=now,
        ),
        available_at=now,
    )
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        uow.outbox.add(event)
        uow.commit()
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        claimed = uow.outbox.claim_ready(
            now=now,
            owner="boundary-scheduler",
            lease_duration=timedelta(seconds=30),
            limit=1,
        )
        uow.commit()

    assert [item.envelope.event_id for item in claimed] == [event.envelope.event_id]


def test_inbox_lease_expires_at_the_exact_microsecond(integration_database) -> None:
    claimed_at = datetime(2026, 7, 22, 3, 15, 56, 516000, tzinfo=UTC)
    lease_duration = timedelta(milliseconds=400)
    expires_at = claimed_at + lease_duration
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        first = uow.inbox.claim(
            consumer="lease-boundary",
            message_id="message-1",
            owner="worker-a",
            now=claimed_at,
            lease_duration=lease_duration,
            max_attempts=3,
        )
        uow.commit()
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        active = uow.inbox.claim(
            consumer="lease-boundary",
            message_id="message-1",
            owner="worker-b",
            now=expires_at - timedelta(microseconds=1),
            lease_duration=lease_duration,
            max_attempts=3,
        )
        uow.commit()
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        expired = uow.inbox.claim(
            consumer="lease-boundary",
            message_id="message-1",
            owner="worker-b",
            now=expires_at,
            lease_duration=lease_duration,
            max_attempts=3,
        )
        uow.commit()

    assert first.should_process is True
    assert active.should_process is False
    assert expired.should_process is True
    assert expired.delivery_attempt == 2


def test_step_retry_is_ready_at_the_exact_microsecond(
    integration_database,
    monkeypatch,
) -> None:
    current_time = datetime(2026, 7, 22, 3, 15, 56, 516000, tzinfo=UTC)

    class FrozenDateTime:
        @classmethod
        def now(cls, tz=None):
            del tz
            return current_time

    monkeypatch.setattr(execution_module, "datetime", FrozenDateTime)
    service = WorkflowApplicationService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory)
    )
    workflow = service.create(
        request=WorkflowCreateRequest(input_data={}),
        workspace_id="integration-retry-boundary",
        actor_id="user",
        idempotency_key="retry-boundary-create-0001",
        trace_id="retry-boundary",
    )
    lifecycle = DurableNodeLifecycle(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory),
        lease_duration=timedelta(seconds=30),
    )
    claim = lifecycle.begin_node(
        workflow_id=workflow.id,
        expected_workflow_version=workflow.version,
        step_key="retry-boundary",
        step_type=StepType.VALIDATE_INPUT,
        running_state=WorkflowStatus.INGESTING,
        node_name="retry-boundary",
        lease_owner="worker-a",
        trace_id="retry-boundary",
    )
    assert claim.lease_token is not None
    lifecycle.fail_node(
        workflow_id=workflow.id,
        step_id=claim.step_id,
        lease_token=claim.lease_token,
        trace_id="retry-boundary",
        error=RuntimeError("retry"),
        retryable=True,
        retry_delay=timedelta(milliseconds=400),
    )

    current_time += timedelta(milliseconds=400, microseconds=-1)
    with pytest.raises(RetryNotReadyError):
        lifecycle.begin_node(
            workflow_id=workflow.id,
            expected_workflow_version=claim.workflow_version,
            step_key="retry-boundary",
            step_type=StepType.VALIDATE_INPUT,
            running_state=WorkflowStatus.INGESTING,
            node_name="retry-boundary",
            lease_owner="worker-b",
            trace_id="retry-boundary",
        )

    current_time += timedelta(microseconds=1)
    retry_claim = lifecycle.begin_node(
        workflow_id=workflow.id,
        expected_workflow_version=claim.workflow_version,
        step_key="retry-boundary",
        step_type=StepType.VALIDATE_INPUT,
        running_state=WorkflowStatus.INGESTING,
        node_name="retry-boundary",
        lease_owner="worker-b",
        trace_id="retry-boundary",
    )

    assert retry_claim.already_completed is False
    assert retry_claim.lease_token is not None


def test_concurrent_outbox_claim_has_no_duplicate_claims(
    integration_database,
) -> None:
    now = datetime.now(UTC)
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        for index in range(20):
            uow.outbox.add(
                OutboxEvent(
                    envelope=EventEnvelope.create(
                        event_type="integration.outbox",
                        aggregate_type="integration",
                        aggregate_id=f"aggregate-{index}",
                        aggregate_version=1,
                        trace_id="integration-trace",
                        payload={"index": index},
                        now=now,
                    ),
                    available_at=now,
                )
            )
        uow.commit()

    publisher = CollectingPublisher()

    def run(owner: str) -> tuple[int, int]:
        dispatcher = OutboxDispatcher(
            uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory),
            publisher=publisher,
            owner=owner,
            lease_duration=timedelta(seconds=30),
            batch_size=20,
        )
        return dispatcher.dispatch_once()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run, ["scheduler-a", "scheduler-b"]))

    assert len(publisher.ids) == 20
    assert len(set(publisher.ids)) == 20


def test_inbox_retry_budget_creates_dead_letter(integration_database, integration_settings) -> None:
    service = WorkflowApplicationService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory)
    )
    workflow = service.create(
        request=WorkflowCreateRequest(input_data={}),
        workspace_id="integration-dlq",
        actor_id="user",
        idempotency_key="dlq-create-0001",
        trace_id="dlq-trace",
    )
    event = service.events(workflow_id=workflow.id, workspace_id="integration-dlq")[0]
    from commercevision_application import InboxCoordinator

    coordinator = InboxCoordinator(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory),
        consumer="integration-consumer",
        owner="integration-owner",
        lease_duration=timedelta(seconds=30),
        max_attempts=2,
    )
    first, _ = coordinator.claim(event.event_id)
    coordinator.mark_failed(event.event_id, first.lease_token, RuntimeError("one"))
    second, _ = coordinator.claim(event.event_id)
    coordinator.mark_failed(event.event_id, second.lease_token, RuntimeError("two"))
    third, _ = coordinator.claim(event.event_id)
    assert third.dead is True


def test_recovery_requeues_expired_step(integration_database, integration_settings) -> None:
    from commercevision_persistence.models import WorkflowStepModel

    service = WorkflowApplicationService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory)
    )
    workflow = service.create(
        request=WorkflowCreateRequest(input_data={}),
        workspace_id="integration-recovery",
        actor_id="user",
        idempotency_key="recovery-create-0001",
        trace_id="recovery-trace",
    )
    lifecycle = DurableNodeLifecycle(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory),
        lease_duration=timedelta(seconds=30),
    )
    claim = lifecycle.begin_node(
        workflow_id=workflow.id,
        expected_workflow_version=workflow.version,
        step_key="validate_input",
        step_type=StepType.VALIDATE_INPUT,
        running_state=WorkflowStatus.INGESTING,
        node_name="validate_input",
        lease_owner="crashed-worker",
        trace_id="recovery-trace",
    )
    with integration_database.engine.begin() as connection:
        connection.execute(
            update(WorkflowStepModel)
            .where(WorkflowStepModel.id == claim.step_id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    recovery = RecoveryService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory),
        batch_size=20,
        stale_after=timedelta(days=1),
    )
    recovered_steps, _ = recovery.recover_once()
    assert recovered_steps == 1
    current = service.get(workflow_id=workflow.id, workspace_id="integration-recovery")
    assert current.steps[0].status.value == "RETRYABLE_FAILED"
