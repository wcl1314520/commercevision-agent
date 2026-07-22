from __future__ import annotations

from datetime import UTC, datetime, timedelta
from importlib import import_module

import pytest
from commercevision_application import (
    InboxCoordinator,
    OutboxDispatcher,
    WorkflowApplicationService,
)
from commercevision_application import execution as execution_module
from commercevision_application import reliability as reliability_module
from commercevision_contracts.events import (
    EventType,
    WorkflowRunRequestedPayload,
)
from commercevision_contracts.workflow import ApprovalRequest, WorkflowCreateRequest
from commercevision_domain import ApprovalDecision, ApprovalType
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from commercevision_persistence import (
    SqlAlchemyUnitOfWork,
    is_unit_of_work_active,
)
from commercevision_persistence.models import InboxMessageModel, OutboxEventModel
from commercevision_scheduler import runtime as scheduler_runtime
from commercevision_tool_runtime import (
    FixtureImageTool,
    ToolDefinition,
    ToolExecutionGateway,
    ToolRegistry,
)
from commercevision_tool_runtime.policy import ToolPolicy
from commercevision_worker.runtime import WorkerRuntime
from sqlalchemy import select

pytestmark = pytest.mark.integration
worker_module = import_module("commercevision_worker.celery_app")


class CapturingPublisher:
    def __init__(self) -> None:
        self.events: list[OutboxEvent] = []

    def publish_event(self, event: OutboxEvent) -> None:
        self.events.append(event)


class CapturingCeleryClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.conf = self

    def update(self, **_kwargs) -> None:
        return None

    def send_task(self, name: str, **kwargs) -> None:
        self.calls.append({"name": name, **kwargs})


class FailingCommitUnitOfWork(SqlAlchemyUnitOfWork):
    def commit(self) -> None:
        raise RuntimeError("durable retry commit unavailable")


@pytest.mark.parametrize(
    ("event_type", "schema_version", "reason"),
    [
        ("asset.event.never-registered", 1, "unknown_event_type"),
        ("workflow.run.requested", 99, "unsupported_schema_version"),
        ("asset.validation.requested", 1, "unhandled_event"),
        ("workflow.run.requested", 1, "malformed_event_payload"),
    ],
)
def test_unknown_or_unsupported_event_is_permanent_and_dead_lettered(
    integration_database,
    integration_settings,
    event_type: str,
    schema_version: int,
    reason: str,
) -> None:
    envelope = EventEnvelope.create(
        event_type=event_type,
        aggregate_type="asset",
        aggregate_id="unknown-event-asset",
        aggregate_version=1,
        trace_id="unknown-event-trace",
        payload={},
        schema_version=schema_version,
    )
    event = OutboxEvent(envelope=envelope, available_at=envelope.occurred_at)
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        uow.outbox.add(event)
        uow.commit()

    worker = WorkerRuntime.build(integration_settings)
    assert worker.process_event(event.envelope.event_id) == "dead-lettered"
    assert worker.process_event(event.envelope.event_id) == "dead-lettered"
    worker.close()

    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        dead_letter = uow.dead_letters.get(
            consumer=integration_settings.worker_consumer_name,
            message_id=event.envelope.event_id,
        )

    assert dead_letter is not None
    assert dead_letter.reason == reason
    assert dead_letter.error_class in {
        "MalformedEventPayloadError",
        "UnknownEventTypeError",
        "UnsupportedSchemaVersionError",
        "UnhandledEventError",
    }


@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        (
            EventType.WORKFLOW_NODE_STARTED,
            {
                "node": "validate_input",
                "step_id": "step-1",
                "step_key": "validate_input",
            },
        ),
        (
            EventType.WORKFLOW_NODE_COMPLETED,
            {
                "node": "understand_product",
                "completed_step_id": "step-1",
                "status": "UNDERSTANDING",
            },
        ),
        (
            EventType.WORKFLOW_HUMAN_INPUT_REQUIRED,
            {"step_id": "step-2", "step_key": "approve_plan:0"},
        ),
        (
            EventType.WORKFLOW_HUMAN_INPUT_RECEIVED,
            {"step_id": "step-2", "decision": "APPROVE"},
        ),
        (
            EventType.WORKFLOW_FAILED,
            {
                "workflow_id": "workflow-observed",
                "step_id": "step-3",
                "error_class": "FixtureFailure",
            },
        ),
        (
            EventType.WORKFLOW_CANCELLED,
            {"workflow_id": "workflow-observed"},
        ),
    ],
)
def test_known_phase1_observation_is_processed_without_false_dlq(
    integration_database,
    integration_settings,
    event_type: EventType,
    payload: dict[str, object],
) -> None:
    envelope = EventEnvelope.create(
        event_type=event_type,
        aggregate_type="workflow",
        aggregate_id="workflow-observed",
        aggregate_version=1,
        trace_id="observed-event-trace",
        payload=payload,
    )
    event = OutboxEvent(envelope=envelope, available_at=envelope.occurred_at)
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        uow.outbox.add(event)
        uow.commit()

    worker = WorkerRuntime.build(integration_settings)
    assert worker.process_event(event.envelope.event_id) == "processed"
    assert worker.process_event(event.envelope.event_id) == "duplicate"
    worker.close()

    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        dead_letter = uow.dead_letters.get(
            consumer=integration_settings.worker_consumer_name,
            message_id=event.envelope.event_id,
        )

    assert dead_letter is None


def test_handler_failure_uses_exact_mysql_retry_timing_and_exhausts_inbox_budget(
    integration_database,
    integration_settings,
    monkeypatch,
) -> None:
    current_time = datetime(2026, 7, 22, 8, 30, 0, 123456, tzinfo=UTC)

    class FrozenDateTime:
        @classmethod
        def now(cls, tz=None):
            del tz
            return current_time

    monkeypatch.setattr(reliability_module, "datetime", FrozenDateTime)
    settings = integration_settings.model_copy(
        update={
            "workflow_message_max_attempts": 2,
            "worker_message_retry_initial_seconds": 0.4,
            "worker_message_retry_max_seconds": 0.5,
        }
    )
    payload = WorkflowRunRequestedPayload(
        workflow_id="missing-workflow",
        action="start",
    )
    envelope = EventEnvelope.create(
        event_type=EventType.WORKFLOW_RUN_REQUESTED.value,
        aggregate_type="workflow",
        aggregate_id="missing-workflow",
        aggregate_version=1,
        trace_id="message-retry-trace",
        payload=payload.model_dump(mode="json", exclude_none=True),
        now=current_time,
    )
    event = OutboxEvent(envelope=envelope, available_at=current_time)
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        uow.outbox.add(event)
        uow.commit()

    publisher = CapturingPublisher()
    dispatcher = OutboxDispatcher(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory),
        publisher=publisher,
        owner="retry-scheduler",
        lease_duration=timedelta(seconds=30),
        batch_size=10,
    )
    worker = WorkerRuntime.build(settings)
    monkeypatch.setattr(worker_module, "_get_runtime", lambda: worker)

    assert dispatcher.dispatch_once() == (1, 0)
    assert worker_module.process_outbox_event.run(envelope.event_id) == "retry-scheduled"
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        first_retry = uow.outbox.get(envelope.event_id)
    assert first_retry is not None
    assert first_retry.published_at is None
    assert first_retry.available_at == current_time + timedelta(milliseconds=400)
    assert worker_module.process_outbox_event.run(envelope.event_id) == "retry-not-ready"
    with integration_database.engine.connect() as connection:
        assert (
            connection.scalar(
                select(InboxMessageModel.delivery_attempts).where(
                    InboxMessageModel.consumer == settings.worker_consumer_name,
                    InboxMessageModel.message_id == envelope.event_id,
                )
            )
            == 1
        )
    assert dispatcher.dispatch_once() == (0, 0)

    current_time += timedelta(milliseconds=400, microseconds=-1)
    assert dispatcher.dispatch_once() == (0, 0)
    current_time += timedelta(microseconds=1)
    assert dispatcher.dispatch_once() == (1, 0)
    assert worker_module.process_outbox_event.run(envelope.event_id) == "retry-scheduled"

    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        second_retry = uow.outbox.get(envelope.event_id)
    assert second_retry is not None
    assert second_retry.available_at == current_time + timedelta(milliseconds=500)

    current_time += timedelta(milliseconds=500)
    assert dispatcher.dispatch_once() == (1, 0)
    assert worker_module.process_outbox_event.run(envelope.event_id) == "dead-lettered"
    worker.close()

    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        dead_letter = uow.dead_letters.get(
            consumer=settings.worker_consumer_name,
            message_id=envelope.event_id,
        )
    with integration_database.engine.connect() as connection:
        inbox_status, delivery_attempts = connection.execute(
            select(InboxMessageModel.status, InboxMessageModel.delivery_attempts).where(
                InboxMessageModel.consumer == settings.worker_consumer_name,
                InboxMessageModel.message_id == envelope.event_id,
            )
        ).one()

    assert len(publisher.events) == 3
    assert inbox_status == "DEAD"
    assert delivery_attempts == 2
    assert dead_letter is not None
    assert dead_letter.attempt_count == 2
    assert dead_letter.reason == "message retry budget exhausted"


def test_failed_retry_transaction_raises_for_celery_transport_redelivery(
    integration_database,
    integration_settings,
    monkeypatch,
) -> None:
    current_time = datetime(2026, 7, 22, 9, 0, 0, 654321, tzinfo=UTC)

    class FrozenDateTime:
        @classmethod
        def now(cls, tz=None):
            del tz
            return current_time

    monkeypatch.setattr(reliability_module, "datetime", FrozenDateTime)
    settings = integration_settings.model_copy(
        update={
            "worker_message_retry_initial_seconds": 1.0,
            "worker_message_retry_max_seconds": 30.0,
        }
    )
    payload = WorkflowRunRequestedPayload(
        workflow_id="missing-workflow",
        action="start",
    )
    envelope = EventEnvelope.create(
        event_type=EventType.WORKFLOW_RUN_REQUESTED.value,
        aggregate_type="workflow",
        aggregate_id="missing-workflow",
        aggregate_version=1,
        trace_id="transport-fallback-trace",
        payload=payload.model_dump(mode="json", exclude_none=True),
        now=current_time,
    )
    event = OutboxEvent(envelope=envelope, available_at=current_time)
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        uow.outbox.add(event)
        uow.commit()

    dispatcher = OutboxDispatcher(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory),
        publisher=CapturingPublisher(),
        owner="transport-fallback-scheduler",
        lease_duration=timedelta(seconds=30),
        batch_size=10,
    )
    assert dispatcher.dispatch_once() == (1, 0)

    worker = WorkerRuntime.build(settings)
    failing_retry_coordinator = InboxCoordinator(
        uow_factory=lambda: FailingCommitUnitOfWork(integration_database.session_factory),
        consumer=settings.worker_consumer_name,
        owner=worker.worker_id,
        lease_duration=timedelta(seconds=settings.workflow_step_lease_seconds),
        max_attempts=settings.workflow_message_max_attempts,
        retry_initial=timedelta(seconds=settings.worker_message_retry_initial_seconds),
        retry_max=timedelta(seconds=settings.worker_message_retry_max_seconds),
    )
    monkeypatch.setattr(
        worker.inbox,
        "schedule_retry",
        failing_retry_coordinator.schedule_retry,
    )
    monkeypatch.setattr(worker_module, "_get_runtime", lambda: worker)

    with pytest.raises(RuntimeError, match="durable retry commit unavailable"):
        worker_module.process_outbox_event.run(envelope.event_id)
    worker.close()

    with integration_database.engine.connect() as connection:
        inbox_status = connection.scalar(
            select(InboxMessageModel.status).where(
                InboxMessageModel.consumer == settings.worker_consumer_name,
                InboxMessageModel.message_id == envelope.event_id,
            )
        )
        outbox_state = connection.execute(
            select(
                OutboxEventModel.published_at,
                OutboxEventModel.available_at,
            ).where(OutboxEventModel.id == envelope.event_id)
        ).one()

    assert inbox_status == "PROCESSING"
    assert outbox_state.published_at == current_time
    assert outbox_state.available_at == current_time


def test_fast_worker_retry_cannot_be_overwritten_by_dispatcher_publish_confirmation(
    integration_database,
    integration_settings,
    monkeypatch,
) -> None:
    current_time = datetime(2026, 7, 22, 9, 30, 0, 111222, tzinfo=UTC)

    class FrozenDateTime:
        @classmethod
        def now(cls, tz=None):
            del tz
            return current_time

    monkeypatch.setattr(reliability_module, "datetime", FrozenDateTime)
    settings = integration_settings.model_copy(
        update={
            "worker_message_retry_initial_seconds": 1.0,
            "worker_message_retry_max_seconds": 30.0,
        }
    )
    payload = WorkflowRunRequestedPayload(
        workflow_id="missing-fast-workflow",
        action="start",
    )
    envelope = EventEnvelope.create(
        event_type=EventType.WORKFLOW_RUN_REQUESTED.value,
        aggregate_type="workflow",
        aggregate_id="missing-fast-workflow",
        aggregate_version=1,
        trace_id="fast-worker-retry-trace",
        payload=payload.model_dump(mode="json", exclude_none=True),
        now=current_time,
    )
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        uow.outbox.add(OutboxEvent(envelope=envelope, available_at=current_time))
        uow.commit()

    worker = WorkerRuntime.build(settings)
    monkeypatch.setattr(worker_module, "_get_runtime", lambda: worker)

    class InlineTaskPublisher:
        results: list[str] = []

        def publish_event(self, event: OutboxEvent) -> None:
            self.results.append(worker_module.process_outbox_event.run(event.envelope.event_id))

    publisher = InlineTaskPublisher()
    dispatcher = OutboxDispatcher(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory),
        publisher=publisher,
        owner="inline-scheduler",
        lease_duration=timedelta(seconds=30),
        batch_size=10,
    )

    assert dispatcher.dispatch_once() == (1, 0)
    worker.close()

    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        scheduled = uow.outbox.get(envelope.event_id)

    assert publisher.results == ["retry-scheduled"]
    assert scheduled is not None
    assert scheduled.published_at is None
    assert scheduled.available_at == current_time + timedelta(seconds=1)


def test_normal_workflow_crosses_outbox_celery_worker_seam_without_false_dlq(
    integration_database,
    integration_settings,
    monkeypatch,
) -> None:
    settings = integration_settings.model_copy(
        update={
            "workflow_queue_name": "integration.workflow",
            "asset_queue_name": "integration.asset",
            "index_queue_name": "integration.index",
            "maintenance_queue_name": "integration.maintenance",
        }
    )
    client = CapturingCeleryClient()
    monkeypatch.setattr(scheduler_runtime, "Celery", lambda *_args, **_kwargs: client)
    worker = WorkerRuntime.build(settings)
    monkeypatch.setattr(worker_module, "_get_runtime", lambda: worker)

    service = WorkflowApplicationService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory)
    )
    dispatcher = OutboxDispatcher(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory),
        publisher=scheduler_runtime.CeleryMessagePublisher(settings),
        owner="full-seam-scheduler",
        lease_duration=timedelta(seconds=30),
        batch_size=100,
    )
    delivered_call_count = 0

    def dispatch_all_ready() -> list[str]:
        nonlocal delivered_call_count
        results: list[str] = []
        while True:
            published, failed = dispatcher.dispatch_once()
            assert failed == 0
            calls = client.calls[delivered_call_count:]
            delivered_call_count = len(client.calls)
            assert published == len(calls)
            if not calls:
                break
            for call in calls:
                assert call["name"] == "commercevision.process_outbox_event"
                assert call["queue"] == settings.workflow_queue_name
                args = call["args"]
                assert isinstance(args, list)
                results.append(worker_module.process_outbox_event.run(str(args[0])))
        return results

    created = service.create(
        request=WorkflowCreateRequest(
            input_data={"fixture_config": {"count": 2}},
        ),
        workspace_id="integration-full-seam",
        actor_id="integration-user",
        idempotency_key="full-seam-create-0001",
        trace_id="full-seam-trace",
    )
    assert set(dispatch_all_ready()) == {"processed"}

    awaiting_plan = service.get(
        workflow_id=created.id,
        workspace_id="integration-full-seam",
    )
    plan_step = [step for step in awaiting_plan.steps if step.step_type.value == "CREATE_PLAN"][-1]
    service.approve(
        workflow_id=created.id,
        workspace_id="integration-full-seam",
        actor_id="integration-user",
        approval_type=ApprovalType.CREATIVE_PLAN,
        request=ApprovalRequest(
            expected_workflow_version=awaiting_plan.version,
            subject_id=plan_step.output_data["creative_plan_ref"],
            subject_version=1,
            decision=ApprovalDecision.APPROVE,
        ),
        idempotency_key="full-seam-plan-approve-0001",
        trace_id="full-seam-trace",
    )
    assert set(dispatch_all_ready()) == {"processed"}

    awaiting_results = service.get(
        workflow_id=created.id,
        workspace_id="integration-full-seam",
    )
    evaluation_step = [
        step for step in awaiting_results.steps if step.step_type.value == "EVALUATE_RESULTS"
    ][-1]
    service.approve(
        workflow_id=created.id,
        workspace_id="integration-full-seam",
        actor_id="integration-user",
        approval_type=ApprovalType.RESULTS,
        request=ApprovalRequest(
            expected_workflow_version=awaiting_results.version,
            subject_id=evaluation_step.output_data["evaluation_report_ref"],
            subject_version=1,
            decision=ApprovalDecision.APPROVE,
        ),
        idempotency_key="full-seam-result-approve-0001",
        trace_id="full-seam-trace",
    )
    assert set(dispatch_all_ready()) == {"processed"}
    worker.close()

    completed = service.get(
        workflow_id=created.id,
        workspace_id="integration-full-seam",
    )
    events = service.events(
        workflow_id=created.id,
        workspace_id="integration-full-seam",
    )
    emitted_event_types = {event.event_type for event in events}
    assert completed.status.value == "COMPLETED"
    assert {
        EventType.WORKFLOW_RUN_REQUESTED,
        EventType.WORKFLOW_RESUME_REQUESTED,
        EventType.WORKFLOW_NODE_STARTED,
        EventType.WORKFLOW_NODE_COMPLETED,
        EventType.WORKFLOW_HUMAN_INPUT_REQUIRED,
        EventType.WORKFLOW_HUMAN_INPUT_RECEIVED,
    }.issubset(emitted_event_types)
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        assert all(
            uow.dead_letters.get(
                consumer=settings.worker_consumer_name,
                message_id=event.event_id,
            )
            is None
            for event in events
        )


@pytest.mark.parametrize(
    ("failure_mode", "expected_status", "expected_event_type"),
    [
        ("retryable", "GENERATING", EventType.WORKFLOW_RUN_REQUESTED),
        ("permanent", "FAILED", EventType.WORKFLOW_FAILED),
    ],
)
def test_phase1_durable_failure_does_not_create_second_worker_retry_schedule(
    integration_database,
    integration_settings,
    monkeypatch,
    failure_mode: str,
    expected_status: str,
    expected_event_type: EventType,
) -> None:
    current_time = datetime.now(UTC) + timedelta(seconds=1)

    class FrozenDateTime:
        @classmethod
        def now(cls, tz=None):
            del tz
            return current_time

    monkeypatch.setattr(reliability_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(execution_module, "datetime", FrozenDateTime)
    worker = WorkerRuntime.build(integration_settings)
    monkeypatch.setattr(worker_module, "_get_runtime", lambda: worker)
    publisher = CapturingPublisher()
    dispatcher = OutboxDispatcher(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory),
        publisher=publisher,
        owner="phase1-failure-scheduler",
        lease_duration=timedelta(seconds=30),
        batch_size=100,
    )
    delivered_event_count = 0

    def dispatch_all_ready() -> list[str]:
        nonlocal delivered_event_count
        results: list[str] = []
        while True:
            published, failed = dispatcher.dispatch_once()
            assert failed == 0
            events = publisher.events[delivered_event_count:]
            delivered_event_count = len(publisher.events)
            assert published == len(events)
            if not events:
                break
            results.extend(
                worker_module.process_outbox_event.run(event.envelope.event_id) for event in events
            )
        return results

    service = WorkflowApplicationService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory)
    )
    created = service.create(
        request=WorkflowCreateRequest(
            input_data={"fixture_config": {"fail": failure_mode}},
        ),
        workspace_id=f"integration-{failure_mode}-failure",
        actor_id="integration-user",
        idempotency_key=f"{failure_mode}-failure-create-0001",
        trace_id=f"{failure_mode}-failure-trace",
    )
    assert set(dispatch_all_ready()) == {"processed"}
    awaiting_plan = service.get(
        workflow_id=created.id,
        workspace_id=f"integration-{failure_mode}-failure",
    )
    plan_step = [step for step in awaiting_plan.steps if step.step_type.value == "CREATE_PLAN"][-1]
    service.approve(
        workflow_id=created.id,
        workspace_id=f"integration-{failure_mode}-failure",
        actor_id="integration-user",
        approval_type=ApprovalType.CREATIVE_PLAN,
        request=ApprovalRequest(
            expected_workflow_version=awaiting_plan.version,
            subject_id=plan_step.output_data["creative_plan_ref"],
            subject_version=1,
            decision=ApprovalDecision.APPROVE,
        ),
        idempotency_key=f"{failure_mode}-failure-approve-0001",
        trace_id=f"{failure_mode}-failure-trace",
    )
    resume_event = [
        event
        for event in service.events(
            workflow_id=created.id,
            workspace_id=f"integration-{failure_mode}-failure",
        )
        if event.event_type == EventType.WORKFLOW_RESUME_REQUESTED
    ][-1]

    current_time = datetime.now(UTC) + timedelta(seconds=1)
    failure_results = dispatch_all_ready()
    worker.close()

    current = service.get(
        workflow_id=created.id,
        workspace_id=f"integration-{failure_mode}-failure",
    )
    events = service.events(
        workflow_id=created.id,
        workspace_id=f"integration-{failure_mode}-failure",
    )
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        persisted_resume = uow.outbox.get(resume_event.event_id)
        dead_letter = uow.dead_letters.get(
            consumer=integration_settings.worker_consumer_name,
            message_id=resume_event.event_id,
        )

    assert "retry-scheduled" not in failure_results
    assert current.status.value == expected_status
    assert expected_event_type in {event.event_type for event in events}
    assert persisted_resume is not None
    assert persisted_resume.published_at is not None
    assert dead_letter is None
    run_events = [event for event in events if event.event_type == EventType.WORKFLOW_RUN_REQUESTED]
    assert len(run_events) == (2 if failure_mode == "retryable" else 1)


def test_worker_restart_duplicate_delivery_and_human_resume(
    integration_database, integration_settings
) -> None:
    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(integration_database.session_factory)

    service = WorkflowApplicationService(uow_factory=uow_factory)
    created = service.create(
        request=WorkflowCreateRequest(
            input_data={"fixture_config": {"count": 2}},
        ),
        workspace_id="integration-runtime",
        actor_id="integration-user",
        idempotency_key="runtime-create-0001",
        trace_id="runtime-trace",
    )
    initial_event = service.events(
        workflow_id=created.id,
        workspace_id="integration-runtime",
    )[0]

    first_worker = WorkerRuntime.build(integration_settings)
    assert first_worker.process_event(initial_event.event_id) == "processed"
    assert first_worker.process_event(initial_event.event_id) == "duplicate"
    first_worker.close()

    waiting_plan = service.get(
        workflow_id=created.id,
        workspace_id="integration-runtime",
    )
    assert waiting_plan.status.value == "AWAITING_PLAN_APPROVAL"
    plan_step = [step for step in waiting_plan.steps if step.step_type.value == "CREATE_PLAN"][-1]
    plan_response = service.approve(
        workflow_id=created.id,
        workspace_id="integration-runtime",
        actor_id="integration-user",
        approval_type=ApprovalType.CREATIVE_PLAN,
        request=ApprovalRequest(
            expected_workflow_version=waiting_plan.version,
            subject_id=plan_step.output_data["creative_plan_ref"],
            subject_version=1,
            decision=ApprovalDecision.APPROVE,
        ),
        idempotency_key="runtime-plan-approve-0001",
        trace_id="runtime-trace",
    )
    resume_plan = [
        event
        for event in service.events(
            workflow_id=created.id,
            workspace_id="integration-runtime",
        )
        if event.event_type == "workflow.resume.requested"
    ][-1]

    second_worker = WorkerRuntime.build(integration_settings)
    assert second_worker.process_event(resume_plan.event_id) == "processed"
    second_worker.close()
    awaiting_results = service.get(
        workflow_id=created.id,
        workspace_id="integration-runtime",
    )
    assert awaiting_results.status.value == "AWAITING_RESULT_APPROVAL"
    assert len(awaiting_results.attempts) == 1

    evaluation_step = [
        step for step in awaiting_results.steps if step.step_type.value == "EVALUATE_RESULTS"
    ][-1]
    result_response = service.approve(
        workflow_id=created.id,
        workspace_id="integration-runtime",
        actor_id="integration-user",
        approval_type=ApprovalType.RESULTS,
        request=ApprovalRequest(
            expected_workflow_version=awaiting_results.version,
            subject_id=evaluation_step.output_data["evaluation_report_ref"],
            subject_version=1,
            decision=ApprovalDecision.APPROVE,
        ),
        idempotency_key="runtime-result-approve-0001",
        trace_id="runtime-trace",
    )
    resume_result = [
        event
        for event in service.events(
            workflow_id=created.id,
            workspace_id="integration-runtime",
        )
        if event.event_type == "workflow.resume.requested"
    ][-1]
    third_worker = WorkerRuntime.build(integration_settings)
    assert third_worker.process_event(resume_result.event_id) == "processed"
    assert third_worker.process_event(resume_result.event_id) == "duplicate"
    third_worker.close()

    completed = service.get(
        workflow_id=created.id,
        workspace_id="integration-runtime",
    )
    assert completed.status.value == "COMPLETED"
    assert len(completed.attempts) == 1
    assert completed.result_data["export_ref"].startswith("fixture://exports/")
    assert plan_response.id == created.id
    assert result_response.id == created.id


def test_tool_execution_never_runs_inside_uow(integration_database) -> None:
    observed: list[bool] = []

    fixture = FixtureImageTool()

    def implementation(context, invocation):
        observed.append(is_unit_of_work_active())
        return fixture(context, invocation)

    registry = ToolRegistry(
        [
            ToolDefinition(
                name=fixture.name,
                version=fixture.version,
                description="transaction boundary test",
                input_schema={},
                output_schema={},
                implementation=implementation,
            )
        ]
    )
    gateway = ToolExecutionGateway(
        registry=registry,
        policy=ToolPolicy(
            version="tool-policy-v1",
            allowed_tools=frozenset({fixture.name}),
            transaction_active=is_unit_of_work_active,
        ),
    )
    from commercevision_tool_runtime import ToolExecutionContext, ToolInvocation

    context = ToolExecutionContext(
        workflow_id="workflow",
        workspace_id="workspace",
        actor_id="user",
        trace_id="trace",
        idempotency_key="key",
        policy_version="tool-policy-v1",
    )
    gateway.execute(
        context=context,
        invocation=ToolInvocation(
            tool_name=fixture.name,
            tool_version=fixture.version,
            arguments={"count": 1},
            idempotency_key="key",
            policy_version="tool-policy-v1",
            reason="boundary test",
        ),
    )
    assert observed == [False]
