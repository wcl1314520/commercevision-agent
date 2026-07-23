from datetime import UTC, datetime, timedelta

import pytest
from commercevision_application import (
    OperationExecutionFailure,
    OperationExecutionRequest,
    OperationExecutionResult,
    OperationExecutorRegistry,
    OperationReconciliationPolicy,
    OperationReconciliationResult,
    OperationRetryPolicy,
)
from commercevision_domain import (
    InvalidTransitionError,
    LeaseConflictError,
    NormalizedOperationError,
    OperationKind,
    OperationState,
    ReconciliationOutcome,
    RetryNotReadyError,
)
from commercevision_domain.messaging import DeadLetterReplay
from commercevision_domain.operations import DurableOperation

NOW = datetime(2026, 7, 23, 8, 30, 0, 123456, tzinfo=UTC)


def create_operation(
    *,
    max_attempts: int = 3,
    execution_max_elapsed: timedelta = timedelta(hours=24),
) -> DurableOperation:
    return DurableOperation.create(
        workspace_id="workspace-a",
        kind=OperationKind.ASSET_VALIDATION,
        target_type="asset_version",
        target_id="asset-version-1",
        target_version=2,
        input_hash="a" * 64,
        input_ref="mysql://asset-versions/asset-version-1",
        max_attempts=max_attempts,
        execution_max_elapsed=execution_max_elapsed,
        now=NOW,
    )


def test_operation_enforces_claim_start_and_success_lifecycle() -> None:
    operation = create_operation()

    token = operation.claim(
        owner="worker-a",
        lease_duration=timedelta(seconds=30),
        now=NOW,
    )
    operation.start(lease_token=token, now=NOW)
    operation.succeed(
        lease_token=token,
        output_ref="mysql://validation-results/result-1",
        now=NOW + timedelta(seconds=1),
    )

    assert operation.state == OperationState.SUCCEEDED
    assert operation.attempt_count == 1
    assert operation.output_ref == "mysql://validation-results/result-1"
    assert operation.completed_at == NOW + timedelta(seconds=1)
    assert operation.lease_token is None

    with pytest.raises(InvalidTransitionError):
        operation.claim(
            owner="worker-b",
            lease_duration=timedelta(seconds=30),
            now=NOW + timedelta(seconds=2),
        )


def test_operation_retry_and_lease_boundaries_are_inclusive() -> None:
    operation = create_operation()
    token = operation.claim(
        owner="worker-a",
        lease_duration=timedelta(microseconds=400),
        now=NOW,
    )
    operation.start(lease_token=token, now=NOW)

    with pytest.raises(LeaseConflictError):
        operation.succeed(
            lease_token=token,
            now=NOW + timedelta(microseconds=400),
        )

    operation.recover_expired_lease(
        retry_at=NOW + timedelta(seconds=1),
        now=NOW + timedelta(microseconds=400),
    )
    assert operation.state == OperationState.RECONCILING
    assert operation.reconciliation_required is True
    assert operation.reconciliation_outcome == ReconciliationOutcome.PENDING

    reconcile_token = operation.claim_reconciliation(
        owner="reconciler-a",
        lease_duration=timedelta(seconds=30),
        now=NOW + timedelta(microseconds=400),
    )
    operation.resolve_reconciliation(
        lease_token=reconcile_token,
        outcome=ReconciliationOutcome.CONFIRMED_FAILURE,
        error=NormalizedOperationError(
            code="PROVIDER_RESULT_NOT_FOUND",
            category="provider",
            message="provider has no result for the recorded request",
            retryable=True,
        ),
        retry_at=NOW + timedelta(seconds=1),
        now=NOW + timedelta(microseconds=500),
    )

    with pytest.raises(RetryNotReadyError):
        operation.retry(
            owner="worker-b",
            lease_duration=timedelta(seconds=30),
            now=NOW + timedelta(seconds=1, microseconds=-1),
        )

    operation.retry(
        owner="worker-b",
        lease_duration=timedelta(seconds=30),
        now=NOW + timedelta(seconds=1),
    )
    assert operation.state == OperationState.CLAIMED
    assert operation.attempt_count == 2


def test_current_execution_generation_can_complete_at_exact_lease_expiry() -> None:
    operation = create_operation()
    token = operation.claim(
        owner="worker-a",
        lease_duration=timedelta(microseconds=400),
        now=NOW,
    )
    operation.start(lease_token=token, now=NOW)
    execution_version = operation.version

    operation.succeed(
        lease_token=token,
        output_ref="mysql://result/exact-expiry",
        provider_request_id="provider-exact-expiry",
        expected_execution_version=execution_version,
        expected_attempt_count=operation.attempt_count,
        now=NOW + timedelta(microseconds=400),
    )

    assert operation.state == OperationState.SUCCEEDED
    assert operation.provider_request_id == "provider-exact-expiry"


def test_reclaimed_execution_accepts_only_late_identity_without_stale_transition() -> None:
    operation = create_operation()
    token = operation.claim(
        owner="worker-a",
        lease_duration=timedelta(microseconds=400),
        now=NOW,
    )
    operation.start(lease_token=token, now=NOW)
    execution_version = operation.version
    execution_attempt = operation.attempt_count
    operation.recover_expired_lease(
        retry_at=NOW + timedelta(microseconds=400),
        reconciliation_deadline_at=NOW + timedelta(seconds=30),
        now=NOW + timedelta(microseconds=400),
    )

    changed = operation.record_late_execution_provider_identity(
        provider_request_id=" provider-after-reclaim ",
        expected_execution_version=execution_version,
        expected_attempt_count=execution_attempt,
        now=NOW + timedelta(microseconds=401),
    )

    assert changed is True
    assert operation.state == OperationState.RECONCILING
    assert operation.provider_request_id == "provider-after-reclaim"
    assert operation.output_ref is None
    assert operation.error is not None
    assert operation.error.code == "EXTERNAL_OUTCOME_UNKNOWN"


def test_execution_failure_preserves_first_provider_request_identity() -> None:
    operation = create_operation()
    first_token = operation.claim(
        owner="worker-a",
        lease_duration=timedelta(seconds=30),
        now=NOW,
    )
    operation.start(lease_token=first_token, now=NOW)
    operation.fail(
        lease_token=first_token,
        error=NormalizedOperationError(
            code="PROVIDER_BUSY",
            category="provider",
            message="provider asked the worker to retry",
            retryable=True,
            provider_request_id="provider-request-first",
        ),
        retry_at=NOW + timedelta(seconds=1),
        now=NOW,
    )

    second_token = operation.retry(
        owner="worker-b",
        lease_duration=timedelta(seconds=30),
        now=NOW + timedelta(seconds=1),
    )
    operation.start(lease_token=second_token, now=NOW + timedelta(seconds=1))
    operation.fail(
        lease_token=second_token,
        error=NormalizedOperationError(
            code="PROVIDER_REJECTED",
            category="provider",
            message="provider rejected the retried request",
            retryable=False,
            provider_request_id="provider-request-second",
        ),
        retry_at=None,
        now=NOW + timedelta(seconds=2),
    )

    assert operation.provider_request_id == "provider-request-first"
    assert operation.error is not None
    assert operation.error.provider_request_id == "provider-request-second"


@pytest.mark.parametrize(
    "result_factory",
    [
        lambda value: OperationExecutionResult(
            operation_id="operation-1",
            output_ref=None,
            provider_request_id=value,
        ),
        lambda value: OperationReconciliationResult(
            operation_id="operation-1",
            outcome=ReconciliationOutcome.CONFIRMED_SUCCESS,
            provider_request_id=value,
        ),
        lambda value: NormalizedOperationError(
            code="PROVIDER_ERROR",
            category="provider",
            message="provider returned an error",
            retryable=False,
            provider_request_id=value,
        ),
    ],
)
def test_provider_result_identity_is_normalized_and_bounded(result_factory) -> None:
    normalized = result_factory("  provider-request-1  ")

    assert normalized.provider_request_id == "provider-request-1"

    with pytest.raises(ValueError, match="provider request id"):
        result_factory(" ")
    with pytest.raises(ValueError, match="provider request id"):
        result_factory("p" * 257)


@pytest.mark.parametrize(
    ("outcome", "expected_state"),
    [
        (ReconciliationOutcome.PENDING, OperationState.RECONCILING),
        (ReconciliationOutcome.NOT_FOUND, OperationState.RECONCILING),
        (ReconciliationOutcome.CONFIRMED_FAILURE, OperationState.FAILED),
        (ReconciliationOutcome.CONFIRMED_SUCCESS, OperationState.SUCCEEDED),
    ],
)
def test_reconciliation_transitions_capture_result_provider_identity_separately(
    outcome: ReconciliationOutcome,
    expected_state: OperationState,
) -> None:
    operation = create_operation()
    token = operation.claim(
        owner="worker-a",
        lease_duration=timedelta(seconds=30),
        now=NOW,
    )
    operation.start(lease_token=token, now=NOW)
    operation.require_reconciliation(
        lease_token=token,
        error=NormalizedOperationError(
            code="EXTERNAL_OUTCOME_UNKNOWN",
            category="provider",
            message="provider outcome is unknown",
            retryable=True,
        ),
        now=NOW + timedelta(microseconds=1),
    )
    reconcile_token = operation.claim_reconciliation(
        owner="reconciler-a",
        lease_duration=timedelta(seconds=30),
        now=NOW + timedelta(microseconds=2),
    )
    error = NormalizedOperationError(
        code=f"PROVIDER_{outcome.value}",
        category="provider",
        message=f"provider returned {outcome.value}",
        retryable=outcome in {ReconciliationOutcome.PENDING, ReconciliationOutcome.NOT_FOUND},
        provider_request_id="latest-reconciliation-error",
    )

    if outcome in {ReconciliationOutcome.PENDING, ReconciliationOutcome.NOT_FOUND}:
        operation.defer_reconciliation(
            lease_token=reconcile_token,
            error=error,
            provider_request_id=" result-provider-id ",
            next_reconciliation_at=NOW + timedelta(seconds=1),
            now=NOW + timedelta(microseconds=3),
        )
    else:
        operation.resolve_reconciliation(
            lease_token=reconcile_token,
            outcome=outcome,
            error=error if outcome == ReconciliationOutcome.CONFIRMED_FAILURE else None,
            retry_at=None,
            output_ref=(
                "mysql://result" if outcome == ReconciliationOutcome.CONFIRMED_SUCCESS else None
            ),
            provider_request_id=" result-provider-id ",
            now=NOW + timedelta(microseconds=3),
        )

    assert operation.state == expected_state
    assert operation.provider_request_id == "result-provider-id"
    assert (operation.error.provider_request_id if operation.error is not None else None) == (
        "latest-reconciliation-error"
        if outcome != ReconciliationOutcome.CONFIRMED_SUCCESS
        else None
    )


def test_claimed_expired_lease_retries_without_assuming_external_work_started() -> None:
    operation = create_operation()
    operation.claim(
        owner="worker-a",
        lease_duration=timedelta(microseconds=400),
        now=NOW,
    )

    operation.recover_expired_lease(
        retry_at=NOW + timedelta(seconds=1),
        now=NOW + timedelta(microseconds=400),
    )

    assert operation.state == OperationState.RETRYABLE_FAILED
    assert operation.reconciliation_required is False
    assert operation.next_attempt_at == NOW + timedelta(seconds=1)


def test_retry_budget_exhaustion_is_terminal_and_preserves_normalized_error() -> None:
    operation = create_operation(max_attempts=1)
    token = operation.claim(
        owner="worker-a",
        lease_duration=timedelta(seconds=30),
        now=NOW,
    )
    operation.start(lease_token=token, now=NOW)
    error = NormalizedOperationError(
        code="OBJECT_INVALID",
        category="validation",
        message="object did not pass validation",
        retryable=False,
    )

    operation.fail(
        lease_token=token,
        error=error,
        retry_at=None,
        now=NOW + timedelta(seconds=1),
    )

    assert operation.state == OperationState.FAILED
    assert operation.error == error
    assert operation.completed_at == NOW + timedelta(seconds=1)


def test_reconciliation_claim_rejects_invalid_lease_parameters() -> None:
    operation = create_operation()
    token = operation.claim(
        owner="worker-a",
        lease_duration=timedelta(seconds=30),
        now=NOW,
    )
    operation.start(lease_token=token, now=NOW)
    operation.require_reconciliation(
        lease_token=token,
        error=NormalizedOperationError(
            code="PROVIDER_TIMEOUT_UNKNOWN",
            category="provider",
            message="provider outcome is unknown",
            retryable=True,
        ),
        now=NOW,
    )

    with pytest.raises(ValueError, match="owner"):
        operation.claim_reconciliation(
            owner="",
            lease_duration=timedelta(seconds=30),
            now=NOW,
        )
    with pytest.raises(ValueError, match="duration"):
        operation.claim_reconciliation(
            owner="reconciler-a",
            lease_duration=timedelta(0),
            now=NOW,
        )


def test_operation_retry_policy_uses_exponential_jitter_and_stable_deadline() -> None:
    operation = create_operation()
    token = operation.claim(
        owner="worker-a",
        lease_duration=timedelta(seconds=30),
        now=NOW,
    )
    operation.start(lease_token=token, now=NOW)
    failure = OperationExecutionFailure(
        NormalizedOperationError(
            code="PROVIDER_TEMPORARY",
            category="provider",
            message="provider is temporarily unavailable",
            retryable=True,
        )
    )
    policy = OperationRetryPolicy(
        initial_delay=timedelta(seconds=2),
        maximum_delay=timedelta(seconds=30),
        maximum_elapsed=timedelta(minutes=5),
        jitter=lambda lower, upper: (lower + upper) / 2,
    )

    error, retry_at = policy.decide(
        operation=operation,
        failure=failure,
        now=NOW + timedelta(seconds=1),
    )

    assert error.retryable is True
    assert retry_at == NOW + timedelta(seconds=3)


@pytest.mark.parametrize("provider_offset", [timedelta(microseconds=-1), timedelta(0)])
def test_reconciliation_policy_normalizes_stale_retry_to_strict_future_readiness(
    provider_offset: timedelta,
) -> None:
    operation = create_operation()
    execution_token = operation.claim(
        owner="worker-a",
        lease_duration=timedelta(seconds=30),
        now=NOW,
    )
    operation.start(lease_token=execution_token, now=NOW)
    operation.require_reconciliation(
        lease_token=execution_token,
        error=NormalizedOperationError(
            code="EXTERNAL_OUTCOME_UNKNOWN",
            category="provider",
            message="provider outcome is unknown",
            retryable=True,
        ),
        deadline_at=NOW + timedelta(minutes=5),
        now=NOW + timedelta(microseconds=1),
    )
    reconciliation_token = operation.claim_reconciliation(
        owner="reconciler-a",
        lease_duration=timedelta(seconds=30),
        now=NOW + timedelta(microseconds=2),
    )
    decision_time = NOW + timedelta(microseconds=3)
    error = NormalizedOperationError(
        code="PROVIDER_STATUS_PENDING",
        category="provider",
        message="provider outcome remains uncertain",
        retryable=True,
    )
    policy = OperationReconciliationPolicy(
        initial_delay=timedelta(seconds=2),
        maximum_delay=timedelta(seconds=4),
        maximum_elapsed=timedelta(minutes=5),
        jitter=lambda lower, upper: lower,
    )

    decided_error, retry_at = policy.decide(
        operation=operation,
        failure=OperationExecutionFailure(
            error,
            retry_at=decision_time + provider_offset,
        ),
        now=decision_time,
    )

    assert decided_error == error
    assert retry_at == decision_time + timedelta(seconds=1)
    operation.defer_reconciliation(
        lease_token=reconciliation_token,
        error=decided_error,
        next_reconciliation_at=retry_at,
        now=decision_time,
    )
    assert operation.lease_token is None
    with pytest.raises(RetryNotReadyError):
        operation.claim_reconciliation(
            owner="reconciler-b",
            lease_duration=timedelta(seconds=30),
            now=retry_at - timedelta(microseconds=1),
        )
    assert (
        operation.claim_reconciliation(
            owner="reconciler-b",
            lease_duration=timedelta(seconds=30),
            now=retry_at,
        )
        is not None
    )


def test_operation_retry_policy_honors_provider_retry_after_and_elapsed_limit() -> None:
    operation = create_operation(execution_max_elapsed=timedelta(seconds=10))
    token = operation.claim(
        owner="worker-a",
        lease_duration=timedelta(seconds=30),
        now=NOW,
    )
    operation.start(lease_token=token, now=NOW)
    policy = OperationRetryPolicy(
        initial_delay=timedelta(seconds=1),
        maximum_delay=timedelta(seconds=30),
        maximum_elapsed=timedelta(seconds=10),
        jitter=lambda lower, upper: lower,
    )

    retry_after = NOW + timedelta(seconds=8)
    retryable_error, retry_at = policy.decide(
        operation=operation,
        failure=OperationExecutionFailure(
            NormalizedOperationError(
                code="PROVIDER_THROTTLED",
                category="provider",
                message="provider requested backoff",
                retryable=True,
            ),
            retry_at=retry_after,
        ),
        now=NOW + timedelta(seconds=1),
    )
    exhausted_error, exhausted_retry_at = policy.decide(
        operation=operation,
        failure=OperationExecutionFailure(
            NormalizedOperationError(
                code="PROVIDER_THROTTLED",
                category="provider",
                message="provider requested excessive backoff",
                retryable=True,
            ),
            retry_at=NOW + timedelta(seconds=11),
        ),
        now=NOW + timedelta(seconds=1),
    )

    assert retryable_error.retryable is True
    assert retry_at == retry_after
    assert exhausted_error.retryable is False
    assert exhausted_retry_at is None


def test_provider_retry_after_is_clamped_to_configured_maximum_delay() -> None:
    operation = create_operation()
    token = operation.claim(
        owner="worker-a",
        lease_duration=timedelta(seconds=30),
        now=NOW,
    )
    operation.start(lease_token=token, now=NOW)
    policy = OperationRetryPolicy(
        initial_delay=timedelta(seconds=1),
        maximum_delay=timedelta(seconds=30),
        maximum_elapsed=timedelta(minutes=5),
    )
    error = NormalizedOperationError(
        code="PROVIDER_THROTTLED",
        category="provider",
        message="provider requested backoff",
        retryable=True,
    )

    _, exact = policy.decide(
        operation=operation,
        failure=OperationExecutionFailure(
            error,
            retry_at=NOW + timedelta(seconds=31),
        ),
        now=NOW + timedelta(seconds=1),
    )
    _, beyond = policy.decide(
        operation=operation,
        failure=OperationExecutionFailure(
            error,
            retry_at=NOW + timedelta(seconds=31, microseconds=1),
        ),
        now=NOW + timedelta(seconds=1),
    )

    assert exact == NOW + timedelta(seconds=31)
    assert beyond == NOW + timedelta(seconds=31)


def test_reconciliation_uncertainty_has_readiness_and_attempt_boundaries() -> None:
    operation = create_operation()
    token = operation.claim(
        owner="worker-a",
        lease_duration=timedelta(seconds=30),
        now=NOW,
    )
    operation.start(lease_token=token, now=NOW)
    operation.require_reconciliation(
        lease_token=token,
        error=NormalizedOperationError(
            code="OUTCOME_UNKNOWN",
            category="provider",
            message="provider outcome is not known",
            retryable=True,
        ),
        deadline_at=NOW + timedelta(minutes=5),
        now=NOW,
    )
    first_token = operation.claim_reconciliation(
        owner="reconciler-a",
        lease_duration=timedelta(seconds=30),
        now=NOW,
    )
    operation.defer_reconciliation(
        lease_token=first_token,
        error=NormalizedOperationError(
            code="QUERY_TEMPORARY",
            category="provider",
            message="provider query failed transiently",
            retryable=True,
        ),
        next_reconciliation_at=NOW + timedelta(seconds=2),
        now=NOW + timedelta(seconds=1),
    )

    with pytest.raises(RetryNotReadyError):
        operation.claim_reconciliation(
            owner="reconciler-b",
            lease_duration=timedelta(seconds=30),
            now=NOW + timedelta(seconds=2, microseconds=-1),
        )

    operation.claim_reconciliation(
        owner="reconciler-b",
        lease_duration=timedelta(seconds=30),
        now=NOW + timedelta(seconds=2),
    )

    assert operation.reconciliation_attempt_count == 2
    assert operation.next_reconciliation_at is None


def test_operation_execution_idempotency_key_is_stable_across_lease_tokens() -> None:
    operation = create_operation()

    first = OperationExecutionRequest.from_operation(operation)
    second = OperationExecutionRequest.from_operation(operation)

    assert first.idempotency_key == second.idempotency_key
    assert first.idempotency_key == f"durable-operation:{operation.id}"
    assert not hasattr(first, "lease_token")


def test_executor_registry_reports_registered_and_missing_operation_kinds() -> None:
    registry = OperationExecutorRegistry()
    executor = object()
    for kind in OperationKind:
        registry.register(kind=kind, executor=executor)  # type: ignore[arg-type]

    assert registry.registered_kinds == frozenset(OperationKind)
    assert registry.missing(OperationKind) == frozenset()


def test_operation_rejects_non_hexadecimal_input_hash() -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        DurableOperation.create(
            workspace_id="workspace-a",
            kind=OperationKind.ASSET_VALIDATION,
            target_type="asset_version",
            target_id="asset-version-1",
            target_version=1,
            input_hash="z" * 64,
            max_attempts=3,
            now=NOW,
        )


def test_dead_letter_replay_timestamp_must_be_aware_utc() -> None:
    with pytest.raises(ValueError, match="UTC"):
        DeadLetterReplay.create(
            source_dead_letter_id="dead-letter-1",
            workspace_id="workspace-a",
            actor_id="admin-a",
            reason="retry after incident recovery",
            replay_attempt=1,
            replay_event_id="event-1",
            now=NOW.astimezone(UTC).replace(tzinfo=None),
        )
