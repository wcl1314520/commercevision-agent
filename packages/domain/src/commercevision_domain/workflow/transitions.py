"""Explicit state transition maps used by domain entities."""

from collections.abc import Mapping
from enum import StrEnum

from .enums import AttemptStatus, RetentionStatus, StepStatus, WorkflowStatus
from .errors import InvalidTransitionError

WORKFLOW_TRANSITIONS: Mapping[WorkflowStatus, frozenset[WorkflowStatus]] = {
    WorkflowStatus.DRAFT: frozenset({WorkflowStatus.INGESTING, WorkflowStatus.CANCELLED}),
    WorkflowStatus.INGESTING: frozenset(
        {WorkflowStatus.UNDERSTANDING, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED}
    ),
    WorkflowStatus.UNDERSTANDING: frozenset(
        {
            WorkflowStatus.AWAITING_PRODUCT_CONFIRMATION,
            WorkflowStatus.RETRIEVING,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        }
    ),
    WorkflowStatus.AWAITING_PRODUCT_CONFIRMATION: frozenset(
        {WorkflowStatus.RETRIEVING, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED}
    ),
    WorkflowStatus.RETRIEVING: frozenset(
        {WorkflowStatus.PLANNING, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED}
    ),
    WorkflowStatus.PLANNING: frozenset(
        {WorkflowStatus.AWAITING_PLAN_APPROVAL, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED}
    ),
    WorkflowStatus.AWAITING_PLAN_APPROVAL: frozenset(
        {
            WorkflowStatus.PLANNING,
            WorkflowStatus.GENERATING,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        }
    ),
    WorkflowStatus.GENERATING: frozenset(
        {WorkflowStatus.EVALUATING, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED}
    ),
    WorkflowStatus.EVALUATING: frozenset(
        {
            WorkflowStatus.REPAIRING,
            WorkflowStatus.AWAITING_RESULT_APPROVAL,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        }
    ),
    WorkflowStatus.REPAIRING: frozenset(
        {
            WorkflowStatus.GENERATING,
            WorkflowStatus.AWAITING_RESULT_APPROVAL,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        }
    ),
    WorkflowStatus.AWAITING_RESULT_APPROVAL: frozenset(
        {
            WorkflowStatus.GENERATING,
            WorkflowStatus.EXPORTING,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        }
    ),
    WorkflowStatus.EXPORTING: frozenset(
        {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED}
    ),
    WorkflowStatus.COMPLETED: frozenset(),
    WorkflowStatus.FAILED: frozenset(),
    WorkflowStatus.CANCELLED: frozenset(),
}

RETENTION_TRANSITIONS: Mapping[RetentionStatus, frozenset[RetentionStatus]] = {
    RetentionStatus.ACTIVE: frozenset({RetentionStatus.EXPIRING}),
    RetentionStatus.EXPIRING: frozenset({RetentionStatus.DELETING}),
    RetentionStatus.DELETING: frozenset({RetentionStatus.EXPIRED}),
    RetentionStatus.EXPIRED: frozenset(),
}

STEP_TRANSITIONS: Mapping[StepStatus, frozenset[StepStatus]] = {
    StepStatus.PENDING: frozenset({StepStatus.QUEUED, StepStatus.CANCELLED}),
    StepStatus.QUEUED: frozenset({StepStatus.CLAIMED, StepStatus.CANCELLED}),
    StepStatus.CLAIMED: frozenset(
        {StepStatus.RUNNING, StepStatus.QUEUED, StepStatus.RETRYABLE_FAILED, StepStatus.CANCELLED}
    ),
    StepStatus.RUNNING: frozenset(
        {
            StepStatus.WAITING_HUMAN,
            StepStatus.SUCCEEDED,
            StepStatus.RETRYABLE_FAILED,
            StepStatus.FAILED,
            StepStatus.CANCELLED,
        }
    ),
    StepStatus.WAITING_HUMAN: frozenset(
        {StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.CANCELLED}
    ),
    StepStatus.RETRYABLE_FAILED: frozenset(
        {StepStatus.QUEUED, StepStatus.FAILED, StepStatus.CANCELLED}
    ),
    StepStatus.SUCCEEDED: frozenset(),
    StepStatus.FAILED: frozenset(),
    StepStatus.CANCELLED: frozenset(),
}

ATTEMPT_TRANSITIONS: Mapping[AttemptStatus, frozenset[AttemptStatus]] = {
    AttemptStatus.CREATED: frozenset({AttemptStatus.SUBMITTING, AttemptStatus.CANCELLED}),
    AttemptStatus.SUBMITTING: frozenset(
        {
            AttemptStatus.SUBMITTED,
            AttemptStatus.SUCCEEDED,
            AttemptStatus.UNKNOWN,
            AttemptStatus.RETRYABLE_FAILED,
            AttemptStatus.PERMANENT_FAILED,
            AttemptStatus.CANCELLED,
        }
    ),
    AttemptStatus.SUBMITTED: frozenset(
        {
            AttemptStatus.POLLING,
            AttemptStatus.SUCCEEDED,
            AttemptStatus.UNKNOWN,
            AttemptStatus.RETRYABLE_FAILED,
            AttemptStatus.PERMANENT_FAILED,
            AttemptStatus.CANCELLED,
        }
    ),
    AttemptStatus.POLLING: frozenset(
        {
            AttemptStatus.SUCCEEDED,
            AttemptStatus.UNKNOWN,
            AttemptStatus.RETRYABLE_FAILED,
            AttemptStatus.PERMANENT_FAILED,
            AttemptStatus.CANCELLED,
        }
    ),
    AttemptStatus.UNKNOWN: frozenset(
        {
            AttemptStatus.SUBMITTED,
            AttemptStatus.POLLING,
            AttemptStatus.SUCCEEDED,
            AttemptStatus.RETRYABLE_FAILED,
            AttemptStatus.PERMANENT_FAILED,
            AttemptStatus.CANCELLED,
        }
    ),
    AttemptStatus.RETRYABLE_FAILED: frozenset(
        {AttemptStatus.SUBMITTING, AttemptStatus.PERMANENT_FAILED, AttemptStatus.CANCELLED}
    ),
    AttemptStatus.SUCCEEDED: frozenset(),
    AttemptStatus.PERMANENT_FAILED: frozenset(),
    AttemptStatus.CANCELLED: frozenset(),
}


def assert_transition[TState: StrEnum](
    current: TState,
    target: TState,
    transitions: Mapping[TState, frozenset[TState]],
) -> None:
    if target == current:
        return
    if target not in transitions[current]:
        raise InvalidTransitionError(f"illegal transition: {current.value} -> {target.value}")
