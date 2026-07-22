"""Durable node lifecycle around transaction-free Agent node work."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from commercevision_domain import (
    AttemptStatus,
    LeaseConflictError,
    NotFoundError,
    StepStatus,
    StepType,
    WorkflowAttempt,
    WorkflowStatus,
    WorkflowStep,
)
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from commercevision_domain.workflow.errors import RetryNotReadyError
from commercevision_tool_runtime import ToolResult

from .ports import UnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class NodeClaim:
    workflow_id: str
    workflow_version: int
    step_id: str
    step_key: str
    lease_token: str | None
    already_completed: bool
    output_data: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class HumanWait:
    workflow_id: str
    workflow_version: int
    step_id: str
    already_completed: bool
    output_data: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class AttemptClaim:
    attempt_id: str
    attempt_number: int
    already_completed: bool
    result_data: dict[str, Any] | None
    provider_request_id: str | None


class DurableNodeLifecycle:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        lease_duration: timedelta,
        default_max_attempts: int = 3,
    ) -> None:
        self._uow_factory = uow_factory
        self._lease_duration = lease_duration
        self._default_max_attempts = default_max_attempts

    def begin_node(
        self,
        *,
        workflow_id: str,
        expected_workflow_version: int,
        step_key: str,
        step_type: StepType,
        running_state: WorkflowStatus,
        node_name: str,
        lease_owner: str,
        trace_id: str,
        input_data: dict[str, Any] | None = None,
    ) -> NodeClaim:
        now = datetime.now(UTC)
        with self._uow_factory() as uow:
            workflow = uow.workflows.get(workflow_id, for_update=True)
            if workflow is None:
                raise NotFoundError(f"workflow {workflow_id} was not found")
            if workflow.status.terminal:
                raise LeaseConflictError(f"workflow {workflow_id} is terminal")
            step = uow.steps.get_by_key(workflow_id, step_key, for_update=True)
            if step is not None and step.status == StepStatus.SUCCEEDED:
                return NodeClaim(
                    workflow_id=workflow.id,
                    workflow_version=workflow.version,
                    step_id=step.id,
                    step_key=step.step_key,
                    lease_token=None,
                    already_completed=True,
                    output_data=step.output_data,
                )
            if step is not None and step.status == StepStatus.WAITING_HUMAN:
                raise LeaseConflictError(f"step {step.id} is waiting for human input")
            if step is None:
                workflow.assert_version(expected_workflow_version)
                step = WorkflowStep.create(
                    workflow_id=workflow.id,
                    step_key=step_key,
                    step_type=step_type,
                    sequence=uow.steps.next_sequence(workflow.id),
                    expected_workflow_version=workflow.version,
                    max_attempts=self._default_max_attempts,
                    input_data=input_data,
                    now=now,
                )
                step.queue(now=now)
                is_new = True
            else:
                if (
                    step.status == StepStatus.RETRYABLE_FAILED
                    and step.next_attempt_at
                    and step.next_attempt_at > now
                ):
                    retry_at = step.next_attempt_at.isoformat()
                    raise RetryNotReadyError(f"step {step.id} retry is scheduled for {retry_at}")
                is_new = False

            lease_token = step.claim(
                owner=lease_owner,
                lease_duration=self._lease_duration,
                now=now,
            )
            step.start(lease_token=lease_token, now=now)
            if workflow.status != running_state or workflow.current_node != node_name:
                workflow.transition(running_state, current_node=node_name, now=now)
            step.expected_workflow_version = workflow.version
            if is_new:
                uow.steps.add(step)
            else:
                uow.steps.save(step)
            uow.workflows.save(workflow)
            uow.outbox.add(
                self._event(
                    workflow_id=workflow.id,
                    workflow_version=workflow.version,
                    event_type="workflow.node.started",
                    trace_id=trace_id,
                    payload={"node": node_name, "step_id": step.id, "step_key": step.step_key},
                    now=now,
                )
            )
            uow.commit()
        return NodeClaim(
            workflow_id=workflow.id,
            workflow_version=workflow.version,
            step_id=step.id,
            step_key=step.step_key,
            lease_token=lease_token,
            already_completed=False,
            output_data=None,
        )

    def complete_node(
        self,
        *,
        workflow_id: str,
        step_id: str,
        lease_token: str,
        target_state: WorkflowStatus,
        next_node: str,
        trace_id: str,
        output_data: dict[str, Any] | None = None,
        output_ref: str | None = None,
        workflow_result: dict[str, Any] | None = None,
    ) -> int:
        now = datetime.now(UTC)
        with self._uow_factory() as uow:
            workflow = uow.workflows.get(workflow_id, for_update=True)
            step = uow.steps.get(step_id, for_update=True)
            if workflow is None or step is None or step.workflow_id != workflow_id:
                raise NotFoundError("workflow step was not found")
            if step.status == StepStatus.SUCCEEDED:
                return workflow.version
            step.succeed(
                output_ref=output_ref,
                output_data=output_data,
                lease_token=lease_token,
                now=now,
            )
            workflow.transition(target_state, current_node=next_node, now=now)
            if workflow_result is not None:
                workflow.result_data = workflow_result
            uow.steps.save(step)
            uow.workflows.save(workflow)
            uow.outbox.add(
                self._event(
                    workflow_id=workflow.id,
                    workflow_version=workflow.version,
                    event_type="workflow.node.completed",
                    trace_id=trace_id,
                    payload={
                        "node": next_node,
                        "completed_step_id": step.id,
                        "status": workflow.status.value,
                    },
                    now=now,
                )
            )
            uow.commit()
        return workflow.version

    def begin_human_wait(
        self,
        *,
        workflow_id: str,
        expected_workflow_version: int,
        step_key: str,
        step_type: StepType,
        lease_owner: str,
        trace_id: str,
    ) -> HumanWait:
        now = datetime.now(UTC)
        with self._uow_factory() as uow:
            workflow = uow.workflows.get(workflow_id, for_update=True)
            if workflow is None:
                raise NotFoundError(f"workflow {workflow_id} was not found")
            step = uow.steps.get_by_key(workflow_id, step_key, for_update=True)
            if step is not None:
                if step.status == StepStatus.SUCCEEDED:
                    return HumanWait(
                        workflow.id,
                        step.expected_workflow_version,
                        step.id,
                        True,
                        step.output_data,
                    )
                if step.status == StepStatus.WAITING_HUMAN:
                    return HumanWait(
                        workflow.id,
                        step.expected_workflow_version,
                        step.id,
                        False,
                        None,
                    )
                raise LeaseConflictError(f"human step {step.id} is in {step.status.value}")
            workflow.assert_version(expected_workflow_version)
            step = WorkflowStep.create(
                workflow_id=workflow.id,
                step_key=step_key,
                step_type=step_type,
                sequence=uow.steps.next_sequence(workflow.id),
                expected_workflow_version=workflow.version,
                max_attempts=1,
                now=now,
            )
            step.queue(now=now)
            token = step.claim(
                owner=lease_owner,
                lease_duration=self._lease_duration,
                now=now,
            )
            step.start(lease_token=token, now=now)
            step.wait_for_human(lease_token=token, now=now)
            uow.steps.add(step)
            uow.outbox.add(
                self._event(
                    workflow_id=workflow.id,
                    workflow_version=workflow.version,
                    event_type="workflow.human_input.required",
                    trace_id=trace_id,
                    payload={"step_id": step.id, "step_key": step.step_key},
                    now=now,
                )
            )
            uow.commit()
        return HumanWait(workflow.id, workflow.version, step.id, False, None)

    def complete_human_wait(
        self,
        *,
        workflow_id: str,
        step_id: str,
        output_data: dict[str, Any],
        trace_id: str,
    ) -> int:
        now = datetime.now(UTC)
        with self._uow_factory() as uow:
            workflow = uow.workflows.get(workflow_id, for_update=True)
            step = uow.steps.get(step_id, for_update=True)
            if workflow is None or step is None or step.workflow_id != workflow_id:
                raise NotFoundError("human workflow step was not found")
            if step.status != StepStatus.SUCCEEDED:
                step.succeed(output_data=output_data, now=now)
                uow.steps.save(step)
                uow.outbox.add(
                    self._event(
                        workflow_id=workflow.id,
                        workflow_version=workflow.version,
                        event_type="workflow.human_input.received",
                        trace_id=trace_id,
                        payload={"step_id": step.id, "decision": output_data.get("decision")},
                        now=now,
                    )
                )
                uow.commit()
        return workflow.version

    def begin_attempt(
        self,
        *,
        workflow_id: str,
        step_id: str,
        idempotency_key: str,
        request_data: dict[str, Any],
    ) -> AttemptClaim:
        now = datetime.now(UTC)
        with self._uow_factory() as uow:
            existing = uow.attempts.get_by_idempotency(idempotency_key, for_update=True)
            if existing is not None and existing.status == AttemptStatus.SUCCEEDED:
                return AttemptClaim(
                    existing.id,
                    existing.attempt_number,
                    True,
                    existing.result_data,
                    existing.provider_request_id,
                )
            if existing is None:
                step = uow.steps.get(step_id, for_update=True)
                if step is None or step.workflow_id != workflow_id:
                    raise NotFoundError(f"step {step_id} was not found")
                attempt = WorkflowAttempt.create(
                    workflow_id=workflow_id,
                    step_id=step_id,
                    attempt_number=step.attempt_count,
                    idempotency_key=idempotency_key,
                    request_data=request_data,
                    now=now,
                )
                attempt.mark_submitting(now=now)
                uow.attempts.add(attempt)
            else:
                attempt = existing
                if attempt.status == AttemptStatus.SUBMITTING:
                    attempt.transition(AttemptStatus.UNKNOWN, now=now)
                if attempt.status in {
                    AttemptStatus.UNKNOWN,
                    AttemptStatus.RETRYABLE_FAILED,
                }:
                    attempt.transition(AttemptStatus.SUBMITTING, now=now)
                elif attempt.status.terminal:
                    raise LeaseConflictError(
                        f"attempt {attempt.id} is terminal in {attempt.status.value}"
                    )
                uow.attempts.save(attempt)
            uow.commit()
        return AttemptClaim(
            attempt.id,
            attempt.attempt_number,
            False,
            None,
            attempt.provider_request_id,
        )

    def complete_attempt(
        self,
        *,
        idempotency_key: str,
        result: ToolResult,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self._uow_factory() as uow:
            attempt = uow.attempts.get_by_idempotency(idempotency_key, for_update=True)
            if attempt is None:
                raise NotFoundError("workflow attempt was not found")
            if attempt.status == AttemptStatus.SUCCEEDED:
                return attempt.result_data or {}
            attempt.provider_request_id = result.provider_request_id
            attempt.succeed(result_data=result.output, now=now)
            uow.attempts.save(attempt)
            uow.commit()
        return result.output

    def fail_node(
        self,
        *,
        workflow_id: str,
        step_id: str,
        lease_token: str,
        trace_id: str,
        error: Exception,
        retryable: bool,
        retry_delay: timedelta,
    ) -> None:
        now = datetime.now(UTC)
        with self._uow_factory() as uow:
            workflow = uow.workflows.get(workflow_id, for_update=True)
            step = uow.steps.get(step_id, for_update=True)
            if workflow is None or step is None:
                raise NotFoundError("workflow step was not found")
            if retryable and step.attempt_count < step.max_attempts:
                retry_at = now + retry_delay
                step.fail_retryable(
                    error_class=type(error).__name__,
                    error_message=str(error),
                    retry_at=retry_at,
                    lease_token=lease_token,
                    now=now,
                )
                uow.outbox.add(
                    self._event(
                        workflow_id=workflow.id,
                        workflow_version=workflow.version,
                        event_type="workflow.run.requested",
                        trace_id=trace_id,
                        payload={"workflow_id": workflow.id, "action": "retry"},
                        now=retry_at,
                    )
                )
            else:
                step.fail_permanently(
                    error_class=type(error).__name__,
                    error_message=str(error),
                    lease_token=lease_token,
                    now=now,
                )
                if not workflow.status.terminal:
                    workflow.transition(
                        WorkflowStatus.FAILED,
                        current_node=workflow.current_node,
                        now=now,
                    )
                    uow.workflows.save(workflow)
                uow.outbox.add(
                    self._event(
                        workflow_id=workflow.id,
                        workflow_version=workflow.version,
                        event_type="workflow.failed",
                        trace_id=trace_id,
                        payload={
                            "workflow_id": workflow.id,
                            "step_id": step.id,
                            "error_class": type(error).__name__,
                        },
                        now=now,
                    )
                )
            uow.steps.save(step)
            uow.commit()

    @staticmethod
    def _event(
        *,
        workflow_id: str,
        workflow_version: int,
        event_type: str,
        trace_id: str,
        payload: dict[str, Any],
        now: datetime,
    ) -> OutboxEvent:
        return OutboxEvent(
            envelope=EventEnvelope.create(
                event_type=event_type,
                aggregate_type="workflow",
                aggregate_id=workflow_id,
                aggregate_version=workflow_version,
                trace_id=trace_id,
                payload=payload,
                now=now,
            ),
            available_at=now,
        )
