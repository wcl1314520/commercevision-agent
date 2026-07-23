import base64
import hashlib
import hmac
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event, Lock

import pytest
from commercevision_api.main import create_app
from commercevision_application import (
    DurableOperationWorker,
    InboxCoordinator,
    OperationApplicationService,
    OperationCreateCommand,
    OperationExecutionBoundary,
    OperationExecutionFailure,
    OperationExecutionRequest,
    OperationExecutionResult,
    OperationReconciliationResult,
    OperationRecoveryService,
)
from commercevision_contracts import (
    EventType,
    ProductCreateRequestV1,
    Settings,
    WorkflowCreateRequest,
)
from commercevision_domain import (
    LeaseConflictError,
    NormalizedOperationError,
    OperationKind,
    OperationState,
    ReconciliationOutcome,
)
from commercevision_domain.messaging import DeadLetterMessage, EventEnvelope, OutboxEvent
from commercevision_persistence import (
    SqlAlchemyOperationUnitOfWork,
    SqlAlchemyUnitOfWork,
    is_unit_of_work_active,
)
from commercevision_persistence.models import (
    DeadLetterReplayModel,
    IdempotencyKeyModel,
    InboxMessageModel,
    OutboxEventModel,
)
from fastapi.testclient import TestClient
from httpx import Headers
from sqlalchemy import select, update

pytestmark = pytest.mark.integration
TRUSTED_PRINCIPAL_CURRENT_KEY_ID = "gateway-2026-07"
TRUSTED_PRINCIPAL_CURRENT_SECRET = "current-test-key-" + ("0" * 32)
TRUSTED_PRINCIPAL_PREVIOUS_KEY_ID = "gateway-2026-06"
TRUSTED_PRINCIPAL_PREVIOUS_SECRET = "previous-test-key-" + ("0" * 32)


def trusted_principal_header(
    *,
    actor_id: str,
    workspace_ids: list[str],
    admin_workspace_ids: list[str] | None = None,
    system_admin: bool = False,
    issued_at: int | None = None,
    key_id: str = TRUSTED_PRINCIPAL_CURRENT_KEY_ID,
    secret: str = TRUSTED_PRINCIPAL_CURRENT_SECRET,
) -> dict[str, str]:
    claims = {
        "actor_id": actor_id,
        "workspace_ids": workspace_ids,
        "admin_workspace_ids": admin_workspace_ids or [],
        "system_admin": system_admin,
        "issued_at": issued_at or int(datetime.now(UTC).timestamp()),
    }
    encoded = (
        base64.urlsafe_b64encode(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )
    signature = hmac.new(
        secret.encode(),
        f"{key_id}.{encoded}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return {"X-Trusted-Principal": f"{key_id}.{encoded}.{signature}"}


def api_settings(integration_settings: Settings) -> Settings:
    return Settings(
        environment="ci",
        service_name="control-api",
        mysql_dsn=integration_settings.mysql_dsn,
        trusted_principal_current_key_id=TRUSTED_PRINCIPAL_CURRENT_KEY_ID,
        trusted_principal_current_hmac_secret=TRUSTED_PRINCIPAL_CURRENT_SECRET,
        trusted_principal_previous_key_id=TRUSTED_PRINCIPAL_PREVIOUS_KEY_ID,
        trusted_principal_previous_hmac_secret=TRUSTED_PRINCIPAL_PREVIOUS_SECRET,
    )


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current


class BlockingOperationReplayExecutor:
    def __init__(
        self,
        *,
        reconcile_only: bool,
        fail_execution: bool,
        failure_retryable: bool = False,
        reconciliation_outcome: ReconciliationOutcome = ReconciliationOutcome.CONFIRMED_SUCCESS,
    ) -> None:
        self.reconcile_only = reconcile_only
        self.fail_execution = fail_execution
        self.failure_retryable = failure_retryable
        self.reconciliation_outcome = reconciliation_outcome
        self.execute_calls = 0
        self.reconcile_calls = 0
        self.started = Event()
        self.release = Event()
        self._counter_lock = Lock()

    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        assert not self.reconcile_only
        with self._counter_lock:
            self.execute_calls += 1
        self._wait_for_release()
        if self.fail_execution:
            raise OperationExecutionFailure(
                NormalizedOperationError(
                    code="REPLAY_PROVIDER_FAILURE",
                    category="provider",
                    message="provider rejected the replayed operation",
                    retryable=self.failure_retryable,
                    provider_request_id=f"replay-provider-{request.operation_id}",
                )
            )
        return OperationExecutionResult(
            operation_id=request.operation_id,
            output_ref=f"mysql://operation-results/{request.operation_id}",
            provider_request_id=f"replay-provider-{request.operation_id}",
        )

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        assert self.reconcile_only
        with self._counter_lock:
            self.reconcile_calls += 1
        self._wait_for_release()
        error = (
            NormalizedOperationError(
                code="REPLAY_RECONCILIATION_FAILURE",
                category="provider",
                message="provider confirmed the replayed operation failed",
                retryable=False,
                provider_request_id=f"replay-provider-{request.operation_id}",
            )
            if self.reconciliation_outcome == ReconciliationOutcome.CONFIRMED_FAILURE
            else None
        )
        return OperationReconciliationResult(
            operation_id=request.operation_id,
            outcome=self.reconciliation_outcome,
            output_ref=(
                f"mysql://operation-results/{request.operation_id}"
                if self.reconciliation_outcome == ReconciliationOutcome.CONFIRMED_SUCCESS
                else None
            ),
            provider_request_id=f"replay-provider-{request.operation_id}",
            error=error,
        )

    def _wait_for_release(self) -> None:
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test did not release replayed provider work")


def _operation_service(integration_database) -> OperationApplicationService:
    return OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )


def _recovery_event_for_operation(
    integration_database,
    *,
    operation_id: str,
    now: datetime,
) -> OutboxEvent:
    scanner = OperationRecoveryService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory),
        batch_size=1,
    )
    assert scanner.recover_once(now=now) == 1
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        events = uow.outbox.list_for_aggregate(operation_id)
    recovery_events = [
        event
        for event in events
        if event.envelope.event_type == EventType.OPERATION_RECOVERY_REQUESTED.value
    ]
    assert len(recovery_events) == 1
    return recovery_events[0]


def _transport_dead_letter(
    integration_database,
    *,
    event: OutboxEvent,
    suffix: str,
) -> DeadLetterMessage:
    dead_letter = DeadLetterMessage.create(
        consumer=f"transport-replay-{suffix}",
        message_id=event.envelope.event_id,
        event_type=event.envelope.event_type,
        payload=event.envelope.payload,
        reason="transport delivery exhausted before operation handling",
        error_class="TransportFailure",
        error_message="simulated transport DLQ",
        attempt_count=3,
        original_created_at=event.envelope.occurred_at,
        workspace_id=event.workspace_id,
        now=datetime.now(UTC),
    )
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        uow.dead_letters.add(dead_letter)
        uow.commit()
    return dead_letter


def _replay_event_through_api(
    integration_database,
    integration_settings: Settings,
    *,
    dead_letter: DeadLetterMessage,
    idempotency_key: str,
) -> OutboxEvent:
    assert dead_letter.workspace_id is not None
    headers = {
        "X-Workspace-Id": dead_letter.workspace_id,
        "Idempotency-Key": idempotency_key,
        **trusted_principal_header(
            actor_id="operation-replay-admin",
            workspace_ids=[dead_letter.workspace_id],
            admin_workspace_ids=[dead_letter.workspace_id],
        ),
    }
    with TestClient(create_app(api_settings(integration_settings))) as client:
        response = client.post(
            f"/api/v1/operator/dead-letters/{dead_letter.id}:replay",
            headers=headers,
            json={"reason": "resume durable operation after transport review"},
        )
    assert response.status_code == 202
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        replay_event = uow.outbox.get(response.json()["replay_event_id"])
    assert replay_event is not None
    return replay_event


def test_trusted_principal_supports_bounded_dual_key_rotation(
    integration_settings,
) -> None:
    workspace_id = "workspace-key-rotation"
    current = trusted_principal_header(
        actor_id="operator-current",
        workspace_ids=[workspace_id],
    )
    previous = trusted_principal_header(
        actor_id="operator-previous",
        workspace_ids=[workspace_id],
        key_id=TRUSTED_PRINCIPAL_PREVIOUS_KEY_ID,
        secret=TRUSTED_PRINCIPAL_PREVIOUS_SECRET,
    )
    unknown = trusted_principal_header(
        actor_id="operator-unknown",
        workspace_ids=[workspace_id],
        key_id="gateway-unknown",
        secret="unknown-test-key-" + ("0" * 32),
    )
    wrong_key_binding = trusted_principal_header(
        actor_id="operator-wrong-binding",
        workspace_ids=[workspace_id],
        key_id=TRUSTED_PRINCIPAL_PREVIOUS_KEY_ID,
        secret=TRUSTED_PRINCIPAL_CURRENT_SECRET,
    )
    oversized_workspace_claim = trusted_principal_header(
        actor_id="operator-oversized-workspace",
        workspace_ids=[workspace_id, "w" * 129],
    )
    headers = {"X-Workspace-Id": workspace_id}
    with TestClient(create_app(api_settings(integration_settings))) as client:
        current_response = client.get(
            "/api/v1/operations",
            headers={**headers, **current},
        )
        previous_response = client.get(
            "/api/v1/operations",
            headers={**headers, **previous},
        )
        unknown_response = client.get(
            "/api/v1/operations",
            headers={**headers, **unknown},
        )
        wrong_binding_response = client.get(
            "/api/v1/operations",
            headers={**headers, **wrong_key_binding},
        )
        oversized_workspace_response = client.get(
            "/api/v1/operations",
            headers={**headers, **oversized_workspace_claim},
        )

    current_only_settings = Settings(
        environment="ci",
        service_name="control-api",
        mysql_dsn=integration_settings.mysql_dsn,
        trusted_principal_current_key_id=TRUSTED_PRINCIPAL_CURRENT_KEY_ID,
        trusted_principal_current_hmac_secret=TRUSTED_PRINCIPAL_CURRENT_SECRET,
    )
    with TestClient(create_app(current_only_settings)) as client:
        removed_previous_response = client.get(
            "/api/v1/operations",
            headers={**headers, **previous},
        )

    assert current_response.status_code == 200
    assert previous_response.status_code == 200
    assert unknown_response.status_code == 401
    assert wrong_binding_response.status_code == 401
    assert oversized_workspace_response.status_code == 401
    assert oversized_workspace_response.json()["code"] == "AUTHENTICATION_REQUIRED"
    assert removed_previous_response.status_code == 401


def test_trusted_principal_actor_id_uses_bounded_unicode_character_length(
    integration_database,
    integration_settings,
) -> None:
    workspace_id = "workspace-actor-boundary"
    dead_letter = seed_http_dead_letter(
        integration_database,
        workspace_id=workspace_id,
    )
    accepted_actor_ids = ["a" * 128, "操" * 128]
    accepted_dead_letters = [
        seed_http_dead_letter(
            integration_database,
            workspace_id=workspace_id,
        )
        for _actor_id in accepted_actor_ids
    ]
    rejected_actor_ids = ["", "a" * 129, "操" * 129]
    headers = {"X-Workspace-Id": workspace_id}

    with TestClient(
        create_app(api_settings(integration_settings)),
        raise_server_exceptions=False,
    ) as client:
        accepted = [
            client.get(
                "/api/v1/operations",
                headers={
                    **headers,
                    **trusted_principal_header(
                        actor_id=actor_id,
                        workspace_ids=[workspace_id],
                    ),
                },
            )
            for actor_id in accepted_actor_ids
        ]
        accepted_replays = [
            client.post(
                f"/api/v1/operator/dead-letters/{accepted_dead_letter.id}:replay",
                headers={
                    **headers,
                    "Idempotency-Key": f"actor-boundary-accepted-{index}",
                    **trusted_principal_header(
                        actor_id=actor_id,
                        workspace_ids=[workspace_id],
                        admin_workspace_ids=[workspace_id],
                    ),
                },
                json={"reason": "persist accepted actor boundary exactly"},
            )
            for index, (actor_id, accepted_dead_letter) in enumerate(
                zip(accepted_actor_ids, accepted_dead_letters, strict=True)
            )
        ]
        rejected = [
            client.get(
                "/api/v1/operations",
                headers={
                    **headers,
                    **trusted_principal_header(
                        actor_id=actor_id,
                        workspace_ids=[workspace_id],
                    ),
                },
            )
            for actor_id in rejected_actor_ids
        ]
        rejected_replay = client.post(
            f"/api/v1/operator/dead-letters/{dead_letter.id}:replay",
            headers={
                **headers,
                "Idempotency-Key": "actor-boundary-replay-key",
                **trusted_principal_header(
                    actor_id="a" * 129,
                    workspace_ids=[workspace_id],
                    admin_workspace_ids=[workspace_id],
                ),
            },
            json={"reason": "must reject invalid actor before persistence"},
        )

    assert [response.status_code for response in accepted] == [200, 200]
    assert [response.status_code for response in accepted_replays] == [202, 202]
    assert [response.status_code for response in rejected] == [401, 401, 401]
    assert all(response.json()["code"] == "AUTHENTICATION_REQUIRED" for response in rejected)
    assert rejected_replay.status_code == 401
    assert rejected_replay.json()["code"] == "AUTHENTICATION_REQUIRED"
    with integration_database.session_factory() as session:
        replay_id = session.scalar(
            select(DeadLetterReplayModel.id).where(
                DeadLetterReplayModel.source_dead_letter_id == dead_letter.id
            )
        )
        persisted_actor_ids = set(
            session.scalars(
                select(DeadLetterReplayModel.actor_id).where(
                    DeadLetterReplayModel.source_dead_letter_id.in_(
                        [item.id for item in accepted_dead_letters]
                    )
                )
            )
        )
    assert replay_id is None
    assert persisted_actor_ids == set(accepted_actor_ids)


def test_operation_http_read_is_workspace_scoped(
    integration_database,
    integration_settings,
) -> None:
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory)
    )
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-http-operation",
            kind=OperationKind.COLLECTION_REBUILD,
            target_type="collection",
            target_id="collection-1",
            target_version=1,
            input_hash="1" * 64,
            input_ref=None,
            max_attempts=4,
        )
    )
    completed_at = datetime.now(UTC)
    lease_token = service.claim(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        owner="worker-http-operation",
        lease_duration=timedelta(seconds=30),
        now=completed_at,
    )
    service.start(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        lease_token=lease_token,
        now=completed_at,
    )
    operation = service.succeed(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        lease_token=lease_token,
        output_ref="mysql://operation-results/http-operation",
        provider_request_id="  provider-http-operation  ",
        now=completed_at + timedelta(microseconds=1),
    )

    with TestClient(create_app(api_settings(integration_settings))) as client:
        principal = trusted_principal_header(
            actor_id="operator-a",
            workspace_ids=[operation.workspace_id],
        )
        response = client.get(
            f"/api/v1/operations/{operation.id}",
            headers={"X-Workspace-Id": operation.workspace_id, **principal},
        )
        hidden = client.get(
            f"/api/v1/operations/{operation.id}",
            headers={"X-Workspace-Id": "workspace-other", **principal},
        )
        scoped_not_found = client.get(
            f"/api/v1/operations/{operation.id}",
            headers={
                "X-Workspace-Id": "workspace-other",
                **trusted_principal_header(
                    actor_id="operator-a",
                    workspace_ids=[operation.workspace_id, "workspace-other"],
                ),
            },
        )
        spoofed = client.get(
            f"/api/v1/operations/{operation.id}",
            headers={
                "X-Workspace-Id": operation.workspace_id,
                "X-Actor-Id": "admin-a",
            },
        )
        invalid_signature = client.get(
            f"/api/v1/operations/{operation.id}",
            headers={
                "X-Workspace-Id": operation.workspace_id,
                "X-Trusted-Principal": "invalid.signature",
            },
        )
        expired = client.get(
            f"/api/v1/operations/{operation.id}",
            headers={
                "X-Workspace-Id": operation.workspace_id,
                **trusted_principal_header(
                    actor_id="operator-a",
                    workspace_ids=[operation.workspace_id],
                    issued_at=1,
                ),
            },
        )

    assert response.status_code == 200
    assert response.json()["id"] == operation.id
    assert response.json()["state"] == "SUCCEEDED"
    assert response.json()["provider_request_id"] == "provider-http-operation"
    assert response.json()["error"] is None
    assert hidden.status_code == 403
    assert hidden.json()["code"] == "WORKSPACE_ACCESS_DENIED"
    assert scoped_not_found.status_code == 404
    assert scoped_not_found.json()["code"] == "NOT_FOUND"
    assert spoofed.status_code == 401
    assert spoofed.json()["code"] == "AUTHENTICATION_REQUIRED"
    assert invalid_signature.status_code == 401
    assert expired.status_code == 401


@pytest.mark.parametrize(
    "replay_case",
    [
        "transport_retryable",
        "transport_retryable_settlement",
        "transport_execution_terminal",
        "transport_reconciling",
        "transport_reconciliation_terminal",
        "terminal_operation",
    ],
)
def test_operator_replay_distinguishes_transport_and_terminal_operation_budgets(
    integration_database,
    integration_settings,
    replay_case: str,
) -> None:
    service = _operation_service(integration_database)
    operation = service.create(
        OperationCreateCommand(
            workspace_id=f"workspace-http-operation-replay-{replay_case}",
            kind=OperationKind.RECONCILIATION,
            target_type="provider_request",
            target_id=f"http-operation-replay-{replay_case}",
            target_version=1,
            input_hash=hashlib.sha256(replay_case.encode()).hexdigest(),
            input_ref=None,
            max_attempts=1 if replay_case == "terminal_operation" else 3,
            max_reconciliation_attempts=4,
        )
    )
    transitioned_at = operation.created_at + timedelta(microseconds=1)
    lease_token = service.claim(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        owner=f"prepare-{replay_case}",
        lease_duration=timedelta(seconds=30),
        now=transitioned_at,
    )
    running = service.start(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        lease_token=lease_token,
        now=transitioned_at,
    )
    if replay_case in {
        "transport_retryable",
        "transport_retryable_settlement",
        "transport_execution_terminal",
    }:
        prepared = service.fail(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
            lease_token=lease_token,
            error=NormalizedOperationError(
                code="RETRYABLE_PROVIDER_FAILURE",
                category="provider",
                message="provider is temporarily unavailable",
                retryable=True,
            ),
            retry_at=transitioned_at,
            expected_execution_version=running.version,
            expected_attempt_count=running.attempt_count,
            now=transitioned_at,
        )
        source_event = _recovery_event_for_operation(
            integration_database,
            operation_id=operation.id,
            now=transitioned_at,
        )
        source_dead_letter = _transport_dead_letter(
            integration_database,
            event=source_event,
            suffix=replay_case,
        )
    elif replay_case in {"transport_reconciling", "transport_reconciliation_terminal"}:
        prepared = service.require_reconciliation(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
            lease_token=lease_token,
            error=NormalizedOperationError(
                code="EXTERNAL_OUTCOME_UNKNOWN",
                category="provider",
                message="provider outcome is unknown",
                retryable=True,
            ),
            expected_execution_version=running.version,
            expected_attempt_count=running.attempt_count,
            now=transitioned_at,
        )
        source_event = _recovery_event_for_operation(
            integration_database,
            operation_id=operation.id,
            now=transitioned_at,
        )
        source_dead_letter = _transport_dead_letter(
            integration_database,
            event=source_event,
            suffix=replay_case,
        )
    else:
        prepared = service.fail(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
            lease_token=lease_token,
            error=NormalizedOperationError(
                code="TERMINAL_PROVIDER_FAILURE",
                category="provider",
                message="provider permanently rejected the operation",
                retryable=False,
            ),
            retry_at=None,
            expected_execution_version=running.version,
            expected_attempt_count=running.attempt_count,
            now=transitioned_at,
        )
        assert prepared.dead_letter_id is not None
        with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
            source_dead_letter = uow.dead_letters.get_by_id(
                workspace_id=operation.workspace_id,
                dead_letter_id=prepared.dead_letter_id,
            )
        assert source_dead_letter is not None

    initial_attempt_count = prepared.attempt_count
    initial_max_attempts = prepared.max_attempts
    initial_reconciliation_attempt_count = prepared.reconciliation_attempt_count
    initial_max_reconciliation_attempts = prepared.max_reconciliation_attempts
    replay_event = _replay_event_through_api(
        integration_database,
        integration_settings,
        dead_letter=source_dead_letter,
        idempotency_key=f"http-operation-replay-{replay_case}-0001",
    )
    executor = BlockingOperationReplayExecutor(
        reconcile_only=replay_case
        in {"transport_reconciling", "transport_reconciliation_terminal"},
        fail_execution=replay_case
        in {
            "terminal_operation",
            "transport_retryable_settlement",
            "transport_execution_terminal",
        },
        failure_retryable=replay_case == "transport_retryable_settlement",
        reconciliation_outcome=(
            ReconciliationOutcome.CONFIRMED_FAILURE
            if replay_case == "transport_reconciliation_terminal"
            else ReconciliationOutcome.CONFIRMED_SUCCESS
        ),
    )
    clock = MutableClock(datetime.now(UTC))
    workers = [
        DurableOperationWorker(
            operations=service,
            execution=OperationExecutionBoundary(
                executor=executor,
                transaction_active=is_unit_of_work_active,
            ),
            owner=f"http-operation-replay-{replay_case}-{index}",
            lease_duration=timedelta(seconds=30),
            clock=clock,
        )
        for index in range(2)
    ]
    barrier = Barrier(2)

    def deliver(worker: DurableOperationWorker):
        barrier.wait()
        return worker.handle_recovery_event(replay_event)

    errors: list[Exception] = []
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(deliver, worker) for worker in workers]
            assert executor.started.wait(timeout=2)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not any(future.done() for future in futures):
                time.sleep(0.01)
            during_provider = service.get(
                workspace_id=operation.workspace_id,
                operation_id=operation.id,
            )
            if replay_case == "terminal_operation":
                assert during_provider.max_attempts == initial_max_attempts + 1
            else:
                assert during_provider.max_attempts == initial_max_attempts
                assert (
                    during_provider.recovery_generation
                    == during_provider.recovery_consumed_generation
                    == 1
                )
            executor.release.set()
            for future in futures:
                try:
                    future.result(timeout=2)
                except Exception as exc:
                    errors.append(exc)
    finally:
        executor.release.set()

    settled = service.get(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )
    if replay_case == "transport_retryable_settlement":
        assert settled.next_attempt_at is not None
        clock.current = settled.next_attempt_at
    redelivered = workers[0].handle_recovery_event(replay_event)
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        children = uow.dead_letters.list_children(
            source_dead_letter_id=source_dead_letter.id,
            workspace_id=operation.workspace_id,
            limit=10,
            cursor=None,
        )
        terminal_events = [uow.outbox.get(child.message_id) for child in children]

    assert errors == []
    assert executor.execute_calls + executor.reconcile_calls == 1
    assert redelivered.max_attempts == (
        initial_max_attempts + 1 if replay_case == "terminal_operation" else initial_max_attempts
    )
    assert redelivered.max_reconciliation_attempts == initial_max_reconciliation_attempts
    if replay_case in {"transport_reconciling", "transport_reconciliation_terminal"}:
        assert redelivered.attempt_count == initial_attempt_count
        assert redelivered.reconciliation_attempt_count == initial_reconciliation_attempt_count + 1
    else:
        assert redelivered.attempt_count == initial_attempt_count + 1
        assert redelivered.reconciliation_attempt_count == initial_reconciliation_attempt_count
    if replay_case == "terminal_operation":
        assert redelivered.state == OperationState.FAILED
        assert len(children) == 1
        assert redelivered.dead_letter_id == children[0].id
    elif replay_case in {
        "transport_execution_terminal",
        "transport_reconciliation_terminal",
    }:
        assert redelivered.state == OperationState.FAILED
        assert redelivered.replay_source_dead_letter_id == source_dead_letter.id
        assert len(children) == 1
        assert redelivered.dead_letter_id == children[0].id
        assert children[0].source_dead_letter_id == source_dead_letter.id
        assert children[0].replay_attempt == replay_event.replay_attempt
        assert terminal_events[0] is not None
        assert terminal_events[0].source_dead_letter_id == source_dead_letter.id
        assert terminal_events[0].replay_attempt == replay_event.replay_attempt
    elif replay_case == "transport_retryable_settlement":
        assert redelivered.state == OperationState.RETRYABLE_FAILED
        assert redelivered.replay_source_dead_letter_id == source_dead_letter.id
        assert children == []
        assert redelivered.recovery_generation == redelivered.recovery_consumed_generation == 1
    else:
        assert redelivered.state == OperationState.SUCCEEDED
        assert redelivered.replay_source_dead_letter_id == source_dead_letter.id
        assert children == []
        assert redelivered.recovery_generation == redelivered.recovery_consumed_generation == 1


def test_transport_replay_resumes_after_marker_commit_without_duplicate_execution(
    integration_database,
    integration_settings,
) -> None:
    service = _operation_service(integration_database)
    operation = service.create(
        OperationCreateCommand(
            workspace_id="workspace-http-transport-replay-crash",
            kind=OperationKind.RECONCILIATION,
            target_type="provider_request",
            target_id="http-transport-replay-crash",
            target_version=1,
            input_hash=hashlib.sha256(b"transport-replay-crash").hexdigest(),
            input_ref=None,
            max_attempts=3,
            max_reconciliation_attempts=4,
        )
    )
    transitioned_at = operation.created_at + timedelta(microseconds=1)
    lease_token = service.claim(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        owner="prepare-transport-replay-crash",
        lease_duration=timedelta(seconds=30),
        now=transitioned_at,
    )
    running = service.start(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        lease_token=lease_token,
        now=transitioned_at,
    )
    prepared = service.fail(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        lease_token=lease_token,
        error=NormalizedOperationError(
            code="RETRYABLE_PROVIDER_FAILURE",
            category="provider",
            message="provider is temporarily unavailable",
            retryable=True,
        ),
        retry_at=transitioned_at,
        expected_execution_version=running.version,
        expected_attempt_count=running.attempt_count,
        now=transitioned_at,
    )
    source_event = _recovery_event_for_operation(
        integration_database,
        operation_id=operation.id,
        now=transitioned_at,
    )
    source_dead_letter = _transport_dead_letter(
        integration_database,
        event=source_event,
        suffix="crash-before-provider",
    )
    replay_event = _replay_event_through_api(
        integration_database,
        integration_settings,
        dead_letter=source_dead_letter,
        idempotency_key="http-transport-replay-crash-0001",
    )
    executor = BlockingOperationReplayExecutor(
        reconcile_only=False,
        fail_execution=False,
    )
    executor.release.set()
    worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="http-transport-replay-crash-worker",
        lease_duration=timedelta(seconds=30),
        clock=MutableClock(datetime.now(UTC)),
    )
    replayed_at = datetime.now(UTC)
    _, replay_applied, should_handle_replay = service.apply_recovery_replay(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        source_dead_letter_id=source_dead_letter.id,
        replay_attempt=replay_event.replay_attempt,
        replay_event_id=replay_event.envelope.event_id,
        recovery_generation=replay_event.envelope.payload["recovery_generation"],
        reconcile_only=False,
        execution_deadline_at=replayed_at + timedelta(hours=1),
        reconciliation_deadline_at=replayed_at + timedelta(hours=1),
        now=replayed_at,
    )
    assert replay_applied
    assert should_handle_replay

    after_marker = service.get(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
    )
    assert executor.execute_calls == 0
    assert after_marker.attempt_count == prepared.attempt_count
    assert after_marker.max_attempts == prepared.max_attempts
    assert after_marker.recovery_generation == after_marker.recovery_consumed_generation == 1

    resumed = worker.handle_recovery_event(replay_event)
    redelivered = worker.handle_recovery_event(replay_event)

    assert executor.execute_calls == 1
    assert resumed.state == redelivered.state == OperationState.SUCCEEDED
    assert resumed.attempt_count == redelivered.attempt_count == prepared.attempt_count + 1
    assert resumed.max_attempts == redelivered.max_attempts == prepared.max_attempts


@pytest.mark.parametrize(
    "post_terminal_write",
    ["recovery_generation", "late_provider_provenance"],
)
def test_terminal_replay_preparation_survives_unrelated_operation_version_writes(
    integration_database,
    integration_settings,
    post_terminal_write: str,
) -> None:
    service = _operation_service(integration_database)
    operation = service.create(
        OperationCreateCommand(
            workspace_id=f"workspace-terminal-replay-liveness-{post_terminal_write}",
            kind=OperationKind.RECONCILIATION,
            target_type="provider_request",
            target_id=f"terminal-replay-liveness-{post_terminal_write}",
            target_version=1,
            input_hash=hashlib.sha256(post_terminal_write.encode()).hexdigest(),
            input_ref=None,
            max_attempts=2 if post_terminal_write == "recovery_generation" else 1,
            max_reconciliation_attempts=4,
        )
    )
    first_attempt_at = operation.created_at + timedelta(microseconds=1)
    first_lease_token = service.claim(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        owner=f"prepare-terminal-replay-liveness-{post_terminal_write}",
        lease_duration=timedelta(seconds=30),
        now=first_attempt_at,
    )
    first_running = service.start(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        lease_token=first_lease_token,
        now=first_attempt_at,
    )
    terminal_lease_token = first_lease_token
    terminal_running = first_running
    recovery_generation = 0
    if post_terminal_write == "recovery_generation":
        retryable = service.fail(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
            lease_token=first_lease_token,
            error=NormalizedOperationError(
                code="RETRYABLE_BEFORE_TERMINAL_REPLAY",
                category="provider",
                message="reserve recovery work before the terminal attempt",
                retryable=True,
            ),
            retry_at=first_attempt_at,
            expected_execution_version=first_running.version,
            expected_attempt_count=first_running.attempt_count,
            now=first_attempt_at,
        )
        recovery_event = _recovery_event_for_operation(
            integration_database,
            operation_id=operation.id,
            now=first_attempt_at,
        )
        recovery_generation = recovery_event.envelope.payload["recovery_generation"]
        terminal_lease_token = service.retry(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
            owner="prepare-terminal-replay-after-recovery-generation",
            lease_duration=timedelta(seconds=30),
            now=first_attempt_at,
        )
        terminal_running = service.start(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
            lease_token=terminal_lease_token,
            now=first_attempt_at,
        )
        assert terminal_running.attempt_count == retryable.attempt_count + 1

    terminal = service.fail(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        lease_token=terminal_lease_token,
        error=NormalizedOperationError(
            code="TERMINAL_BEFORE_REPLAY",
            category="provider",
            message="operation failed before its terminal dead letter was replayed",
            retryable=False,
        ),
        retry_at=None,
        expected_execution_version=terminal_running.version,
        expected_attempt_count=terminal_running.attempt_count,
        now=first_attempt_at,
    )
    assert terminal.dead_letter_id is not None
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        source_dead_letter = uow.dead_letters.get_by_id(
            workspace_id=operation.workspace_id,
            dead_letter_id=terminal.dead_letter_id,
        )
    assert source_dead_letter is not None

    late_provider_request_id = None
    if post_terminal_write == "recovery_generation":
        after_unrelated_write = service.consume_recovery_generation(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
            generation=recovery_generation,
            now=first_attempt_at + timedelta(microseconds=1),
        )
        assert after_unrelated_write.recovery_consumed_generation == recovery_generation
    else:
        late_provider_request_id = f"late-provider-{operation.id}"
        after_unrelated_write = service.succeed(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
            lease_token=terminal_lease_token,
            output_ref=None,
            provider_request_id=late_provider_request_id,
            expected_execution_version=terminal_running.version,
            expected_attempt_count=terminal_running.attempt_count,
            now=first_attempt_at + timedelta(microseconds=1),
        )
        assert after_unrelated_write.state == OperationState.FAILED
        assert after_unrelated_write.provider_request_id == late_provider_request_id

    replay_event = _replay_event_through_api(
        integration_database,
        integration_settings,
        dead_letter=source_dead_letter,
        idempotency_key=f"terminal-replay-liveness-{post_terminal_write}-0001",
    )
    replayed_at = datetime.now(UTC)
    prepared, replay_applied, should_execute = service.apply_recovery_replay(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        source_dead_letter_id=source_dead_letter.id,
        replay_attempt=replay_event.replay_attempt,
        replay_event_id=replay_event.envelope.event_id,
        recovery_generation=0,
        reconcile_only=False,
        execution_deadline_at=replayed_at + timedelta(hours=1),
        reconciliation_deadline_at=replayed_at + timedelta(hours=1),
        now=replayed_at,
    )
    assert replay_applied
    assert should_execute
    assert prepared.state == OperationState.RETRYABLE_FAILED
    assert prepared.max_attempts == terminal.max_attempts + 1
    with integration_database.session_factory() as session:
        prepared_lifecycle = session.scalar(
            select(DeadLetterReplayModel).where(
                DeadLetterReplayModel.replay_event_id == replay_event.envelope.event_id
            )
        )
    assert prepared_lifecycle is not None
    assert prepared_lifecycle.lifecycle_state == "PREPARED"
    assert prepared_lifecycle.operation_id == operation.id
    assert prepared_lifecycle.preparation_kind == "TERMINAL_OPERATION"
    assert prepared_lifecycle.work_kind == "EXECUTION"
    assert prepared_lifecycle.prepared_operation_version == prepared.version
    assert prepared_lifecycle.claim_token is None
    assert prepared_lifecycle.completed_at is None

    executor = BlockingOperationReplayExecutor(
        reconcile_only=False,
        fail_execution=False,
    )
    executor.release.set()
    worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner=f"terminal-replay-liveness-{post_terminal_write}",
        lease_duration=timedelta(seconds=30),
    )
    resumed = worker.handle_recovery_event(replay_event)
    redelivered = worker.handle_recovery_event(replay_event)
    with integration_database.session_factory() as session:
        completed_lifecycle = session.scalar(
            select(DeadLetterReplayModel).where(
                DeadLetterReplayModel.replay_event_id == replay_event.envelope.event_id
            )
        )

    assert executor.execute_calls == 1
    assert resumed.state == redelivered.state == OperationState.SUCCEEDED
    assert resumed.attempt_count == terminal.attempt_count + 1
    assert resumed.max_attempts == terminal.max_attempts + 1
    assert resumed.replay_source_dead_letter_id == source_dead_letter.id
    assert resumed.replay_attempt == replay_event.replay_attempt
    assert completed_lifecycle is not None
    assert completed_lifecycle.lifecycle_state == "COMPLETED"
    assert completed_lifecycle.claim_token is None
    assert (
        completed_lifecycle.prepared_operation_version
        < completed_lifecycle.claimed_operation_version
        < completed_lifecycle.completed_operation_version
    )
    assert completed_lifecycle.claimed_at is not None
    assert completed_lifecycle.completed_at is not None
    if late_provider_request_id is not None:
        assert resumed.provider_request_id == late_provider_request_id


@pytest.mark.parametrize("replay_origin", ["transport", "terminal"])
def test_replay_marker_winner_settles_when_other_delivery_wins_provider_claim(
    integration_database,
    integration_settings,
    monkeypatch,
    replay_origin: str,
) -> None:
    marker_service = _operation_service(integration_database)
    claim_service = _operation_service(integration_database)
    operation = marker_service.create(
        OperationCreateCommand(
            workspace_id=f"workspace-http-replay-marker-claim-race-{replay_origin}",
            kind=OperationKind.RECONCILIATION,
            target_type="provider_request",
            target_id=f"http-replay-marker-claim-race-{replay_origin}",
            target_version=1,
            input_hash=hashlib.sha256(
                f"replay-marker-claim-race-{replay_origin}".encode()
            ).hexdigest(),
            input_ref=None,
            max_attempts=1 if replay_origin == "terminal" else 3,
            max_reconciliation_attempts=4,
        )
    )
    transitioned_at = operation.created_at + timedelta(microseconds=1)
    lease_token = marker_service.claim(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        owner="prepare-replay-marker-claim-race",
        lease_duration=timedelta(seconds=30),
        now=transitioned_at,
    )
    running = marker_service.start(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        lease_token=lease_token,
        now=transitioned_at,
    )
    if replay_origin == "transport":
        prepared = marker_service.fail(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
            lease_token=lease_token,
            error=NormalizedOperationError(
                code="RETRYABLE_PROVIDER_FAILURE",
                category="provider",
                message="provider is temporarily unavailable",
                retryable=True,
            ),
            retry_at=transitioned_at,
            expected_execution_version=running.version,
            expected_attempt_count=running.attempt_count,
            now=transitioned_at,
        )
        source_event = _recovery_event_for_operation(
            integration_database,
            operation_id=operation.id,
            now=transitioned_at,
        )
        source_dead_letter = _transport_dead_letter(
            integration_database,
            event=source_event,
            suffix=f"marker-claim-race-{replay_origin}",
        )
    else:
        prepared = marker_service.fail(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
            lease_token=lease_token,
            error=NormalizedOperationError(
                code="TERMINAL_PROVIDER_FAILURE",
                category="provider",
                message="provider permanently rejected the operation",
                retryable=False,
            ),
            retry_at=None,
            expected_execution_version=running.version,
            expected_attempt_count=running.attempt_count,
            now=transitioned_at,
        )
        assert prepared.dead_letter_id is not None
        with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
            source_dead_letter = uow.dead_letters.get_by_id(
                workspace_id=operation.workspace_id,
                dead_letter_id=prepared.dead_letter_id,
            )
        assert source_dead_letter is not None
    replay_event = _replay_event_through_api(
        integration_database,
        integration_settings,
        dead_letter=source_dead_letter,
        idempotency_key=f"http-replay-marker-claim-race-{replay_origin}-0001",
    )
    inbox_consumer = f"http-replay-marker-claim-race-{replay_origin}"
    inbox = InboxCoordinator(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory),
        consumer=inbox_consumer,
        owner="http-replay-marker-claim-race-inbox",
        lease_duration=timedelta(seconds=30),
        max_attempts=3,
    )
    inbox_claim, claimed_event = inbox.claim(replay_event.envelope.event_id)
    assert claimed_event.envelope.event_id == replay_event.envelope.event_id
    assert inbox_claim.should_process
    assert inbox_claim.lease_token is not None
    executor = BlockingOperationReplayExecutor(
        reconcile_only=False,
        fail_execution=False,
    )
    marker_committed = Barrier(2)
    provider_claimed = Barrier(2)
    apply_recovery_replay = marker_service.apply_recovery_replay

    def pause_marker_winner(**kwargs):
        result = apply_recovery_replay(**kwargs)
        marker_committed.wait(timeout=5)
        provider_claimed.wait(timeout=5)
        return result

    monkeypatch.setattr(marker_service, "apply_recovery_replay", pause_marker_winner)
    marker_worker = DurableOperationWorker(
        operations=marker_service,
        execution=OperationExecutionBoundary(
            executor=executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="http-replay-marker-winner",
        lease_duration=timedelta(seconds=30),
    )
    claim_worker = DurableOperationWorker(
        operations=claim_service,
        execution=OperationExecutionBoundary(
            executor=executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="http-replay-claim-winner",
        lease_duration=timedelta(seconds=30),
    )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            marker_future = pool.submit(marker_worker.handle_recovery_event, replay_event)
            marker_committed.wait(timeout=5)
            claim_future = pool.submit(claim_worker.handle_recovery_event, replay_event)
            assert executor.started.wait(timeout=5)
            during_provider = claim_service.get(
                workspace_id=operation.workspace_id,
                operation_id=operation.id,
            )
            assert during_provider.state == OperationState.RUNNING
            assert during_provider.max_attempts == (
                prepared.max_attempts + 1 if replay_origin == "terminal" else prepared.max_attempts
            )
            with pytest.raises(LeaseConflictError, match="claim token does not match"):
                marker_service.complete_recovery_replay(
                    workspace_id=operation.workspace_id,
                    operation_id=operation.id,
                    source_dead_letter_id=source_dead_letter.id,
                    replay_attempt=replay_event.replay_attempt,
                    replay_event_id=replay_event.envelope.event_id,
                    claim_token="stale-replay-claim-token",
                )
            provider_claimed.wait(timeout=5)
            marker_result = marker_future.result(timeout=5)
            executor.release.set()
            claim_result = claim_future.result(timeout=5)
    finally:
        executor.release.set()

    inbox.mark_processed(replay_event.envelope.event_id, inbox_claim.lease_token)
    monkeypatch.setattr(marker_service, "apply_recovery_replay", apply_recovery_replay)
    redelivered = marker_worker.handle_recovery_event(replay_event)
    with integration_database.session_factory() as session:
        replay_marker = session.scalar(
            select(DeadLetterReplayModel).where(
                DeadLetterReplayModel.replay_event_id == replay_event.envelope.event_id
            )
        )
        inbox_message = session.get(
            InboxMessageModel,
            (inbox_consumer, replay_event.envelope.event_id),
        )
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        children = uow.dead_letters.list_children(
            source_dead_letter_id=source_dead_letter.id,
            workspace_id=operation.workspace_id,
            limit=10,
            cursor=None,
        )

    assert marker_result.state == OperationState.RUNNING
    assert claim_result.state == redelivered.state == OperationState.SUCCEEDED
    assert executor.execute_calls == 1
    assert replay_marker is not None
    assert replay_marker.prepared_at is not None
    assert replay_marker.lifecycle_state == "COMPLETED"
    assert replay_marker.operation_id == operation.id
    assert replay_marker.preparation_kind == (
        "TERMINAL_OPERATION" if replay_origin == "terminal" else "TRANSPORT"
    )
    assert replay_marker.work_kind == "EXECUTION"
    assert replay_marker.claim_token is None
    assert (
        replay_marker.prepared_operation_version
        < replay_marker.claimed_operation_version
        < replay_marker.completed_operation_version
    )
    assert inbox_message is not None
    assert inbox_message.status == "PROCESSED"
    assert inbox_message.delivery_attempts == 1
    assert inbox_message.processed_at is not None
    assert redelivered.replay_source_dead_letter_id == source_dead_letter.id
    if replay_origin == "transport":
        assert redelivered.recovery_generation == redelivered.recovery_consumed_generation == 1
    else:
        assert redelivered.recovery_generation == redelivered.recovery_consumed_generation == 0
    assert redelivered.attempt_count == prepared.attempt_count + 1
    assert redelivered.max_attempts == (
        prepared.max_attempts + 1 if replay_origin == "terminal" else prepared.max_attempts
    )
    assert children == []


@pytest.mark.parametrize(
    ("claim_origin", "reconciliation_outcome"),
    [
        ("same_owner", ReconciliationOutcome.CONFIRMED_SUCCESS),
        ("same_owner", ReconciliationOutcome.CONFIRMED_FAILURE),
        ("unrelated_owner", ReconciliationOutcome.CONFIRMED_SUCCESS),
        ("unrelated_owner", ReconciliationOutcome.CONFIRMED_FAILURE),
    ],
)
def test_claimed_replay_crash_converges_through_lease_recovery(
    integration_database,
    integration_settings,
    claim_origin: str,
    reconciliation_outcome: ReconciliationOutcome,
) -> None:
    service = _operation_service(integration_database)
    operation = service.create(
        OperationCreateCommand(
            workspace_id=f"workspace-claimed-replay-crash-{claim_origin}",
            kind=OperationKind.RECONCILIATION,
            target_type="provider_request",
            target_id=f"claimed-replay-crash-{claim_origin}",
            target_version=1,
            input_hash=hashlib.sha256(claim_origin.encode()).hexdigest(),
            input_ref=None,
            max_attempts=1,
            max_reconciliation_attempts=2,
        )
    )
    failed_at = operation.created_at + timedelta(microseconds=1)
    initial_token = service.claim(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        owner="prepare-claimed-replay-crash",
        lease_duration=timedelta(seconds=30),
        now=failed_at,
    )
    running = service.start(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        lease_token=initial_token,
        now=failed_at,
    )
    terminal = service.fail(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        lease_token=initial_token,
        error=NormalizedOperationError(
            code="TERMINAL_BEFORE_CLAIM_CRASH",
            category="provider",
            message="operation failed before replay claim crash",
            retryable=False,
        ),
        retry_at=None,
        expected_execution_version=running.version,
        expected_attempt_count=running.attempt_count,
        now=failed_at,
    )
    assert terminal.dead_letter_id is not None
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        source_dead_letter = uow.dead_letters.get_by_id(
            workspace_id=operation.workspace_id,
            dead_letter_id=terminal.dead_letter_id,
        )
    assert source_dead_letter is not None
    replay_event = _replay_event_through_api(
        integration_database,
        integration_settings,
        dead_letter=source_dead_letter,
        idempotency_key=f"claimed-replay-crash-{claim_origin}-0001",
    )
    published_at = datetime.now(UTC)
    with integration_database.session_factory.begin() as session:
        session.execute(
            update(OutboxEventModel)
            .where(OutboxEventModel.id == replay_event.envelope.event_id)
            .values(published_at=published_at)
        )

    consumer = f"claimed-replay-crash-{claim_origin}"
    initial_inbox = InboxCoordinator(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory),
        consumer=consumer,
        owner="initial-replay-delivery",
        lease_duration=timedelta(microseconds=1),
        max_attempts=3,
    )
    initial_inbox_claim, _ = initial_inbox.claim(replay_event.envelope.event_id)
    assert initial_inbox_claim.should_process
    assert initial_inbox_claim.lease_token is not None

    replayed_at = datetime.now(UTC)
    prepared, replay_applied, should_execute = service.apply_recovery_replay(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        source_dead_letter_id=source_dead_letter.id,
        replay_attempt=replay_event.replay_attempt,
        replay_event_id=replay_event.envelope.event_id,
        recovery_generation=0,
        reconcile_only=False,
        execution_deadline_at=replayed_at + timedelta(hours=1),
        reconciliation_deadline_at=replayed_at + timedelta(hours=1),
        now=replayed_at,
    )
    assert replay_applied
    assert should_execute
    claim_at = replayed_at + timedelta(microseconds=1)
    replay_owner = "claimed-replay-crash-worker"
    if claim_origin == "unrelated_owner":
        unrelated_token = service.retry(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
            owner="unrelated-operation-owner",
            lease_duration=timedelta(seconds=1),
            now=claim_at,
        )
        service.start(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
            lease_token=unrelated_token,
            now=claim_at,
        )
    claim = service.claim_recovery_replay(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        source_dead_letter_id=source_dead_letter.id,
        replay_attempt=replay_event.replay_attempt,
        replay_event_id=replay_event.envelope.event_id,
        recovery_generation=0,
        reconcile_only=False,
        owner=replay_owner,
        lease_duration=timedelta(seconds=1),
        now=claim_at,
    )
    assert claim.lease_token is not None
    assert claim.provider_claimed is (claim_origin == "same_owner")

    executor = BlockingOperationReplayExecutor(
        reconcile_only=False,
        fail_execution=False,
    )
    executor.release.set()
    redelivery_worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner=replay_owner,
        lease_duration=timedelta(seconds=1),
    )
    redelivery_inbox = InboxCoordinator(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory),
        consumer=consumer,
        owner=(
            "initial-replay-delivery"
            if claim_origin == "same_owner"
            else "replacement-replay-delivery"
        ),
        lease_duration=timedelta(seconds=30),
        max_attempts=3,
    )
    redelivery_claim, _ = redelivery_inbox.claim(replay_event.envelope.event_id)
    assert redelivery_claim.should_process
    assert redelivery_claim.lease_token is not None
    redelivered = redelivery_worker.handle_recovery_event(replay_event)
    redelivery_inbox.mark_processed(
        replay_event.envelope.event_id,
        redelivery_claim.lease_token,
    )
    assert redelivered.state == OperationState.RUNNING
    assert executor.execute_calls == 0

    scanner = OperationRecoveryService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(integration_database.session_factory),
        batch_size=1,
    )
    recovered_at = claim_at + timedelta(seconds=1)
    assert scanner.recover_once(now=recovered_at) == 1
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        recovery_events = [
            event
            for event in uow.outbox.list_for_aggregate(operation.id)
            if (
                event.envelope.event_type == EventType.OPERATION_RECOVERY_REQUESTED.value
                and event.envelope.event_id != replay_event.envelope.event_id
                and event.envelope.payload["recovery_generation"] > 0
            )
        ]
    assert len(recovery_events) == 1

    reconciliation_executor = BlockingOperationReplayExecutor(
        reconcile_only=True,
        fail_execution=False,
        reconciliation_outcome=reconciliation_outcome,
    )
    reconciliation_executor.release.set()
    reconciliation_worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=reconciliation_executor,
            transaction_active=is_unit_of_work_active,
        ),
        owner="claimed-replay-reconciliation-worker",
        lease_duration=timedelta(seconds=30),
        clock=MutableClock(recovered_at + timedelta(microseconds=1)),
    )
    recovered = reconciliation_worker.handle_recovery_event(recovery_events[0])
    with integration_database.session_factory() as session:
        replay_lifecycle = session.scalar(
            select(DeadLetterReplayModel).where(
                DeadLetterReplayModel.replay_event_id == replay_event.envelope.event_id
            )
        )
        replay_inbox = session.get(
            InboxMessageModel,
            (consumer, replay_event.envelope.event_id),
        )
    assert replay_lifecycle is not None
    completed_snapshot = (
        replay_lifecycle.lifecycle_state,
        replay_lifecycle.claim_token,
        replay_lifecycle.completed_at,
        replay_lifecycle.completed_operation_version,
    )
    service.complete_recovery_replay(
        workspace_id=operation.workspace_id,
        operation_id=operation.id,
        source_dead_letter_id=source_dead_letter.id,
        replay_attempt=replay_event.replay_attempt,
        replay_event_id=replay_event.envelope.event_id,
        claim_token=claim.lease_token,
        now=recovered_at + timedelta(days=1),
    )
    with integration_database.session_factory() as session:
        repeated_lifecycle = session.scalar(
            select(DeadLetterReplayModel).where(
                DeadLetterReplayModel.replay_event_id == replay_event.envelope.event_id
            )
        )
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        child_dead_letters = uow.dead_letters.list_children(
            source_dead_letter_id=source_dead_letter.id,
            workspace_id=operation.workspace_id,
            limit=10,
            cursor=None,
        )

    assert reconciliation_executor.reconcile_calls == 1
    assert replay_lifecycle.lifecycle_state == "COMPLETED"
    assert replay_lifecycle.claim_token is None
    assert replay_lifecycle.completed_at is not None
    assert replay_lifecycle.completed_operation_version is not None
    assert replay_inbox is not None
    assert replay_inbox.status == "PROCESSED"
    assert replay_inbox.delivery_attempts == 2
    assert replay_inbox.processed_at is not None
    assert repeated_lifecycle is not None
    assert (
        repeated_lifecycle.lifecycle_state,
        repeated_lifecycle.claim_token,
        repeated_lifecycle.completed_at,
        repeated_lifecycle.completed_operation_version,
    ) == completed_snapshot
    if reconciliation_outcome == ReconciliationOutcome.CONFIRMED_SUCCESS:
        assert recovered.state == OperationState.SUCCEEDED
        assert child_dead_letters == []
    else:
        assert recovered.state == OperationState.FAILED
        assert len(child_dead_letters) == 1
        assert child_dead_letters[0].source_dead_letter_id == source_dead_letter.id


def seed_http_dead_letter(
    integration_database,
    *,
    workspace_id: str = "workspace-http-dlq",
) -> DeadLetterMessage:
    now = datetime(2026, 7, 23, 12, 0, 0, 123456, tzinfo=UTC)
    event = OutboxEvent(
        envelope=EventEnvelope.create(
            event_type="workflow.run.requested",
            aggregate_type="workflow",
            aggregate_id="workflow-http-dlq",
            aggregate_version=1,
            trace_id="trace-http-dlq",
            payload={"workflow_id": "workflow-http-dlq", "action": "recover"},
            now=now,
        ),
        available_at=now,
        workspace_id=workspace_id,
    )
    dead_letter = DeadLetterMessage.create(
        consumer="worker-http",
        message_id=event.envelope.event_id,
        event_type=event.envelope.event_type,
        payload=event.envelope.payload,
        reason="unsupported event version",
        attempt_count=1,
        original_created_at=now,
        workspace_id=workspace_id,
        now=now,
    )
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        uow.outbox.add(event)
        uow.dead_letters.add(dead_letter)
        uow.commit()
    return dead_letter


def _workspace_http_headers(
    workspace_id: str,
    **headers: str,
) -> Headers:
    return Headers(
        [
            (b"X-Workspace-Id", workspace_id.encode("latin-1")),
            *[(name.encode("ascii"), value.encode("ascii")) for name, value in headers.items()],
        ]
    )


@pytest.mark.parametrize(
    ("stored_workspace_id", "request_workspace_id"),
    [
        ("Workspace-X", "workspace-x"),
    ],
)
def test_workspace_identity_is_exact_across_http_and_mysql_surfaces(
    integration_database,
    integration_settings,
    stored_workspace_id: str,
    request_workspace_id: str,
) -> None:
    product_payload = {
        "source_namespace": "MANUAL",
        "external_id": "EXACT-WORKSPACE-PRODUCT",
        "source_version": "v1",
        "title": "Exact workspace product",
        "category_code": "security.identity",
        "brand": "Boundary",
        "attributes": {"probe": "workspace"},
        "expires_at": None,
    }
    sku_payload = {
        **product_payload,
        "external_id": "EXACT-WORKSPACE-SKU",
        "title": "Cross-workspace SKU probe",
    }
    workflow_payload = {
        "workflow_type": "FIXTURE_IMAGE_GENERATION",
        "input_data": {"probe": "workspace"},
        "retention_hours": 72,
    }
    operation = _operation_service(integration_database).create(
        OperationCreateCommand(
            workspace_id=stored_workspace_id,
            kind=OperationKind.COLLECTION_REBUILD,
            target_type="collection",
            target_id="exact-workspace-operation",
            target_version=1,
            input_hash=hashlib.sha256(stored_workspace_id.encode()).hexdigest(),
            input_ref=None,
            max_attempts=2,
        )
    )
    dead_letter = seed_http_dead_letter(
        integration_database,
        workspace_id=stored_workspace_id,
    )
    stored_headers = _workspace_http_headers(
        stored_workspace_id,
        **{
            "X-Actor-Id": "workspace-identity-user",
            "Idempotency-Key": "workspace-identity-shared-key",
        },
    )
    request_headers = _workspace_http_headers(
        request_workspace_id,
        **{
            "X-Actor-Id": "workspace-identity-user",
            "Idempotency-Key": "workspace-identity-shared-key",
        },
    )
    operator_headers = _workspace_http_headers(
        request_workspace_id,
        **{
            "Idempotency-Key": "workspace-identity-replay-key",
            **trusted_principal_header(
                actor_id="workspace-identity-admin",
                workspace_ids=[request_workspace_id],
                admin_workspace_ids=[request_workspace_id],
            ),
        },
    )
    operation_headers = _workspace_http_headers(
        request_workspace_id,
        **trusted_principal_header(
            actor_id="workspace-identity-reader",
            workspace_ids=[request_workspace_id],
        ),
    )

    with TestClient(create_app(api_settings(integration_settings))) as client:
        stored_product = client.post(
            "/api/v1/products",
            headers=stored_headers,
            json=product_payload,
        )
        hidden_product = client.get(
            f"/api/v1/products/{stored_product.json()['id']}",
            headers=_workspace_http_headers(request_workspace_id),
        )
        cross_workspace_sku = client.post(
            f"/api/v1/products/{stored_product.json()['id']}/skus",
            headers=_workspace_http_headers(
                request_workspace_id,
                **{
                    "X-Actor-Id": "workspace-identity-user",
                    "Idempotency-Key": "workspace-identity-sku-key",
                },
            ),
            json=sku_payload,
        )
        request_product = client.post(
            "/api/v1/products",
            headers=request_headers,
            json=product_payload,
        )

        stored_workflow = client.post(
            "/api/v1/workflows",
            headers=stored_headers,
            json=workflow_payload,
        )
        hidden_workflow = client.get(
            f"/api/v1/workflows/{stored_workflow.json()['id']}",
            headers=_workspace_http_headers(request_workspace_id),
        )
        request_workflow = client.post(
            "/api/v1/workflows",
            headers=request_headers,
            json=workflow_payload,
        )

        hidden_operation = client.get(
            f"/api/v1/operations/{operation.id}",
            headers=operation_headers,
        )
        hidden_dead_letter = client.get(
            f"/api/v1/operator/dead-letters/{dead_letter.id}",
            headers=operator_headers,
        )
        hidden_replay = client.post(
            f"/api/v1/operator/dead-letters/{dead_letter.id}:replay",
            headers=operator_headers,
            json={"reason": "must not cross an exact workspace boundary"},
        )

    assert stored_product.status_code == 201
    assert hidden_product.status_code == 404
    assert cross_workspace_sku.status_code == 404
    assert request_product.status_code == 201
    assert request_product.json()["workspace_id"] == request_workspace_id
    assert request_product.json()["id"] != stored_product.json()["id"]
    assert stored_workflow.status_code == 202
    assert hidden_workflow.status_code == 404
    assert request_workflow.status_code == 202
    assert request_workflow.json()["workspace_id"] == request_workspace_id
    assert request_workflow.json()["id"] != stored_workflow.json()["id"]
    assert hidden_operation.status_code == 404
    assert hidden_dead_letter.status_code == 404
    assert hidden_replay.status_code == 404
    with integration_database.session_factory() as session:
        assert (
            session.scalar(
                select(DeadLetterReplayModel.id).where(
                    DeadLetterReplayModel.source_dead_letter_id == dead_letter.id
                )
            )
            is None
        )


@pytest.mark.parametrize(
    "invalid_workspace_id",
    [
        " workspace",
        "workspace ",
        "café",
        "cafe\u0301",
        "工作区",
    ],
)
def test_direct_application_writes_reject_invalid_workspace_identity(
    integration_database,
    integration_settings,
    invalid_workspace_id: str,
) -> None:
    product_request = ProductCreateRequestV1(
        source_namespace="MANUAL",
        external_id="INVALID-WORKSPACE-PRODUCT",
        source_version="v1",
        title="Invalid workspace product",
        category_code="security.identity",
        brand="Boundary",
        attributes={"probe": "workspace-contract"},
    )
    workflow_request = WorkflowCreateRequest(
        input_data={"probe": "workspace-contract"},
    )
    with pytest.raises(ValueError, match="workspace_id must match"):
        _operation_service(integration_database).create(
            OperationCreateCommand(
                workspace_id=invalid_workspace_id,
                kind=OperationKind.COLLECTION_REBUILD,
                target_type="collection",
                target_id="invalid-workspace-operation",
                target_version=1,
                input_hash=hashlib.sha256(invalid_workspace_id.encode()).hexdigest(),
                input_ref=None,
                max_attempts=2,
            )
        )

    app = create_app(api_settings(integration_settings))
    with TestClient(app):
        with pytest.raises(ValueError, match="workspace_id must match"):
            app.state.container.catalog.create_product(
                request=product_request,
                workspace_id=invalid_workspace_id,
                actor_id="workspace-contract-user",
                idempotency_key="invalid-workspace-product-key",
                trace_id="invalid-workspace-product",
            )
        with pytest.raises(ValueError, match="workspace_id must match"):
            app.state.container.workflows.create(
                request=workflow_request,
                workspace_id=invalid_workspace_id,
                actor_id="workspace-contract-user",
                idempotency_key="invalid-workspace-workflow-key",
                trace_id="invalid-workspace-workflow",
            )


def seed_child_dead_letter(
    integration_database,
    *,
    source: DeadLetterMessage,
    index: int,
    created_at: datetime,
) -> DeadLetterMessage:
    child = DeadLetterMessage.create(
        consumer=f"worker-http-child-{index}",
        message_id=f"message-http-child-{source.id}-{index}",
        event_type=source.event_type,
        payload=source.payload,
        reason=f"replay child failure {index}",
        attempt_count=index + 1,
        original_created_at=source.original_created_at,
        workspace_id=source.workspace_id,
        source_dead_letter_id=source.id,
        replay_attempt=index + 1,
        now=created_at,
    )
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        uow.dead_letters.add(child)
        uow.commit()
    return child


def test_dead_letter_child_history_is_complete_with_stable_bounded_paging(
    integration_database,
    integration_settings,
) -> None:
    wide_root = seed_http_dead_letter(integration_database)
    base_time = datetime(2026, 7, 23, 13, 0, tzinfo=UTC)
    wide_children = [
        seed_child_dead_letter(
            integration_database,
            source=wide_root,
            index=index,
            created_at=base_time + timedelta(microseconds=index),
        )
        for index in range(5)
    ]
    deep_root = seed_http_dead_letter(integration_database)
    deep_children = []
    parent = deep_root
    for index in range(40):
        parent = seed_child_dead_letter(
            integration_database,
            source=parent,
            index=100 + index,
            created_at=base_time + timedelta(seconds=1, microseconds=index),
        )
        deep_children.append(parent)

    headers = {
        "X-Workspace-Id": "workspace-http-dlq",
        **trusted_principal_header(
            actor_id="admin-history",
            workspace_ids=["workspace-http-dlq"],
            admin_workspace_ids=["workspace-http-dlq"],
        ),
    }
    with TestClient(create_app(api_settings(integration_settings))) as client:
        for index in range(5):
            replay = client.post(
                f"/api/v1/operator/dead-letters/{wide_root.id}:replay",
                headers={
                    **headers,
                    "Idempotency-Key": f"history-replay-{index:04d}",
                },
                json={"reason": f"verify replay history page {index}"},
            )
            assert replay.status_code == 202

        returned_replay_attempts: list[int] = []
        replay_cursor = None
        while True:
            params = {"replay_limit": 2}
            if replay_cursor is not None:
                params["replay_cursor"] = replay_cursor
            response = client.get(
                f"/api/v1/operator/dead-letters/{wide_root.id}",
                headers=headers,
                params=params,
            )
            assert response.status_code == 200
            body = response.json()
            assert len(body["replays"]) <= 2
            returned_replay_attempts.extend(item["replay_attempt"] for item in body["replays"])
            replay_cursor = body["replays_next_cursor"]
            if replay_cursor is None:
                break

        returned_wide_ids: list[str] = []
        cursor = None
        while True:
            params = {"child_limit": 2}
            if cursor is not None:
                params["child_cursor"] = cursor
            response = client.get(
                f"/api/v1/operator/dead-letters/{wide_root.id}",
                headers=headers,
                params=params,
            )
            assert response.status_code == 200
            body = response.json()
            assert len(body["child_dead_letters"]) <= 2
            returned_wide_ids.extend(item["id"] for item in body["child_dead_letters"])
            cursor = body["child_dead_letters_next_cursor"]
            if cursor is None:
                break

        current = deep_root
        for expected_child in deep_children:
            response = client.get(
                f"/api/v1/operator/dead-letters/{current.id}",
                headers=headers,
                params={"child_limit": 1},
            )
            assert response.status_code == 200
            body = response.json()
            assert [item["id"] for item in body["child_dead_letters"]] == [expected_child.id]
            assert body["child_dead_letters_next_cursor"] is None
            current = expected_child
        leaf = client.get(
            f"/api/v1/operator/dead-letters/{current.id}",
            headers=headers,
            params={"child_limit": 1},
        )

    assert returned_wide_ids == [child.id for child in wide_children]
    assert returned_replay_attempts == [1, 2, 3, 4, 5]
    assert leaf.status_code == 200
    assert leaf.json()["child_dead_letters"] == []
    assert leaf.json()["child_dead_letters_next_cursor"] is None


def test_dead_letter_replay_idempotency_scope_supports_workspace_bounds(
    integration_database,
    integration_settings,
) -> None:
    workspace_ids = ["w", "w" * 128]
    first_replays: list[dict[str, object]] = []
    different_key_replays: list[dict[str, object]] = []
    dead_letter_ids: list[str] = []

    with TestClient(
        create_app(api_settings(integration_settings)),
        raise_server_exceptions=False,
    ) as client:
        for workspace_id in workspace_ids:
            dead_letter = seed_http_dead_letter(
                integration_database,
                workspace_id=workspace_id,
            )
            dead_letter_ids.append(dead_letter.id)
            headers = {
                "X-Workspace-Id": workspace_id,
                **trusted_principal_header(
                    actor_id="bounded-scope-admin",
                    workspace_ids=[workspace_id],
                    admin_workspace_ids=[workspace_id],
                ),
            }
            first = client.post(
                f"/api/v1/operator/dead-letters/{dead_letter.id}:replay",
                headers={**headers, "Idempotency-Key": "replay-scope-shared-key"},
                json={"reason": "verify bounded replay scope"},
            )
            duplicate = client.post(
                f"/api/v1/operator/dead-letters/{dead_letter.id}:replay",
                headers={**headers, "Idempotency-Key": "replay-scope-shared-key"},
                json={"reason": "verify bounded replay scope"},
            )
            different_key = client.post(
                f"/api/v1/operator/dead-letters/{dead_letter.id}:replay",
                headers={**headers, "Idempotency-Key": "replay-scope-other-key"},
                json={"reason": "verify bounded replay scope"},
            )

            assert first.status_code == 202, first.text
            assert duplicate.status_code == 202, duplicate.text
            assert different_key.status_code == 202, different_key.text
            assert duplicate.json() == first.json()
            assert different_key.json()["id"] != first.json()["id"]
            first_replays.append(first.json())
            different_key_replays.append(different_key.json())

    assert first_replays[0]["id"] != first_replays[1]["id"]
    assert different_key_replays[0]["id"] != different_key_replays[1]["id"]
    with integration_database.session_factory() as session:
        records = list(
            session.scalars(
                select(IdempotencyKeyModel).where(
                    IdempotencyKeyModel.resource_type == "dead_letter_replay"
                )
            )
        )
    assert len(records) == 4
    assert len({record.scope for record in records}) == 2
    assert all(len(record.scope) <= 160 for record in records)
    assert all(record.scope.startswith("dead-letter-replay:v1:") for record in records)
    assert all(
        any(record.scope.endswith(f":{dead_letter_id}") for dead_letter_id in dead_letter_ids)
        for record in records
    )
    assert {record.scope for record in records} == {
        (
            "dead-letter-replay:v1:"
            f"{hashlib.sha256(workspace_id.encode('utf-8')).hexdigest()}:"
            f"{dead_letter_id}"
        )
        for workspace_id, dead_letter_id in zip(
            workspace_ids,
            dead_letter_ids,
            strict=True,
        )
    }


def test_dead_letter_replay_canonicalizes_uuid_before_idempotency(
    integration_database,
    integration_settings,
) -> None:
    first_dead_letter = seed_http_dead_letter(integration_database)
    second_dead_letter = seed_http_dead_letter(integration_database)
    headers = {
        "X-Workspace-Id": "workspace-http-dlq",
        "Idempotency-Key": "canonical-dead-letter-key",
        **trusted_principal_header(
            actor_id="canonical-uuid-admin",
            workspace_ids=["workspace-http-dlq"],
            admin_workspace_ids=["workspace-http-dlq"],
        ),
    }
    replay_request = {"reason": "verify canonical dead-letter identity"}

    with TestClient(create_app(api_settings(integration_settings))) as client:
        uppercase = client.post(
            f"/api/v1/operator/dead-letters/{first_dead_letter.id.upper()}:replay",
            headers=headers,
            json=replay_request,
        )
        lowercase = client.post(
            f"/api/v1/operator/dead-letters/{first_dead_letter.id}:replay",
            headers=headers,
            json=replay_request,
        )
        distinct = client.post(
            f"/api/v1/operator/dead-letters/{second_dead_letter.id}:replay",
            headers=headers,
            json=replay_request,
        )
        malformed = client.post(
            "/api/v1/operator/dead-letters/not-a-uuid:replay",
            headers=headers,
            json=replay_request,
        )

    assert uppercase.status_code == 202
    assert lowercase.status_code == 202
    assert lowercase.json() == uppercase.json()
    assert distinct.status_code == 202
    assert distinct.json()["id"] != uppercase.json()["id"]
    assert malformed.status_code == 404
    assert malformed.json()["code"] == "NOT_FOUND"
    with integration_database.session_factory() as session:
        replays = list(session.scalars(select(DeadLetterReplayModel)))
        replay_scopes = list(
            session.scalars(
                select(IdempotencyKeyModel.scope).where(
                    IdempotencyKeyModel.resource_type == "dead_letter_replay"
                )
            )
        )
    assert len(replays) == 2
    assert {replay.source_dead_letter_id for replay in replays} == {
        first_dead_letter.id,
        second_dead_letter.id,
    }
    assert len(replay_scopes) == 2
    assert any(scope.endswith(f":{first_dead_letter.id}") for scope in replay_scopes)
    assert any(scope.endswith(f":{second_dead_letter.id}") for scope in replay_scopes)


def test_dead_letter_http_requires_admin_and_replay_is_idempotent(
    integration_database,
    integration_settings,
) -> None:
    dead_letter = seed_http_dead_letter(integration_database)
    admin_headers = {
        "X-Workspace-Id": "workspace-http-dlq",
        **trusted_principal_header(
            actor_id="admin-a",
            workspace_ids=["workspace-http-dlq", "workspace-other"],
            admin_workspace_ids=["workspace-http-dlq"],
        ),
    }

    with TestClient(create_app(api_settings(integration_settings))) as client:
        denied = client.get(
            "/api/v1/operator/dead-letters",
            headers={
                "X-Workspace-Id": "workspace-http-dlq",
                **trusted_principal_header(
                    actor_id="user-a",
                    workspace_ids=["workspace-http-dlq"],
                ),
            },
        )
        listed = client.get(
            "/api/v1/operator/dead-letters?limit=1",
            headers=admin_headers,
        )
        hidden = client.get(
            f"/api/v1/operator/dead-letters/{dead_letter.id}",
            headers={**admin_headers, "X-Workspace-Id": "workspace-other"},
        )
        replay_headers = {
            **admin_headers,
            "Idempotency-Key": "http-replay-dead-letter-0001",
        }
        first = client.post(
            f"/api/v1/operator/dead-letters/{dead_letter.id}:replay",
            headers=replay_headers,
            json={"reason": "operator verified dependency recovery"},
        )
        duplicate = client.post(
            f"/api/v1/operator/dead-letters/{dead_letter.id}:replay",
            headers=replay_headers,
            json={"reason": "operator verified dependency recovery"},
        )
        coordinator = InboxCoordinator(
            uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory),
            consumer="http-replay-worker",
            owner="http-replay-worker-a",
            lease_duration=timedelta(seconds=30),
            max_attempts=1,
        )
        claim, _ = coordinator.claim(first.json()["replay_event_id"])
        assert claim.lease_token is not None
        coordinator.mark_failed(
            first.json()["replay_event_id"],
            claim.lease_token,
            RuntimeError("replayed operation failed again"),
        )
        dead_claim, _ = coordinator.claim(first.json()["replay_event_id"])
        assert dead_claim.dead is True
        first_detail = client.get(
            f"/api/v1/operator/dead-letters/{dead_letter.id}",
            headers=admin_headers,
        )
        child_dead_letter_id = first_detail.json()["child_dead_letters"][0]["id"]
        child_replay = client.post(
            f"/api/v1/operator/dead-letters/{child_dead_letter_id}:replay",
            headers={
                **admin_headers,
                "Idempotency-Key": "http-replay-dead-letter-child-0001",
            },
            json={"reason": "retry child failure after second review"},
        )
        child_claim, _ = coordinator.claim(child_replay.json()["replay_event_id"])
        assert child_claim.lease_token is not None
        coordinator.mark_failed(
            child_replay.json()["replay_event_id"],
            child_claim.lease_token,
            RuntimeError("replayed child failed again"),
        )
        child_dead_claim, _ = coordinator.claim(child_replay.json()["replay_event_id"])
        assert child_dead_claim.dead is True
        detail = client.get(
            f"/api/v1/operator/dead-letters/{dead_letter.id}",
            headers=admin_headers,
        )
        child_detail = client.get(
            f"/api/v1/operator/dead-letters/{child_dead_letter_id}",
            headers=admin_headers,
        )

    assert denied.status_code == 403
    assert denied.json()["code"] == "ADMIN_REQUIRED"
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == dead_letter.id
    assert hidden.status_code == 403
    assert hidden.json()["code"] == "ADMIN_REQUIRED"
    assert first.status_code == 202
    assert duplicate.status_code == 202
    assert duplicate.json()["id"] == first.json()["id"]
    assert detail.status_code == 200
    assert detail.json()["dead_letter"]["id"] == dead_letter.id
    assert len(detail.json()["replays"]) == 1
    assert first_detail.status_code == 200
    assert child_replay.status_code == 202
    assert len(detail.json()["child_dead_letters"]) == 1
    assert detail.json()["child_dead_letters_next_cursor"] is None
    assert detail.json()["child_dead_letters"][0]["source_dead_letter_id"] == dead_letter.id
    assert detail.json()["child_dead_letters"][0]["replay_attempt"] == 1
    assert child_detail.status_code == 200
    assert len(child_detail.json()["child_dead_letters"]) == 1
    assert (
        child_detail.json()["child_dead_letters"][0]["source_dead_letter_id"]
        == child_dead_letter_id
    )


def test_orphaned_legacy_dead_letters_are_system_admin_read_only(
    integration_database,
    integration_settings,
) -> None:
    now = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
    event = OutboxEvent(
        envelope=EventEnvelope.create(
            event_type="legacy.event",
            aggregate_type="legacy",
            aggregate_id="legacy-aggregate",
            aggregate_version=1,
            trace_id="legacy-trace",
            payload={
                "legacy_id": "legacy-aggregate",
                "workspace_id": " workspace-must-not-be-normalized",
            },
            now=now,
        ),
        available_at=now,
    )
    dead_letter = DeadLetterMessage.create(
        consumer="legacy-worker",
        message_id=event.envelope.event_id,
        event_type=event.envelope.event_type,
        payload=event.envelope.payload,
        reason="legacy failure without workspace provenance",
        attempt_count=1,
        original_created_at=now,
        now=now,
    )
    with SqlAlchemyUnitOfWork(integration_database.session_factory) as uow:
        uow.outbox.add(event)
        uow.dead_letters.add(dead_letter)
        uow.commit()
    workspace_admin = trusted_principal_header(
        actor_id="workspace-admin",
        workspace_ids=["workspace-http-dlq"],
        admin_workspace_ids=["workspace-http-dlq"],
    )
    payload_name_admin = trusted_principal_header(
        actor_id="payload-name-admin",
        workspace_ids=["workspace-must-not-be-normalized"],
        admin_workspace_ids=["workspace-must-not-be-normalized"],
    )
    system_admin = trusted_principal_header(
        actor_id="system-admin",
        workspace_ids=[],
        system_admin=True,
    )

    with TestClient(create_app(api_settings(integration_settings))) as client:
        denied = client.get(
            "/api/v1/operator/legacy-dead-letters",
            headers=workspace_admin,
        )
        workspace_detail = client.get(
            f"/api/v1/operator/dead-letters/{dead_letter.id}",
            headers={
                "X-Workspace-Id": "workspace-must-not-be-normalized",
                **payload_name_admin,
            },
        )
        listed = client.get(
            "/api/v1/operator/legacy-dead-letters",
            headers=system_admin,
        )
        detail = client.get(
            f"/api/v1/operator/legacy-dead-letters/{dead_letter.id}",
            headers=system_admin,
        )
        replay_denied = client.post(
            f"/api/v1/operator/legacy-dead-letters/{dead_letter.id}:replay",
            headers={**system_admin, "Idempotency-Key": "legacy-replay-denied"},
            json={"reason": "legacy provenance is unknown"},
        )

    assert denied.status_code == 403
    assert denied.json()["code"] == "ADMIN_REQUIRED"
    assert workspace_detail.status_code == 404
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == dead_letter.id
    assert detail.status_code == 200
    assert detail.json()["dead_letter"]["id"] == dead_letter.id
    assert detail.json()["child_dead_letters"] == []
    assert replay_denied.status_code == 405


def test_dead_letter_http_cursor_is_bounded_and_malformed_cursor_is_stable(
    integration_database,
    integration_settings,
) -> None:
    first_dead_letter = seed_http_dead_letter(integration_database)
    second_dead_letter = seed_http_dead_letter(integration_database)
    headers = {
        "X-Workspace-Id": "workspace-http-dlq",
        **trusted_principal_header(
            actor_id="admin-a",
            workspace_ids=["workspace-http-dlq"],
            admin_workspace_ids=["workspace-http-dlq"],
        ),
    }

    with TestClient(create_app(api_settings(integration_settings))) as client:
        first_page = client.get(
            "/api/v1/operator/dead-letters?limit=1",
            headers=headers,
        )
        second_page = client.get(
            "/api/v1/operator/dead-letters",
            headers=headers,
            params={"limit": 1, "cursor": first_page.json()["next_cursor"]},
        )
        malformed = client.get(
            "/api/v1/operator/dead-letters?cursor=a",
            headers=headers,
        )
        over_limit = client.get(
            "/api/v1/operator/dead-letters?limit=101",
            headers=headers,
        )
        oversized_cursor = client.get(
            "/api/v1/operator/dead-letters",
            headers=headers,
            params={"cursor": "a" * 1025},
        )

    returned_ids = {
        first_page.json()["items"][0]["id"],
        second_page.json()["items"][0]["id"],
    }
    assert returned_ids == {first_dead_letter.id, second_dead_letter.id}
    assert second_page.json()["next_cursor"] is None
    assert malformed.status_code == 400
    assert malformed.json()["code"] == "INVALID_ARGUMENT"
    assert over_limit.status_code == 422
    assert over_limit.json()["code"] == "VALIDATION_ERROR"
    assert oversized_cursor.status_code == 422
    assert oversized_cursor.json()["code"] == "VALIDATION_ERROR"
