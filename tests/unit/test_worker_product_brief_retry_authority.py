from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Literal

import pytest
from commercevision_application import (
    DurableNodeLifecycle,
    ProductBriefContinuation,
    ProductBriefGenerationAuthority,
    ProductBriefRecoveryClaim,
    RecoveryService,
    StaleProductBriefContinuation,
)
from commercevision_contracts import Settings
from commercevision_contracts.events import EventType, WorkflowRunRequestedPayload
from commercevision_contracts.workflow import product_brief_checkpoint_generation
from commercevision_domain import (
    AttemptStatus,
    StepStatus,
    StepType,
    Workflow,
    WorkflowAttempt,
    WorkflowStatus,
    WorkflowStep,
)
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from commercevision_worker.runtime import WorkerRuntime

WORKSPACE_ID = "catalog-retry-authority"
WORKFLOW_ID = "019fb001-0000-7000-8000-000000000001"
PRODUCT_ID = "019fb001-0000-7000-8000-000000000002"
PRODUCT_BRIEF_ID = "019fb001-0000-7000-8000-000000000003"
PRODUCT_BRIEF_VERSION_ID = "019fb001-0000-7000-8000-000000000004"
APPROVAL_ID = "019fb001-0000-7000-8000-000000000005"
INITIAL_STEP_ID = "019fb001-0000-7000-8000-000000000006"
ANALYSIS_TRACE_ID = "product-brief-analysis-trace"
INITIAL_STEP_LEASE_TOKEN = "product-brief-initial-step-lease"
CHECKPOINT_GENERATION = product_brief_checkpoint_generation(
    workspace_id=WORKSPACE_ID,
    product_brief_version_id=PRODUCT_BRIEF_VERSION_ID,
    initial_step_id=INITIAL_STEP_ID,
    initial_step_lease_token=INITIAL_STEP_LEASE_TOKEN,
)


class RecordingInbox:
    def __init__(self, event: OutboxEvent) -> None:
        self.event = event
        self.processed: list[tuple[str, str]] = []

    def claim(self, event_id: str) -> tuple[SimpleNamespace, OutboxEvent]:
        assert event_id == self.event.envelope.event_id
        return (
            SimpleNamespace(
                already_processed=False,
                dead=False,
                retry_not_ready=False,
                should_process=True,
                lease_token="inbox-lease",
                delivery_attempt=1,
            ),
            self.event,
        )

    def mark_processed(self, event_id: str, lease_token: str) -> None:
        self.processed.append((event_id, lease_token))

    def mark_permanent_failed(self, *_args: object, **_kwargs: object) -> None:
        pytest.fail("ProductBrief retry must not be dead-lettered")

    def schedule_retry(self, *_args: object, **_kwargs: object) -> None:
        pytest.fail("ProductBrief retry must not consume another Inbox retry")


class RecordingOutbox:
    def __init__(self) -> None:
        self.events: list[OutboxEvent] = []

    def add(self, event: OutboxEvent) -> None:
        self.events.append(event)


class RetryEventUnitOfWork:
    def __init__(
        self,
        *,
        workflow: Workflow,
        step: WorkflowStep,
        attempt: WorkflowAttempt,
        attempts: list[WorkflowAttempt] | None = None,
    ) -> None:
        self.workflow = workflow
        self.step = step
        self.attempt = attempt
        self.attempt_records = {
            item.id: item for item in (attempts if attempts is not None else [attempt])
        }
        self.outbox = RecordingOutbox()
        self.saved_steps: list[WorkflowStep] = []
        self.saved_attempts: list[WorkflowAttempt] = []
        self.saved_workflows: list[Workflow] = []
        self.commit_count = 0
        self.workflows = SimpleNamespace(
            get=lambda workflow_id, **_kwargs: (
                self.workflow if workflow_id == self.workflow.id else None
            ),
            save=self.saved_workflows.append,
        )
        self.steps = SimpleNamespace(
            get=lambda step_id, **_kwargs: self.step if step_id == self.step.id else None,
            save=self.saved_steps.append,
        )
        self.attempts = SimpleNamespace(
            get=lambda attempt_id, **_kwargs: self.attempt_records.get(attempt_id),
            get_latest_for_step=lambda step_id, **_kwargs: (
                self.attempt if step_id == self.step.id else None
            ),
            save=self.saved_attempts.append,
        )

    def __enter__(self) -> RetryEventUnitOfWork:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def commit(self) -> None:
        self.commit_count += 1


class BoundEventRouter:
    def __init__(self) -> None:
        self.handler: Any = None

    def resolve(self, _envelope: EventEnvelope):
        assert self.handler is not None
        return self.handler


class RecordingAgent:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


class RetryAuthorityLifecycle:
    def __init__(
        self,
        *,
        stale_reason: Literal["expired", "superseded"] | None = None,
    ) -> None:
        self.stale_reason = stale_reason
        self.calls: list[dict[str, object]] = []

    def recover_product_brief_continuation(
        self,
        **kwargs: object,
    ) -> ProductBriefRecoveryClaim:
        self.calls.append(kwargs)
        if self.stale_reason is not None:
            return ProductBriefRecoveryClaim(
                stale_reason=self.stale_reason,
                workflow_id=WORKFLOW_ID,
                workflow_version=17,
                workspace_id=WORKSPACE_ID,
                actor_id="reviewer",
                input_data={"fixture_config": {"count": 1}},
                current_node="execute_tool",
                continuation=None,
                node_claim=None,
                generation_authority=None,
            )
        continuation = ProductBriefContinuation(
            workspace_id=WORKSPACE_ID,
            product_brief_version_id=PRODUCT_BRIEF_VERSION_ID,
            product_brief_version_number=3,
            approval_id=APPROVAL_ID,
        )
        authority = ProductBriefGenerationAuthority(
            workspace_id=WORKSPACE_ID,
            workflow_id=WORKFLOW_ID,
            product_id=PRODUCT_ID,
            product_brief_id=PRODUCT_BRIEF_ID,
            product_brief_version_id=PRODUCT_BRIEF_VERSION_ID,
            product_brief_version_number=3,
            approval_id=APPROVAL_ID,
            initial_step_id=INITIAL_STEP_ID,
            checkpoint_generation=CHECKPOINT_GENERATION,
        )
        return ProductBriefRecoveryClaim(
            stale_reason=None,
            workflow_id=WORKFLOW_ID,
            workflow_version=17,
            workspace_id=WORKSPACE_ID,
            actor_id="reviewer",
            input_data={"fixture_config": {"count": 1}},
            current_node="execute_tool",
            continuation=continuation,
            node_claim=None,
            generation_authority=authority,
        )


def _retry_event() -> OutboxEvent:
    now = datetime.now(UTC)
    return OutboxEvent(
        envelope=EventEnvelope.create(
            event_type=EventType.WORKFLOW_RUN_REQUESTED.value,
            aggregate_type="workflow",
            aggregate_id=WORKFLOW_ID,
            aggregate_version=17,
            trace_id=ANALYSIS_TRACE_ID,
            payload=WorkflowRunRequestedPayload(
                workflow_id=WORKFLOW_ID,
                action="retry",
                reason="product-brief-generation-retry",
                product_brief_version_id=PRODUCT_BRIEF_VERSION_ID,
                product_brief_version_number=3,
            ).model_dump(mode="json", exclude_none=True),
            now=now,
        ),
        available_at=now,
        workspace_id=WORKSPACE_ID,
    )


def _runtime(
    *,
    event: OutboxEvent,
    lifecycle: RetryAuthorityLifecycle,
    agent: RecordingAgent,
) -> tuple[WorkerRuntime, RecordingInbox]:
    inbox = RecordingInbox(event)
    router = BoundEventRouter()
    runtime = WorkerRuntime(
        database=object(),  # type: ignore[arg-type]
        settings=Settings(environment="ci"),
        worker_id="product-brief-retry-worker",
        inbox=inbox,  # type: ignore[arg-type]
        agent=agent,  # type: ignore[arg-type]
        event_router=router,  # type: ignore[arg-type]
        operation_worker=object(),  # type: ignore[arg-type]
        operation_executors=object(),  # type: ignore[arg-type]
        object_storage=None,
        resources=(),
        lifecycle=lifecycle,  # type: ignore[arg-type]
    )
    router.handler = runtime._handle_workflow_event
    return runtime, inbox


def test_product_brief_retry_restores_exact_generation_with_analysis_trace() -> None:
    event = _retry_event()
    lifecycle = RetryAuthorityLifecycle()
    agent = RecordingAgent()
    runtime, inbox = _runtime(event=event, lifecycle=lifecycle, agent=agent)

    assert runtime.process_event(event.envelope.event_id) == "processed"

    assert lifecycle.calls == [
        {
            "workflow_id": WORKFLOW_ID,
            "expected_workflow_version": 17,
            "workspace_id": WORKSPACE_ID,
            "product_brief_version_id": PRODUCT_BRIEF_VERSION_ID,
            "product_brief_version_number": 3,
            "lease_owner": "product-brief-retry-worker",
            "trace_id": ANALYSIS_TRACE_ID,
        }
    ]
    assert len(agent.calls) == 1
    initial_state = agent.calls[0]["initial_state"]
    assert initial_state.trace_id == ANALYSIS_TRACE_ID
    assert initial_state.product_brief_checkpoint_generation == CHECKPOINT_GENERATION
    assert initial_state.product_brief_version_id == PRODUCT_BRIEF_VERSION_ID
    assert agent.calls[0]["preclaimed_step_id"] == INITIAL_STEP_ID
    assert inbox.processed == [(event.envelope.event_id, "inbox-lease")]


def test_retryable_product_brief_failure_emits_exact_continuation_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    workflow = Workflow.create(
        workspace_id=WORKSPACE_ID,
        created_by="reviewer",
        workflow_type="COMMERCE_IMAGE_GENERATION",
        input_data={"schema_version": "1.0", "product_id": PRODUCT_ID},
        retention=timedelta(hours=72),
        now=now,
    )
    for status in (
        WorkflowStatus.INGESTING,
        WorkflowStatus.UNDERSTANDING,
        WorkflowStatus.RETRIEVING,
        WorkflowStatus.PLANNING,
        WorkflowStatus.AWAITING_PLAN_APPROVAL,
        WorkflowStatus.GENERATING,
    ):
        workflow.transition(status, current_node="execute_tool", now=now)
    step = WorkflowStep.create(
        workflow_id=workflow.id,
        step_key="execute_tool:0",
        step_type=StepType.EXECUTE_TOOL,
        sequence=1,
        expected_workflow_version=workflow.version,
        max_attempts=3,
        now=now,
    )
    step.queue(now=now)
    lease_token = step.claim(
        owner="product-brief-retry-worker",
        lease_duration=timedelta(minutes=5),
        now=now,
    )
    step.start(lease_token=lease_token, now=now)
    attempt = WorkflowAttempt.create(
        workflow_id=workflow.id,
        step_id=step.id,
        attempt_number=step.attempt_count,
        idempotency_key="product-brief-retry-attempt",
        now=now,
    )
    attempt.mark_submitting(now=now)
    uow = RetryEventUnitOfWork(workflow=workflow, step=step, attempt=attempt)
    lifecycle = DurableNodeLifecycle(
        uow_factory=lambda: uow,  # type: ignore[arg-type,return-value]
        lease_duration=timedelta(minutes=5),
    )
    monkeypatch.setattr(
        lifecycle,
        "_completion_stale_reason",
        lambda **_kwargs: None,
    )
    continuation = ProductBriefContinuation(
        workspace_id=WORKSPACE_ID,
        product_brief_version_id=PRODUCT_BRIEF_VERSION_ID,
        product_brief_version_number=3,
        approval_id=APPROVAL_ID,
    )

    lifecycle.fail_node(
        workflow_id=workflow.id,
        step_id=step.id,
        attempt_id=attempt.id,
        lease_token=lease_token,
        trace_id=ANALYSIS_TRACE_ID,
        error=RuntimeError("retryable provider failure"),
        retryable=True,
        retry_delay=timedelta(seconds=2),
        expected_workflow_version=workflow.version,
        product_brief_continuation=continuation,
    )

    assert len(uow.outbox.events) == 1
    retry_event = uow.outbox.events[0]
    assert retry_event.envelope.event_type == EventType.WORKFLOW_RUN_REQUESTED
    assert retry_event.envelope.trace_id == ANALYSIS_TRACE_ID
    assert retry_event.envelope.payload == {
        "workflow_id": workflow.id,
        "action": "retry",
        "reason": "product-brief-generation-retry",
        "product_brief_version_id": PRODUCT_BRIEF_VERSION_ID,
        "product_brief_version_number": 3,
    }
    assert uow.saved_steps == [step]
    assert uow.saved_attempts == [attempt]
    assert attempt.status == AttemptStatus.RETRYABLE_FAILED
    assert attempt.error_class == "RuntimeError"
    assert attempt.error_message == "retryable provider failure"
    assert uow.commit_count == 1


def _running_attempt(
    *,
    max_attempts: int = 3,
) -> tuple[Workflow, WorkflowStep, WorkflowAttempt, str]:
    now = datetime.now(UTC)
    workflow = Workflow.create(
        workspace_id=WORKSPACE_ID,
        created_by="reviewer",
        workflow_type="COMMERCE_IMAGE_GENERATION",
        input_data={"schema_version": "1.0", "product_id": PRODUCT_ID},
        retention=timedelta(hours=72),
        now=now,
    )
    for status in (
        WorkflowStatus.INGESTING,
        WorkflowStatus.UNDERSTANDING,
        WorkflowStatus.RETRIEVING,
        WorkflowStatus.PLANNING,
        WorkflowStatus.AWAITING_PLAN_APPROVAL,
        WorkflowStatus.GENERATING,
    ):
        workflow.transition(status, current_node="execute_tool", now=now)
    step = WorkflowStep.create(
        workflow_id=workflow.id,
        step_key="execute_tool:0",
        step_type=StepType.EXECUTE_TOOL,
        sequence=1,
        expected_workflow_version=workflow.version,
        max_attempts=max_attempts,
        now=now,
    )
    step.queue(now=now)
    lease_token = step.claim(
        owner="product-brief-retry-worker",
        lease_duration=timedelta(minutes=5),
        now=now,
    )
    step.start(lease_token=lease_token, now=now)
    attempt = WorkflowAttempt.create(
        workflow_id=workflow.id,
        step_id=step.id,
        attempt_number=step.attempt_count,
        idempotency_key=f"attempt-{step.attempt_count}",
        now=now,
    )
    attempt.mark_submitting(now=now)
    return workflow, step, attempt, lease_token


def test_permanent_failure_settles_exact_attempt_in_same_unit_of_work() -> None:
    workflow, step, attempt, lease_token = _running_attempt()
    uow = RetryEventUnitOfWork(workflow=workflow, step=step, attempt=attempt)
    lifecycle = DurableNodeLifecycle(
        uow_factory=lambda: uow,  # type: ignore[arg-type,return-value]
        lease_duration=timedelta(minutes=5),
    )

    lifecycle.fail_node(
        workflow_id=workflow.id,
        step_id=step.id,
        attempt_id=attempt.id,
        lease_token=lease_token,
        trace_id=ANALYSIS_TRACE_ID,
        error=RuntimeError("permanent provider failure"),
        retryable=False,
        retry_delay=timedelta(seconds=2),
    )

    assert attempt.status == AttemptStatus.PERMANENT_FAILED
    assert attempt.completed_at is not None
    assert attempt.error_class == "RuntimeError"
    assert attempt.error_message == "permanent provider failure"
    assert step.status == StepStatus.FAILED
    assert workflow.status == WorkflowStatus.FAILED
    assert uow.saved_attempts == [attempt]
    assert uow.saved_steps == [step]
    assert uow.saved_workflows == [workflow]
    assert uow.commit_count == 1


def test_retry_budget_exhaustion_terminally_settles_exact_attempt() -> None:
    workflow, step, attempt, lease_token = _running_attempt(max_attempts=1)
    uow = RetryEventUnitOfWork(workflow=workflow, step=step, attempt=attempt)
    lifecycle = DurableNodeLifecycle(
        uow_factory=lambda: uow,  # type: ignore[arg-type,return-value]
        lease_duration=timedelta(minutes=5),
    )

    lifecycle.fail_node(
        workflow_id=workflow.id,
        step_id=step.id,
        attempt_id=attempt.id,
        lease_token=lease_token,
        trace_id=ANALYSIS_TRACE_ID,
        error=RuntimeError("retry budget exhausted"),
        retryable=True,
        retry_delay=timedelta(seconds=2),
    )

    assert attempt.status == AttemptStatus.PERMANENT_FAILED
    assert attempt.completed_at is not None
    assert step.status == StepStatus.FAILED
    assert workflow.status == WorkflowStatus.FAILED
    assert uow.outbox.events[0].envelope.event_type == EventType.WORKFLOW_FAILED
    assert uow.commit_count == 1


def test_stale_failure_cancels_exact_attempt_and_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow, step, attempt, lease_token = _running_attempt()
    uow = RetryEventUnitOfWork(workflow=workflow, step=step, attempt=attempt)
    lifecycle = DurableNodeLifecycle(
        uow_factory=lambda: uow,  # type: ignore[arg-type,return-value]
        lease_duration=timedelta(minutes=5),
    )
    monkeypatch.setattr(
        lifecycle,
        "_completion_stale_reason",
        lambda **_kwargs: "superseded",
    )
    continuation = ProductBriefContinuation(
        workspace_id=WORKSPACE_ID,
        product_brief_version_id=PRODUCT_BRIEF_VERSION_ID,
        product_brief_version_number=3,
        approval_id=APPROVAL_ID,
    )

    with pytest.raises(StaleProductBriefContinuation, match="superseded"):
        lifecycle.fail_node(
            workflow_id=workflow.id,
            step_id=step.id,
            attempt_id=attempt.id,
            lease_token=lease_token,
            trace_id=ANALYSIS_TRACE_ID,
            error=RuntimeError("superseded provider failure"),
            retryable=True,
            retry_delay=timedelta(seconds=2),
            expected_workflow_version=workflow.version,
            product_brief_continuation=continuation,
        )

    assert attempt.status == AttemptStatus.CANCELLED
    assert attempt.completed_at is not None
    assert step.status == StepStatus.CANCELLED
    assert step.completed_at is not None
    assert uow.saved_attempts == [attempt]
    assert uow.saved_steps == [step]
    assert uow.commit_count == 1


def test_failure_settles_only_the_exact_executing_attempt() -> None:
    workflow, step, first_attempt, first_lease_token = _running_attempt()
    now = datetime.now(UTC)
    step.fail_retryable(
        error_class="RuntimeError",
        error_message="first attempt interrupted",
        retry_at=now,
        lease_token=first_lease_token,
        now=now,
    )
    second_lease_token = step.claim(
        owner="product-brief-retry-worker",
        lease_duration=timedelta(minutes=5),
        now=now,
    )
    step.start(lease_token=second_lease_token, now=now)
    second_attempt = WorkflowAttempt.create(
        workflow_id=workflow.id,
        step_id=step.id,
        attempt_number=step.attempt_count,
        idempotency_key="attempt-2",
        now=now,
    )
    second_attempt.mark_submitting(now=now)
    later_observed_attempt = WorkflowAttempt.create(
        workflow_id=workflow.id,
        step_id=step.id,
        attempt_number=step.attempt_count + 1,
        idempotency_key="attempt-3",
        now=now,
    )
    later_observed_attempt.mark_submitting(now=now)
    uow = RetryEventUnitOfWork(
        workflow=workflow,
        step=step,
        attempt=later_observed_attempt,
        attempts=[first_attempt, second_attempt, later_observed_attempt],
    )
    lifecycle = DurableNodeLifecycle(
        uow_factory=lambda: uow,  # type: ignore[arg-type,return-value]
        lease_duration=timedelta(minutes=5),
    )

    lifecycle.fail_node(
        workflow_id=workflow.id,
        step_id=step.id,
        attempt_id=second_attempt.id,
        lease_token=second_lease_token,
        trace_id=ANALYSIS_TRACE_ID,
        error=RuntimeError("second attempt failed"),
        retryable=True,
        retry_delay=timedelta(seconds=2),
    )

    assert first_attempt.status == AttemptStatus.SUBMITTING
    assert first_attempt.error_class is None
    assert second_attempt.status == AttemptStatus.RETRYABLE_FAILED
    assert second_attempt.error_message == "second attempt failed"
    assert later_observed_attempt.status == AttemptStatus.SUBMITTING
    assert later_observed_attempt.error_class is None
    assert uow.saved_attempts == [second_attempt]
    assert uow.commit_count == 1


class RecoveryUnitOfWork:
    def __init__(
        self,
        *,
        workflow: Workflow,
        step: WorkflowStep,
        now: datetime,
    ) -> None:
        self.workflow = workflow
        self.step = step
        self.now = now
        self.outbox = RecordingOutbox()
        self.saved_steps: list[WorkflowStep] = []
        self.commit_count = 0
        self.steps = SimpleNamespace(
            list_expired_leases=lambda **_kwargs: [self.step],
            save=self.saved_steps.append,
        )
        self.workflows = SimpleNamespace(
            get=lambda workflow_id: self.workflow if workflow_id == self.workflow.id else None,
            list_recoverable=lambda **_kwargs: [],
        )
        self.outbox.has_unpublished = lambda **_kwargs: False  # type: ignore[attr-defined]

    def __enter__(self) -> RecoveryUnitOfWork:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def database_now(self) -> datetime:
        return self.now

    def commit(self) -> None:
        self.commit_count += 1


def test_generic_recovery_does_not_parse_product_brief_metadata_collision() -> None:
    now = datetime.now(UTC)
    expired_at = now - timedelta(minutes=2)
    workflow = Workflow.create(
        workspace_id=WORKSPACE_ID,
        created_by="fixture-user",
        workflow_type="FIXTURE_IMAGE_GENERATION",
        input_data={},
        retention=timedelta(hours=72),
        now=expired_at,
    )
    step = WorkflowStep.create(
        workflow_id=workflow.id,
        step_key="execute_tool:0",
        step_type=StepType.EXECUTE_TOOL,
        sequence=1,
        expected_workflow_version=workflow.version,
        max_attempts=3,
        input_data={"product_brief_generation": {}},
        now=expired_at,
    )
    step.queue(now=expired_at)
    lease_token = step.claim(
        owner="expired-worker",
        lease_duration=timedelta(minutes=1),
        now=expired_at,
    )
    step.start(lease_token=lease_token, now=expired_at)
    uow = RecoveryUnitOfWork(workflow=workflow, step=step, now=now)
    recovery = RecoveryService(
        uow_factory=lambda: uow,  # type: ignore[arg-type,return-value]
        batch_size=10,
        stale_after=timedelta(minutes=5),
    )

    assert recovery.recover_once() == (1, 0)

    assert step.status == StepStatus.RETRYABLE_FAILED
    assert uow.saved_steps == [step]
    assert len(uow.outbox.events) == 1
    assert uow.outbox.events[0].envelope.payload["reason"] == "expired_step_lease"
    assert uow.commit_count == 1


@pytest.mark.parametrize("stale_reason", ["expired", "superseded"])
def test_stale_product_brief_retry_is_a_processed_noop(
    stale_reason: Literal["expired", "superseded"],
) -> None:
    event = _retry_event()
    lifecycle = RetryAuthorityLifecycle(stale_reason=stale_reason)
    agent = RecordingAgent()
    runtime, inbox = _runtime(event=event, lifecycle=lifecycle, agent=agent)

    assert runtime.process_event(event.envelope.event_id) == "processed"

    assert lifecycle.calls[0]["trace_id"] == ANALYSIS_TRACE_ID
    assert agent.calls == []
    assert inbox.processed == [(event.envelope.event_id, "inbox-lease")]
