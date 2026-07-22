from datetime import UTC, datetime, timedelta

import pytest
from commercevision_domain import (
    AttemptStatus,
    ConcurrencyError,
    InvalidTransitionError,
    LeaseConflictError,
    StepType,
    Workflow,
    WorkflowAttempt,
    WorkflowStatus,
    WorkflowStep,
)


def test_workflow_happy_path_and_terminal_invariant() -> None:
    workflow = Workflow.create(
        workspace_id="workspace",
        created_by="user",
        workflow_type="fixture",
        input_data={},
        retention=timedelta(hours=72),
    )
    path = [
        WorkflowStatus.INGESTING,
        WorkflowStatus.UNDERSTANDING,
        WorkflowStatus.RETRIEVING,
        WorkflowStatus.PLANNING,
        WorkflowStatus.AWAITING_PLAN_APPROVAL,
        WorkflowStatus.GENERATING,
        WorkflowStatus.EVALUATING,
        WorkflowStatus.AWAITING_RESULT_APPROVAL,
        WorkflowStatus.EXPORTING,
        WorkflowStatus.COMPLETED,
    ]
    for target in path:
        workflow.transition(target)

    assert workflow.status == WorkflowStatus.COMPLETED
    assert workflow.version == 11
    with pytest.raises(InvalidTransitionError):
        workflow.transition(WorkflowStatus.GENERATING)


def test_workflow_rejects_stale_version_and_illegal_transition() -> None:
    workflow = Workflow.create(
        workspace_id="workspace",
        created_by="user",
        workflow_type="fixture",
        input_data={},
        retention=timedelta(hours=72),
    )
    with pytest.raises(ConcurrencyError):
        workflow.transition(WorkflowStatus.INGESTING, expected_version=2)
    with pytest.raises(InvalidTransitionError):
        workflow.transition(WorkflowStatus.GENERATING)


def test_step_lease_recovery_and_token_validation() -> None:
    now = datetime.now(UTC)
    step = WorkflowStep.create(
        workflow_id="workflow",
        step_key="execute:0",
        step_type=StepType.EXECUTE_TOOL,
        sequence=1,
        expected_workflow_version=1,
        max_attempts=3,
        now=now,
    )
    step.queue(now=now)
    token = step.claim(
        owner="worker-a",
        lease_duration=timedelta(seconds=30),
        now=now,
    )
    step.start(lease_token=token, now=now)

    with pytest.raises(LeaseConflictError):
        step.succeed(lease_token="wrong-token", now=now)

    expired_at = now + timedelta(seconds=31)
    step.recover_expired_lease(retry_at=expired_at, now=expired_at)
    assert step.status.value == "RETRYABLE_FAILED"
    assert step.lease_token is None

    new_token = step.claim(
        owner="worker-b",
        lease_duration=timedelta(seconds=30),
        now=expired_at,
    )
    step.start(lease_token=new_token, now=expired_at)
    step.succeed(lease_token=new_token, output_data={"ok": True}, now=expired_at)
    assert step.status.value == "SUCCEEDED"
    assert step.attempt_count == 2


def test_attempt_unknown_requires_reconciliation_path() -> None:
    attempt = WorkflowAttempt.create(
        workflow_id="workflow",
        step_id="step",
        attempt_number=1,
        idempotency_key="key",
    )
    attempt.mark_submitting()
    attempt.transition(AttemptStatus.UNKNOWN)
    attempt.transition(AttemptStatus.SUBMITTED)
    attempt.transition(AttemptStatus.POLLING)
    attempt.succeed(result_data={"asset_ref": "fixture://asset"})

    assert attempt.status == AttemptStatus.SUCCEEDED
    assert attempt.result_data == {"asset_ref": "fixture://asset"}
