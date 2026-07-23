"""Mappings for the Durable Operation aggregate."""

from commercevision_domain.operations import (
    DurableOperation,
    NormalizedOperationError,
    OperationKind,
    OperationState,
    ReconciliationOutcome,
)

from .models import DurableOperationModel


def operation_to_model(operation: DurableOperation) -> DurableOperationModel:
    error = operation.error
    return DurableOperationModel(
        id=operation.id,
        workspace_id=operation.workspace_id,
        kind=operation.kind.value,
        target_type=operation.target_type,
        target_id=operation.target_id,
        target_version=operation.target_version,
        input_hash=operation.input_hash,
        input_ref=operation.input_ref,
        output_ref=operation.output_ref,
        provider_request_id=operation.provider_request_id,
        state=operation.state.value,
        lease_owner=operation.lease_owner,
        lease_token=operation.lease_token,
        lease_expires_at=operation.lease_expires_at,
        attempt_count=operation.attempt_count,
        max_attempts=operation.max_attempts,
        next_attempt_at=operation.next_attempt_at,
        execution_deadline_at=operation.execution_deadline_at,
        reconciliation_attempt_count=operation.reconciliation_attempt_count,
        max_reconciliation_attempts=operation.max_reconciliation_attempts,
        next_reconciliation_at=operation.next_reconciliation_at,
        reconciliation_started_at=operation.reconciliation_started_at,
        reconciliation_deadline_at=operation.reconciliation_deadline_at,
        reconciliation_required=operation.reconciliation_required,
        reconciliation_outcome=operation.reconciliation_outcome.value,
        dead_letter_id=operation.dead_letter_id,
        replay_source_dead_letter_id=operation.replay_source_dead_letter_id,
        replay_attempt=operation.replay_attempt,
        recovery_generation=operation.recovery_generation,
        recovery_consumed_generation=operation.recovery_consumed_generation,
        error_code=error.code if error else None,
        error_category=error.category if error else None,
        error_message=error.message if error else None,
        error_retryable=error.retryable if error else None,
        error_provider_request_id=error.provider_request_id if error else None,
        created_at=operation.created_at,
        updated_at=operation.updated_at,
        last_attempt_at=operation.last_attempt_at,
        started_at=operation.started_at,
        completed_at=operation.completed_at,
        version=operation.version,
    )


def operation_from_model(model: DurableOperationModel) -> DurableOperation:
    error = None
    if (
        model.error_code is not None
        and model.error_category is not None
        and model.error_message is not None
        and model.error_retryable is not None
    ):
        error = NormalizedOperationError(
            code=model.error_code,
            category=model.error_category,
            message=model.error_message,
            retryable=model.error_retryable,
            provider_request_id=model.error_provider_request_id,
        )
    return DurableOperation(
        id=model.id,
        workspace_id=model.workspace_id,
        kind=OperationKind(model.kind),
        target_type=model.target_type,
        target_id=model.target_id,
        target_version=model.target_version,
        input_hash=model.input_hash,
        input_ref=model.input_ref,
        output_ref=model.output_ref,
        provider_request_id=model.provider_request_id,
        state=OperationState(model.state),
        lease_owner=model.lease_owner,
        lease_token=model.lease_token,
        lease_expires_at=model.lease_expires_at,
        attempt_count=model.attempt_count,
        max_attempts=model.max_attempts,
        next_attempt_at=model.next_attempt_at,
        execution_deadline_at=model.execution_deadline_at,
        reconciliation_attempt_count=model.reconciliation_attempt_count,
        max_reconciliation_attempts=model.max_reconciliation_attempts,
        next_reconciliation_at=model.next_reconciliation_at,
        reconciliation_started_at=model.reconciliation_started_at,
        reconciliation_deadline_at=model.reconciliation_deadline_at,
        reconciliation_required=model.reconciliation_required,
        reconciliation_outcome=ReconciliationOutcome(model.reconciliation_outcome),
        dead_letter_id=model.dead_letter_id,
        replay_source_dead_letter_id=model.replay_source_dead_letter_id,
        replay_attempt=model.replay_attempt,
        recovery_generation=model.recovery_generation,
        recovery_consumed_generation=model.recovery_consumed_generation,
        error=error,
        created_at=model.created_at,
        updated_at=model.updated_at,
        last_attempt_at=model.last_attempt_at,
        started_at=model.started_at,
        completed_at=model.completed_at,
        version=model.version,
    )
