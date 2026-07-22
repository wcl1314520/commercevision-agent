"""Mappings between framework-independent entities and ORM rows."""

from commercevision_domain.messaging import DeadLetterMessage, EventEnvelope, OutboxEvent
from commercevision_domain.workflow.entities import (
    Approval,
    Workflow,
    WorkflowAttempt,
    WorkflowStep,
)
from commercevision_domain.workflow.enums import (
    ApprovalDecision,
    ApprovalType,
    AttemptStatus,
    RetentionStatus,
    StepStatus,
    StepType,
    WorkflowStatus,
)

from .models import (
    ApprovalModel,
    DeadLetterMessageModel,
    OutboxEventModel,
    WorkflowAttemptModel,
    WorkflowModel,
    WorkflowStepModel,
)


def workflow_to_model(entity: Workflow) -> WorkflowModel:
    return WorkflowModel(
        id=entity.id,
        workspace_id=entity.workspace_id,
        created_by=entity.created_by,
        workflow_type=entity.workflow_type,
        status=entity.status.value,
        retention_status=entity.retention_status.value,
        current_node=entity.current_node,
        version=entity.version,
        input_json=entity.input_data,
        result_json=entity.result_data,
        expires_at=entity.expires_at,
        cancellation_requested_at=entity.cancellation_requested_at,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
    )


def workflow_from_model(model: WorkflowModel) -> Workflow:
    return Workflow(
        id=model.id,
        workspace_id=model.workspace_id,
        created_by=model.created_by,
        workflow_type=model.workflow_type,
        status=WorkflowStatus(model.status),
        retention_status=RetentionStatus(model.retention_status),
        current_node=model.current_node,
        version=model.version,
        input_data=model.input_json,
        result_data=model.result_json,
        expires_at=model.expires_at,
        cancellation_requested_at=model.cancellation_requested_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def step_to_model(entity: WorkflowStep) -> WorkflowStepModel:
    return WorkflowStepModel(
        id=entity.id,
        workflow_id=entity.workflow_id,
        step_key=entity.step_key,
        step_type=entity.step_type.value,
        status=entity.status.value,
        sequence=entity.sequence,
        expected_workflow_version=entity.expected_workflow_version,
        lease_owner=entity.lease_owner,
        lease_token=entity.lease_token,
        lease_expires_at=entity.lease_expires_at,
        attempt_count=entity.attempt_count,
        max_attempts=entity.max_attempts,
        next_attempt_at=entity.next_attempt_at,
        input_ref=entity.input_ref,
        output_ref=entity.output_ref,
        input_json=entity.input_data,
        output_json=entity.output_data,
        error_class=entity.error_class,
        error_message=entity.error_message,
        started_at=entity.started_at,
        completed_at=entity.completed_at,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
        version=entity.version,
    )


def step_from_model(model: WorkflowStepModel) -> WorkflowStep:
    return WorkflowStep(
        id=model.id,
        workflow_id=model.workflow_id,
        step_key=model.step_key,
        step_type=StepType(model.step_type),
        status=StepStatus(model.status),
        sequence=model.sequence,
        expected_workflow_version=model.expected_workflow_version,
        lease_owner=model.lease_owner,
        lease_token=model.lease_token,
        lease_expires_at=model.lease_expires_at,
        attempt_count=model.attempt_count,
        max_attempts=model.max_attempts,
        next_attempt_at=model.next_attempt_at,
        input_ref=model.input_ref,
        output_ref=model.output_ref,
        input_data=model.input_json,
        output_data=model.output_json,
        error_class=model.error_class,
        error_message=model.error_message,
        started_at=model.started_at,
        completed_at=model.completed_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
        version=model.version,
    )


def attempt_to_model(entity: WorkflowAttempt) -> WorkflowAttemptModel:
    return WorkflowAttemptModel(
        id=entity.id,
        workflow_id=entity.workflow_id,
        step_id=entity.step_id,
        attempt_number=entity.attempt_number,
        idempotency_key=entity.idempotency_key,
        status=entity.status.value,
        provider_request_id=entity.provider_request_id,
        request_ref=entity.request_ref,
        result_ref=entity.result_ref,
        request_json=entity.request_data,
        result_json=entity.result_data,
        error_class=entity.error_class,
        error_message=entity.error_message,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
        started_at=entity.started_at,
        completed_at=entity.completed_at,
        version=entity.version,
    )


def attempt_from_model(model: WorkflowAttemptModel) -> WorkflowAttempt:
    return WorkflowAttempt(
        id=model.id,
        workflow_id=model.workflow_id,
        step_id=model.step_id,
        attempt_number=model.attempt_number,
        idempotency_key=model.idempotency_key,
        status=AttemptStatus(model.status),
        provider_request_id=model.provider_request_id,
        request_ref=model.request_ref,
        result_ref=model.result_ref,
        request_data=model.request_json,
        result_data=model.result_json,
        error_class=model.error_class,
        error_message=model.error_message,
        created_at=model.created_at,
        updated_at=model.updated_at,
        started_at=model.started_at,
        completed_at=model.completed_at,
        version=model.version,
    )


def approval_to_model(entity: Approval) -> ApprovalModel:
    return ApprovalModel(
        id=entity.id,
        workflow_id=entity.workflow_id,
        approval_type=entity.approval_type.value,
        subject_id=entity.subject_id,
        subject_version=entity.subject_version,
        decision=entity.decision.value,
        reason_code=entity.reason_code,
        comment_ref=entity.comment_ref,
        approved_by=entity.approved_by,
        expected_workflow_version=entity.expected_workflow_version,
        created_at=entity.created_at,
    )


def approval_from_model(model: ApprovalModel) -> Approval:
    return Approval(
        id=model.id,
        workflow_id=model.workflow_id,
        approval_type=ApprovalType(model.approval_type),
        subject_id=model.subject_id,
        subject_version=model.subject_version,
        decision=ApprovalDecision(model.decision),
        reason_code=model.reason_code,
        comment_ref=model.comment_ref,
        approved_by=model.approved_by,
        expected_workflow_version=model.expected_workflow_version,
        created_at=model.created_at,
    )


def outbox_to_model(entity: OutboxEvent) -> OutboxEventModel:
    envelope = entity.envelope
    return OutboxEventModel(
        id=envelope.event_id,
        aggregate_type=envelope.aggregate_type,
        aggregate_id=envelope.aggregate_id,
        event_type=envelope.event_type,
        schema_version=envelope.schema_version,
        aggregate_version=envelope.aggregate_version,
        trace_id=envelope.trace_id,
        payload_json=envelope.payload,
        occurred_at=envelope.occurred_at,
        available_at=entity.available_at,
        published_at=entity.published_at,
        publish_attempts=entity.publish_attempts,
        lock_owner=entity.lock_owner,
        lock_token=entity.lock_token,
        locked_until=entity.locked_until,
        last_error=entity.last_error,
    )


def outbox_from_model(model: OutboxEventModel) -> OutboxEvent:
    return OutboxEvent(
        envelope=EventEnvelope(
            event_id=model.id,
            event_type=model.event_type,
            schema_version=model.schema_version,
            aggregate_type=model.aggregate_type,
            aggregate_id=model.aggregate_id,
            aggregate_version=model.aggregate_version,
            occurred_at=model.occurred_at,
            trace_id=model.trace_id,
            payload=model.payload_json,
        ),
        available_at=model.available_at,
        published_at=model.published_at,
        publish_attempts=model.publish_attempts,
        lock_owner=model.lock_owner,
        lock_token=model.lock_token,
        locked_until=model.locked_until,
        last_error=model.last_error,
    )


def dead_letter_to_model(entity: DeadLetterMessage) -> DeadLetterMessageModel:
    return DeadLetterMessageModel(
        id=entity.id,
        consumer=entity.consumer,
        message_id=entity.message_id,
        event_type=entity.event_type,
        payload_json=entity.payload,
        reason=entity.reason,
        error_class=entity.error_class,
        error_message=entity.error_message,
        attempt_count=entity.attempt_count,
        original_created_at=entity.original_created_at,
        created_at=entity.created_at,
    )
