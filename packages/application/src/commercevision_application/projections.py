"""Read-model projections shared by API and tests."""

from commercevision_contracts.workflow import (
    ApprovalResponse,
    WorkflowAttemptResponse,
    WorkflowResponse,
    WorkflowStepResponse,
)
from commercevision_domain.workflow.entities import (
    Approval,
    Workflow,
    WorkflowAttempt,
    WorkflowStep,
)


def workflow_response(
    workflow: Workflow,
    *,
    steps: list[WorkflowStep] | None = None,
    attempts: list[WorkflowAttempt] | None = None,
    approvals: list[Approval] | None = None,
) -> WorkflowResponse:
    return WorkflowResponse(
        id=workflow.id,
        workspace_id=workflow.workspace_id,
        created_by=workflow.created_by,
        workflow_type=workflow.workflow_type,
        status=workflow.status,
        retention_status=workflow.retention_status,
        current_node=workflow.current_node,
        version=workflow.version,
        input_data=workflow.input_data,
        result_data=workflow.result_data,
        expires_at=workflow.expires_at,
        cancellation_requested_at=workflow.cancellation_requested_at,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
        steps=[
            WorkflowStepResponse(
                id=step.id,
                step_key=step.step_key,
                step_type=step.step_type,
                status=step.status,
                sequence=step.sequence,
                attempt_count=step.attempt_count,
                max_attempts=step.max_attempts,
                lease_expires_at=step.lease_expires_at,
                output_ref=step.output_ref,
                output_data=step.output_data,
                error_class=step.error_class,
                error_message=step.error_message,
                started_at=step.started_at,
                completed_at=step.completed_at,
            )
            for step in steps or []
        ],
        attempts=[
            WorkflowAttemptResponse(
                id=attempt.id,
                step_id=attempt.step_id,
                attempt_number=attempt.attempt_number,
                idempotency_key=attempt.idempotency_key,
                status=attempt.status,
                provider_request_id=attempt.provider_request_id,
                result_ref=attempt.result_ref,
                result_data=attempt.result_data,
                error_class=attempt.error_class,
                error_message=attempt.error_message,
                started_at=attempt.started_at,
                completed_at=attempt.completed_at,
            )
            for attempt in attempts or []
        ],
        approvals=[
            ApprovalResponse(
                id=approval.id,
                approval_type=approval.approval_type,
                subject_id=approval.subject_id,
                subject_version=approval.subject_version,
                decision=approval.decision,
                approved_by=approval.approved_by,
                expected_workflow_version=approval.expected_workflow_version,
                created_at=approval.created_at,
            )
            for approval in approvals or []
        ],
    )
