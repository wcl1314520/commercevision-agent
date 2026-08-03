from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from urllib.parse import urlparse

import boto3
import httpx
import pytest
from alembic import command
from alembic.config import Config
from botocore.client import Config as BotoConfig
from commercevision_api.asset_routes import asset_router
from commercevision_api.errors import install_error_handlers
from commercevision_application import (
    AuthenticatedPrincipal,
    DeadLetterOperatorService,
    DurableOperationWorker,
    ImageIndexingExecutor,
    OperationApplicationService,
    OperationExecutionBoundary,
    OperationExecutionFailure,
    OperationExecutionRequest,
    OperationExecutorRegistry,
    OperationReconciliationPolicy,
    OperationRecoveryService,
    OperationRetryPolicy,
)
from commercevision_contracts import (
    EmbeddingImageInputV1,
    EmbeddingProviderErrorV1,
    EmbeddingProviderFailure,
    EmbeddingProviderRequestV1,
    EmbeddingProviderResultV1,
    EmbeddingVectorV1,
    MilvusVectorIdentityV1,
    MilvusVectorProofV1,
)
from commercevision_contracts.events import (
    AssetIndexDeleteRequestedPayload,
    AssetIndexRequestedPayload,
)
from commercevision_domain import (
    CollectionSpec,
    CollectionState,
    EmbeddingState,
    OperationKind,
    OperationState,
    StorageLocationClass,
    VectorKind,
    new_uuid7,
)
from commercevision_object_storage import MinioObjectStorage
from commercevision_persistence import (
    ImageIndexNotApplicable,
    MySqlExactImageReference,
    MySqlImageIndexRequestService,
    MySqlIndexingAuthority,
    SqlAlchemyImageIndexStatusQueries,
    SqlAlchemyOperationUnitOfWork,
    SqlAlchemyOperatorUnitOfWork,
    create_database,
    is_unit_of_work_active,
)
from commercevision_persistence.repositories import OutboxRepository
from commercevision_retrieval import MilvusVectorIndexAdapter
from commercevision_worker.runtime import WorkerRuntime
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.integration

WORKSPACE = "workspace-index-mysql"
PROVIDER = "alibaba-model-studio"
_MILVUS_URI = os.getenv("CV_TEST_MILVUS_URI", "http://127.0.0.1:19531")
_MINIO_ENDPOINT = os.getenv("CV_TEST_MINIO_ENDPOINT", "http://127.0.0.1:19000")
_MINIO_ACCESS_KEY = os.getenv("CV_TEST_MINIO_ACCESS_KEY", "commercevision")
_MINIO_SECRET_KEY = os.getenv("CV_TEST_MINIO_SECRET_KEY", "commercevision-secret")


class _AllowWorkspaceAdminPolicy:
    def require_admin(
        self,
        *,
        workspace_id: str,
        principal: AuthenticatedPrincipal,
    ) -> None:
        assert workspace_id in principal.admin_workspace_ids

    def require_system_admin(self, *, principal: AuthenticatedPrincipal) -> None:
        assert principal.is_system_admin


@pytest.fixture
def indexing_database(integration_settings, monkeypatch):
    source_url = make_url(integration_settings.mysql_dsn)
    database_name = f"commercevision_ticket09_{uuid.uuid4().hex[:8]}"
    admin_engine = create_engine(source_url.set(database="mysql"))
    test_url = source_url.set(database=database_name)
    with admin_engine.begin() as connection:
        connection.execute(
            text(
                f"CREATE DATABASE `{database_name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            )
        )
    monkeypatch.setenv(
        "CV_MIGRATION_MYSQL_DSN",
        test_url.render_as_string(hide_password=False),
    )
    command.upgrade(
        Config(str(Path(__file__).parents[2] / "alembic.ini")),
        "head",
    )
    database = create_database(
        integration_settings.model_copy(
            update={"mysql_dsn": test_url.render_as_string(hide_password=False)}
        )
    )
    try:
        yield database
    finally:
        database.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{database_name}`"))
        admin_engine.dispose()


@pytest.fixture
def real_index_infra() -> Iterator[
    tuple[object, str, MinioObjectStorage, MilvusVectorIndexAdapter, CollectionSpec]
]:
    bucket = f"cv-t09-{uuid.uuid4().hex[:20]}"
    s3 = boto3.client(
        "s3",
        endpoint_url=_MINIO_ENDPOINT,
        aws_access_key_id=_MINIO_ACCESS_KEY,
        aws_secret_access_key=_MINIO_SECRET_KEY,
        region_name="us-east-1",
        config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    spec = CollectionSpec.create(
        model_family="qwen3-vl-embedding",
        pinned_revision=f"ticket09-{uuid.uuid4().hex}",
        dimension=4,
        vector_kind=VectorKind.IMAGE,
        schema_version=1,
        index_spec_version="hnsw-cosine-v1",
    )
    storage = MinioObjectStorage(
        endpoint=_MINIO_ENDPOINT,
        presign_endpoint=_MINIO_ENDPOINT,
        access_key=_MINIO_ACCESS_KEY,
        secret_key=_MINIO_SECRET_KEY,
        session_token=None,
        region="us-east-1",
        buckets={StorageLocationClass.FOUNDATION: bucket},
        verify_tls=False,
        force_path_style=True,
        require_encryption=False,
        connect_timeout=3,
        read_timeout=10,
    )
    vectors = MilvusVectorIndexAdapter(
        uri=_MILVUS_URI,
        timeout_seconds=15,
        readiness_timeout_seconds=10,
    )
    bucket_created = False
    milvus_ready = False

    def cleanup() -> None:
        failures: list[Exception] = []
        if milvus_ready:
            try:
                client = vectors._get_client()  # noqa: SLF001 - exact test cleanup
                if client.has_collection(collection_name=spec.physical_name, timeout=5):
                    client.drop_collection(collection_name=spec.physical_name, timeout=5)
            except Exception as adapter_cleanup_error:
                try:
                    from pymilvus import MilvusClient

                    admin = MilvusClient(uri=_MILVUS_URI, timeout=5)
                    try:
                        if admin.has_collection(collection_name=spec.physical_name, timeout=5):
                            admin.drop_collection(collection_name=spec.physical_name, timeout=5)
                    finally:
                        admin.close()
                except Exception as admin_cleanup_error:
                    failures.extend([adapter_cleanup_error, admin_cleanup_error])
        try:
            storage.close()
        except Exception as storage_cleanup_error:
            failures.append(storage_cleanup_error)
        try:
            vectors.close()
        except Exception as vector_cleanup_error:
            failures.append(vector_cleanup_error)
        if bucket_created:
            try:
                paginator = s3.get_paginator("list_object_versions")
                for page in paginator.paginate(Bucket=bucket):
                    owned = [
                        {"Key": item["Key"], "VersionId": item["VersionId"]}
                        for item in (
                            *page.get("Versions", []),
                            *page.get("DeleteMarkers", []),
                        )
                    ]
                    for item in owned:
                        s3.delete_object(
                            Bucket=bucket,
                            Key=item["Key"],
                            VersionId=item["VersionId"],
                        )
                s3.delete_bucket(Bucket=bucket)
            except Exception as bucket_cleanup_error:
                failures.append(bucket_cleanup_error)
        try:
            s3.close()
        except Exception as client_cleanup_error:
            failures.append(client_cleanup_error)
        if failures:
            raise ExceptionGroup("real index fixture cleanup failed", failures)

    try:
        s3.create_bucket(Bucket=bucket)
        bucket_created = True
        s3.put_bucket_versioning(
            Bucket=bucket,
            VersioningConfiguration={"Status": "Enabled"},
        )
        storage.assert_ready({StorageLocationClass.FOUNDATION})
        vectors.assert_ready()
        milvus_ready = True
    except Exception as exc:
        cleanup()
        pytest.skip(f"real MinIO/Milvus indexing services unavailable: {exc}")
    try:
        yield s3, bucket, storage, vectors, spec
    finally:
        assert bucket.startswith("cv-t09-")
        cleanup()


def _seed_available_image(
    database,
    *,
    asset_kind: str = "IMAGE",
    content_sha256: str = "a" * 64,
    byte_size: int = 128,
) -> tuple[str, str, str]:
    asset_id = new_uuid7()
    asset_version_id = new_uuid7()
    rights_record_id = new_uuid7()
    now = datetime.now(UTC).replace(tzinfo=None)
    with database.engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        connection.execute(
            text(
                "INSERT INTO assets "
                "(id, workspace_id, retention_class, asset_kind, workflow_id, product_id, "
                "sku_id, status, block_reason, current_version_id, current_rights_record_id, "
                "retention_deadline, version, created_at, updated_at) VALUES "
                "(:asset, :workspace, 'FOUNDATION', :asset_kind, NULL, NULL, NULL, "
                "'AVAILABLE', NULL, :asset_version, :rights, NULL, 3, :now, :now)"
            ),
            {
                "asset": asset_id,
                "workspace": WORKSPACE,
                "asset_kind": asset_kind,
                "asset_version": asset_version_id,
                "rights": rights_record_id,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO asset_versions "
                "(id, workspace_id, asset_id, version_number, upload_session_id, filename, "
                "sha256, byte_size, declared_mime, detected_mime, image_format, width, "
                "height, frame_count, category, role, integrity_policy_version, "
                "validation_policy_version, validation_transfer_policy_version, "
                "validation_transfer_policy_snapshot_sha256, created_at) VALUES "
                "(:version, :workspace, :asset, 7, :upload, 'image.png', :sha, :byte_size, "
                "'image/png', 'image/png', 'PNG', 64, 64, 1, 'APPAREL', 'HERO', "
                "'integrity-v1', 'validation-v1', 'transfer-v1', :transfer_sha, :now)"
            ),
            {
                "version": asset_version_id,
                "workspace": WORKSPACE,
                "asset": asset_id,
                "upload": new_uuid7(),
                "sha": content_sha256,
                "byte_size": byte_size,
                "transfer_sha": "b" * 64,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO rights_records "
                "(id, workspace_id, asset_id, asset_version_id, version_number, decision, "
                "owner_reference, source, license_reference, derivative_allowed, "
                "public_demo_allowed, evidence_reference, terms_sha256, valid_from, "
                "valid_until, perpetual, supersedes_record_id, created_by, created_at, "
                "permissions_sealed_at) VALUES "
                "(:rights, :workspace, :asset, :version, 3, 'GRANT', 'owner', 'contract', "
                "'license', 0, 0, 'evidence://index', :terms, :valid_from, NULL, 1, "
                "NULL, 'test', :now, NULL)"
            ),
            {
                "rights": rights_record_id,
                "workspace": WORKSPACE,
                "asset": asset_id,
                "version": asset_version_id,
                "terms": "c" * 64,
                "valid_from": now - timedelta(days=1),
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO rights_record_uses "
                "(workspace_id, asset_id, rights_record_id, allowed_use, created_at) "
                "VALUES (:workspace, :asset, :rights, 'RETRIEVAL', :now)"
            ),
            {"workspace": WORKSPACE, "asset": asset_id, "rights": rights_record_id, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO rights_record_providers "
                "(workspace_id, asset_id, rights_record_id, allowed_provider, created_at) "
                "VALUES (:workspace, :asset, :rights, :provider, :now)"
            ),
            {
                "workspace": WORKSPACE,
                "asset": asset_id,
                "rights": rights_record_id,
                "provider": PROVIDER,
                "now": now,
            },
        )
        connection.execute(
            text("UPDATE rights_records SET permissions_sealed_at = :now WHERE id = :rights"),
            {"rights": rights_record_id, "now": now},
        )
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    return asset_id, asset_version_id, rights_record_id


def _requests(
    database,
    *,
    collection_spec: CollectionSpec | None = None,
    max_attempts: int = 5,
    max_reconciliation_attempts: int = 8,
) -> MySqlImageIndexRequestService:
    return MySqlImageIndexRequestService(
        session_factory=database.session_factory,
        collection_spec=collection_spec
        or CollectionSpec.create(
            model_family="qwen3-vl-embedding",
            pinned_revision="2026-06-30",
            dimension=4,
            vector_kind=VectorKind.IMAGE,
            schema_version=1,
            index_spec_version="hnsw-cosine-v1",
        ),
        provider=PROVIDER,
        model_id="qwen3-vl-embedding",
        model_configuration_version="embedding-config-v1",
        preprocessing_version="image-preprocess-v1",
        max_attempts=max_attempts,
        max_reconciliation_attempts=max_reconciliation_attempts,
    )


def _provider_result() -> EmbeddingProviderResultV1:
    return EmbeddingProviderResultV1(
        vectors=[EmbeddingVectorV1(values=[0.1, 0.2, 0.3, 0.4])],
        provider=PROVIDER,
        provider_request_id="provider-request-1",
        actual_model="qwen3-vl-embedding-2026-06-30",
        latency_ms=1,
    )


def _seed_real_controlled_image(
    database,
    s3: object,
    bucket: str,
) -> tuple[str, str, bytes]:
    content = (b"ticket09-real-controlled-image-" * 40)[:1024]
    digest = hashlib.sha256(content).hexdigest()
    asset_id, version_id, _ = _seed_available_image(
        database,
        content_sha256=digest,
        byte_size=len(content),
    )
    key = f"ticket09/{version_id}/image.bin"
    uploaded = s3.put_object(  # type: ignore[attr-defined]
        Bucket=bucket,
        Key=key,
        Body=content,
        ContentType="application/octet-stream",
        Metadata={"sha256": digest},
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO asset_objects "
                "(id, workspace_id, asset_version_id, role, backend, location, bucket, "
                "`key`, provider_version_id, etag, byte_size, sha256, state, version, "
                "created_at, updated_at) VALUES "
                "(:id, :workspace, :version, 'CONTROLLED_ORIGINAL', 'MINIO', "
                "'FOUNDATION', :bucket, :key, :provider_version, :etag, :byte_size, "
                ":sha, 'CONTROLLED', 1, :now, :now)"
            ),
            {
                "id": new_uuid7(),
                "workspace": WORKSPACE,
                "version": version_id,
                "bucket": bucket,
                "key": key,
                "provider_version": uploaded["VersionId"],
                "etag": uploaded["ETag"],
                "byte_size": len(content),
                "sha": digest,
                "now": now,
            },
        )
    return asset_id, version_id, content


class _DownloadingDeterministicProvider:
    def __init__(
        self,
        *,
        timeout_failures: int = 0,
        wrong_dimension: bool = False,
        after_download: Callable[[], None] | None = None,
    ) -> None:
        self.calls = 0
        self.verified_downloads = 0
        self.timeout_failures = timeout_failures
        self.wrong_dimension = wrong_dimension
        self.after_download = after_download

    def embed(self, request: EmbeddingProviderRequestV1) -> EmbeddingProviderResultV1:
        self.calls += 1
        image = request.images[0]
        url = image.url.get_secret_value()
        assert urlparse(url).hostname in {"127.0.0.1", "localhost"}
        response = httpx.get(
            url,
            headers={
                name: value.get_secret_value() for name, value in image.required_headers.items()
            },
            timeout=10,
        )
        response.raise_for_status()
        assert len(response.content) == image.byte_size
        assert hashlib.sha256(response.content).hexdigest() == image.content_sha256
        self.verified_downloads += 1
        if self.after_download is not None:
            self.after_download()
        if self.timeout_failures > 0:
            self.timeout_failures -= 1
            raise TimeoutError("deterministic provider timeout")
        digest = hashlib.sha256(response.content).digest()
        dimension = 3 if self.wrong_dimension else 4
        return EmbeddingProviderResultV1(
            vectors=[
                EmbeddingVectorV1(values=[(digest[index] + 1) / 256 for index in range(dimension)])
            ],
            provider=PROVIDER,
            provider_request_id=f"deterministic-{self.calls}",
            actual_model="deterministic-ticket09",
            latency_ms=1,
        )


class _UnknownOnceVectorIndex:
    def __init__(self, delegate: MilvusVectorIndexAdapter) -> None:
        self.delegate = delegate
        self.unknown_upserts = 1

    def ensure_collection(self, request) -> None:
        self.delegate.ensure_collection(request)

    def upsert(self, request) -> None:
        self.delegate.upsert(request)
        if self.unknown_upserts > 0:
            self.unknown_upserts -= 1
            raise TimeoutError("simulated lost Milvus response")

    def prove(self, identity):
        return self.delegate.prove(identity)

    def delete_if_generation(self, identity):
        return self.delegate.delete_if_generation(identity)


class _UnknownUpsertThenUnavailableProof(_UnknownOnceVectorIndex):
    def __init__(self, delegate: MilvusVectorIndexAdapter) -> None:
        super().__init__(delegate)
        self.proof_failures = 1

    def prove(self, identity):
        if self.proof_failures > 0:
            self.proof_failures -= 1
            raise ConnectionError("simulated reconciliation outage")
        return self.delegate.prove(identity)


class _SimulatedWorkerCrash(BaseException):
    pass


class _CrashAfterRealUpsertVectorIndex:
    def __init__(self, delegate: MilvusVectorIndexAdapter) -> None:
        self.delegate = delegate
        self.crashes = 1

    def ensure_collection(self, request) -> None:
        self.delegate.ensure_collection(request)

    def upsert(self, request) -> None:
        self.delegate.upsert(request)
        if self.crashes > 0:
            self.crashes -= 1
            raise _SimulatedWorkerCrash("worker stopped after Milvus committed")

    def prove(self, identity):
        return self.delegate.prove(identity)

    def delete_if_generation(self, identity):
        return self.delegate.delete_if_generation(identity)


class _BlockedBeforeRealUpsertVectorIndex:
    def __init__(self, delegate: MilvusVectorIndexAdapter) -> None:
        self.delegate = delegate
        self.entered = Event()
        self.release = Event()

    def ensure_collection(self, request) -> None:
        self.delegate.ensure_collection(request)

    def upsert(self, request) -> None:
        self.entered.set()
        if not self.release.wait(timeout=10):
            raise AssertionError("test did not release the blocked Milvus upsert")
        self.delegate.upsert(request)

    def prove(self, identity):
        return self.delegate.prove(identity)

    def delete_if_generation(self, identity):
        return self.delegate.delete_if_generation(identity)


class _CrashAfterIndexCommitExecutor:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.crashes = 1

    def execute(self, request):
        result = self.delegate.execute(request)
        if self.crashes > 0:
            self.crashes -= 1
            raise _SimulatedWorkerCrash("worker stopped before operation success commit")
        return result

    def reconcile(self, request):
        return self.delegate.reconcile(request)

    def mark_terminal_failure(self, request) -> None:
        self.delegate.mark_terminal_failure(request)


class _OutageAfterRealEnsureVectorIndex:
    def __init__(self, delegate: MilvusVectorIndexAdapter) -> None:
        self.delegate = delegate
        self.outages = 1

    def ensure_collection(self, request) -> None:
        self.delegate.ensure_collection(request)
        if self.outages > 0:
            self.outages -= 1
            raise ConnectionError("simulated Milvus connection outage")

    def upsert(self, request) -> None:
        self.delegate.upsert(request)

    def prove(self, identity):
        return self.delegate.prove(identity)

    def delete_if_generation(self, identity):
        return self.delegate.delete_if_generation(identity)


class _IndexClock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC) + timedelta(minutes=1)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int = 3) -> None:
        self.now += timedelta(seconds=seconds)


def _real_index_runtime(
    *,
    database,
    storage: MinioObjectStorage,
    vectors,
    spec: CollectionSpec,
    provider,
    executor_wrapper: Callable[[object], object] | None = None,
    authority_factory: Callable[[object], MySqlIndexingAuthority] | None = None,
) -> tuple[object, MySqlIndexingAuthority, OperationApplicationService, _IndexClock]:
    authority = (
        authority_factory(database.session_factory)
        if authority_factory is not None
        else MySqlIndexingAuthority(database.session_factory)
    )
    executor = ImageIndexingExecutor(
        authority=authority,
        references=MySqlExactImageReference(
            session_factory=database.session_factory,
            storage=storage,
            lifetime=timedelta(minutes=2),
        ),
        embedding=provider,
        vectors=vectors,
    )
    if executor_wrapper is not None:
        executor = executor_wrapper(executor)
    registry = OperationExecutorRegistry()
    registry.register(kind=OperationKind.ASSET_INDEXING, executor=executor)
    operations = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(database.session_factory)
    )
    clock = _IndexClock()
    worker = DurableOperationWorker(
        operations=operations,
        execution=OperationExecutionBoundary(
            executor=registry,
            transaction_active=is_unit_of_work_active,
        ),
        owner="ticket09-real-index-worker",
        lease_duration=timedelta(seconds=30),
        retry_policy=OperationRetryPolicy(
            initial_delay=timedelta(seconds=1),
            maximum_delay=timedelta(seconds=1),
            maximum_elapsed=timedelta(hours=1),
        ),
        reconciliation_policy=OperationReconciliationPolicy(
            initial_delay=timedelta(seconds=1),
            maximum_delay=timedelta(seconds=1),
            maximum_elapsed=timedelta(hours=1),
        ),
        clock=clock,
    )
    runtime = SimpleNamespace(
        database=database,
        operation_worker=worker,
        image_index_authority=authority,
        image_vector_index=vectors,
    )
    return runtime, authority, operations, clock


def _outbox_event(database, *, aggregate_id: str, event_type: str, trace_id: str):
    with database.session_factory() as session:
        events = OutboxRepository(session).list_for_aggregate(aggregate_id)
    return next(
        event
        for event in reversed(events)
        if event.envelope.event_type == event_type and event.envelope.trace_id == trace_id
    )


def _vector_identity(
    authority: MySqlIndexingAuthority,
    request: OperationExecutionRequest,
) -> MilvusVectorIdentityV1:
    target = authority.load_for_reconciliation(request)
    return MilvusVectorIdentityV1(
        collection_name=target.collection_spec.physical_name,
        embedding_record_id=target.embedding_record_id,
        milvus_primary_key=f"{target.embedding_record_id}:g{target.write_generation}",
        input_hash=target.input_hash,
        embedding_spec_sha256=target.embedding_spec_sha256,
        write_generation=target.write_generation,
    )


def _recover_expired_index_operation(
    *,
    database,
    runtime,
    operations: OperationApplicationService,
    clock: _IndexClock,
    request: OperationExecutionRequest,
):
    running = operations.get(
        workspace_id=request.workspace_id,
        operation_id=request.operation_id,
    )
    assert running.state is OperationState.RUNNING
    assert running.lease_expires_at is not None
    recovered_at = running.lease_expires_at
    assert (
        OperationRecoveryService(
            uow_factory=lambda: SqlAlchemyOperationUnitOfWork(database.session_factory),
            batch_size=10,
        ).recover_once(now=recovered_at)
        == 1
    )
    clock.now = recovered_at
    recovery_event = _outbox_event(
        database,
        aggregate_id=request.operation_id,
        event_type="operation.recovery.requested",
        trace_id=f"operation-recovery:{request.operation_id}",
    )
    return runtime.operation_worker.handle_recovery_event(recovery_event)


def _activate_and_claim(
    authority: MySqlIndexingAuthority,
    request: OperationExecutionRequest,
):
    target = authority.load_for_provisioning(request)
    authority.activate_collection(target)
    return authority.claim_for_submission(request)


def _grant_replacement_rights(
    database,
    *,
    asset_id: str,
    asset_version_id: str,
) -> str:
    rights_id = new_uuid7()
    now = datetime.now(UTC).replace(tzinfo=None)
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO rights_records "
                "(id, workspace_id, asset_id, asset_version_id, version_number, decision, "
                "owner_reference, source, license_reference, derivative_allowed, "
                "public_demo_allowed, evidence_reference, terms_sha256, valid_from, "
                "valid_until, perpetual, supersedes_record_id, created_by, created_at, "
                "permissions_sealed_at) VALUES "
                "(:rights, :workspace, :asset, :version, 4, 'GRANT', 'owner', 'contract', "
                "'license-2', 0, 0, 'evidence://regrant', :terms, :valid_from, NULL, 1, "
                "NULL, 'test', :now, NULL)"
            ),
            {
                "rights": rights_id,
                "workspace": WORKSPACE,
                "asset": asset_id,
                "version": asset_version_id,
                "terms": "d" * 64,
                "valid_from": now - timedelta(minutes=1),
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO rights_record_uses "
                "(workspace_id, asset_id, rights_record_id, allowed_use, created_at) "
                "VALUES (:workspace, :asset, :rights, 'RETRIEVAL', :now)"
            ),
            {"workspace": WORKSPACE, "asset": asset_id, "rights": rights_id, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO rights_record_providers "
                "(workspace_id, asset_id, rights_record_id, allowed_provider, created_at) "
                "VALUES (:workspace, :asset, :rights, :provider, :now)"
            ),
            {
                "workspace": WORKSPACE,
                "asset": asset_id,
                "rights": rights_id,
                "provider": PROVIDER,
                "now": now,
            },
        )
        connection.execute(
            text("UPDATE rights_records SET permissions_sealed_at = :now WHERE id = :rights"),
            {"rights": rights_id, "now": now},
        )
        connection.execute(
            text(
                "UPDATE assets SET status = 'AVAILABLE', block_reason = NULL, "
                "current_rights_record_id = :rights, version = version + 1 "
                "WHERE workspace_id = :workspace AND id = :asset"
            ),
            {"rights": rights_id, "workspace": WORKSPACE, "asset": asset_id},
        )
    return rights_id


def _block_asset(database, asset_id: str) -> None:
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE assets SET status = 'BLOCKED', "
                "block_reason = 'ADMINISTRATIVELY_BLOCKED', version = version + 1 "
                "WHERE workspace_id = :workspace AND id = :asset"
            ),
            {"workspace": WORKSPACE, "asset": asset_id},
        )


def test_real_index_matrix_01_incremental_happy_path(
    indexing_database,
    real_index_infra,
) -> None:
    s3, bucket, storage, vectors, spec = real_index_infra
    asset_id, _, _ = _seed_real_controlled_image(indexing_database, s3, bucket)
    request = (
        _requests(indexing_database, collection_spec=spec)
        .request_current_image(
            workspace_id=WORKSPACE,
            asset_id=asset_id,
        )
        .operation
    )
    provider = _DownloadingDeterministicProvider()
    runtime, authority, operations, _ = _real_index_runtime(
        database=indexing_database,
        storage=storage,
        vectors=vectors,
        spec=spec,
        provider=provider,
    )
    event = _outbox_event(
        indexing_database,
        aggregate_id=request.target_id,
        event_type="asset.index.requested",
        trace_id=request.operation_id,
    )

    WorkerRuntime._handle_asset_index(runtime, event)

    completed = operations.get(
        workspace_id=request.workspace_id,
        operation_id=request.operation_id,
    )
    assert completed.state is OperationState.SUCCEEDED
    assert provider.calls == provider.verified_downloads == 1
    identity = _vector_identity(authority, request)
    assert vectors.prove(identity).matches(identity)


def test_real_index_matrix_02_duplicate_event_and_upsert_are_idempotent(
    indexing_database,
    real_index_infra,
) -> None:
    s3, bucket, storage, vectors, spec = real_index_infra
    asset_id, _, _ = _seed_real_controlled_image(indexing_database, s3, bucket)
    request = (
        _requests(indexing_database, collection_spec=spec)
        .request_current_image(
            workspace_id=WORKSPACE,
            asset_id=asset_id,
        )
        .operation
    )
    provider = _DownloadingDeterministicProvider()
    runtime, authority, operations, _ = _real_index_runtime(
        database=indexing_database,
        storage=storage,
        vectors=vectors,
        spec=spec,
        provider=provider,
    )
    event = _outbox_event(
        indexing_database,
        aggregate_id=request.target_id,
        event_type="asset.index.requested",
        trace_id=request.operation_id,
    )

    WorkerRuntime._handle_asset_index(runtime, event)
    WorkerRuntime._handle_asset_index(runtime, event)

    completed = operations.get(
        workspace_id=request.workspace_id,
        operation_id=request.operation_id,
    )
    assert completed.state is OperationState.SUCCEEDED
    assert provider.calls == provider.verified_downloads == 1
    identity = _vector_identity(authority, request)
    assert identity.write_generation == 1
    assert vectors.prove(identity).matches(identity)


def test_real_index_matrix_03_dimension_mismatch_fails_before_real_milvus_upsert(
    indexing_database,
    real_index_infra,
) -> None:
    s3, bucket, storage, vectors, spec = real_index_infra
    asset_id, _, _ = _seed_real_controlled_image(indexing_database, s3, bucket)
    request = (
        _requests(indexing_database, collection_spec=spec)
        .request_current_image(
            workspace_id=WORKSPACE,
            asset_id=asset_id,
        )
        .operation
    )
    provider = _DownloadingDeterministicProvider(wrong_dimension=True)
    runtime, authority, operations, _ = _real_index_runtime(
        database=indexing_database,
        storage=storage,
        vectors=vectors,
        spec=spec,
        provider=provider,
    )
    event = _outbox_event(
        indexing_database,
        aggregate_id=request.target_id,
        event_type="asset.index.requested",
        trace_id=request.operation_id,
    )

    WorkerRuntime._handle_asset_index(runtime, event)

    assert (
        operations.get(
            workspace_id=request.workspace_id,
            operation_id=request.operation_id,
        ).state
        is OperationState.FAILED
    )
    assert provider.calls == provider.verified_downloads == 1
    identity = _vector_identity(authority, request)
    assert identity.write_generation == 1
    assert vectors.prove(identity).exists is False
    with indexing_database.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT state FROM embedding_records WHERE id = :id"),
                {"id": request.target_id},
            ).scalar_one()
            == EmbeddingState.PERMANENT_FAILED.value
        )


def test_real_index_matrix_04_provider_timeout_retries_after_verified_download(
    indexing_database,
    real_index_infra,
) -> None:
    s3, bucket, storage, vectors, spec = real_index_infra
    asset_id, _, _ = _seed_real_controlled_image(indexing_database, s3, bucket)
    request = (
        _requests(indexing_database, collection_spec=spec)
        .request_current_image(
            workspace_id=WORKSPACE,
            asset_id=asset_id,
        )
        .operation
    )
    provider = _DownloadingDeterministicProvider(timeout_failures=1)
    runtime, authority, operations, clock = _real_index_runtime(
        database=indexing_database,
        storage=storage,
        vectors=vectors,
        spec=spec,
        provider=provider,
    )
    event = _outbox_event(
        indexing_database,
        aggregate_id=request.target_id,
        event_type="asset.index.requested",
        trace_id=request.operation_id,
    )

    WorkerRuntime._handle_asset_index(runtime, event)
    assert (
        operations.get(
            workspace_id=request.workspace_id,
            operation_id=request.operation_id,
        ).state
        is OperationState.RETRYABLE_FAILED
    )
    clock.advance()
    WorkerRuntime._handle_asset_index(runtime, event)

    assert (
        operations.get(
            workspace_id=request.workspace_id,
            operation_id=request.operation_id,
        ).state
        is OperationState.SUCCEEDED
    )
    assert provider.calls == provider.verified_downloads == 2
    identity = _vector_identity(authority, request)
    assert identity.write_generation == 2
    assert vectors.prove(identity).matches(identity)


def test_real_index_matrix_05_milvus_outage_does_not_activate_or_call_provider(
    indexing_database,
    real_index_infra,
) -> None:
    s3, bucket, storage, vectors, spec = real_index_infra
    asset_id, _, _ = _seed_real_controlled_image(indexing_database, s3, bucket)
    request = (
        _requests(indexing_database, collection_spec=spec)
        .request_current_image(
            workspace_id=WORKSPACE,
            asset_id=asset_id,
        )
        .operation
    )
    provider = _DownloadingDeterministicProvider()
    outage_vectors = _OutageAfterRealEnsureVectorIndex(vectors)
    runtime, authority, operations, clock = _real_index_runtime(
        database=indexing_database,
        storage=storage,
        vectors=outage_vectors,
        spec=spec,
        provider=provider,
    )
    event = _outbox_event(
        indexing_database,
        aggregate_id=request.target_id,
        event_type="asset.index.requested",
        trace_id=request.operation_id,
    )

    WorkerRuntime._handle_asset_index(runtime, event)
    assert (
        operations.get(
            workspace_id=request.workspace_id,
            operation_id=request.operation_id,
        ).state
        is OperationState.RETRYABLE_FAILED
    )
    assert provider.calls == provider.verified_downloads == 0
    with indexing_database.engine.connect() as connection:
        assert connection.execute(
            text("SELECT state, is_read_enabled, is_write_enabled FROM collection_registry")
        ).one() == (CollectionState.PLANNED.value, 0, 0)

    clock.advance()
    WorkerRuntime._handle_asset_index(runtime, event)

    assert (
        operations.get(
            workspace_id=request.workspace_id,
            operation_id=request.operation_id,
        ).state
        is OperationState.SUCCEEDED
    )
    assert provider.calls == provider.verified_downloads == 1
    identity = _vector_identity(authority, request)
    assert vectors.prove(identity).matches(identity)


def test_real_index_matrix_06_crash_after_upsert_reconciles_exact_vector(
    indexing_database,
    real_index_infra,
) -> None:
    s3, bucket, storage, vectors, spec = real_index_infra
    asset_id, _, _ = _seed_real_controlled_image(indexing_database, s3, bucket)
    request = (
        _requests(indexing_database, collection_spec=spec)
        .request_current_image(
            workspace_id=WORKSPACE,
            asset_id=asset_id,
        )
        .operation
    )
    provider = _DownloadingDeterministicProvider()
    unknown_vectors = _UnknownOnceVectorIndex(vectors)
    runtime, authority, operations, clock = _real_index_runtime(
        database=indexing_database,
        storage=storage,
        vectors=unknown_vectors,
        spec=spec,
        provider=provider,
    )
    event = _outbox_event(
        indexing_database,
        aggregate_id=request.target_id,
        event_type="asset.index.requested",
        trace_id=request.operation_id,
    )

    WorkerRuntime._handle_asset_index(runtime, event)
    assert (
        operations.get(
            workspace_id=request.workspace_id,
            operation_id=request.operation_id,
        ).state
        is OperationState.RECONCILING
    )
    clock.advance()
    WorkerRuntime._handle_asset_index(runtime, event)

    assert (
        operations.get(
            workspace_id=request.workspace_id,
            operation_id=request.operation_id,
        ).state
        is OperationState.SUCCEEDED
    )
    assert provider.calls == provider.verified_downloads == 1
    identity = _vector_identity(authority, request)
    assert vectors.prove(identity).matches(identity)


def test_real_index_worker_crash_after_upsert_recovers_expired_lease_exactly_once(
    indexing_database,
    real_index_infra,
) -> None:
    s3, bucket, storage, vectors, spec = real_index_infra
    asset_id, _, _ = _seed_real_controlled_image(indexing_database, s3, bucket)
    request = (
        _requests(indexing_database, collection_spec=spec)
        .request_current_image(workspace_id=WORKSPACE, asset_id=asset_id)
        .operation
    )
    provider = _DownloadingDeterministicProvider()
    crashing_vectors = _CrashAfterRealUpsertVectorIndex(vectors)
    runtime, authority, operations, clock = _real_index_runtime(
        database=indexing_database,
        storage=storage,
        vectors=crashing_vectors,
        spec=spec,
        provider=provider,
    )
    event = _outbox_event(
        indexing_database,
        aggregate_id=request.target_id,
        event_type="asset.index.requested",
        trace_id=request.operation_id,
    )

    with pytest.raises(_SimulatedWorkerCrash):
        WorkerRuntime._handle_asset_index(runtime, event)
    succeeded = _recover_expired_index_operation(
        database=indexing_database,
        runtime=runtime,
        operations=operations,
        clock=clock,
        request=request,
    )

    assert succeeded.state is OperationState.SUCCEEDED
    assert provider.calls == provider.verified_downloads == 1
    identity = _vector_identity(authority, request)
    assert identity.write_generation == 1
    assert vectors.prove(identity).matches(identity)
    next_generation = identity.model_copy(
        update={
            "milvus_primary_key": f"{identity.embedding_record_id}:g2",
            "write_generation": 2,
        }
    )
    assert vectors.prove(next_generation).exists is False


def test_real_index_worker_crash_after_mysql_commit_recovers_operation_success(
    indexing_database,
    real_index_infra,
) -> None:
    s3, bucket, storage, vectors, spec = real_index_infra
    asset_id, _, _ = _seed_real_controlled_image(indexing_database, s3, bucket)
    request = (
        _requests(indexing_database, collection_spec=spec)
        .request_current_image(workspace_id=WORKSPACE, asset_id=asset_id)
        .operation
    )
    provider = _DownloadingDeterministicProvider()
    runtime, authority, operations, clock = _real_index_runtime(
        database=indexing_database,
        storage=storage,
        vectors=vectors,
        spec=spec,
        provider=provider,
        executor_wrapper=_CrashAfterIndexCommitExecutor,
    )
    event = _outbox_event(
        indexing_database,
        aggregate_id=request.target_id,
        event_type="asset.index.requested",
        trace_id=request.operation_id,
    )

    with pytest.raises(_SimulatedWorkerCrash):
        WorkerRuntime._handle_asset_index(runtime, event)
    with indexing_database.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT state FROM embedding_records WHERE id = :id"),
                {"id": request.target_id},
            ).scalar_one()
            == EmbeddingState.INDEXED.value
        )
    succeeded = _recover_expired_index_operation(
        database=indexing_database,
        runtime=runtime,
        operations=operations,
        clock=clock,
        request=request,
    )

    assert succeeded.state is OperationState.SUCCEEDED
    assert provider.calls == provider.verified_downloads == 1
    identity = _vector_identity(authority, request)
    assert identity.write_generation == 1
    assert vectors.prove(identity).matches(identity)
    next_generation = identity.model_copy(
        update={
            "milvus_primary_key": f"{identity.embedding_record_id}:g2",
            "write_generation": 2,
        }
    )
    assert vectors.prove(next_generation).exists is False


def test_real_index_operator_dlq_replay_is_the_only_permanent_failure_revival(
    indexing_database,
    real_index_infra,
) -> None:
    s3, bucket, storage, vectors, spec = real_index_infra
    asset_id, _, _ = _seed_real_controlled_image(indexing_database, s3, bucket)
    request = (
        _requests(indexing_database, collection_spec=spec, max_attempts=1)
        .request_current_image(
            workspace_id=WORKSPACE,
            asset_id=asset_id,
        )
        .operation
    )
    failed_runtime, authority, operations, _ = _real_index_runtime(
        database=indexing_database,
        storage=storage,
        vectors=vectors,
        spec=spec,
        provider=_RetryableProviderFailure(),
    )
    event = _outbox_event(
        indexing_database,
        aggregate_id=request.target_id,
        event_type="asset.index.requested",
        trace_id=request.operation_id,
    )

    WorkerRuntime._handle_asset_index(failed_runtime, event)
    failed = operations.get(
        workspace_id=request.workspace_id,
        operation_id=request.operation_id,
    )
    assert failed.state is OperationState.FAILED
    assert failed.dead_letter_id is not None
    with indexing_database.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT state FROM embedding_records WHERE id = :id"),
                {"id": request.target_id},
            ).scalar_one()
            == EmbeddingState.PERMANENT_FAILED.value
        )

    # Ordinary duplicate delivery remains terminal and cannot reopen the embedding.
    WorkerRuntime._handle_asset_index(failed_runtime, event)
    assert (
        operations.get(
            workspace_id=request.workspace_id,
            operation_id=request.operation_id,
        ).state
        is OperationState.FAILED
    )

    principal = AuthenticatedPrincipal(
        actor_id="ticket09-index-operator",
        workspace_ids=frozenset({WORKSPACE}),
        admin_workspace_ids=frozenset({WORKSPACE}),
    )
    dead_letters = DeadLetterOperatorService(
        uow_factory=lambda: SqlAlchemyOperatorUnitOfWork(indexing_database.session_factory),
        access_policy=_AllowWorkspaceAdminPolicy(),
    )
    replay = dead_letters.replay(
        workspace_id=WORKSPACE,
        dead_letter_id=failed.dead_letter_id,
        principal=principal,
        reason="operator verified provider recovery",
        idempotency_key="ticket09-image-index-replay-0001",
        trace_id="ticket09-image-index-replay-trace",
    )
    with indexing_database.session_factory() as session:
        replay_event = OutboxRepository(session).get(replay.replay_event_id)
    assert replay_event is not None

    provider = _DownloadingDeterministicProvider()
    success_runtime, _, _, _ = _real_index_runtime(
        database=indexing_database,
        storage=storage,
        vectors=vectors,
        spec=spec,
        provider=provider,
    )
    succeeded = success_runtime.operation_worker.handle_recovery_event(replay_event)

    assert succeeded.state is OperationState.SUCCEEDED
    assert succeeded.replay_source_dead_letter_id == failed.dead_letter_id
    assert succeeded.replay_attempt == replay.replay_attempt
    assert provider.calls == provider.verified_downloads == 1
    identity = _vector_identity(authority, request)
    assert vectors.prove(identity).matches(identity)
    with indexing_database.engine.connect() as connection:
        state, generation = connection.execute(
            text("SELECT state, write_generation FROM embedding_records WHERE id = :id"),
            {"id": request.target_id},
        ).one()
    assert (state, generation) == (EmbeddingState.INDEXED.value, 2)


def test_real_index_operator_replay_recovers_exhausted_reconciliation_same_generation(
    indexing_database,
    real_index_infra,
) -> None:
    s3, bucket, storage, vectors, spec = real_index_infra
    asset_id, _, _ = _seed_real_controlled_image(indexing_database, s3, bucket)
    request = (
        _requests(
            indexing_database,
            collection_spec=spec,
            max_reconciliation_attempts=1,
        )
        .request_current_image(workspace_id=WORKSPACE, asset_id=asset_id)
        .operation
    )
    provider = _DownloadingDeterministicProvider()
    unstable_vectors = _UnknownUpsertThenUnavailableProof(vectors)
    runtime, authority, operations, clock = _real_index_runtime(
        database=indexing_database,
        storage=storage,
        vectors=unstable_vectors,
        spec=spec,
        provider=provider,
    )
    event = _outbox_event(
        indexing_database,
        aggregate_id=request.target_id,
        event_type="asset.index.requested",
        trace_id=request.operation_id,
    )
    WorkerRuntime._handle_asset_index(runtime, event)
    assert (
        operations.get(
            workspace_id=WORKSPACE,
            operation_id=request.operation_id,
        ).state
        is OperationState.RECONCILING
    )
    clock.advance()
    WorkerRuntime._handle_asset_index(runtime, event)
    failed = operations.get(
        workspace_id=WORKSPACE,
        operation_id=request.operation_id,
    )
    assert failed.state is OperationState.FAILED
    assert failed.dead_letter_id is not None
    with indexing_database.engine.connect() as connection:
        assert connection.execute(
            text("SELECT state, write_generation FROM embedding_records WHERE id = :id"),
            {"id": request.target_id},
        ).one() == (EmbeddingState.PERMANENT_FAILED.value, 1)

    principal = AuthenticatedPrincipal(
        actor_id="ticket09-reconciliation-operator",
        workspace_ids=frozenset({WORKSPACE}),
        admin_workspace_ids=frozenset({WORKSPACE}),
    )
    dead_letters = DeadLetterOperatorService(
        uow_factory=lambda: SqlAlchemyOperatorUnitOfWork(indexing_database.session_factory),
        access_policy=_AllowWorkspaceAdminPolicy(),
    )
    replay = dead_letters.replay(
        workspace_id=WORKSPACE,
        dead_letter_id=failed.dead_letter_id,
        principal=principal,
        reason="operator verified exact vector after reconciliation outage",
        idempotency_key="ticket09-reconciliation-replay-0001",
        trace_id="ticket09-reconciliation-replay-trace",
    )
    with indexing_database.session_factory() as session:
        replay_event = OutboxRepository(session).get(replay.replay_event_id)
    assert replay_event is not None

    succeeded = runtime.operation_worker.handle_recovery_event(replay_event)

    assert succeeded.state is OperationState.SUCCEEDED
    assert succeeded.replay_source_dead_letter_id == failed.dead_letter_id
    assert provider.calls == provider.verified_downloads == 1
    identity = _vector_identity(authority, request)
    assert identity.write_generation == 1
    assert vectors.prove(identity).matches(identity)
    with indexing_database.engine.connect() as connection:
        assert connection.execute(
            text("SELECT state, write_generation FROM embedding_records WHERE id = :id"),
            {"id": request.target_id},
        ).one() == (EmbeddingState.INDEXED.value, 1)


def test_real_index_extra_rights_race_after_provider_deletes_stale_vector(
    indexing_database,
    real_index_infra,
) -> None:
    s3, bucket, storage, vectors, spec = real_index_infra
    asset_id, _, _ = _seed_real_controlled_image(indexing_database, s3, bucket)
    request = (
        _requests(indexing_database, collection_spec=spec)
        .request_current_image(
            workspace_id=WORKSPACE,
            asset_id=asset_id,
        )
        .operation
    )
    provider = _DownloadingDeterministicProvider(
        after_download=lambda: _block_asset(indexing_database, asset_id)
    )
    runtime, authority, operations, _ = _real_index_runtime(
        database=indexing_database,
        storage=storage,
        vectors=vectors,
        spec=spec,
        provider=provider,
    )
    event = _outbox_event(
        indexing_database,
        aggregate_id=request.target_id,
        event_type="asset.index.requested",
        trace_id=request.operation_id,
    )

    WorkerRuntime._handle_asset_index(runtime, event)

    assert (
        operations.get(
            workspace_id=request.workspace_id,
            operation_id=request.operation_id,
        ).state
        is OperationState.SUCCEEDED
    )
    identity = _vector_identity(authority, request)
    assert vectors.prove(identity).matches(identity)
    delete_event = _outbox_event(
        indexing_database,
        aggregate_id=request.target_id,
        event_type="asset.index.delete-requested",
        trace_id=request.operation_id,
    )
    WorkerRuntime._handle_asset_index_delete(runtime, delete_event)
    assert vectors.prove(identity).exists is False
    with indexing_database.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT state FROM embedding_records WHERE id = :id"),
                {"id": request.target_id},
            ).scalar_one()
            == EmbeddingState.DELETED.value
        )


def test_real_index_extra_revocation_after_index_deletes_exact_generation(
    indexing_database,
    real_index_infra,
) -> None:
    s3, bucket, storage, vectors, spec = real_index_infra
    asset_id, version_id, _ = _seed_real_controlled_image(
        indexing_database,
        s3,
        bucket,
    )
    request = (
        _requests(indexing_database, collection_spec=spec)
        .request_current_image(
            workspace_id=WORKSPACE,
            asset_id=asset_id,
        )
        .operation
    )
    provider = _DownloadingDeterministicProvider()
    runtime, authority, _, _ = _real_index_runtime(
        database=indexing_database,
        storage=storage,
        vectors=vectors,
        spec=spec,
        provider=provider,
    )
    event = _outbox_event(
        indexing_database,
        aggregate_id=request.target_id,
        event_type="asset.index.requested",
        trace_id=request.operation_id,
    )
    WorkerRuntime._handle_asset_index(runtime, event)
    identity = _vector_identity(authority, request)
    assert vectors.prove(identity).matches(identity)

    _block_asset(indexing_database, asset_id)
    assert (
        authority.mark_current_asset_stale(
            workspace_id=WORKSPACE,
            asset_id=asset_id,
            asset_version_id=version_id,
            reason="ASSET_BLOCKED",
        )
        == 1
    )
    delete_event = _outbox_event(
        indexing_database,
        aggregate_id=request.target_id,
        event_type="asset.index.delete-requested",
        trace_id=request.operation_id,
    )
    WorkerRuntime._handle_asset_index_delete(runtime, delete_event)

    assert vectors.prove(identity).exists is False


def test_real_index_matrix_07_rights_change_regrant_fences_late_old_delete(
    indexing_database,
    real_index_infra,
) -> None:
    s3, bucket, storage, vectors, spec = real_index_infra
    asset_id, version_id, _ = _seed_real_controlled_image(
        indexing_database,
        s3,
        bucket,
    )
    service = _requests(indexing_database, collection_spec=spec)
    first = service.request_current_image(
        workspace_id=WORKSPACE,
        asset_id=asset_id,
    ).operation
    provider = _DownloadingDeterministicProvider()
    runtime, authority, operations, _ = _real_index_runtime(
        database=indexing_database,
        storage=storage,
        vectors=vectors,
        spec=spec,
        provider=provider,
    )
    first_event = _outbox_event(
        indexing_database,
        aggregate_id=first.target_id,
        event_type="asset.index.requested",
        trace_id=first.operation_id,
    )
    WorkerRuntime._handle_asset_index(runtime, first_event)
    first_identity = _vector_identity(authority, first)
    assert vectors.prove(first_identity).matches(first_identity)
    _grant_replacement_rights(
        indexing_database,
        asset_id=asset_id,
        asset_version_id=version_id,
    )
    second = service.request_current_image(
        workspace_id=WORKSPACE,
        asset_id=asset_id,
    ).operation
    old_delete = _outbox_event(
        indexing_database,
        aggregate_id=first.target_id,
        event_type="asset.index.delete-requested",
        trace_id=first.operation_id,
    )
    second_event = _outbox_event(
        indexing_database,
        aggregate_id=second.target_id,
        event_type="asset.index.requested",
        trace_id=second.operation_id,
    )

    WorkerRuntime._handle_asset_index(runtime, second_event)
    second_identity = _vector_identity(authority, second)
    assert (
        operations.get(
            workspace_id=second.workspace_id,
            operation_id=second.operation_id,
        ).state
        is OperationState.SUCCEEDED
    )
    assert provider.calls == provider.verified_downloads == 2
    assert vectors.prove(first_identity).matches(first_identity)
    assert vectors.prove(second_identity).matches(second_identity)

    WorkerRuntime._handle_asset_index_delete(runtime, old_delete)

    assert vectors.prove(first_identity).exists is False
    assert vectors.prove(second_identity).matches(second_identity)
    with indexing_database.engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT state, write_generation, operation_id FROM embedding_records WHERE id = :id"
            ),
            {"id": second.target_id},
        ).one() == (
            EmbeddingState.INDEXED.value,
            2,
            second.operation_id,
        )


def test_real_index_regrant_cleans_processing_generation_after_worker_crash(
    indexing_database,
    real_index_infra,
) -> None:
    s3, bucket, storage, vectors, spec = real_index_infra
    asset_id, version_id, _ = _seed_real_controlled_image(
        indexing_database,
        s3,
        bucket,
    )
    service = _requests(indexing_database, collection_spec=spec)
    first = service.request_current_image(
        workspace_id=WORKSPACE,
        asset_id=asset_id,
    ).operation
    provider = _DownloadingDeterministicProvider()
    crashing_vectors = _CrashAfterRealUpsertVectorIndex(vectors)
    runtime, authority, operations, _ = _real_index_runtime(
        database=indexing_database,
        storage=storage,
        vectors=crashing_vectors,
        spec=spec,
        provider=provider,
    )
    first_event = _outbox_event(
        indexing_database,
        aggregate_id=first.target_id,
        event_type="asset.index.requested",
        trace_id=first.operation_id,
    )
    with pytest.raises(_SimulatedWorkerCrash):
        WorkerRuntime._handle_asset_index(runtime, first_event)
    assert (
        operations.get(
            workspace_id=WORKSPACE,
            operation_id=first.operation_id,
        ).state
        is OperationState.RUNNING
    )
    first_identity = _vector_identity(authority, first)
    assert vectors.prove(first_identity).matches(first_identity)

    _grant_replacement_rights(
        indexing_database,
        asset_id=asset_id,
        asset_version_id=version_id,
    )
    second = service.request_current_image(
        workspace_id=WORKSPACE,
        asset_id=asset_id,
    ).operation
    old_delete = _outbox_event(
        indexing_database,
        aggregate_id=first.target_id,
        event_type="asset.index.delete-requested",
        trace_id=first.operation_id,
    )
    second_event = _outbox_event(
        indexing_database,
        aggregate_id=second.target_id,
        event_type="asset.index.requested",
        trace_id=second.operation_id,
    )

    WorkerRuntime._handle_asset_index(runtime, second_event)
    second_identity = _vector_identity(authority, second)
    assert vectors.prove(second_identity).matches(second_identity)
    WorkerRuntime._handle_asset_index_delete(runtime, old_delete)

    assert vectors.prove(first_identity).exists is False
    assert vectors.prove(second_identity).matches(second_identity)
    with indexing_database.engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT state, write_generation, operation_id FROM embedding_records WHERE id = :id"
            ),
            {"id": second.target_id},
        ).one() == (
            EmbeddingState.INDEXED.value,
            2,
            second.operation_id,
        )


def test_real_index_late_superseded_upsert_reemits_exact_cleanup(
    indexing_database,
    real_index_infra,
) -> None:
    s3, bucket, storage, vectors, spec = real_index_infra
    asset_id, version_id, _ = _seed_real_controlled_image(
        indexing_database,
        s3,
        bucket,
    )
    service = _requests(indexing_database, collection_spec=spec)
    first = service.request_current_image(
        workspace_id=WORKSPACE,
        asset_id=asset_id,
    ).operation
    provider = _DownloadingDeterministicProvider()
    blocked_vectors = _BlockedBeforeRealUpsertVectorIndex(vectors)
    runtime, authority, operations, _ = _real_index_runtime(
        database=indexing_database,
        storage=storage,
        vectors=blocked_vectors,
        spec=spec,
        provider=provider,
    )
    first_event = _outbox_event(
        indexing_database,
        aggregate_id=first.target_id,
        event_type="asset.index.requested",
        trace_id=first.operation_id,
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(WorkerRuntime._handle_asset_index, runtime, first_event)
        try:
            assert blocked_vectors.entered.wait(timeout=10)
            first_identity = _vector_identity(authority, first)
            _grant_replacement_rights(
                indexing_database,
                asset_id=asset_id,
                asset_version_id=version_id,
            )
            second = service.request_current_image(
                workspace_id=WORKSPACE,
                asset_id=asset_id,
            ).operation
            early_delete = _outbox_event(
                indexing_database,
                aggregate_id=first.target_id,
                event_type="asset.index.delete-requested",
                trace_id=first.operation_id,
            )

            WorkerRuntime._handle_asset_index_delete(runtime, early_delete)
            assert vectors.prove(first_identity).exists is False
        finally:
            blocked_vectors.release.set()
        future.result(timeout=20)

    assert vectors.prove(first_identity).matches(first_identity)
    late_delete = _outbox_event(
        indexing_database,
        aggregate_id=first.target_id,
        event_type="asset.index.delete-requested",
        trace_id=first.operation_id,
    )
    assert late_delete.envelope.event_id != early_delete.envelope.event_id
    late_payload = AssetIndexDeleteRequestedPayload.model_validate(late_delete.envelope.payload)
    assert late_payload.write_generation == 1
    assert late_payload.reason == "SUPERSEDED"

    second_event = _outbox_event(
        indexing_database,
        aggregate_id=second.target_id,
        event_type="asset.index.requested",
        trace_id=second.operation_id,
    )
    WorkerRuntime._handle_asset_index(runtime, second_event)
    second_identity = _vector_identity(authority, second)
    assert vectors.prove(second_identity).matches(second_identity)

    WorkerRuntime._handle_asset_index_delete(runtime, late_delete)

    assert vectors.prove(first_identity).exists is False
    assert vectors.prove(second_identity).matches(second_identity)
    assert (
        operations.get(
            workspace_id=WORKSPACE,
            operation_id=first.operation_id,
        ).state
        is OperationState.SUCCEEDED
    )
    with indexing_database.engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT state, write_generation, operation_id FROM embedding_records WHERE id = :id"
            ),
            {"id": second.target_id},
        ).one() == (
            EmbeddingState.INDEXED.value,
            2,
            second.operation_id,
        )


def test_real_index_lost_superseded_commit_response_recovers_from_mysql_marker(
    indexing_database,
    real_index_infra,
) -> None:
    s3, bucket, storage, vectors, spec = real_index_infra
    asset_id, version_id, _ = _seed_real_controlled_image(
        indexing_database,
        s3,
        bucket,
    )
    service = _requests(indexing_database, collection_spec=spec)
    first = service.request_current_image(
        workspace_id=WORKSPACE,
        asset_id=asset_id,
    ).operation
    blocked_vectors = _BlockedBeforeRealUpsertVectorIndex(vectors)
    runtime, authority, operations, clock = _real_index_runtime(
        database=indexing_database,
        storage=storage,
        vectors=blocked_vectors,
        spec=spec,
        provider=_DownloadingDeterministicProvider(),
        authority_factory=_LostSupersededCommitResponseAuthority,
    )
    first_event = _outbox_event(
        indexing_database,
        aggregate_id=first.target_id,
        event_type="asset.index.requested",
        trace_id=first.operation_id,
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(WorkerRuntime._handle_asset_index, runtime, first_event)
        try:
            assert blocked_vectors.entered.wait(timeout=10)
            first_identity = _vector_identity(authority, first)
            _grant_replacement_rights(
                indexing_database,
                asset_id=asset_id,
                asset_version_id=version_id,
            )
            second = service.request_current_image(
                workspace_id=WORKSPACE,
                asset_id=asset_id,
            ).operation
            early_delete = _outbox_event(
                indexing_database,
                aggregate_id=first.target_id,
                event_type="asset.index.delete-requested",
                trace_id=first.operation_id,
            )
            WorkerRuntime._handle_asset_index_delete(runtime, early_delete)
        finally:
            blocked_vectors.release.set()
        future.result(timeout=20)

    assert (
        operations.get(
            workspace_id=WORKSPACE,
            operation_id=first.operation_id,
        ).state
        is OperationState.RECONCILING
    )
    assert vectors.prove(first_identity).matches(first_identity)
    late_delete = _outbox_event(
        indexing_database,
        aggregate_id=first.target_id,
        event_type="asset.index.delete-requested",
        trace_id=first.operation_id,
    )
    assert late_delete.envelope.event_id != early_delete.envelope.event_id
    _outbox_event(
        indexing_database,
        aggregate_id=first.target_id,
        event_type="asset.index.completed",
        trace_id=first.operation_id,
    )

    clock.advance()
    WorkerRuntime._handle_asset_index(runtime, first_event)

    assert (
        operations.get(
            workspace_id=WORKSPACE,
            operation_id=first.operation_id,
        ).state
        is OperationState.SUCCEEDED
    )
    second_event = _outbox_event(
        indexing_database,
        aggregate_id=second.target_id,
        event_type="asset.index.requested",
        trace_id=second.operation_id,
    )
    WorkerRuntime._handle_asset_index(runtime, second_event)
    second_identity = _vector_identity(authority, second)
    WorkerRuntime._handle_asset_index_delete(runtime, late_delete)

    assert vectors.prove(first_identity).exists is False
    assert vectors.prove(second_identity).matches(second_identity)


def test_duplicate_and_concurrent_requests_reload_one_atomic_winner(indexing_database) -> None:
    asset_id, _, _ = _seed_available_image(indexing_database)
    service = _requests(indexing_database)
    status_queries = SqlAlchemyImageIndexStatusQueries(indexing_database.session_factory)

    assert status_queries.get_current(workspace_id=WORKSPACE, asset_id=asset_id).state == (
        "NOT_REQUESTED"
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: service.request_current_image(
                    workspace_id=WORKSPACE,
                    asset_id=asset_id,
                ),
                range(2),
            )
        )
    replay = service.request_current_image(workspace_id=WORKSPACE, asset_id=asset_id)

    assert len({result.operation.operation_id for result in results + [replay]}) == 1
    assert sum(result.created for result in results + [replay]) == 1
    with indexing_database.engine.connect() as connection:
        counts = connection.execute(
            text(
                "SELECT "
                "(SELECT COUNT(*) FROM collection_registry), "
                "(SELECT COUNT(*) FROM embedding_records), "
                "(SELECT COUNT(*) FROM durable_operations), "
                "(SELECT COUNT(*) FROM outbox_events "
                " WHERE event_type = 'asset.index.requested')"
            )
        ).one()
        request_payload = connection.execute(
            text(
                "SELECT payload_json FROM outbox_events WHERE event_type = 'asset.index.requested'"
            )
        ).scalar_one()
        collection_state = connection.execute(
            text("SELECT state, is_read_enabled, is_write_enabled FROM collection_registry")
        ).one()
    assert tuple(counts) == (1, 1, 1, 1)
    assert tuple(collection_state) == (CollectionState.PLANNED.value, 0, 0)
    if isinstance(request_payload, str):
        request_payload = json.loads(request_payload)
    typed_request = AssetIndexRequestedPayload.model_validate(request_payload)
    winner = results[0].operation
    assert typed_request.asset_version_number == 7
    assert typed_request.operation_epoch == winner.target_version == 1
    assert typed_request.operation_input_hash == winner.input_hash
    assert typed_request.embedding_input_hash != winner.input_hash
    assert MySqlIndexingAuthority(indexing_database.session_factory).validate_request_event(
        typed_request
    )
    public_status = status_queries.get_current(workspace_id=WORKSPACE, asset_id=asset_id)
    assert public_status.state == EmbeddingState.PENDING.value
    assert {
        "collection_id",
        "collection_name",
        "milvus_primary_key",
        "provider_request_id",
    }.isdisjoint(public_status.model_dump())


def test_non_image_asset_fails_closed_without_creating_index_work(indexing_database) -> None:
    asset_id, _, _ = _seed_available_image(indexing_database, asset_kind="LORA")

    with pytest.raises(ImageIndexNotApplicable, match="not an IMAGE"):
        _requests(indexing_database).request_current_image(
            workspace_id=WORKSPACE,
            asset_id=asset_id,
        )

    with indexing_database.engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM embedding_records")).scalar_one() == 0


def test_status_projection_immediately_rejects_superseded_rights_authority(
    indexing_database,
) -> None:
    asset_id, version_id, _ = _seed_available_image(indexing_database)
    request = (
        _requests(indexing_database)
        .request_current_image(
            workspace_id=WORKSPACE,
            asset_id=asset_id,
        )
        .operation
    )
    authority = MySqlIndexingAuthority(indexing_database.session_factory)
    target = _activate_and_claim(authority, request)
    target = authority.record_provider_result(target, _provider_result())
    assert authority.commit_after_upsert(target).indexed is True

    _grant_replacement_rights(
        indexing_database,
        asset_id=asset_id,
        asset_version_id=version_id,
    )

    status = SqlAlchemyImageIndexStatusQueries(indexing_database.session_factory).get_current(
        workspace_id=WORKSPACE, asset_id=asset_id
    )
    assert status.state == EmbeddingState.STALE.value
    assert status.failure_reason == "RIGHTS_CHANGED"
    assert status.retryable is False


def test_real_mysql_asgi_status_is_bounded_and_non_enumerating(indexing_database) -> None:
    asset_id, _, _ = _seed_available_image(indexing_database)
    status = SqlAlchemyImageIndexStatusQueries(indexing_database.session_factory)

    class PrincipalResolver:
        @staticmethod
        def resolve(_token: str | None) -> AuthenticatedPrincipal:
            return AuthenticatedPrincipal(
                actor_id="index-status-reader",
                workspace_ids=frozenset({WORKSPACE, "workspace-other"}),
                admin_workspace_ids=frozenset(),
            )

    class AccessPolicy:
        @staticmethod
        def require_workspace(
            *,
            workspace_id: str,
            principal: AuthenticatedPrincipal,
        ) -> None:
            assert workspace_id in principal.workspace_ids

    app = FastAPI()
    app.state.container = SimpleNamespace(
        principal_resolver=PrincipalResolver(),
        access_policy=AccessPolicy(),
        image_index_status=status,
    )
    install_error_handlers(app)

    @app.middleware("http")
    async def request_context(request, call_next):
        request.state.request_id = "request-index-status"
        request.state.trace_id = "trace-index-status"
        return await call_next(request)

    app.include_router(asset_router)
    unknown_id = new_uuid7()
    with TestClient(app) as client:
        current = client.get(
            f"/api/v1/assets/{asset_id}/index-status",
            headers={"X-Workspace-Id": WORKSPACE},
        )
        unknown = client.get(
            f"/api/v1/assets/{unknown_id}/index-status",
            headers={"X-Workspace-Id": WORKSPACE},
        )
        cross_workspace = client.get(
            f"/api/v1/assets/{asset_id}/index-status",
            headers={"X-Workspace-Id": "workspace-other"},
        )

    assert current.status_code == 200
    assert set(current.json()) == {
        "asset_id",
        "asset_version_id",
        "state",
        "retryable",
        "failure_reason",
        "indexed_at",
        "updated_at",
    }
    assert {
        "collection_id",
        "collection_name",
        "milvus_primary_key",
        "provider_request_id",
    }.isdisjoint(current.json())
    assert unknown.status_code == cross_workspace.status_code == 404
    assert unknown.json() == cross_workspace.json()


class _Unused:
    @staticmethod
    def ensure_collection(_request) -> None:
        pass

    def __getattr__(self, name: str):
        raise AssertionError(f"{name} must not be used during reconciliation")


class _ExactProofVectors:
    def prove(self, identity):
        return MilvusVectorProofV1(
            exists=True,
            milvus_primary_key=identity.milvus_primary_key,
            input_hash=identity.input_hash,
            embedding_spec_sha256=identity.embedding_spec_sha256,
            write_generation=identity.write_generation,
        )


class _WritableVectors:
    @staticmethod
    def ensure_collection(_request) -> None:
        pass

    @staticmethod
    def upsert(_request) -> None:
        pass


class _ExactReference:
    @staticmethod
    def temporary_input(target):
        return EmbeddingImageInputV1(
            asset_version_id=target.asset_version_id,
            content_sha256=target.content_sha256,
            byte_size=128,
            url=SecretStr("https://controlled.invalid/exact"),
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
        )


class _RetryableProviderFailure:
    @staticmethod
    def embed(_request):
        raise EmbeddingProviderFailure(
            EmbeddingProviderErrorV1(
                code="EMBEDDING_TIMEOUT",
                category="TIMEOUT",
                safe_message="Embedding provider timed out before submission",
                retryable=True,
                outcome_unknown=False,
            )
        )


class _SuccessfulProvider:
    @staticmethod
    def embed(_request):
        return _provider_result()


class _LostFirstClaimResponseAuthority(MySqlIndexingAuthority):
    def __init__(self, session_factory) -> None:
        super().__init__(session_factory)
        self.claim_responses_lost = 1

    def claim_for_submission(self, request):
        target = super().claim_for_submission(request)
        if self.claim_responses_lost > 0:
            self.claim_responses_lost -= 1
            raise TimeoutError("simulated lost MySQL claim response")
        return target


class _LostSupersededCommitResponseAuthority(MySqlIndexingAuthority):
    def __init__(self, session_factory) -> None:
        super().__init__(session_factory)
        self.commit_responses_lost = 1

    def commit_after_upsert(self, target):
        decision = super().commit_after_upsert(target)
        if decision.stale_reason == "SUPERSEDED" and self.commit_responses_lost > 0:
            self.commit_responses_lost -= 1
            raise TimeoutError("simulated lost superseded MySQL commit response")
        return decision


class _UnavailableProvisioningVectors:
    @staticmethod
    def ensure_collection(_request) -> None:
        raise ConnectionError("Milvus is unavailable")


def test_crash_after_upsert_reconcile_finalizes_real_mysql_generation(indexing_database) -> None:
    asset_id, _, _ = _seed_available_image(indexing_database)
    request = (
        _requests(indexing_database)
        .request_current_image(
            workspace_id=WORKSPACE,
            asset_id=asset_id,
        )
        .operation
    )
    authority = MySqlIndexingAuthority(indexing_database.session_factory)
    target = _activate_and_claim(authority, request)
    assert request.target_version == 1
    assert target.asset_version_number == 7
    with indexing_database.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT version FROM embedding_records WHERE id = :id"),
                {"id": request.target_id},
            ).scalar_one()
            > 1
        )
    target = authority.record_provider_result(target, _provider_result())
    executor = ImageIndexingExecutor(
        authority=authority,
        references=_Unused(),
        embedding=_Unused(),
        vectors=_ExactProofVectors(),
    )

    result = executor.reconcile(request)

    assert result.output_ref == f"mysql://embedding-records/{request.target_id}"
    with indexing_database.engine.connect() as connection:
        facts = connection.execute(
            text(
                "SELECT state, write_generation, provider_request_id, actual_model "
                "FROM embedding_records WHERE id = :id"
            ),
            {"id": request.target_id},
        ).one()
    assert tuple(facts) == (
        EmbeddingState.INDEXED.value,
        1,
        "provider-request-1",
        "qwen3-vl-embedding-2026-06-30",
    )


def test_retryable_provider_failure_returns_real_mysql_to_retryable_generation(
    indexing_database,
) -> None:
    asset_id, _, _ = _seed_available_image(indexing_database)
    request = (
        _requests(indexing_database)
        .request_current_image(
            workspace_id=WORKSPACE,
            asset_id=asset_id,
        )
        .operation
    )
    authority = MySqlIndexingAuthority(indexing_database.session_factory)
    executor = ImageIndexingExecutor(
        authority=authority,
        references=_ExactReference(),
        embedding=_RetryableProviderFailure(),
        vectors=_Unused(),
    )

    with pytest.raises(OperationExecutionFailure, match="Embedding provider timed out"):
        executor.execute(request)

    with indexing_database.engine.connect() as connection:
        state, generation, collection_state, read_enabled, write_enabled = connection.execute(
            text(
                "SELECT e.state, e.write_generation, c.state, "
                "c.is_read_enabled, c.is_write_enabled "
                "FROM embedding_records e "
                "JOIN collection_registry c ON c.id = e.collection_id "
                "WHERE e.id = :id"
            ),
            {"id": request.target_id},
        ).one()
    assert (state, generation) == (EmbeddingState.RETRYABLE_FAILED.value, 1)
    assert (collection_state, read_enabled, write_enabled) == (
        CollectionState.ACTIVE.value,
        1,
        1,
    )

    retried = _activate_and_claim(authority, request)
    assert retried.write_generation == 2


def test_provisioning_exhaustion_never_advertises_collection_active(
    indexing_database,
) -> None:
    asset_id, _, _ = _seed_available_image(indexing_database)
    request = (
        _requests(indexing_database, max_attempts=1)
        .request_current_image(
            workspace_id=WORKSPACE,
            asset_id=asset_id,
        )
        .operation
    )
    authority = MySqlIndexingAuthority(indexing_database.session_factory)
    executor = ImageIndexingExecutor(
        authority=authority,
        references=_Unused(),
        embedding=_Unused(),
        vectors=_UnavailableProvisioningVectors(),
    )
    registry = OperationExecutorRegistry()
    registry.register(kind=OperationKind.ASSET_INDEXING, executor=executor)
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(indexing_database.session_factory)
    )
    worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=registry,
            transaction_active=is_unit_of_work_active,
        ),
        owner="ticket09-provisioning-worker",
        lease_duration=timedelta(seconds=30),
        retry_policy=OperationRetryPolicy(
            initial_delay=timedelta(seconds=1),
            maximum_delay=timedelta(seconds=1),
            maximum_elapsed=timedelta(hours=1),
        ),
    )

    failed = worker.execute(
        workspace_id=request.workspace_id,
        operation_id=request.operation_id,
    )

    assert failed.state is OperationState.FAILED
    with indexing_database.engine.connect() as connection:
        facts = connection.execute(
            text(
                "SELECT e.state, e.write_generation, c.state, "
                "c.is_read_enabled, c.is_write_enabled "
                "FROM embedding_records e "
                "JOIN collection_registry c ON c.id = e.collection_id "
                "WHERE e.id = :id"
            ),
            {"id": request.target_id},
        ).one()
    assert tuple(facts) == (
        EmbeddingState.PERMANENT_FAILED.value,
        0,
        CollectionState.PLANNED.value,
        0,
        0,
    )


def test_durable_worker_attempt_exhaustion_converges_retryable_embedding_terminal(
    indexing_database,
) -> None:
    asset_id, _, _ = _seed_available_image(indexing_database)
    request = (
        _requests(indexing_database, max_attempts=1)
        .request_current_image(
            workspace_id=WORKSPACE,
            asset_id=asset_id,
        )
        .operation
    )
    authority = MySqlIndexingAuthority(indexing_database.session_factory)
    executor = ImageIndexingExecutor(
        authority=authority,
        references=_ExactReference(),
        embedding=_RetryableProviderFailure(),
        vectors=_Unused(),
    )
    registry = OperationExecutorRegistry()
    registry.register(kind=OperationKind.ASSET_INDEXING, executor=executor)
    service = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(indexing_database.session_factory)
    )
    worker = DurableOperationWorker(
        operations=service,
        execution=OperationExecutionBoundary(
            executor=registry,
            transaction_active=is_unit_of_work_active,
        ),
        owner="ticket09-terminal-worker",
        lease_duration=timedelta(seconds=30),
        retry_policy=OperationRetryPolicy(
            initial_delay=timedelta(seconds=1),
            maximum_delay=timedelta(seconds=1),
            maximum_elapsed=timedelta(hours=1),
        ),
    )

    failed = worker.execute(
        workspace_id=request.workspace_id,
        operation_id=request.operation_id,
    )

    assert failed.state is OperationState.FAILED
    assert failed.dead_letter_id is not None
    with indexing_database.engine.connect() as connection:
        state = connection.execute(
            text("SELECT state FROM embedding_records WHERE id = :id"),
            {"id": request.target_id},
        ).scalar_one()
    assert state == EmbeddingState.PERMANENT_FAILED.value
    assert authority.mark_terminal_failure(request) is True


def test_lost_claim_response_reuses_same_processing_generation_on_retry(
    indexing_database,
) -> None:
    asset_id, _, _ = _seed_available_image(indexing_database)
    request = (
        _requests(indexing_database)
        .request_current_image(workspace_id=WORKSPACE, asset_id=asset_id)
        .operation
    )
    authority = _LostFirstClaimResponseAuthority(indexing_database.session_factory)
    executor = ImageIndexingExecutor(
        authority=authority,
        references=_ExactReference(),
        embedding=_SuccessfulProvider(),
        vectors=_WritableVectors(),
    )
    registry = OperationExecutorRegistry()
    registry.register(kind=OperationKind.ASSET_INDEXING, executor=executor)
    operations = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(indexing_database.session_factory)
    )
    clock = _IndexClock()
    worker = DurableOperationWorker(
        operations=operations,
        execution=OperationExecutionBoundary(
            executor=registry,
            transaction_active=is_unit_of_work_active,
        ),
        owner="ticket09-lost-claim-response-worker",
        lease_duration=timedelta(seconds=30),
        retry_policy=OperationRetryPolicy(
            initial_delay=timedelta(seconds=1),
            maximum_delay=timedelta(seconds=1),
            maximum_elapsed=timedelta(hours=1),
        ),
        clock=clock,
    )

    first = worker.execute(workspace_id=WORKSPACE, operation_id=request.operation_id)
    assert first.state is OperationState.RETRYABLE_FAILED
    with indexing_database.engine.connect() as connection:
        assert connection.execute(
            text("SELECT state, write_generation FROM embedding_records WHERE id = :id"),
            {"id": request.target_id},
        ).one() == (EmbeddingState.PROCESSING.value, 1)

    clock.advance()
    succeeded = worker.execute(workspace_id=WORKSPACE, operation_id=request.operation_id)

    assert succeeded.state is OperationState.SUCCEEDED
    with indexing_database.engine.connect() as connection:
        assert connection.execute(
            text("SELECT state, write_generation FROM embedding_records WHERE id = :id"),
            {"id": request.target_id},
        ).one() == (EmbeddingState.INDEXED.value, 1)


def test_final_rights_change_enters_delete_pending_and_typed_outbox(indexing_database) -> None:
    asset_id, _, _ = _seed_available_image(indexing_database)
    request = (
        _requests(indexing_database)
        .request_current_image(
            workspace_id=WORKSPACE,
            asset_id=asset_id,
        )
        .operation
    )
    authority = MySqlIndexingAuthority(indexing_database.session_factory)
    target = _activate_and_claim(authority, request)
    target = authority.record_provider_result(target, _provider_result())
    with indexing_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE assets SET status = 'BLOCKED', block_reason = 'ADMINISTRATIVELY_BLOCKED', "
                "version = version + 1 WHERE workspace_id = :workspace AND id = :asset"
            ),
            {"workspace": WORKSPACE, "asset": asset_id},
        )

    decision = authority.commit_after_upsert(target)

    assert decision.indexed is False
    assert authority.commit_after_upsert(target) == decision
    with indexing_database.engine.connect() as connection:
        embedding = connection.execute(
            text("SELECT state, stale_reason FROM embedding_records WHERE id = :id"),
            {"id": request.target_id},
        ).one()
        deletion = connection.execute(
            text(
                "SELECT payload_json FROM outbox_events "
                "WHERE event_type = 'asset.index.delete-requested'"
            )
        ).scalar_one()
        completion_count = connection.execute(
            text("SELECT COUNT(*) FROM outbox_events WHERE event_type = 'asset.index.completed'")
        ).scalar_one()
    if isinstance(deletion, str):
        deletion = json.loads(deletion)
    assert embedding[0] == EmbeddingState.DELETE_PENDING.value
    assert completion_count == 1
    assert deletion["embedding_record_id"] == request.target_id
    assert deletion["write_generation"] == 1

    delete_payload = AssetIndexDeleteRequestedPayload.model_validate(deletion)
    identity = authority.load_delete_target(delete_payload)
    assert identity.milvus_primary_key == f"{request.target_id}:g1"
    assert authority.complete_delete(delete_payload) is True
    assert authority.complete_delete(delete_payload) is False
    assert authority.commit_after_upsert(target) == decision
    with indexing_database.engine.connect() as connection:
        state = connection.execute(
            text("SELECT state FROM embedding_records WHERE id = :id"),
            {"id": request.target_id},
        ).scalar_one()
        completion_count = connection.execute(
            text("SELECT COUNT(*) FROM outbox_events WHERE event_type = 'asset.index.completed'")
        ).scalar_one()
    assert state == EmbeddingState.DELETED.value
    assert completion_count == 1


def test_indexed_completion_is_idempotent_after_authority_commit(
    indexing_database,
) -> None:
    asset_id, _, _ = _seed_available_image(indexing_database)
    request = (
        _requests(indexing_database)
        .request_current_image(
            workspace_id=WORKSPACE,
            asset_id=asset_id,
        )
        .operation
    )
    authority = MySqlIndexingAuthority(indexing_database.session_factory)
    target = _activate_and_claim(authority, request)
    target = authority.record_provider_result(target, _provider_result())

    first = authority.commit_after_upsert(target)
    second = authority.commit_after_upsert(target)

    assert first == second
    assert first.indexed is True
    assert first.stale_reason is None
    with indexing_database.engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM outbox_events WHERE event_type = 'asset.index.completed'"
                )
            ).scalar_one()
            == 1
        )


def test_revocation_after_index_immediately_hides_and_deletes_exact_generation(
    indexing_database,
) -> None:
    asset_id, version_id, _ = _seed_available_image(indexing_database)
    request = (
        _requests(indexing_database)
        .request_current_image(
            workspace_id=WORKSPACE,
            asset_id=asset_id,
        )
        .operation
    )
    authority = MySqlIndexingAuthority(indexing_database.session_factory)
    target = _activate_and_claim(authority, request)
    target = authority.record_provider_result(target, _provider_result())
    assert authority.commit_after_upsert(target).indexed is True
    with indexing_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE assets SET status = 'BLOCKED', "
                "block_reason = 'ADMINISTRATIVELY_BLOCKED', version = version + 1 "
                "WHERE workspace_id = :workspace AND id = :asset"
            ),
            {"workspace": WORKSPACE, "asset": asset_id},
        )

    assert (
        authority.mark_current_asset_stale(
            workspace_id=WORKSPACE,
            asset_id=asset_id,
            asset_version_id=version_id,
            reason="ASSET_BLOCKED",
        )
        == 1
    )
    assert (
        authority.mark_current_asset_stale(
            workspace_id=WORKSPACE,
            asset_id=asset_id,
            asset_version_id=version_id,
            reason="ASSET_BLOCKED",
        )
        == 0
    )
    with indexing_database.engine.connect() as connection:
        state = connection.execute(
            text("SELECT state FROM embedding_records WHERE id = :id"),
            {"id": request.target_id},
        ).scalar_one()
        deletion = connection.execute(
            text(
                "SELECT payload_json FROM outbox_events "
                "WHERE event_type = 'asset.index.delete-requested'"
            )
        ).scalar_one()
    assert state == EmbeddingState.DELETE_PENDING.value
    if isinstance(deletion, str):
        deletion = json.loads(deletion)
    payload = AssetIndexDeleteRequestedPayload.model_validate(deletion)
    assert authority.complete_delete(payload) is True


def test_rights_reindex_of_indexed_record_enqueues_exact_old_generation_delete(
    indexing_database,
) -> None:
    asset_id, version_id, _ = _seed_available_image(indexing_database)
    service = _requests(indexing_database)
    first = service.request_current_image(
        workspace_id=WORKSPACE,
        asset_id=asset_id,
    ).operation
    authority = MySqlIndexingAuthority(indexing_database.session_factory)
    first_target = _activate_and_claim(authority, first)
    first_target = authority.record_provider_result(first_target, _provider_result())
    assert authority.commit_after_upsert(first_target).indexed is True
    _grant_replacement_rights(
        indexing_database,
        asset_id=asset_id,
        asset_version_id=version_id,
    )

    second_result = service.request_current_image(
        workspace_id=WORKSPACE,
        asset_id=asset_id,
    )

    assert second_result.created is True
    second = second_result.operation
    assert second.target_version == 2
    with indexing_database.engine.connect() as connection:
        payload_json = connection.execute(
            text(
                "SELECT payload_json FROM outbox_events "
                "WHERE event_type = 'asset.index.delete-requested'"
            )
        ).scalar_one()
    if isinstance(payload_json, str):
        payload_json = json.loads(payload_json)
    old_delete = AssetIndexDeleteRequestedPayload.model_validate(payload_json)
    assert old_delete.operation_id == first.operation_id
    assert old_delete.write_generation == 1
    assert old_delete.reason == "SUPERSEDED"

    second_target = _activate_and_claim(authority, second)
    assert second_target.write_generation == 2
    second_target = authority.record_provider_result(second_target, _provider_result())
    assert authority.commit_after_upsert(second_target).indexed is True
    old_identity = authority.load_delete_target(old_delete)
    assert old_identity.milvus_primary_key == f"{first.target_id}:g1"
    assert authority.complete_delete(old_delete) is False
    assert authority.mark_terminal_failure(first) is False
    with indexing_database.engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT state, write_generation, operation_id FROM embedding_records WHERE id = :id"
            ),
            {"id": first.target_id},
        ).one() == (EmbeddingState.INDEXED.value, 2, second.operation_id)


def test_rights_reindex_of_processing_record_enqueues_exact_generation_delete(
    indexing_database,
) -> None:
    asset_id, version_id, _ = _seed_available_image(indexing_database)
    service = _requests(indexing_database)
    first = service.request_current_image(
        workspace_id=WORKSPACE,
        asset_id=asset_id,
    ).operation
    authority = MySqlIndexingAuthority(indexing_database.session_factory)
    first_target = _activate_and_claim(authority, first)
    first_target = authority.record_provider_result(first_target, _provider_result())
    assert first_target.write_generation == 1
    _grant_replacement_rights(
        indexing_database,
        asset_id=asset_id,
        asset_version_id=version_id,
    )

    second = service.request_current_image(
        workspace_id=WORKSPACE,
        asset_id=asset_id,
    ).operation

    delete_event = _outbox_event(
        indexing_database,
        aggregate_id=first.target_id,
        event_type="asset.index.delete-requested",
        trace_id=first.operation_id,
    )
    payload = AssetIndexDeleteRequestedPayload.model_validate(delete_event.envelope.payload)
    assert payload.operation_id == first.operation_id
    assert payload.write_generation == 1
    assert payload.reason == "SUPERSEDED"
    assert second.operation_id != first.operation_id
    assert authority.mark_terminal_failure(first) is False


def test_regrant_reopens_one_operation_and_late_old_delete_cannot_remove_new_generation(
    indexing_database,
) -> None:
    asset_id, version_id, _ = _seed_available_image(indexing_database)
    service = _requests(indexing_database)
    first = service.request_current_image(
        workspace_id=WORKSPACE,
        asset_id=asset_id,
    ).operation
    authority = MySqlIndexingAuthority(indexing_database.session_factory)
    first_target = _activate_and_claim(authority, first)
    first_target = authority.record_provider_result(first_target, _provider_result())
    assert authority.commit_after_upsert(first_target).indexed is True
    with indexing_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE assets SET status = 'BLOCKED', "
                "block_reason = 'ADMINISTRATIVELY_BLOCKED', version = version + 1 "
                "WHERE workspace_id = :workspace AND id = :asset"
            ),
            {"workspace": WORKSPACE, "asset": asset_id},
        )
    assert (
        authority.mark_current_asset_stale(
            workspace_id=WORKSPACE,
            asset_id=asset_id,
            asset_version_id=version_id,
            reason="RIGHTS_INVALID",
        )
        == 1
    )
    with indexing_database.engine.connect() as connection:
        old_payload_json = connection.execute(
            text(
                "SELECT payload_json FROM outbox_events "
                "WHERE event_type = 'asset.index.delete-requested'"
            )
        ).scalar_one()
    if isinstance(old_payload_json, str):
        old_payload_json = json.loads(old_payload_json)
    old_payload = AssetIndexDeleteRequestedPayload.model_validate(old_payload_json)
    assert authority.complete_delete(old_payload) is True

    new_rights_id = new_uuid7()
    now = datetime.now(UTC).replace(tzinfo=None)
    with indexing_database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO rights_records "
                "(id, workspace_id, asset_id, asset_version_id, version_number, decision, "
                "owner_reference, source, license_reference, derivative_allowed, "
                "public_demo_allowed, evidence_reference, terms_sha256, valid_from, "
                "valid_until, perpetual, supersedes_record_id, created_by, created_at, "
                "permissions_sealed_at) VALUES "
                "(:rights, :workspace, :asset, :version, 4, 'GRANT', 'owner', 'contract', "
                "'license-2', 0, 0, 'evidence://regrant', :terms, :valid_from, NULL, 1, "
                "NULL, 'test', :now, NULL)"
            ),
            {
                "rights": new_rights_id,
                "workspace": WORKSPACE,
                "asset": asset_id,
                "version": version_id,
                "terms": "d" * 64,
                "valid_from": now - timedelta(minutes=1),
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO rights_record_uses "
                "(workspace_id, asset_id, rights_record_id, allowed_use, created_at) "
                "VALUES (:workspace, :asset, :rights, 'RETRIEVAL', :now)"
            ),
            {"workspace": WORKSPACE, "asset": asset_id, "rights": new_rights_id, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO rights_record_providers "
                "(workspace_id, asset_id, rights_record_id, allowed_provider, created_at) "
                "VALUES (:workspace, :asset, :rights, :provider, :now)"
            ),
            {
                "workspace": WORKSPACE,
                "asset": asset_id,
                "rights": new_rights_id,
                "provider": PROVIDER,
                "now": now,
            },
        )
        connection.execute(
            text("UPDATE rights_records SET permissions_sealed_at = :now WHERE id = :rights"),
            {"rights": new_rights_id, "now": now},
        )
        connection.execute(
            text(
                "UPDATE assets SET status = 'AVAILABLE', block_reason = NULL, "
                "current_rights_record_id = :rights, version = version + 1 "
                "WHERE workspace_id = :workspace AND id = :asset"
            ),
            {"rights": new_rights_id, "workspace": WORKSPACE, "asset": asset_id},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        regrants = list(
            pool.map(
                lambda _: service.request_current_image(
                    workspace_id=WORKSPACE,
                    asset_id=asset_id,
                ),
                range(2),
            )
        )
    assert len({item.operation.operation_id for item in regrants}) == 1
    assert sum(item.created for item in regrants) == 1
    second = regrants[0].operation
    assert second.operation_id != first.operation_id
    second_target = _activate_and_claim(authority, second)
    assert second_target.write_generation == 2
    second_target = authority.record_provider_result(second_target, _provider_result())
    assert authority.commit_after_upsert(second_target).indexed is True

    old_identity = authority.load_delete_target(old_payload)
    assert old_identity.milvus_primary_key == f"{first.target_id}:g1"
    assert authority.complete_delete(old_payload) is False
    with indexing_database.engine.connect() as connection:
        record_count, operation_count, state, generation, current_operation = connection.execute(
            text(
                "SELECT "
                "(SELECT COUNT(*) FROM embedding_records), "
                "(SELECT COUNT(*) FROM durable_operations), "
                "state, write_generation, operation_id "
                "FROM embedding_records WHERE id = :id"
            ),
            {"id": first.target_id},
        ).one()
    assert (record_count, operation_count) == (1, 2)
    assert (state, generation, current_operation) == (
        EmbeddingState.INDEXED.value,
        2,
        second.operation_id,
    )


def test_reindex_does_not_reopen_permanent_failure(indexing_database) -> None:
    asset_id, _, _ = _seed_available_image(indexing_database)
    service = _requests(indexing_database)
    first = service.request_current_image(
        workspace_id=WORKSPACE,
        asset_id=asset_id,
    ).operation
    with indexing_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE embedding_records SET state = 'PERMANENT_FAILED' "
                "WHERE workspace_id = :workspace AND id = :id"
            ),
            {"workspace": WORKSPACE, "id": first.target_id},
        )

    replay_one = service.request_current_image(workspace_id=WORKSPACE, asset_id=asset_id)
    replay_two = service.request_current_image(workspace_id=WORKSPACE, asset_id=asset_id)

    assert replay_one.created is replay_two.created is False
    assert replay_one.operation.operation_id == replay_two.operation.operation_id
    assert replay_one.operation.operation_id == first.operation_id
    with indexing_database.engine.connect() as connection:
        state, operation_count = connection.execute(
            text(
                "SELECT state, (SELECT COUNT(*) FROM durable_operations) "
                "FROM embedding_records WHERE id = :id"
            ),
            {"id": first.target_id},
        ).one()
    assert (state, operation_count) == (EmbeddingState.PERMANENT_FAILED.value, 1)


def test_only_matching_running_operator_replay_can_reopen_permanent_embedding(
    indexing_database,
) -> None:
    asset_id, _, _ = _seed_available_image(indexing_database)
    request = (
        _requests(indexing_database, max_attempts=1)
        .request_current_image(workspace_id=WORKSPACE, asset_id=asset_id)
        .operation
    )
    authority = MySqlIndexingAuthority(indexing_database.session_factory)
    operations = OperationApplicationService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(indexing_database.session_factory)
    )
    failing_executor = ImageIndexingExecutor(
        authority=authority,
        references=_ExactReference(),
        embedding=_RetryableProviderFailure(),
        vectors=_WritableVectors(),
    )
    failing_registry = OperationExecutorRegistry()
    failing_registry.register(kind=OperationKind.ASSET_INDEXING, executor=failing_executor)
    failed = DurableOperationWorker(
        operations=operations,
        execution=OperationExecutionBoundary(
            executor=failing_registry,
            transaction_active=is_unit_of_work_active,
        ),
        owner="ticket09-replay-failure-worker",
        lease_duration=timedelta(seconds=30),
    ).execute(workspace_id=WORKSPACE, operation_id=request.operation_id)
    assert failed.state is OperationState.FAILED
    assert failed.dead_letter_id is not None

    principal = AuthenticatedPrincipal(
        actor_id="ticket09-replay-operator",
        workspace_ids=frozenset({WORKSPACE}),
        admin_workspace_ids=frozenset({WORKSPACE}),
    )
    dead_letters = DeadLetterOperatorService(
        uow_factory=lambda: SqlAlchemyOperatorUnitOfWork(indexing_database.session_factory),
        access_policy=_AllowWorkspaceAdminPolicy(),
    )
    replay = dead_letters.replay(
        workspace_id=WORKSPACE,
        dead_letter_id=failed.dead_letter_id,
        principal=principal,
        reason="operator verified safe IMAGE replay",
        idempotency_key="ticket09-lightweight-replay-0001",
        trace_id="ticket09-lightweight-replay-trace",
    )
    with indexing_database.session_factory() as session:
        replay_event = OutboxRepository(session).get(replay.replay_event_id)
    assert replay_event is not None

    success_executor = ImageIndexingExecutor(
        authority=authority,
        references=_ExactReference(),
        embedding=_SuccessfulProvider(),
        vectors=_WritableVectors(),
    )
    success_registry = OperationExecutorRegistry()
    success_registry.register(kind=OperationKind.ASSET_INDEXING, executor=success_executor)
    succeeded = DurableOperationWorker(
        operations=operations,
        execution=OperationExecutionBoundary(
            executor=success_registry,
            transaction_active=is_unit_of_work_active,
        ),
        owner="ticket09-replay-success-worker",
        lease_duration=timedelta(seconds=30),
    ).handle_recovery_event(replay_event)

    assert succeeded.state is OperationState.SUCCEEDED
    assert succeeded.replay_source_dead_letter_id == failed.dead_letter_id
    assert succeeded.replay_attempt == 1
    with indexing_database.engine.connect() as connection:
        assert connection.execute(
            text("SELECT state, write_generation FROM embedding_records WHERE id = :id"),
            {"id": request.target_id},
        ).one() == (EmbeddingState.INDEXED.value, 2)


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE embedding_records SET state = 'INDEXED' WHERE id = :id",
        "UPDATE collection_registry SET is_write_enabled = 0 WHERE id = "
        "(SELECT collection_id FROM embedding_records WHERE id = :id)",
        "UPDATE embedding_records SET model_id = 'conflicting-model' WHERE id = :id",
    ],
)
def test_claim_fails_closed_for_illegal_state_collection_or_registry_drift(
    indexing_database,
    mutation: str,
) -> None:
    asset_id, _, _ = _seed_available_image(indexing_database)
    request = (
        _requests(indexing_database)
        .request_current_image(
            workspace_id=WORKSPACE,
            asset_id=asset_id,
        )
        .operation
    )
    with indexing_database.engine.begin() as connection:
        connection.execute(text(mutation), {"id": request.target_id})

    with pytest.raises(ValueError):
        MySqlIndexingAuthority(indexing_database.session_factory).claim_for_submission(request)
