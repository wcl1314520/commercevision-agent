from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from commercevision_application import (
    OperationExecutionFailure,
    OperationExecutionRequest,
    ProductBriefAnalysisExecutor,
    ProductBriefProviderArtifactService,
    ProviderArtifactOwner,
)
from commercevision_application.product_brief_ports import StoredProviderCall
from commercevision_contracts.product_briefs import (
    PROVIDER_ARTIFACT_KEY_SCHEMA_VERSION,
    PreparedProviderArtifact,
    ProviderArtifactKind,
    ProviderArtifactReference,
    ProviderArtifactState,
    ProviderArtifactWrite,
    VisionProviderStatus,
)
from commercevision_domain import OperationKind, RetentionClass, StorageLocationClass
from commercevision_persistence import SqlAlchemyProductBriefUnitOfWork
from sqlalchemy import exc, text
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration

WORKSPACE_ID = "provider-artifact-ledger-workspace"
WORKFLOW_ID = "019fa200-0000-7000-8000-000000000001"
PRODUCT_ID = "019fa200-0000-7000-8000-000000000002"
PRODUCT_BRIEF_ID = "019fa200-0000-7000-8000-000000000003"
OPERATION_ID = "019fa200-0000-7000-8000-000000000004"


class SimulatedProcessCrash(BaseException):
    pass


class CommitObservingArtifactStore:
    def __init__(self, engine: Engine, *, crash_after_write: bool = False) -> None:
        self._engine = engine
        self._crash_after_write = crash_after_write
        self.observed_states: list[str] = []
        self.write_count = 0

    def prepare(
        self,
        artifact: ProviderArtifactWrite,
        *,
        ledger_id: str,
        write_fence: str,
    ) -> PreparedProviderArtifact:
        key = (
            f"product-brief/{artifact.operation_id}/"
            f"attempt-{artifact.operation_attempt}/call-{artifact.call_index}/"
            f"{artifact.kind.value.lower()}.json"
        )
        target_sha256 = hashlib.sha256(
            f"MINIO\0PROVIDER_RESULT\0provider-results\0{key}".encode()
        ).hexdigest()
        return PreparedProviderArtifact(
            ledger_id=ledger_id,
            key_schema_version=PROVIDER_ARTIFACT_KEY_SCHEMA_VERSION,
            storage_backend="MINIO",
            location=StorageLocationClass.PROVIDER_RESULT,
            bucket="provider-results",
            key=key,
            target_sha256=target_sha256,
            content_type=artifact.content_type,
            expected_sha256=artifact.sha256,
            expected_byte_size=len(artifact.payload),
            retention_class=artifact.retention_class,
            retention_deadline=artifact.retention_deadline,
            write_fence=write_fence,
        )

    def write_prepared(
        self,
        artifact: ProviderArtifactWrite,
        target: PreparedProviderArtifact,
    ) -> ProviderArtifactReference:
        self.write_count += 1
        with self._engine.connect() as connection:
            state = connection.execute(
                text("SELECT state FROM product_brief_provider_artifacts WHERE id = :artifact_id"),
                {"artifact_id": target.ledger_id},
            ).scalar_one()
        self.observed_states.append(state)
        if self._crash_after_write:
            raise SimulatedProcessCrash
        return ProviderArtifactReference(
            storage_backend=target.storage_backend,
            location=target.location,
            bucket=target.bucket,
            key=target.key,
            provider_version_id="provider-version-1",
            etag='"provider-etag-1"',
            sha256=artifact.sha256,
            byte_size=len(artifact.payload),
            retention_class=artifact.retention_class,
            retention_deadline=artifact.retention_deadline,
        )

    def stat_matches(self, target, stat) -> bool:
        del target, stat
        return True


def _seed_owner_graph(engine: Engine) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    deadline = now + timedelta(days=1)
    db_now = now.replace(tzinfo=None)
    db_deadline = deadline.replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO workflows "
                "(id, workspace_id, created_by, workflow_type, status, "
                "retention_status, current_node, version, input_json, result_json, "
                "expires_at, cancellation_requested_at, created_at, updated_at) "
                "VALUES (:id, :workspace, 'ticket-07', 'product-understanding', "
                "'UNDERSTANDING', 'ACTIVE', 'understand_product', 1, JSON_OBJECT(), "
                "NULL, :deadline, NULL, :now, :now)"
            ),
            {
                "id": WORKFLOW_ID,
                "workspace": WORKSPACE_ID,
                "deadline": db_deadline,
                "now": db_now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO products "
                "(id, workspace_id, source_namespace, external_id, source_version, "
                "title, category_code, brand, attributes_json, expires_at, version, "
                "created_at, updated_at) VALUES "
                "(:id, :workspace, 'MANUAL', 'ticket-07-product', 'v1', "
                "'Ticket 07 Product', 'beauty.test', 'Ledger', JSON_OBJECT(), "
                "NULL, 1, :now, :now)"
            ),
            {"id": PRODUCT_ID, "workspace": WORKSPACE_ID, "now": db_now},
        )
        connection.execute(
            text(
                "INSERT INTO durable_operations "
                "(id, workspace_id, kind, target_type, target_id, target_version, "
                "input_hash, input_ref, output_ref, provider_request_id, state, "
                "lease_owner, lease_token, lease_expires_at, attempt_count, "
                "max_attempts, next_attempt_at, execution_deadline_at, "
                "reconciliation_attempt_count, max_reconciliation_attempts, "
                "next_reconciliation_at, reconciliation_started_at, "
                "reconciliation_deadline_at, reconciliation_required, "
                "reconciliation_outcome, dead_letter_id, replay_source_dead_letter_id, "
                "replay_attempt, recovery_generation, recovery_consumed_generation, "
                "error_code, error_category, error_message, error_retryable, "
                "error_provider_request_id, created_at, updated_at, last_attempt_at, "
                "started_at, completed_at, version) VALUES "
                "(:id, :workspace, 'PRODUCT_BRIEF_ANALYSIS', 'product_brief', "
                ":brief_id, 1, :input_hash, 'mysql://analysis/ticket-07', NULL, NULL, "
                "'RUNNING', 'ticket-07-worker', :lease_token, :lease_expires, 1, 3, "
                "NULL, :deadline, 0, 3, NULL, NULL, NULL, 0, 'NOT_REQUIRED', NULL, "
                "NULL, 0, 0, 0, NULL, NULL, NULL, NULL, NULL, :now, :now, :now, "
                ":now, NULL, 1)"
            ),
            {
                "id": OPERATION_ID,
                "workspace": WORKSPACE_ID,
                "brief_id": PRODUCT_BRIEF_ID,
                "input_hash": "1" * 64,
                "lease_token": "019fa200-0000-7000-8000-000000000099",
                "lease_expires": db_deadline,
                "deadline": db_deadline,
                "now": db_now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO product_briefs "
                "(id, workspace_id, workflow_id, product_id, operation_id, "
                "created_by, state, current_version_id, confirmed_version_id, "
                "version, retention_class, retention_deadline, created_at, updated_at) "
                "VALUES (:id, :workspace, :workflow_id, :product_id, :operation_id, "
                "'ticket-07', 'DRAFT', NULL, NULL, 1, 'TASK', :deadline, :now, :now)"
            ),
            {
                "id": PRODUCT_BRIEF_ID,
                "workspace": WORKSPACE_ID,
                "workflow_id": WORKFLOW_ID,
                "product_id": PRODUCT_ID,
                "operation_id": OPERATION_ID,
                "deadline": db_deadline,
                "now": db_now,
            },
        )
    return now, deadline


def _artifact(
    deadline: datetime,
    *,
    kind: ProviderArtifactKind = ProviderArtifactKind.REQUEST,
) -> ProviderArtifactWrite:
    payload = (
        b'{"raw":"provider-request"}'
        if kind == ProviderArtifactKind.REQUEST
        else b'{"raw":"provider-response"}'
    )
    return ProviderArtifactWrite(
        operation_id=OPERATION_ID,
        operation_attempt=1,
        call_index=0,
        kind=kind,
        content_type="application/json",
        payload=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
        retention_class=RetentionClass.TASK,
        retention_deadline=deadline,
    )


def _service(
    integration_database,
    store: CommitObservingArtifactStore,
    *,
    now: datetime,
) -> ProductBriefProviderArtifactService:
    return ProductBriefProviderArtifactService(
        uow_factory=lambda: SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory),
        artifact_store=store,
        clock=lambda: now,
    )


def _authorize(uow) -> datetime:
    assert uow.workflows.get(
        WORKFLOW_ID,
        workspace_id=WORKSPACE_ID,
        for_update=True,
    )
    assert uow.product_briefs.get(
        workspace_id=WORKSPACE_ID,
        product_brief_id=PRODUCT_BRIEF_ID,
        for_update=True,
    )
    assert uow.operations.get(
        OPERATION_ID,
        workspace_id=WORKSPACE_ID,
        for_update=True,
    )
    return uow.database_now()


def test_mysql_commits_intent_before_storage_and_completion_after_write(
    integration_database,
) -> None:
    now, deadline = _seed_owner_graph(integration_database.engine)
    store = CommitObservingArtifactStore(integration_database.engine)
    reference = _service(
        integration_database,
        store,
        now=now,
    ).store_artifact(
        _artifact(deadline),
        owner=ProviderArtifactOwner(
            workspace_id=WORKSPACE_ID,
            product_brief_id=PRODUCT_BRIEF_ID,
        ),
        authorize_intent=_authorize,
    )

    assert store.observed_states == ["INTENDED"]
    assert reference.provider_version_id == "provider-version-1"
    with SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory) as uow:
        artifact = uow.product_briefs.get_provider_artifact(
            workspace_id=WORKSPACE_ID,
            operation_id=OPERATION_ID,
            operation_attempt=1,
            call_index=0,
            kind=ProviderArtifactKind.REQUEST,
        )
    assert artifact is not None
    assert artifact.state == ProviderArtifactState.STORED
    assert artifact.version == 2
    assert artifact.provider_version_id == "provider-version-1"
    assert artifact.etag == '"provider-etag-1"'


def test_mysql_artifact_id_lookup_is_scoped_to_the_workspace(
    integration_database,
) -> None:
    now, deadline = _seed_owner_graph(integration_database.engine)
    store = CommitObservingArtifactStore(integration_database.engine)
    _service(
        integration_database,
        store,
        now=now,
    ).store_artifact(
        _artifact(deadline),
        owner=ProviderArtifactOwner(
            workspace_id=WORKSPACE_ID,
            product_brief_id=PRODUCT_BRIEF_ID,
        ),
        authorize_intent=_authorize,
    )

    with SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory) as uow:
        artifact = uow.product_brief_artifacts.get_provider_artifact(
            workspace_id=WORKSPACE_ID,
            operation_id=OPERATION_ID,
            operation_attempt=1,
            call_index=0,
            kind=ProviderArtifactKind.REQUEST,
        )
        assert artifact is not None
        assert (
            uow.product_brief_artifacts.get_provider_artifact_by_id(
                workspace_id=WORKSPACE_ID,
                artifact_id=artifact.id,
            )
            == artifact
        )
        assert (
            uow.product_brief_artifacts.get_provider_artifact_by_id(
                workspace_id="another-workspace",
                artifact_id=artifact.id,
            )
            is None
        )


def test_mysql_crash_after_write_leaves_exact_intent_without_raw_payload(
    integration_database,
) -> None:
    now, deadline = _seed_owner_graph(integration_database.engine)
    store = CommitObservingArtifactStore(
        integration_database.engine,
        crash_after_write=True,
    )

    with pytest.raises(SimulatedProcessCrash):
        _service(
            integration_database,
            store,
            now=now,
        ).store_artifact(
            _artifact(deadline),
            owner=ProviderArtifactOwner(
                workspace_id=WORKSPACE_ID,
                product_brief_id=PRODUCT_BRIEF_ID,
            ),
            authorize_intent=_authorize,
        )

    assert store.observed_states == ["INTENDED"]
    with integration_database.engine.connect() as connection:
        artifact = (
            connection.execute(
                text(
                    "SELECT state, object_key, target_sha256, provider_version_id, "
                    "etag, version FROM product_brief_provider_artifacts"
                )
            )
            .mappings()
            .one()
        )
        columns = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() "
                    "AND table_name = 'product_brief_provider_artifacts'"
                )
            )
        }
    assert artifact["state"] == "INTENDED"
    assert artifact["object_key"].endswith("/call-0/request.json")
    assert len(artifact["target_sha256"]) == 64
    assert artifact["provider_version_id"] is None
    assert artifact["etag"] is None
    assert artifact["version"] == 1
    assert "payload" not in columns


def test_mysql_completed_call_requires_exact_stored_request_and_response_rows(
    integration_database,
) -> None:
    now, deadline = _seed_owner_graph(integration_database.engine)
    store = CommitObservingArtifactStore(integration_database.engine)
    service = _service(integration_database, store, now=now)
    owner = ProviderArtifactOwner(
        workspace_id=WORKSPACE_ID,
        product_brief_id=PRODUCT_BRIEF_ID,
    )
    request_reference = service.store_artifact(
        _artifact(deadline),
        owner=owner,
        authorize_intent=_authorize,
    )
    executor = object.__new__(ProductBriefAnalysisExecutor)
    executor._artifact_service = service
    operation_request = OperationExecutionRequest(
        operation_id=OPERATION_ID,
        workspace_id=WORKSPACE_ID,
        kind=OperationKind.PRODUCT_BRIEF_ANALYSIS,
        target_type="product_brief",
        target_id=PRODUCT_BRIEF_ID,
        target_version=1,
        input_hash="1" * 64,
        input_ref="mysql://analysis/ticket-07",
        provider_request_id=None,
        attempt_count=1,
        idempotency_key=f"durable-operation:{OPERATION_ID}",
    )
    missing_response = ProviderArtifactReference(
        storage_backend="MINIO",
        location=StorageLocationClass.PROVIDER_RESULT,
        bucket="provider-results",
        key=(f"product-brief/{OPERATION_ID}/attempt-1/call-0/response.json"),
        provider_version_id="provider-version-1",
        etag='"provider-etag-1"',
        sha256=_artifact(
            deadline,
            kind=ProviderArtifactKind.RESPONSE,
        ).sha256,
        byte_size=len(b'{"raw":"provider-response"}'),
        retention_class=RetentionClass.TASK,
        retention_deadline=deadline,
    )

    def candidate(
        response_reference: ProviderArtifactReference,
    ) -> StoredProviderCall:
        return StoredProviderCall(
            id="019fa200-0000-7000-8000-000000000010",
            workspace_id=WORKSPACE_ID,
            product_brief_id=PRODUCT_BRIEF_ID,
            operation_id=OPERATION_ID,
            operation_attempt=1,
            call_index=0,
            status=VisionProviderStatus.SUCCEEDED,
            provider="alibaba-model-studio",
            endpoint_region="cn-hangzhou",
            endpoint_host="dashscope.aliyuncs.com",
            requested_model="qwen-vl",
            submitted_model_snapshot="qwen-vl-2026-07-01",
            resolved_model="qwen-vl-2026-07-01",
            prompt_version="prompt-v1",
            config_snapshot_sha256="2" * 64,
            request_id="provider-request-1",
            input_tokens=10,
            output_tokens=20,
            total_tokens=30,
            latency_ms=250,
            request_artifact=request_reference,
            response_artifact=response_reference,
            error_code=None,
            error_category=None,
            error_retryable=None,
            retention_class=RetentionClass.TASK,
            retention_deadline=deadline,
            created_at=now,
        )

    with (
        SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory) as uow,
        pytest.raises(OperationExecutionFailure) as exc_info,
    ):
        executor._store_provider_calls_once(
            uow=uow,
            request=operation_request,
            candidates=(candidate(missing_response),),
        )
    assert exc_info.value.error.code == "VISION_PROVIDER_ARTIFACT_LEDGER_MISMATCH"

    response_reference = service.store_artifact(
        _artifact(deadline, kind=ProviderArtifactKind.RESPONSE),
        owner=owner,
        authorize_intent=_authorize,
    )
    with SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory) as uow:
        _, inserted = executor._store_provider_calls_once(
            uow=uow,
            request=operation_request,
            candidates=(candidate(response_reference),),
        )
        uow.commit()
    assert inserted is True

    with integration_database.engine.connect() as connection:
        call = (
            connection.execute(
                text(
                    "SELECT request_artifact_id, response_artifact_id "
                    "FROM product_brief_provider_calls"
                )
            )
            .mappings()
            .one()
        )
        artifact_ids = {
            row["kind"]: row["id"]
            for row in connection.execute(
                text("SELECT id, kind FROM product_brief_provider_artifacts")
            ).mappings()
        }
    assert call["request_artifact_id"] == artifact_ids["REQUEST"]
    assert call["response_artifact_id"] == artifact_ids["RESPONSE"]


def test_mysql_unknown_call_keeps_provenance_with_unresolved_response_ledger(
    integration_database,
) -> None:
    now, deadline = _seed_owner_graph(integration_database.engine)
    owner = ProviderArtifactOwner(
        workspace_id=WORKSPACE_ID,
        product_brief_id=PRODUCT_BRIEF_ID,
    )
    request_reference = _service(
        integration_database,
        CommitObservingArtifactStore(integration_database.engine),
        now=now,
    ).store_artifact(
        _artifact(deadline),
        owner=owner,
        authorize_intent=_authorize,
    )
    response_store = CommitObservingArtifactStore(
        integration_database.engine,
        crash_after_write=True,
    )
    with pytest.raises(SimulatedProcessCrash):
        _service(integration_database, response_store, now=now).store_artifact(
            _artifact(deadline, kind=ProviderArtifactKind.RESPONSE),
            owner=owner,
            authorize_intent=_authorize,
        )

    request = OperationExecutionRequest(
        operation_id=OPERATION_ID,
        workspace_id=WORKSPACE_ID,
        kind=OperationKind.PRODUCT_BRIEF_ANALYSIS,
        target_type="product_brief",
        target_id=PRODUCT_BRIEF_ID,
        target_version=1,
        input_hash="1" * 64,
        input_ref="mysql://analysis/ticket-07",
        provider_request_id=None,
        attempt_count=1,
        idempotency_key=f"durable-operation:{OPERATION_ID}",
    )
    candidate = StoredProviderCall(
        id="019fa200-0000-7000-8000-000000000011",
        workspace_id=WORKSPACE_ID,
        product_brief_id=PRODUCT_BRIEF_ID,
        operation_id=OPERATION_ID,
        operation_attempt=1,
        call_index=0,
        status=VisionProviderStatus.UNKNOWN,
        provider="alibaba-model-studio",
        endpoint_region="cn-hangzhou",
        endpoint_host="dashscope.aliyuncs.com",
        requested_model="qwen-vl",
        submitted_model_snapshot="qwen-vl-2026-07-01",
        resolved_model=None,
        prompt_version="prompt-v1",
        config_snapshot_sha256="2" * 64,
        request_id="provider-request-response-received",
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        latency_ms=250,
        request_artifact=request_reference,
        response_artifact=None,
        error_code="PROVIDER_RESPONSE_ARTIFACT_OUTCOME_UNKNOWN",
        error_category="unknown_outcome",
        error_retryable=False,
        retention_class=RetentionClass.TASK,
        retention_deadline=deadline,
        created_at=now,
    )
    executor = object.__new__(ProductBriefAnalysisExecutor)

    with SqlAlchemyProductBriefUnitOfWork(integration_database.session_factory) as uow:
        stored, inserted = executor._store_provider_calls_once(
            uow=uow,
            request=request,
            candidates=(candidate,),
        )
        uow.commit()

    assert inserted is True
    assert stored[0].status == VisionProviderStatus.UNKNOWN
    assert stored[0].request_id == "provider-request-response-received"
    assert stored[0].error_code == "PROVIDER_RESPONSE_ARTIFACT_OUTCOME_UNKNOWN"
    with integration_database.engine.connect() as connection:
        rows = tuple(
            connection.execute(
                text(
                    "SELECT provider_call.status, provider_call.request_id, "
                    "provider_call.error_code, provider_call.response_artifact_id, "
                    "artifact.state, artifact.kind "
                    "FROM product_brief_provider_calls AS provider_call "
                    "JOIN product_brief_provider_artifacts AS artifact "
                    "ON artifact.workspace_id = provider_call.workspace_id "
                    "AND artifact.operation_id = provider_call.operation_id "
                    "AND artifact.operation_attempt = provider_call.operation_attempt "
                    "AND artifact.call_index = provider_call.call_index "
                    "AND artifact.kind = 'RESPONSE'"
                )
            ).mappings()
        )
    assert rows == (
        {
            "status": "UNKNOWN",
            "request_id": "provider-request-response-received",
            "error_code": "PROVIDER_RESPONSE_ARTIFACT_OUTCOME_UNKNOWN",
            "response_artifact_id": None,
            "state": "INTENDED",
            "kind": "RESPONSE",
        },
    )
    assert response_store.write_count == 1


def test_mysql_ledger_trigger_allows_only_versioned_lifecycle_updates(
    integration_database,
) -> None:
    now, deadline = _seed_owner_graph(integration_database.engine)
    store = CommitObservingArtifactStore(
        integration_database.engine,
        crash_after_write=True,
    )
    with pytest.raises(SimulatedProcessCrash):
        _service(integration_database, store, now=now).store_artifact(
            _artifact(deadline),
            owner=ProviderArtifactOwner(
                workspace_id=WORKSPACE_ID,
                product_brief_id=PRODUCT_BRIEF_ID,
            ),
            authorize_intent=_authorize,
        )

    with integration_database.engine.begin() as connection:
        artifact_id = connection.execute(
            text("SELECT id FROM product_brief_provider_artifacts")
        ).scalar_one()
        connection.execute(
            text(
                "UPDATE product_brief_provider_artifacts "
                "SET state = 'UNKNOWN', unknown_reason = 'DIRECT_SQL_TEST', "
                "version = version + 1, updated_at = :now WHERE id = :id"
            ),
            {"id": artifact_id, "now": now.replace(tzinfo=None)},
        )
        connection.execute(
            text(
                "UPDATE product_brief_provider_artifacts "
                "SET unknown_reason = 'DIRECT_SQL_RECHECK', version = version + 1, "
                "updated_at = DATE_ADD(updated_at, INTERVAL 1 MICROSECOND) "
                "WHERE id = :id"
            ),
            {"id": artifact_id},
        )
        connection.execute(
            text(
                "UPDATE product_brief_provider_artifacts "
                "SET state = 'STORED', provider_version_id = 'direct-version-1', "
                "etag = '\"direct-etag-1\"', unknown_reason = NULL, "
                "stored_at = :now, version = version + 1, "
                "updated_at = DATE_ADD(updated_at, INTERVAL 1 MICROSECOND) "
                "WHERE id = :id"
            ),
            {"id": artifact_id, "now": now.replace(tzinfo=None)},
        )

    with integration_database.engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT state, provider_version_id, etag, unknown_reason, version "
                    "FROM product_brief_provider_artifacts WHERE id = :id"
                ),
                {"id": artifact_id},
            )
            .mappings()
            .one()
        )
    assert row == {
        "state": "STORED",
        "provider_version_id": "direct-version-1",
        "etag": '"direct-etag-1"',
        "unknown_reason": None,
        "version": 4,
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "SET operation_attempt = operation_attempt + 1, version = version + 1",
        "SET object_key = CONCAT(object_key, '-changed'), version = version + 1",
        "SET expected_byte_size = expected_byte_size + 1, version = version + 1",
        "SET retention_deadline = DATE_ADD(retention_deadline, INTERVAL 1 SECOND), "
        "version = version + 1",
        "SET created_at = DATE_ADD(created_at, INTERVAL 1 SECOND), version = version + 1",
        "SET state = 'UNKNOWN', unknown_reason = 'BAD_VERSION', version = version + 2",
        "SET updated_at = DATE_ADD(updated_at, INTERVAL 1 SECOND), version = version + 1",
    ],
)
def test_mysql_ledger_trigger_rejects_illegal_direct_sql_updates(
    integration_database,
    mutation: str,
) -> None:
    now, deadline = _seed_owner_graph(integration_database.engine)
    store = CommitObservingArtifactStore(
        integration_database.engine,
        crash_after_write=True,
    )
    with pytest.raises(SimulatedProcessCrash):
        _service(integration_database, store, now=now).store_artifact(
            _artifact(deadline),
            owner=ProviderArtifactOwner(
                workspace_id=WORKSPACE_ID,
                product_brief_id=PRODUCT_BRIEF_ID,
            ),
            authorize_intent=_authorize,
        )

    with (
        integration_database.engine.begin() as connection,
        pytest.raises(exc.DatabaseError, match="provider artifact lifecycle"),
    ):
        connection.execute(text(f"UPDATE product_brief_provider_artifacts {mutation}"))


def test_mysql_ledger_trigger_rejects_update_after_stored_and_delete(
    integration_database,
) -> None:
    now, deadline = _seed_owner_graph(integration_database.engine)
    store = CommitObservingArtifactStore(integration_database.engine)
    _service(integration_database, store, now=now).store_artifact(
        _artifact(deadline),
        owner=ProviderArtifactOwner(
            workspace_id=WORKSPACE_ID,
            product_brief_id=PRODUCT_BRIEF_ID,
        ),
        authorize_intent=_authorize,
    )

    with (
        integration_database.engine.begin() as connection,
        pytest.raises(exc.DatabaseError, match="provider artifact lifecycle"),
    ):
        connection.execute(
            text(
                "UPDATE product_brief_provider_artifacts "
                "SET updated_at = DATE_ADD(updated_at, INTERVAL 1 SECOND), "
                "version = version + 1"
            )
        )
    with (
        integration_database.engine.begin() as connection,
        pytest.raises(exc.DatabaseError, match="provider artifacts cannot be deleted"),
    ):
        connection.execute(text("DELETE FROM product_brief_provider_artifacts"))


def test_mysql_rejects_forged_physical_target_hash(
    integration_database,
) -> None:
    now, deadline = _seed_owner_graph(integration_database.engine)
    store = CommitObservingArtifactStore(
        integration_database.engine,
        crash_after_write=True,
    )
    with pytest.raises(SimulatedProcessCrash):
        _service(integration_database, store, now=now).store_artifact(
            _artifact(deadline),
            owner=ProviderArtifactOwner(
                workspace_id=WORKSPACE_ID,
                product_brief_id=PRODUCT_BRIEF_ID,
            ),
            authorize_intent=_authorize,
        )

    with (
        integration_database.engine.begin() as connection,
        pytest.raises(
            exc.DatabaseError,
            match="ck_pb_provider_artifacts_target_identity",
        ),
    ):
        connection.execute(
            text(
                "INSERT INTO product_brief_provider_artifacts "
                "(id, workspace_id, product_brief_id, operation_id, "
                "operation_attempt, call_index, kind, state, key_schema_version, "
                "storage_backend, location, bucket, object_key, target_sha256, "
                "content_type, expected_sha256, expected_byte_size, "
                "retention_class, retention_deadline, write_fence, "
                "provider_version_id, etag, unknown_reason, version, stored_at, "
                "created_at, updated_at) "
                "SELECT :new_id, workspace_id, product_brief_id, operation_id, "
                "operation_attempt, call_index + 1, kind, state, key_schema_version, "
                "storage_backend, location, bucket, object_key, :forged_target, "
                "content_type, expected_sha256, expected_byte_size, "
                "retention_class, retention_deadline, :write_fence, "
                "provider_version_id, etag, unknown_reason, version, stored_at, "
                "created_at, updated_at "
                "FROM product_brief_provider_artifacts LIMIT 1"
            ),
            {
                "new_id": "019fa200-0000-7000-8000-0000000000f2",
                "forged_target": "0" * 64,
                "write_fence": "f" * 64,
            },
        )


def _insert_completed_call(
    connection,
    *,
    now: datetime,
    deadline: datetime,
    request_artifact_id: str | None,
    request_key: str,
    request_version_id: str,
    request_etag: str,
    operation_attempt: int = 1,
    call_index: int = 0,
    request_storage_backend: str = "MINIO",
    request_bucket: str = "provider-results",
    request_sha256: str | None = None,
    request_byte_size: int | None = None,
    retention_deadline: datetime | None = None,
    response_artifact_id: str | None = None,
    response_storage_backend: str | None = None,
    response_bucket: str | None = None,
    response_key: str | None = None,
    response_version_id: str | None = None,
    response_etag: str | None = None,
    response_sha256: str | None = None,
    response_byte_size: int | None = None,
    status: str = "SUCCEEDED",
    error_code: str | None = None,
    error_category: str | None = None,
    error_retryable: bool | None = None,
) -> None:
    connection.execute(
        text(
            "INSERT INTO product_brief_provider_calls "
            "(id, workspace_id, product_brief_id, operation_id, operation_attempt, "
            "call_index, status, provider, endpoint_region, endpoint_host, "
            "requested_model, submitted_model_snapshot, resolved_model, "
            "prompt_version, config_snapshot_sha256, request_id, input_tokens, "
            "output_tokens, total_tokens, latency_ms, request_artifact_id, "
            "request_artifact_storage_backend, request_artifact_location, "
            "request_artifact_bucket, request_artifact_key, "
            "request_artifact_provider_version_id, request_artifact_etag, "
            "request_artifact_sha256, request_artifact_byte_size, "
            "response_artifact_id, response_artifact_storage_backend, "
            "response_artifact_location, response_artifact_bucket, "
            "response_artifact_key, response_artifact_provider_version_id, "
            "response_artifact_etag, response_artifact_sha256, "
            "response_artifact_byte_size, error_code, error_category, "
            "error_retryable, retention_class, retention_deadline, created_at) "
            "VALUES (:id, :workspace, :brief_id, :operation_id, "
            ":operation_attempt, :call_index, :status, "
            "'alibaba-model-studio', 'cn-hangzhou', 'dashscope.aliyuncs.com', "
            "'qwen-vl', 'qwen-vl-2026-07-01', :resolved_model, 'prompt-v1', "
            ":config_hash, 'provider-request-1', 10, 20, 30, 250, "
            ":request_artifact_id, :request_storage_backend, 'PROVIDER_RESULT', "
            ":request_bucket, "
            ":request_key, :request_version_id, :request_etag, :request_hash, "
            ":request_size, :response_artifact_id, :response_storage_backend, "
            ":response_location, :response_bucket, :response_key, "
            ":response_version_id, :response_etag, :response_hash, :response_size, "
            ":error_code, :error_category, :error_retryable, 'TASK', :deadline, :now)"
        ),
        {
            "id": "019fa200-0000-7000-8000-000000000011",
            "workspace": WORKSPACE_ID,
            "brief_id": PRODUCT_BRIEF_ID,
            "operation_id": OPERATION_ID,
            "operation_attempt": operation_attempt,
            "call_index": call_index,
            "status": status,
            "resolved_model": ("qwen-vl-2026-07-01" if status == "SUCCEEDED" else None),
            "config_hash": "2" * 64,
            "request_artifact_id": request_artifact_id,
            "request_storage_backend": request_storage_backend,
            "request_bucket": request_bucket,
            "request_key": request_key,
            "request_version_id": request_version_id,
            "request_etag": request_etag,
            "request_hash": request_sha256 or _artifact(deadline).sha256,
            "request_size": (
                request_byte_size
                if request_byte_size is not None
                else len(_artifact(deadline).payload)
            ),
            "response_artifact_id": response_artifact_id,
            "response_storage_backend": response_storage_backend,
            "response_location": ("PROVIDER_RESULT" if response_artifact_id is not None else None),
            "response_bucket": response_bucket,
            "response_key": response_key,
            "response_version_id": response_version_id,
            "response_etag": response_etag,
            "response_hash": response_sha256,
            "response_size": response_byte_size,
            "error_code": error_code,
            "error_category": error_category,
            "error_retryable": error_retryable,
            "deadline": (retention_deadline or deadline).replace(tzinfo=None),
            "now": now.replace(tzinfo=None),
        },
    )


def test_mysql_succeeded_call_trigger_requires_stored_response_binding(
    integration_database,
) -> None:
    now, deadline = _seed_owner_graph(integration_database.engine)
    store = CommitObservingArtifactStore(integration_database.engine)
    request_reference = _service(
        integration_database,
        store,
        now=now,
    ).store_artifact(
        _artifact(deadline),
        owner=ProviderArtifactOwner(
            workspace_id=WORKSPACE_ID,
            product_brief_id=PRODUCT_BRIEF_ID,
        ),
        authorize_intent=_authorize,
    )
    with integration_database.engine.connect() as connection:
        request_artifact_id = connection.execute(
            text("SELECT id FROM product_brief_provider_artifacts WHERE kind = 'REQUEST'")
        ).scalar_one()

    with (
        integration_database.engine.begin() as connection,
        pytest.raises(
            exc.DatabaseError,
            match="provider call response artifact binding is invalid",
        ),
    ):
        _insert_completed_call(
            connection,
            now=now,
            deadline=deadline,
            request_artifact_id=request_artifact_id,
            request_key=request_reference.key,
            request_version_id=request_reference.provider_version_id,
            request_etag=request_reference.etag,
            request_sha256=request_reference.sha256,
            request_byte_size=request_reference.byte_size,
        )


@pytest.mark.parametrize(
    ("status", "error_retryable"),
    [("TIMEOUT", True), ("UNKNOWN", False)],
)
def test_mysql_non_successful_call_allows_no_response_artifact(
    integration_database,
    status: str,
    error_retryable: bool,
) -> None:
    now, deadline = _seed_owner_graph(integration_database.engine)
    store = CommitObservingArtifactStore(integration_database.engine)
    request_reference = _service(
        integration_database,
        store,
        now=now,
    ).store_artifact(
        _artifact(deadline),
        owner=ProviderArtifactOwner(
            workspace_id=WORKSPACE_ID,
            product_brief_id=PRODUCT_BRIEF_ID,
        ),
        authorize_intent=_authorize,
    )
    with integration_database.engine.connect() as connection:
        request_artifact_id = connection.execute(
            text("SELECT id FROM product_brief_provider_artifacts WHERE kind = 'REQUEST'")
        ).scalar_one()

    with integration_database.engine.begin() as connection:
        _insert_completed_call(
            connection,
            now=now,
            deadline=deadline,
            request_artifact_id=request_artifact_id,
            request_key=request_reference.key,
            request_version_id=request_reference.provider_version_id,
            request_etag=request_reference.etag,
            request_sha256=request_reference.sha256,
            request_byte_size=request_reference.byte_size,
            status=status,
            error_code=f"PROVIDER_{status}",
            error_category="provider_failure",
            error_retryable=error_retryable,
        )

    with integration_database.engine.connect() as connection:
        persisted = (
            connection.execute(
                text("SELECT status, response_artifact_id FROM product_brief_provider_calls")
            )
            .mappings()
            .one()
        )
    assert persisted == {"status": status, "response_artifact_id": None}


@pytest.mark.parametrize(
    "mismatch",
    [
        "missing_link",
        "wrong_operation_attempt",
        "wrong_call_index",
        "wrong_backend",
        "wrong_bucket",
        "wrong_key",
        "wrong_version",
        "wrong_etag",
        "wrong_sha256",
        "wrong_byte_size",
        "wrong_retention",
    ],
)
def test_mysql_completed_call_trigger_rejects_invalid_direct_sql_request_binding(
    integration_database,
    mismatch: str,
) -> None:
    now, deadline = _seed_owner_graph(integration_database.engine)
    store = CommitObservingArtifactStore(integration_database.engine)
    reference = _service(integration_database, store, now=now).store_artifact(
        _artifact(deadline),
        owner=ProviderArtifactOwner(
            workspace_id=WORKSPACE_ID,
            product_brief_id=PRODUCT_BRIEF_ID,
        ),
        authorize_intent=_authorize,
    )
    with integration_database.engine.connect() as connection:
        artifact_id = connection.execute(
            text("SELECT id FROM product_brief_provider_artifacts")
        ).scalar_one()
    values = {
        "request_artifact_id": (None if mismatch == "missing_link" else artifact_id),
        "request_key": (f"{reference.key}-wrong" if mismatch == "wrong_key" else reference.key),
        "request_version_id": (
            "wrong-version" if mismatch == "wrong_version" else reference.provider_version_id
        ),
        "request_etag": ('"wrong-etag"' if mismatch == "wrong_etag" else reference.etag),
        "operation_attempt": 2 if mismatch == "wrong_operation_attempt" else 1,
        "call_index": 1 if mismatch == "wrong_call_index" else 0,
        "request_storage_backend": (
            "OSS" if mismatch == "wrong_backend" else reference.storage_backend
        ),
        "request_bucket": (
            "wrong-provider-results" if mismatch == "wrong_bucket" else reference.bucket
        ),
        "request_sha256": ("f" * 64 if mismatch == "wrong_sha256" else reference.sha256),
        "request_byte_size": (
            reference.byte_size + 1 if mismatch == "wrong_byte_size" else reference.byte_size
        ),
        "retention_deadline": (
            deadline + timedelta(seconds=1) if mismatch == "wrong_retention" else deadline
        ),
    }

    with (
        integration_database.engine.begin() as connection,
        pytest.raises(exc.DatabaseError),
    ):
        _insert_completed_call(
            connection,
            now=now,
            deadline=deadline,
            **values,
        )


@pytest.mark.parametrize(
    "artifact_variant",
    ["intended", "response_kind"],
)
def test_mysql_completed_call_trigger_requires_stored_request_kind(
    integration_database,
    artifact_variant: str,
) -> None:
    now, deadline = _seed_owner_graph(integration_database.engine)
    store = CommitObservingArtifactStore(
        integration_database.engine,
        crash_after_write=artifact_variant == "intended",
    )
    kind = (
        ProviderArtifactKind.RESPONSE
        if artifact_variant == "response_kind"
        else ProviderArtifactKind.REQUEST
    )
    try:
        reference = _service(integration_database, store, now=now).store_artifact(
            _artifact(deadline, kind=kind),
            owner=ProviderArtifactOwner(
                workspace_id=WORKSPACE_ID,
                product_brief_id=PRODUCT_BRIEF_ID,
            ),
            authorize_intent=_authorize,
        )
    except SimulatedProcessCrash:
        reference = None
    with integration_database.engine.connect() as connection:
        artifact = (
            connection.execute(
                text(
                    "SELECT id, object_key, expected_sha256, expected_byte_size "
                    "FROM product_brief_provider_artifacts"
                )
            )
            .mappings()
            .one()
        )

    with (
        integration_database.engine.begin() as connection,
        pytest.raises(
            exc.DatabaseError,
            match="provider call request artifact binding is invalid",
        ),
    ):
        _insert_completed_call(
            connection,
            now=now,
            deadline=deadline,
            request_artifact_id=artifact["id"],
            request_key=artifact["object_key"],
            request_version_id=(
                reference.provider_version_id if reference is not None else "uncommitted-version"
            ),
            request_etag=(reference.etag if reference is not None else '"uncommitted-etag"'),
            request_sha256=artifact["expected_sha256"],
            request_byte_size=artifact["expected_byte_size"],
        )


def test_mysql_completed_call_trigger_accepts_exact_required_response_binding(
    integration_database,
) -> None:
    now, deadline = _seed_owner_graph(integration_database.engine)
    store = CommitObservingArtifactStore(integration_database.engine)
    service = _service(integration_database, store, now=now)
    owner = ProviderArtifactOwner(
        workspace_id=WORKSPACE_ID,
        product_brief_id=PRODUCT_BRIEF_ID,
    )
    request_reference = service.store_artifact(
        _artifact(deadline),
        owner=owner,
        authorize_intent=_authorize,
    )
    response_reference = service.store_artifact(
        _artifact(deadline, kind=ProviderArtifactKind.RESPONSE),
        owner=owner,
        authorize_intent=_authorize,
    )
    with integration_database.engine.connect() as connection:
        artifact_ids = {
            row["kind"]: row["id"]
            for row in connection.execute(
                text("SELECT id, kind FROM product_brief_provider_artifacts")
            ).mappings()
        }

    with integration_database.engine.begin() as connection:
        _insert_completed_call(
            connection,
            now=now,
            deadline=deadline,
            request_artifact_id=artifact_ids["REQUEST"],
            request_key=request_reference.key,
            request_version_id=request_reference.provider_version_id,
            request_etag=request_reference.etag,
            request_sha256=request_reference.sha256,
            request_byte_size=request_reference.byte_size,
            response_artifact_id=artifact_ids["RESPONSE"],
            response_storage_backend=response_reference.storage_backend,
            response_bucket=response_reference.bucket,
            response_key=response_reference.key,
            response_version_id=response_reference.provider_version_id,
            response_etag=response_reference.etag,
            response_sha256=response_reference.sha256,
            response_byte_size=response_reference.byte_size,
        )

    with integration_database.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM product_brief_provider_calls")
            ).scalar_one()
            == 1
        )


@pytest.mark.parametrize("mismatch", ["wrong_kind", "wrong_key"])
def test_mysql_completed_call_trigger_rejects_invalid_response_binding(
    integration_database,
    mismatch: str,
) -> None:
    now, deadline = _seed_owner_graph(integration_database.engine)
    store = CommitObservingArtifactStore(integration_database.engine)
    service = _service(integration_database, store, now=now)
    owner = ProviderArtifactOwner(
        workspace_id=WORKSPACE_ID,
        product_brief_id=PRODUCT_BRIEF_ID,
    )
    request_reference = service.store_artifact(
        _artifact(deadline),
        owner=owner,
        authorize_intent=_authorize,
    )
    response_reference = service.store_artifact(
        _artifact(deadline, kind=ProviderArtifactKind.RESPONSE),
        owner=owner,
        authorize_intent=_authorize,
    )
    with integration_database.engine.connect() as connection:
        artifact_ids = {
            row["kind"]: row["id"]
            for row in connection.execute(
                text("SELECT id, kind FROM product_brief_provider_artifacts")
            ).mappings()
        }
    selected_response = request_reference if mismatch == "wrong_kind" else response_reference

    with (
        integration_database.engine.begin() as connection,
        pytest.raises(
            exc.DatabaseError,
            match="provider call response artifact binding is invalid",
        ),
    ):
        _insert_completed_call(
            connection,
            now=now,
            deadline=deadline,
            request_artifact_id=artifact_ids["REQUEST"],
            request_key=request_reference.key,
            request_version_id=request_reference.provider_version_id,
            request_etag=request_reference.etag,
            request_sha256=request_reference.sha256,
            request_byte_size=request_reference.byte_size,
            response_artifact_id=artifact_ids[
                "REQUEST" if mismatch == "wrong_kind" else "RESPONSE"
            ],
            response_storage_backend=selected_response.storage_backend,
            response_bucket=selected_response.bucket,
            response_key=(
                f"{selected_response.key}-wrong"
                if mismatch == "wrong_key"
                else selected_response.key
            ),
            response_version_id=selected_response.provider_version_id,
            response_etag=selected_response.etag,
            response_sha256=selected_response.sha256,
            response_byte_size=selected_response.byte_size,
        )
