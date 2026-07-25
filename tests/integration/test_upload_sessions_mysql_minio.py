from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote, urlparse

import boto3
import commercevision_api.container as api_container
import httpx
import pytest
from botocore.client import Config
from botocore.exceptions import ClientError
from commercevision_api.main import create_app
from commercevision_application import (
    OperationExecutionRequest,
    OperationExecutionResult,
    OperationReconciliationResult,
    OperationRecoveryService,
    UploadSessionMaintenanceService,
)
from commercevision_application.asset_cleanup_dispatch import UploadCleanupPolicy
from commercevision_application.asset_integrity import ImageUploadIntegrityVerifier
from commercevision_application.asset_promotion import UploadPromoter
from commercevision_contracts import Settings
from commercevision_contracts.object_storage import (
    BoundedReadRequest,
    ConditionalCopyRequest,
    ConditionalDeleteRequest,
    ObjectReference,
    ObjectStat,
    ObjectStorage,
    PresignedRequest,
    PresignPutRequest,
    TemporaryReadRequest,
)
from commercevision_domain import (
    OperationKind,
    ReconciliationOutcome,
    StorageLocationClass,
    StoragePreconditionError,
    StorageUnavailableError,
    UploadObjectMissingError,
    new_uuid7,
)
from commercevision_object_storage import build_object_storage, close_object_storage
from commercevision_persistence import (
    SqlAlchemyAssetUnitOfWork,
    SqlAlchemyOperationUnitOfWork,
    SqlAlchemyUnitOfWork,
    is_unit_of_work_active,
)
from commercevision_worker.readiness import probe_worker_dependencies
from commercevision_worker.runtime import WorkerRuntime
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

VALID_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
VALID_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEBQoH"
    "BwYIDAoMDAsKCwsNDhIQDQ4RDgsLEBYQERMUFRUVDA8XGBYUGBIUFRT/2wBDAQME"
    "BAUEBQkFBQkUDQsNFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFB"
    "QUFBQUFBQUFBQUFBQUFBT/wAARCAADAAIDASIAAhEBAxEB/8QAHwAAAQUBAQEB"
    "AQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQR"
    "BRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3"
    "ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWW"
    "l5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo"
    "6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QA"
    "tREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMz"
    "UvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVm"
    "Z2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6"
    "wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEA"
    "PwDxKiiiv6oP5/P/2Q=="
)
VALID_WEBP = base64.b64decode(
    "UklGRkAAAABXRUJQVlA4IDQAAADQAQCdASoCAAMAAMASJaACdLoB+AADsAD+98Rf"
    "/6FD+hQ/oUP/ntH/3+q974e98P9iwAAA"
)
OVERSIZED_DIMENSION_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAABQEAAAABCAIAAADhIP+cAAAAHUlEQVR4nO3BAQ0A"
    "AAgDoF/7d7aHA9rZAAAAQP47eBIACKoGDtcAAAAASUVORK5CYII="
)
ANIMATED_WEBP = base64.b64decode(
    "UklGRoQAAABXRUJQVlA4WAoAAAACAAAAAQAAAQAAQU5JTQYAAAAAAAAAAABBTk1G"
    "KAAAAAAAAAAAAAEAAAEAAGQAAAJWUDhMDwAAAC8BQAAABxD9j/4HIqL/AQBBTk1G"
    "KAAAAAAAAAAAAAEAAAEAAGQAAABWUDhMDwAAAC8BQAAAB9D/iP4HIqL/AQA="
)
TRUSTED_KEY_ID = "upload-integration"
TRUSTED_SECRET = "upload-integration-secret-0000000000000001"


class SuccessfulAssetValidationExecutor:
    def __init__(self) -> None:
        self.requests: list[OperationExecutionRequest] = []

    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        self.requests.append(request)
        return OperationExecutionResult(
            operation_id=request.operation_id,
            output_ref=f"mysql://asset-validation-results/{request.target_id}",
        )

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        return OperationReconciliationResult(
            operation_id=request.operation_id,
            outcome=ReconciliationOutcome.CONFIRMED_SUCCESS,
            output_ref=f"mysql://asset-validation-results/{request.target_id}",
        )


class SimulatedFinalizeCrash(RuntimeError):
    pass


class CrashOnFirstStatStorage:
    def __init__(self, delegate: ObjectStorage) -> None:
        self._delegate = delegate
        self._crash = True

    @property
    def backend(self):
        return self._delegate.backend

    def stat(self, reference: ObjectReference) -> ObjectStat:
        if self._crash:
            self._crash = False
            raise SimulatedFinalizeCrash("process stopped after claiming finalize")
        return self._delegate.stat(reference)

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)


class CrashAfterFirstCopyStorage:
    def __init__(self, delegate: ObjectStorage) -> None:
        self._delegate = delegate
        self._crash_after_copy = True

    @staticmethod
    def _assert_outside_transaction() -> None:
        assert not is_unit_of_work_active()

    @property
    def backend(self):
        return self._delegate.backend

    def presign_put(self, request: PresignPutRequest) -> PresignedRequest:
        self._assert_outside_transaction()
        return self._delegate.presign_put(request)

    def stat(self, reference: ObjectReference) -> ObjectStat:
        self._assert_outside_transaction()
        return self._delegate.stat(reference)

    def open_bounded_read(self, request: BoundedReadRequest):
        self._assert_outside_transaction()
        return self._delegate.open_bounded_read(request)

    def copy_if_absent(self, request: ConditionalCopyRequest) -> ObjectStat:
        self._assert_outside_transaction()
        result = self._delegate.copy_if_absent(request)
        if self._crash_after_copy:
            self._crash_after_copy = False
            raise SimulatedFinalizeCrash("process stopped after object copy")
        return result

    def delete_if_match(self, request: ConditionalDeleteRequest) -> bool:
        self._assert_outside_transaction()
        return self._delegate.delete_if_match(request)

    def temporary_read(self, request: TemporaryReadRequest) -> PresignedRequest:
        self._assert_outside_transaction()
        return self._delegate.temporary_read(request)


class BoundaryFaultStorage:
    def __init__(
        self,
        delegate: ObjectStorage,
        *,
        fault: str | None = None,
    ) -> None:
        self._delegate = delegate
        self._fault = fault
        self._armed = False
        self._fault_triggered = False
        self._first_io_observer: Callable[[], None] | None = None
        self.observed_finalize_io = False

    @property
    def backend(self):
        return self._delegate.backend

    def arm(
        self,
        *,
        first_io_observer: Callable[[], None] | None = None,
    ) -> None:
        self._armed = True
        self._first_io_observer = first_io_observer

    def _before_finalize_io(self) -> None:
        if not self._armed:
            return
        assert not is_unit_of_work_active()
        if not self.observed_finalize_io:
            if self._first_io_observer is not None:
                self._first_io_observer()
            self.observed_finalize_io = True

    def _should_fault(self, boundary: str) -> bool:
        if self._fault_triggered or self._fault != boundary:
            return False
        self._fault_triggered = True
        return True

    def presign_put(self, request: PresignPutRequest) -> PresignedRequest:
        return self._delegate.presign_put(request)

    def stat(self, reference: ObjectReference) -> ObjectStat:
        self._before_finalize_io()
        if reference.location == StorageLocationClass.QUARANTINE and self._should_fault(
            "source-stat"
        ):
            raise StorageUnavailableError("simulated source HEAD outage")
        return self._delegate.stat(reference)

    def open_bounded_read(self, request: BoundedReadRequest):
        self._before_finalize_io()
        if self._should_fault("bounded-read"):
            raise StorageUnavailableError("simulated bounded-read outage")
        return self._delegate.open_bounded_read(request)

    def copy_if_absent(self, request: ConditionalCopyRequest) -> ObjectStat:
        self._before_finalize_io()
        return self._delegate.copy_if_absent(request)

    def delete_if_match(self, request: ConditionalDeleteRequest) -> bool:
        self._before_finalize_io()
        return self._delegate.delete_if_match(request)

    def temporary_read(self, request: TemporaryReadRequest) -> PresignedRequest:
        self._before_finalize_io()
        return self._delegate.temporary_read(request)


@pytest.fixture
def upload_settings(integration_settings: Settings) -> Settings:
    return Settings(
        environment="ci",
        service_name="upload-integration",
        mysql_dsn=integration_settings.mysql_dsn,
        object_store_backend="minio",
        object_store_endpoint="http://127.0.0.1:19000",
        object_store_presign_endpoint="http://127.0.0.1:19000",
        object_store_access_key="commercevision",
        object_store_secret_key="commercevision-secret",
        object_store_region="us-east-1",
        object_store_force_path_style=True,
        trusted_principal_current_key_id=TRUSTED_KEY_ID,
        trusted_principal_current_hmac_secret=TRUSTED_SECRET,
    )


@pytest.fixture
def minio_client(upload_settings: Settings) -> Iterator[object]:
    client = boto3.client(
        "s3",
        endpoint_url=upload_settings.object_store_endpoint,
        aws_access_key_id=upload_settings.object_store_access_key,
        aws_secret_access_key=upload_settings.object_store_secret_key.get_secret_value(),
        region_name=upload_settings.object_store_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    try:
        for bucket in upload_settings.object_store_buckets.values():
            with suppress(client.exceptions.BucketAlreadyOwnedByYou):
                client.create_bucket(Bucket=bucket)
            client.put_bucket_versioning(
                Bucket=bucket,
                VersioningConfiguration={"Status": "Enabled"},
            )
    except Exception as exc:
        pytest.skip(f"MinIO integration service unavailable: {exc}")
    yield client


def test_real_worker_preflight_queries_mysql_and_authenticated_bucket_controls(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del integration_database, minio_client

    assert probe_worker_dependencies(upload_settings) == {
        "mysql": "ok",
        "object_storage": "ok",
    }


def _headers(
    key: str,
    workspace_id: str = "upload-workspace",
    *,
    actor_id: str = "upload-tester",
) -> dict[str, str]:
    return {
        **_read_headers(workspace_id, actor_id=actor_id),
        "X-Actor-Id": actor_id,
        "Idempotency-Key": key,
    }


def _read_headers(
    workspace_id: str = "upload-workspace",
    *,
    actor_id: str = "upload-tester",
) -> dict[str, str]:
    encoded = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "actor_id": actor_id,
                    "workspace_ids": [workspace_id],
                    "admin_workspace_ids": [],
                    "system_admin": False,
                    "issued_at": int(datetime.now(UTC).timestamp()),
                },
                separators=(",", ":"),
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    signature = hmac.new(
        TRUSTED_SECRET.encode(),
        f"{TRUSTED_KEY_ID}.{encoded}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Workspace-Id": workspace_id,
        "X-Trusted-Principal": f"{TRUSTED_KEY_ID}.{encoded}.{signature}",
    }


def _create_session(
    client: TestClient,
    *,
    idempotency_key: str,
    content: bytes = VALID_PNG,
    sha256: str | None = None,
    filename: str = "pixel.png",
    declared_mime: str = "image/png",
    retention_class: str = "FOUNDATION",
    workflow_id: str | None = None,
    product_id: str | None = None,
    sku_id: str | None = None,
) -> dict[str, object]:
    response = client.post(
        "/api/v1/upload-sessions",
        headers=_headers(idempotency_key),
        json=_upload_payload(
            content=content,
            sha256=sha256,
            filename=filename,
            declared_mime=declared_mime,
            retention_class=retention_class,
            workflow_id=workflow_id,
            product_id=product_id,
            sku_id=sku_id,
        ),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _upload_maintenance(
    *,
    integration_database: object,
    upload_settings: Settings,
) -> UploadSessionMaintenanceService:
    return UploadSessionMaintenanceService(
        uow_factory=lambda: SqlAlchemyAssetUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        ),
        batch_size=upload_settings.scheduler_batch_size,
        cleanup_policy=UploadCleanupPolicy(
            max_attempts=upload_settings.upload_cleanup_max_attempts,
            max_reconciliation_attempts=(upload_settings.upload_cleanup_reconcile_max_attempts),
            execution_max_elapsed=timedelta(
                seconds=upload_settings.operation_retry_max_elapsed_seconds
            ),
            presign_replay_grace=timedelta(
                seconds=upload_settings.upload_cleanup_presign_grace_seconds
            ),
            reconciliation_horizon=timedelta(
                seconds=upload_settings.upload_cleanup_reconcile_horizon_seconds
            ),
        ),
    )


def test_upload_http_requires_a_trusted_workspace_principal(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del minio_client
    payload = _upload_payload()

    with TestClient(create_app(upload_settings)) as client:
        missing = client.post(
            "/api/v1/upload-sessions",
            headers={
                "X-Workspace-Id": "upload-workspace",
                "X-Actor-Id": "browser-forged-actor",
                "Idempotency-Key": "upload-auth-missing-principal-0001",
            },
            json=payload,
        )
        assert missing.status_code == 401, missing.text

        forged = client.post(
            "/api/v1/upload-sessions",
            headers={
                "X-Workspace-Id": "upload-workspace",
                "X-Actor-Id": "browser-forged-actor",
                "X-Trusted-Principal": "forged.principal.signature",
                "Idempotency-Key": "upload-auth-forged-principal-0001",
            },
            json=payload,
        )
        assert forged.status_code == 401, forged.text

        wrong_workspace = client.post(
            "/api/v1/upload-sessions",
            headers={
                **_read_headers("other-workspace"),
                "X-Workspace-Id": "upload-workspace",
                "X-Actor-Id": "browser-forged-actor",
                "Idempotency-Key": "upload-auth-wrong-workspace-0001",
            },
            json=payload,
        )
        assert wrong_workspace.status_code == 403, wrong_workspace.text

        missing_actor = client.post(
            "/api/v1/upload-sessions",
            headers={
                **_read_headers(actor_id="trusted-upload-actor"),
                "Idempotency-Key": "upload-auth-missing-actor-0001",
            },
            json=payload,
        )
        assert missing_actor.status_code == 422, missing_actor.text
        assert missing_actor.json()["code"] == "VALIDATION_ERROR"

        mismatched_actor = client.post(
            "/api/v1/upload-sessions",
            headers={
                **_read_headers(actor_id="trusted-upload-actor"),
                "X-Actor-Id": "different-actor",
                "Idempotency-Key": "upload-auth-mismatched-actor-0001",
            },
            json=payload,
        )
        assert mismatched_actor.status_code == 401, mismatched_actor.text
        assert mismatched_actor.json()["code"] == "AUTHENTICATION_REQUIRED"

        trusted = client.post(
            "/api/v1/upload-sessions",
            headers=_headers(
                "upload-auth-trusted-principal-0001",
                actor_id="trusted-upload-actor",
            ),
            json=payload,
        )
        assert trusted.status_code == 201, trusted.text
        upload_session_id = trusted.json()["id"]

        missing_read_principal = client.get(
            f"/api/v1/upload-sessions/{upload_session_id}",
            headers={"X-Workspace-Id": "upload-workspace"},
        )
        assert missing_read_principal.status_code == 401

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        persisted_actor = connection.execute(
            text("SELECT actor_id FROM upload_sessions WHERE id = :upload_session_id"),
            {"upload_session_id": upload_session_id},
        ).scalar_one()
    assert persisted_actor == "trusted-upload-actor"


def _upload_payload(
    *,
    content: bytes = VALID_PNG,
    sha256: str | None = None,
    filename: str = "pixel.png",
    declared_mime: str = "image/png",
    retention_class: str = "FOUNDATION",
    workflow_id: str | None = None,
    product_id: str | None = None,
    sku_id: str | None = None,
) -> dict[str, object]:
    return {
        "retention_class": retention_class,
        "asset_kind": "IMAGE",
        "filename": filename,
        "declared_mime": declared_mime,
        "byte_length": len(content),
        "sha256": sha256 or hashlib.sha256(content).hexdigest(),
        "workflow_id": workflow_id,
        "product_id": product_id,
        "sku_id": sku_id,
        "category": "beauty.skincare",
        "role": "product-primary",
    }


def _catalog_payload(*, external_id: str, title: str) -> dict[str, object]:
    return {
        "source_namespace": "MANUAL",
        "external_id": external_id,
        "source_version": "manual-v1",
        "title": title,
        "category_code": "beauty.skincare",
        "brand": "Upload Integration",
        "attributes": {},
        "expires_at": None,
    }


def _direct_upload(session: dict[str, object], content: bytes = VALID_PNG) -> None:
    upload = session["upload"]
    assert isinstance(upload, dict)
    required_headers = upload["required_headers"]
    assert isinstance(required_headers, dict)
    direct_headers = {
        str(name): str(value)
        for name, value in required_headers.items()
        if str(name).lower() != "content-length"
    }
    uploaded = httpx.put(
        str(upload["url"]),
        headers=direct_headers,
        content=content,
    )
    assert uploaded.status_code == 200, uploaded.text


def _object_location(session: dict[str, object]) -> tuple[str, str]:
    upload = session["upload"]
    assert isinstance(upload, dict)
    path = unquote(urlparse(str(upload["url"])).path).lstrip("/")
    bucket, key = path.split("/", 1)
    return bucket, key


def _overwrite_uploaded_object(
    minio_client: object,
    *,
    session: dict[str, object],
    content: bytes,
) -> None:
    bucket, key = _object_location(session)
    minio_client.put_object(  # type: ignore[attr-defined]
        Bucket=bucket,
        Key=key,
        Body=content,
        ContentType="image/png",
        Metadata={
            "upload-session-id": str(session["id"]),
            "sha256": base64.b64encode(hashlib.sha256(content).digest()).decode(),
        },
    )


def test_browser_can_direct_upload_and_finalize_one_quarantined_asset(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    sha256 = hashlib.sha256(VALID_PNG).hexdigest()
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(client, idempotency_key="create-upload-0001")
        source_bucket, source_key = _object_location(session)
        assert session["status"] == "OPEN"
        assert "storage_key" not in session
        assert "bucket" not in session

        _direct_upload(session)

        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-upload-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
        body = finalized.json()
        assert body["upload_session"]["status"] == "FINALIZED"
        assert body["asset"]["status"] == "QUARANTINED"
        assert body["asset_version"]["sha256"] == sha256
        assert body["asset_version"]["detected_mime"] == "image/png"
        assert body["asset_version"]["object_state"] == "QUARANTINED"
        assert body["validation_operation"]["state"] == "PENDING"

        refreshed = client.get(
            f"/api/v1/upload-sessions/{session['id']}",
            headers=_read_headers(),
        )
        assert refreshed.status_code == 200
        assert refreshed.json()["asset_version_id"] == body["asset_version"]["id"]

        asset = client.get(
            f"/api/v1/assets/{body['asset']['id']}",
            headers=_read_headers(),
        )
        assert asset.status_code == 200
        assert asset.json()["current_version"]["id"] == body["asset_version"]["id"]

        hidden_asset = client.get(
            f"/api/v1/assets/{body['asset']['id']}",
            headers=_read_headers("upload-other-workspace"),
        )
        assert hidden_asset.status_code == 404
        assert hidden_asset.json()["code"] == "NOT_FOUND"

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        object_fact = (
            connection.execute(
                text(
                    "SELECT ao.location, ao.bucket, ao.`key`, "
                    "us.destination_bucket, us.destination_key, us.cleanup_operation_id "
                    "FROM asset_objects AS ao "
                    "JOIN asset_versions AS av ON av.id = ao.asset_version_id "
                    "JOIN upload_sessions AS us ON us.id = av.upload_session_id "
                    "WHERE av.upload_session_id = :upload_session_id"
                ),
                {"upload_session_id": session["id"]},
            )
            .mappings()
            .one()
        )
    assert object_fact["location"] == "QUARANTINE"
    assert (object_fact["bucket"], object_fact["key"]) == (source_bucket, source_key)
    assert object_fact["cleanup_operation_id"] is None
    assert minio_client.head_object(  # type: ignore[attr-defined]
        Bucket=source_bucket,
        Key=source_key,
    )["ContentLength"] == len(VALID_PNG)
    with pytest.raises(ClientError) as missing_destination:
        minio_client.head_object(  # type: ignore[attr-defined]
            Bucket=object_fact["destination_bucket"],
            Key=object_fact["destination_key"],
        )
    assert missing_destination.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404


def test_mysql_rejects_invalid_quarantined_object_facts(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del minio_client
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-object-constraint-0001",
        )
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-object-constraint-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text

    asset_version_id = finalized.json()["asset_version"]["id"]
    with (
        pytest.raises((IntegrityError, OperationalError)),
        integration_database.engine.begin() as connection,  # type: ignore[attr-defined]
    ):
        connection.execute(
            text(
                "UPDATE asset_objects SET provider_version_id = NULL "
                "WHERE asset_version_id = :asset_version_id"
            ),
            {"asset_version_id": asset_version_id},
        )
    with (
        pytest.raises((IntegrityError, OperationalError)),
        integration_database.engine.begin() as connection,  # type: ignore[attr-defined]
    ):
        connection.execute(
            text(
                "UPDATE asset_objects SET location = 'FOUNDATION' "
                "WHERE asset_version_id = :asset_version_id"
            ),
            {"asset_version_id": asset_version_id},
        )


def test_upload_session_id_is_canonicalized_before_mysql_lookup(
    integration_database: object,
    upload_settings: Settings,
) -> None:
    canonical_id = "019f8a00-0000-7000-8000-000000000001"
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-canonical-upload-id-0001",
        )
        with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
            connection.execute(
                text("UPDATE upload_sessions SET id = :canonical_id WHERE id = :upload_session_id"),
                {
                    "canonical_id": canonical_id,
                    "upload_session_id": session["id"],
                },
            )

        canonicalized = client.get(
            f"/api/v1/upload-sessions/{canonical_id.upper()}",
            headers=_read_headers(),
        )
        confusable = client.get(
            f"/api/v1/upload-sessions/{canonical_id.replace('a', 'á', 1)}",
            headers=_read_headers(),
        )

    assert canonicalized.status_code == 200, canonicalized.text
    assert canonicalized.json()["id"] == canonical_id
    assert confusable.status_code == 404, confusable.text


def test_finalize_commits_lease_before_io_and_all_business_facts_atomically(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del minio_client
    storage = BoundaryFaultStorage(build_object_storage(upload_settings))
    monkeypatch.setattr(api_container, "build_object_storage", lambda _settings: storage)

    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(client, idempotency_key="create-atomic-finalize-0001")
        _direct_upload(session)

        def assert_claimed_lease_is_visible() -> None:
            with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
                claim = (
                    connection.execute(
                        text(
                            "SELECT workspace_id, state, finalize_lease_token, "
                            "finalize_lease_expires_at, version "
                            "FROM upload_sessions WHERE id = :upload_session_id"
                        ),
                        {"upload_session_id": session["id"]},
                    )
                    .mappings()
                    .one()
                )
            assert claim["workspace_id"] == "upload-workspace"
            assert claim["state"] == "FINALIZING"
            assert claim["finalize_lease_token"] is not None
            assert claim["finalize_lease_expires_at"] is not None
            assert claim["version"] == int(session["version"]) + 1

        storage.arm(first_io_observer=assert_claimed_lease_is_visible)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-atomic-finalize-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
        assert storage.observed_finalize_io
        body = finalized.json()
        replayed_after_response_loss = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-atomic-finalize-0001"),
            json={"expected_version": session["version"]},
        )
        assert replayed_after_response_loss.status_code == 202
        assert (
            replayed_after_response_loss.json()["asset_version"]["id"]
            == body["asset_version"]["id"]
        )
        asset_id = body["asset"]["id"]
        asset_version_id = body["asset_version"]["id"]
        operation_id = body["validation_operation"]["id"]

        with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
            facts = (
                connection.execute(
                    text(
                        "SELECT "
                        "(SELECT COUNT(*) FROM assets WHERE id = :asset_id "
                        "AND workspace_id = :workspace_id AND status = 'QUARANTINED') AS assets, "
                        "(SELECT COUNT(*) FROM asset_versions "
                        "WHERE id = :asset_version_id AND upload_session_id = :upload_session_id) "
                        "AS versions, "
                        "(SELECT COUNT(*) FROM asset_objects "
                        "WHERE asset_version_id = :asset_version_id "
                        "AND state = 'QUARANTINED' AND provider_version_id IS NOT NULL) "
                        "AS objects, "
                        "(SELECT COUNT(*) FROM durable_operations "
                        "WHERE id = :operation_id AND workspace_id = :workspace_id) AS operations, "
                        "(SELECT COUNT(*) FROM outbox_events "
                        "WHERE aggregate_id = :operation_id "
                        "AND event_type = 'asset.validation.requested') AS outbox_events, "
                        "(SELECT COUNT(*) FROM outbox_events "
                        "WHERE aggregate_id = :asset_id "
                        "AND event_type = 'asset.upload.finalized') AS finalized_events, "
                        "(SELECT COUNT(*) FROM audit_events "
                        "WHERE resource_id = :upload_session_id "
                        "AND action = 'asset.upload.finalized') AS audit_events, "
                        "(SELECT COUNT(*) FROM upload_sessions "
                        "WHERE id = :upload_session_id AND workspace_id = :workspace_id "
                        "AND state = 'FINALIZED' "
                        "AND finalized_asset_version_id = :asset_version_id "
                        "AND validation_operation_id = :operation_id) AS finalized_sessions"
                    ),
                    {
                        "asset_id": asset_id,
                        "asset_version_id": asset_version_id,
                        "operation_id": operation_id,
                        "upload_session_id": session["id"],
                        "workspace_id": "upload-workspace",
                    },
                )
                .mappings()
                .one()
            )
        assert set(facts.values()) == {1}


def test_finalize_cannot_reclaim_after_session_and_crashed_lease_expire(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del minio_client
    storage = CrashOnFirstStatStorage(build_object_storage(upload_settings))
    monkeypatch.setattr(api_container, "build_object_storage", lambda _settings: storage)

    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-crashed-expired-finalize-0001",
        )
        _direct_upload(session)
        finalize_key = "finalize-crashed-expired-finalize-0001"
        with pytest.raises(SimulatedFinalizeCrash, match="after claiming finalize"):
            client.post(
                f"/api/v1/upload-sessions/{session['id']}:finalize",
                headers=_headers(finalize_key),
                json={"expected_version": session["version"]},
            )

        expired_at = datetime.now(UTC) - timedelta(seconds=1)
        with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
            connection.execute(
                text(
                    "UPDATE upload_sessions "
                    "SET expires_at = :expired_at, finalize_lease_expires_at = :expired_at "
                    "WHERE id = :upload_session_id"
                ),
                {
                    "expired_at": expired_at.replace(tzinfo=None),
                    "upload_session_id": session["id"],
                },
            )

        retried = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers(finalize_key),
            json={"expected_version": session["version"]},
        )

    assert retried.status_code == 410, retried.text
    assert retried.json()["code"] == "UPLOAD_EXPIRED"
    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        facts = (
            connection.execute(
                text(
                    "SELECT "
                    "(SELECT state FROM upload_sessions WHERE id = :upload_session_id) "
                    "AS session_state, "
                    "(SELECT COUNT(*) FROM asset_versions "
                    "WHERE upload_session_id = :upload_session_id) AS asset_versions"
                ),
                {"upload_session_id": session["id"]},
            )
            .mappings()
            .one()
        )
    assert facts["session_state"] == "EXPIRED"
    assert facts["asset_versions"] == 0


def test_upload_and_finalize_idempotency_keys_bind_the_exact_request_hash(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del integration_database, minio_client
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(client, idempotency_key="create-hash-binding-0001")
        create_conflict = client.post(
            "/api/v1/upload-sessions",
            headers=_headers("create-hash-binding-0001"),
            json=_upload_payload(filename="different.png"),
        )
        assert create_conflict.status_code == 409
        assert create_conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"

        _direct_upload(session)
        finalize_key = "finalize-hash-binding-0001"
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers(finalize_key),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text

        finalize_conflict = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers(finalize_key),
            json={"expected_version": int(session["version"]) + 1},
        )
        assert finalize_conflict.status_code == 409
        assert finalize_conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"


def test_upload_associations_are_workspace_scoped_and_composite(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del integration_database, minio_client
    with TestClient(create_app(upload_settings)) as client:
        product = client.post(
            "/api/v1/products",
            headers=_headers("create-upload-product-0001"),
            json=_catalog_payload(
                external_id="UPLOAD-PRODUCT-001",
                title="Upload Product",
            ),
        )
        assert product.status_code == 201, product.text
        product_id = product.json()["id"]
        sku = client.post(
            f"/api/v1/products/{product_id}/skus",
            headers=_headers("create-upload-sku-0001"),
            json=_catalog_payload(
                external_id="UPLOAD-SKU-001",
                title="Upload SKU",
            ),
        )
        assert sku.status_code == 201, sku.text
        sku_id = sku.json()["id"]

        session = _create_session(
            client,
            idempotency_key="create-associated-upload-0001",
            product_id=product_id,
            sku_id=sku_id,
        )
        hidden_read = client.get(
            f"/api/v1/upload-sessions/{session['id']}",
            headers=_read_headers("upload-other-workspace"),
        )
        assert hidden_read.status_code == 404
        assert hidden_read.json()["code"] == "NOT_FOUND"

        hidden_finalize = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers(
                "finalize-associated-hidden-0001",
                "upload-other-workspace",
            ),
            json={"expected_version": session["version"]},
        )
        assert hidden_finalize.status_code == 404
        assert hidden_finalize.json()["code"] == "NOT_FOUND"

        cross_workspace_association = client.post(
            "/api/v1/upload-sessions",
            headers=_headers(
                "create-associated-hidden-0001",
                "upload-other-workspace",
            ),
            json=_upload_payload(product_id=product_id, sku_id=sku_id),
        )
        assert cross_workspace_association.status_code == 404
        assert cross_workspace_association.json()["code"] == "NOT_FOUND"

        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-associated-upload-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
        assert finalized.json()["asset"]["product_id"] == product_id
        assert finalized.json()["asset"]["sku_id"] == sku_id


def test_task_asset_retention_is_anchored_to_its_workflow(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        workflow = client.post(
            "/api/v1/workflows",
            headers=_headers("create-task-workflow-0001"),
            json={
                "workflow_type": "FIXTURE_IMAGE_GENERATION",
                "input_data": {"fixture_config": {"count": 1}},
                "retention_hours": 168,
            },
        )
        assert workflow.status_code == 202, workflow.text
        workflow_body = workflow.json()

        session = _create_session(
            client,
            idempotency_key="create-task-upload-0001",
            retention_class="TASK",
            workflow_id=workflow_body["id"],
        )
        session_expiry = datetime.fromisoformat(session["expires_at"])
        workflow_created = datetime.fromisoformat(workflow_body["created_at"])
        workflow_expiry = datetime.fromisoformat(workflow_body["expires_at"])
        task_asset_deadline = min(
            workflow_expiry,
            workflow_created + timedelta(hours=72),
        )
        assert session_expiry <= task_asset_deadline

        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-task-upload-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
        asset = finalized.json()["asset"]
        assert asset["retention_class"] == "TASK"
        assert asset["workflow_id"] == workflow_body["id"]
        assert datetime.fromisoformat(asset["retention_deadline"]) == task_asset_deadline

        with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
            command_expiries = list(
                connection.execute(
                    text(
                        "SELECT expires_at FROM idempotency_keys "
                        "WHERE resource_id = :upload_session_id"
                    ),
                    {"upload_session_id": session["id"]},
                ).scalars()
            )
            assert len(command_expiries) == 2
            assert all(
                expiry.replace(tzinfo=UTC) <= task_asset_deadline for expiry in command_expiries
            )

        rejected_session = _create_session(
            client,
            idempotency_key="create-task-rejected-upload-0001",
            retention_class="TASK",
            workflow_id=workflow_body["id"],
        )
        _direct_upload(rejected_session)
        _overwrite_uploaded_object(
            minio_client,
            session=rejected_session,
            content=VALID_PNG + b"x",
        )
        rejected = client.post(
            f"/api/v1/upload-sessions/{rejected_session['id']}:finalize",
            headers=_headers("finalize-task-rejected-upload-0001"),
            json={"expected_version": rejected_session["version"]},
        )
        assert rejected.status_code == 422, rejected.text

        with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
            rejected_audit = (
                connection.execute(
                    text(
                        "SELECT created_at, expires_at FROM audit_events "
                        "WHERE resource_id = :upload_session_id "
                        "AND action = 'asset.upload.rejected'"
                    ),
                    {"upload_session_id": rejected_session["id"]},
                )
                .mappings()
                .one()
            )
        rejected_audit_created = rejected_audit["created_at"].replace(tzinfo=UTC)
        rejected_audit_expiry = rejected_audit["expires_at"].replace(tzinfo=UTC)
        assert rejected_audit_expiry - rejected_audit_created == timedelta(days=180)
        assert rejected_audit_expiry > workflow_expiry


@pytest.mark.parametrize(
    ("content", "filename", "mime_type", "image_format"),
    [
        (VALID_JPEG, "pixel.jpg", "image/jpeg", "JPEG"),
        (VALID_WEBP, "pixel.webp", "image/webp", "WEBP"),
    ],
)
def test_finalize_accepts_each_supported_raster_format(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
    content: bytes,
    filename: str,
    mime_type: str,
    image_format: str,
) -> None:
    del integration_database, minio_client
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key=f"create-format-{image_format.lower()}",
            content=content,
            filename=filename,
            declared_mime=mime_type,
        )
        _direct_upload(session, content)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers(f"finalize-format-{image_format.lower()}"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
        assert finalized.json()["asset_version"]["image_format"] == image_format
        assert finalized.json()["asset_version"]["detected_mime"] == mime_type


@pytest.mark.parametrize(
    ("content", "filename", "mime_type", "case"),
    [
        (VALID_PNG, "pixel.jpg", "image/jpeg", "mime"),
        (VALID_PNG[:-8], "pixel.png", "image/png", "truncated"),
        (OVERSIZED_DIMENSION_PNG, "wide.png", "image/png", "dimensions"),
        (ANIMATED_WEBP, "animated.webp", "image/webp", "frames"),
    ],
)
def test_finalize_rejects_unsafe_or_inconsistent_image_decode(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
    content: bytes,
    filename: str,
    mime_type: str,
    case: str,
) -> None:
    del integration_database, minio_client
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key=f"create-invalid-image-{case}",
            content=content,
            filename=filename,
            declared_mime=mime_type,
        )
        _direct_upload(session, content)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers(f"finalize-invalid-image-{case}"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 422
        assert finalized.json()["code"] == "OBJECT_MISMATCH"


@pytest.mark.parametrize(
    ("tampered", "case"),
    [
        (VALID_PNG[:-1] + bytes([VALID_PNG[-1] ^ 1]), "checksum"),
        (VALID_PNG + b"x", "length"),
    ],
)
def test_finalize_rejects_tampered_object_facts(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
    tampered: bytes,
    case: str,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key=f"create-mismatch-{case}",
        )
        _direct_upload(session)
        _overwrite_uploaded_object(
            minio_client,
            session=session,
            content=tampered,
        )

        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers(f"finalize-mismatch-{case}"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 422
        assert finalized.json()["code"] == "OBJECT_MISMATCH"

        replay = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers(f"finalize-mismatch-{case}"),
            json={"expected_version": session["version"]},
        )
        assert replay.status_code == 422
        assert replay.json()["code"] == "OBJECT_MISMATCH"

        current = client.get(
            f"/api/v1/upload-sessions/{session['id']}",
            headers=_read_headers(),
        )
        assert current.status_code == 200
        assert current.json()["status"] == "ABORTED"
        assert current.json()["failure_code"] == "OBJECT_MISMATCH"

        if case == "length":
            cleanup_operation_id = current.json()["cleanup_operation_id"]
            assert cleanup_operation_id is not None
            with SqlAlchemyUnitOfWork(
                integration_database.session_factory  # type: ignore[attr-defined]
            ) as uow:
                cleanup_event = next(
                    event
                    for event in uow.outbox.list_for_aggregate(cleanup_operation_id)
                    if event.envelope.event_type == "asset.delete.requested"
                )
            simulated_now = datetime.now(UTC)
            with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
                connection.execute(
                    text(
                        "UPDATE upload_sessions SET expires_at = :expired_at "
                        "WHERE id = :upload_session_id"
                    ),
                    {
                        "expired_at": (simulated_now - timedelta(seconds=1)).replace(tzinfo=None),
                        "upload_session_id": session["id"],
                    },
                )
                connection.execute(
                    text(
                        "UPDATE outbox_events SET available_at = :available_at WHERE id = :event_id"
                    ),
                    {
                        "available_at": (simulated_now - timedelta(seconds=1)).replace(tzinfo=None),
                        "event_id": cleanup_event.envelope.event_id,
                    },
                )
            worker = WorkerRuntime.build(upload_settings)
            try:
                assert worker.process_event(cleanup_event.envelope.event_id) == "processed"
            finally:
                worker.close()
            with SqlAlchemyOperationUnitOfWork(
                integration_database.session_factory  # type: ignore[attr-defined]
            ) as uow:
                cleanup_operation = uow.operations.get(
                    cleanup_operation_id,
                    workspace_id="upload-workspace",
                )
            assert cleanup_operation is not None
            assert cleanup_operation.state.value == "RECONCILING"
            with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
                connection.execute(
                    text(
                        "UPDATE durable_operations "
                        "SET next_reconciliation_at = :next_reconciliation_at "
                        "WHERE id = :operation_id"
                    ),
                    {
                        "next_reconciliation_at": (
                            datetime.now(UTC) - timedelta(seconds=1)
                        ).replace(tzinfo=None),
                        "operation_id": cleanup_operation_id,
                    },
                )
            scanner = OperationRecoveryService(
                uow_factory=lambda: SqlAlchemyOperationUnitOfWork(
                    integration_database.session_factory  # type: ignore[attr-defined]
                ),
                batch_size=10,
            )
            assert scanner.recover_once(now=datetime.now(UTC)) == 1
            with SqlAlchemyUnitOfWork(
                integration_database.session_factory  # type: ignore[attr-defined]
            ) as uow:
                recovery_event = next(
                    event
                    for event in uow.outbox.list_for_aggregate(cleanup_operation_id)
                    if event.envelope.event_type == "operation.recovery.requested"
                )
            recovery_worker = WorkerRuntime.build(upload_settings)
            try:
                assert (
                    recovery_worker.process_event(recovery_event.envelope.event_id) == "processed"
                )
            finally:
                recovery_worker.close()
            source = _object_location(session)
            with pytest.raises(ClientError) as missing:
                minio_client.head_object(  # type: ignore[attr-defined]
                    Bucket=source[0],
                    Key=source[1],
                )
            assert missing.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404


def test_expired_and_aborted_sessions_cannot_finalize(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del minio_client
    with TestClient(create_app(upload_settings)) as client:
        expired = _create_session(client, idempotency_key="create-expired-0001")
        with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
            connection.execute(
                text(
                    "UPDATE upload_sessions SET expires_at = :expires_at "
                    "WHERE id = :upload_session_id"
                ),
                {
                    "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).replace(tzinfo=None),
                    "upload_session_id": expired["id"],
                },
            )
        expired_create_replay = client.post(
            "/api/v1/upload-sessions",
            headers=_headers("create-expired-0001"),
            json=_upload_payload(),
        )
        assert expired_create_replay.status_code == 410
        assert expired_create_replay.json()["code"] == "UPLOAD_EXPIRED"

        expired_finalize = client.post(
            f"/api/v1/upload-sessions/{expired['id']}:finalize",
            headers=_headers("finalize-expired-0001"),
            json={"expected_version": expired["version"]},
        )
        assert expired_finalize.status_code == 410
        assert expired_finalize.json()["code"] == "UPLOAD_EXPIRED"

        aborted = _create_session(client, idempotency_key="create-aborted-0001")
        abort_response = client.post(
            f"/api/v1/upload-sessions/{aborted['id']}:abort",
            headers=_headers("abort-upload-0001"),
            json={"expected_version": aborted["version"]},
        )
        assert abort_response.status_code == 200
        assert abort_response.json()["status"] == "ABORTED"
        aborted_finalize = client.post(
            f"/api/v1/upload-sessions/{aborted['id']}:finalize",
            headers=_headers("finalize-aborted-0001"),
            json={"expected_version": abort_response.json()["version"]},
        )
        assert aborted_finalize.status_code == 409
        assert aborted_finalize.json()["code"] == "UPLOAD_ABORTED"


def test_abort_defers_cleanup_until_the_presigned_put_can_no_longer_replay(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    delayed_cleanup_settings = upload_settings.model_copy(
        update={"operation_retry_max_elapsed_seconds": 60.0}
    )
    with TestClient(create_app(delayed_cleanup_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-abort-replay-window-0001",
        )
        aborted = client.post(
            f"/api/v1/upload-sessions/{session['id']}:abort",
            headers=_headers("abort-replay-window-0001"),
            json={"expected_version": session["version"]},
        )
        assert aborted.status_code == 200, aborted.text
        cleanup_operation_id = aborted.json()["cleanup_operation_id"]
        assert cleanup_operation_id

        # An issued presigned URL cannot be revoked by changing MySQL state.
        _direct_upload(session)
        bucket, key = _object_location(session)
        assert minio_client.head_object(Bucket=bucket, Key=key)["ContentLength"] == len(VALID_PNG)  # type: ignore[attr-defined]

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        cleanup_schedule = (
            connection.execute(
                text(
                    "SELECT oe.id AS event_id, oe.available_at, op.execution_deadline_at "
                    "FROM outbox_events AS oe "
                    "JOIN durable_operations AS op ON op.id = oe.aggregate_id "
                    "WHERE oe.aggregate_id = :cleanup_operation_id "
                    "AND oe.event_type = 'asset.delete.requested'"
                ),
                {"cleanup_operation_id": cleanup_operation_id},
            )
            .mappings()
            .one()
        )

    session_expires_at = datetime.fromisoformat(session["expires_at"])
    available_at = cleanup_schedule["available_at"].replace(tzinfo=UTC)
    assert available_at == session_expires_at + timedelta(
        seconds=delayed_cleanup_settings.upload_cleanup_presign_grace_seconds
    )
    assert cleanup_schedule["execution_deadline_at"].replace(
        tzinfo=UTC
    ) == available_at + timedelta(
        seconds=delayed_cleanup_settings.operation_retry_max_elapsed_seconds
    )

    worker = WorkerRuntime.build(delayed_cleanup_settings)
    try:
        assert worker.process_event(cleanup_schedule["event_id"]) == "retry-not-ready"
        with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
            connection.execute(
                text("UPDATE outbox_events SET available_at = :available_at WHERE id = :event_id"),
                {
                    "available_at": (datetime.now(UTC) - timedelta(seconds=1)).replace(tzinfo=None),
                    "event_id": cleanup_schedule["event_id"],
                },
            )
        assert worker.process_event(cleanup_schedule["event_id"]) == "processed"
    finally:
        worker.close()

    with pytest.raises(ClientError) as missing:
        minio_client.head_object(Bucket=bucket, Key=key)  # type: ignore[attr-defined]
    assert missing.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404


def test_scheduler_expires_an_abandoned_open_upload_without_api_traffic(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-autonomous-expiry-0001",
        )
        _direct_upload(session)
    bucket, key = _object_location(session)
    expired_at = datetime.now(UTC) - timedelta(
        seconds=upload_settings.upload_cleanup_presign_grace_seconds + 1
    )
    with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
        connection.execute(
            text(
                "UPDATE upload_sessions SET expires_at = :expires_at WHERE id = :upload_session_id"
            ),
            {
                "expires_at": expired_at.replace(tzinfo=None),
                "upload_session_id": session["id"],
            },
        )

    maintenance = _upload_maintenance(
        integration_database=integration_database,
        upload_settings=upload_settings,
    )
    assert maintenance.expire_due_once(now=datetime.now(UTC)) == 1

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        facts = (
            connection.execute(
                text(
                    "SELECT us.state, us.cleanup_operation_id, "
                    "op.max_attempts, oe.id AS event_id "
                    "FROM upload_sessions AS us "
                    "JOIN durable_operations AS op "
                    "ON op.workspace_id = us.workspace_id "
                    "AND op.id = us.cleanup_operation_id "
                    "JOIN outbox_events AS oe ON oe.aggregate_id = us.cleanup_operation_id "
                    "WHERE us.id = :upload_session_id "
                    "AND oe.event_type = 'asset.delete.requested'"
                ),
                {"upload_session_id": session["id"]},
            )
            .mappings()
            .one()
        )
    assert facts["state"] == "EXPIRED"
    assert facts["cleanup_operation_id"]
    assert facts["max_attempts"] == upload_settings.upload_cleanup_max_attempts

    worker = WorkerRuntime.build(upload_settings)
    try:
        assert worker.process_event(facts["event_id"]) == "processed"
    finally:
        worker.close()

    with pytest.raises(ClientError) as missing:
        minio_client.head_object(Bucket=bucket, Key=key)  # type: ignore[attr-defined]
    assert missing.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404


def test_durable_operation_reconciles_a_presigned_put_replayed_after_initial_cleanup(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-late-put-reconciliation-0001",
        )
        aborted = client.post(
            f"/api/v1/upload-sessions/{session['id']}:abort",
            headers=_headers("abort-late-put-reconciliation-0001"),
            json={"expected_version": session["version"]},
        )
        assert aborted.status_code == 200, aborted.text
        initial_cleanup_operation_id = aborted.json()["cleanup_operation_id"]

    with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
        initial_event_id = connection.execute(
            text(
                "SELECT id FROM outbox_events "
                "WHERE aggregate_id = :operation_id "
                "AND event_type = 'asset.delete.requested'"
            ),
            {"operation_id": initial_cleanup_operation_id},
        ).scalar_one()
        connection.execute(
            text("UPDATE outbox_events SET available_at = :available_at WHERE id = :event_id"),
            {
                "available_at": (datetime.now(UTC) - timedelta(seconds=1)).replace(tzinfo=None),
                "event_id": initial_event_id,
            },
        )

    worker = WorkerRuntime.build(upload_settings)
    try:
        assert worker.process_event(initial_event_id) == "processed"
    finally:
        worker.close()

    # The URL remains a bearer capability until its signed expiry.
    _direct_upload(session)
    bucket, key = _object_location(session)
    assert minio_client.head_object(Bucket=bucket, Key=key)["ContentLength"] == len(VALID_PNG)  # type: ignore[attr-defined]

    with SqlAlchemyOperationUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        cleanup_operation = uow.operations.get(
            initial_cleanup_operation_id,
            workspace_id="upload-workspace",
        )
    assert cleanup_operation is not None
    assert cleanup_operation.state.value == "RECONCILING"

    recovery = OperationRecoveryService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        ),
        batch_size=upload_settings.scheduler_batch_size,
        reconciliation_max_elapsed=timedelta(
            seconds=upload_settings.operation_reconciliation_max_elapsed_seconds
        ),
    )
    assert recovery.recover_once(now=datetime.now(UTC)) == 1
    with SqlAlchemyUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        recovery_event = next(
            event
            for event in uow.outbox.list_for_aggregate(initial_cleanup_operation_id)
            if event.envelope.event_type == "operation.recovery.requested"
        )
    with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
        connection.execute(
            text("UPDATE outbox_events SET available_at = :available_at WHERE id = :event_id"),
            {
                "available_at": (datetime.now(UTC) - timedelta(seconds=1)).replace(tzinfo=None),
                "event_id": recovery_event.envelope.event_id,
            },
        )

    worker = WorkerRuntime.build(upload_settings)
    try:
        assert worker.process_event(recovery_event.envelope.event_id) == "processed"
    finally:
        worker.close()

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        operation_facts = (
            connection.execute(
                text(
                    "SELECT us.cleanup_operation_id, op.state, op.max_attempts "
                    "FROM upload_sessions AS us "
                    "JOIN durable_operations AS op ON op.id = us.cleanup_operation_id "
                    "WHERE us.id = :upload_session_id"
                ),
                {"upload_session_id": session["id"]},
            )
            .mappings()
            .one()
        )
    assert operation_facts == {
        "cleanup_operation_id": initial_cleanup_operation_id,
        "state": "RECONCILING",
        "max_attempts": upload_settings.upload_cleanup_max_attempts,
    }
    with pytest.raises(ClientError) as missing:
        minio_client.head_object(Bucket=bucket, Key=key)  # type: ignore[attr-defined]
    assert missing.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404


def test_expiry_atomically_schedules_one_durable_cleanup_command(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del minio_client
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-expiry-cleanup-command-0001",
        )
        with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
            connection.execute(
                text(
                    "UPDATE upload_sessions SET expires_at = :expires_at "
                    "WHERE id = :upload_session_id"
                ),
                {
                    "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).replace(tzinfo=None),
                    "upload_session_id": session["id"],
                },
            )

        expired = client.get(
            f"/api/v1/upload-sessions/{session['id']}",
            headers=_read_headers(),
        )
        assert expired.status_code == 200, expired.text
        expired_body = expired.json()
        assert expired_body["status"] == "EXPIRED"
        cleanup_operation_id = expired_body["cleanup_operation_id"]
        assert cleanup_operation_id

        repeated = client.get(
            f"/api/v1/upload-sessions/{session['id']}",
            headers=_read_headers(),
        )
        assert repeated.status_code == 200
        assert repeated.json()["cleanup_operation_id"] == cleanup_operation_id
        assert repeated.json()["version"] == expired_body["version"]

        with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
            facts = (
                connection.execute(
                    text(
                        "SELECT us.cleanup_operation_id, us.version AS session_version, "
                        "op.kind, op.target_type, op.target_id, op.target_version, op.state, "
                        "oe.event_type, oe.aggregate_id, oe.workspace_id, oe.payload_json "
                        "FROM upload_sessions AS us "
                        "JOIN durable_operations AS op "
                        "ON op.workspace_id = us.workspace_id "
                        "AND op.id = us.cleanup_operation_id "
                        "JOIN outbox_events AS oe "
                        "ON oe.workspace_id = op.workspace_id "
                        "AND oe.aggregate_id = op.id "
                        "WHERE us.workspace_id = :workspace_id "
                        "AND us.id = :upload_session_id"
                    ),
                    {
                        "workspace_id": "upload-workspace",
                        "upload_session_id": session["id"],
                    },
                )
                .mappings()
                .one()
            )

        assert facts["cleanup_operation_id"] == cleanup_operation_id
        assert facts["kind"] == "ASSET_DELETION"
        assert facts["target_type"] == "UPLOAD_SESSION"
        assert facts["target_id"] == session["id"]
        assert facts["target_version"] == facts["session_version"]
        assert facts["state"] == "PENDING"
        assert facts["event_type"] == "asset.delete.requested"
        assert facts["aggregate_id"] == cleanup_operation_id
        payload = json.loads(facts["payload_json"])
        assert payload == {
            "operation_id": cleanup_operation_id,
            "workspace_id": "upload-workspace",
            "target_type": "UPLOAD_SESSION",
            "target_id": session["id"],
            "target_version": facts["session_version"],
            "reason": "UPLOAD_EXPIRED",
        }


def test_duplicate_and_concurrent_finalize_converge_on_one_asset_version(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del integration_database, minio_client
    app_one = create_app(upload_settings)
    app_two = create_app(upload_settings)
    with TestClient(app_one) as first_client, TestClient(app_two) as second_client:
        session = _create_session(first_client, idempotency_key="create-concurrent-0001")
        _direct_upload(session)

        def finalize(client: TestClient, key: str):
            return client.post(
                f"/api/v1/upload-sessions/{session['id']}:finalize",
                headers=_headers(key),
                json={"expected_version": session["version"]},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(finalize, first_client, "finalize-concurrent-a"),
                executor.submit(finalize, second_client, "finalize-concurrent-b"),
            ]
            responses = [future.result(timeout=30) for future in futures]

        final_bodies: list[dict[str, object]] = []
        for index, response in enumerate(responses):
            if response.status_code != 202:
                assert response.status_code == 409, response.text
                response = finalize(
                    first_client,
                    "finalize-concurrent-a" if index == 0 else "finalize-concurrent-b",
                )
            assert response.status_code == 202, response.text
            final_bodies.append(response.json())

        first_version = final_bodies[0]["asset_version"]
        second_version = final_bodies[1]["asset_version"]
        assert isinstance(first_version, dict)
        assert isinstance(second_version, dict)
        assert first_version["id"] == second_version["id"]

        duplicate = finalize(first_client, "finalize-concurrent-a")
        assert duplicate.status_code == 202
        assert duplicate.json()["asset_version"]["id"] == first_version["id"]


def test_storage_outage_releases_finalize_for_same_idempotent_retry(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del integration_database, minio_client
    with TestClient(create_app(upload_settings)) as healthy_client:
        session = _create_session(healthy_client, idempotency_key="create-outage-0001")
        _direct_upload(session)

    unavailable_settings = upload_settings.model_copy(
        update={
            "object_store_endpoint": "http://127.0.0.1:19999",
            "object_store_presign_endpoint": "http://127.0.0.1:19999",
            "object_store_connect_timeout_seconds": 0.2,
            "object_store_read_timeout_seconds": 0.2,
        }
    )
    finalize_idempotency = "finalize-outage-0001"
    with TestClient(create_app(unavailable_settings)) as unavailable_client:
        unavailable = unavailable_client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers(finalize_idempotency),
            json={"expected_version": session["version"]},
        )
        assert unavailable.status_code == 503
        assert unavailable.json()["code"] == "STORAGE_UNAVAILABLE"

    with TestClient(create_app(upload_settings)) as recovered_client:
        current = recovered_client.get(
            f"/api/v1/upload-sessions/{session['id']}",
            headers=_read_headers(),
        )
        assert current.status_code == 200
        assert current.json()["status"] == "OPEN"

        recovered = recovered_client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers(finalize_idempotency),
            json={"expected_version": session["version"]},
        )
        assert recovered.status_code == 202, recovered.text
        assert recovered.json()["upload_session"]["status"] == "FINALIZED"


def test_storage_outage_crossing_expiry_schedules_one_durable_cleanup(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = BoundaryFaultStorage(
        build_object_storage(upload_settings),
        fault="source-stat",
    )
    monkeypatch.setattr(api_container, "build_object_storage", lambda _settings: storage)

    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-expiring-storage-outage-0001",
        )
        source = _object_location(session)
        _direct_upload(session)

        def expire_during_storage_io() -> None:
            with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
                connection.execute(
                    text(
                        "UPDATE upload_sessions SET expires_at = :expires_at "
                        "WHERE id = :upload_session_id"
                    ),
                    {
                        "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).replace(
                            tzinfo=None
                        ),
                        "upload_session_id": session["id"],
                    },
                )

        storage.arm(first_io_observer=expire_during_storage_io)
        unavailable = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-expiring-storage-outage-0001"),
            json={"expected_version": session["version"]},
        )
        assert unavailable.status_code == 503, unavailable.text
        assert unavailable.json()["code"] == "STORAGE_UNAVAILABLE"

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        facts = (
            connection.execute(
                text(
                    "SELECT us.state, us.cleanup_operation_id, "
                    "(SELECT COUNT(*) FROM durable_operations AS op "
                    "WHERE op.target_type = 'UPLOAD_SESSION' "
                    "AND op.target_id = us.id AND op.kind = 'ASSET_DELETION') "
                    "AS cleanup_operations, "
                    "(SELECT COUNT(*) FROM outbox_events AS oe "
                    "JOIN durable_operations AS op ON op.id = oe.aggregate_id "
                    "WHERE op.target_type = 'UPLOAD_SESSION' "
                    "AND op.target_id = us.id "
                    "AND oe.event_type = 'asset.delete.requested') AS cleanup_events "
                    "FROM upload_sessions AS us WHERE us.id = :upload_session_id"
                ),
                {"upload_session_id": session["id"]},
            )
            .mappings()
            .one()
        )
    assert facts == {
        "state": "EXPIRED",
        "cleanup_operation_id": facts["cleanup_operation_id"],
        "cleanup_operations": 1,
        "cleanup_events": 1,
    }
    assert facts["cleanup_operation_id"] is not None
    assert minio_client.head_object(  # type: ignore[attr-defined]
        Bucket=source[0],
        Key=source[1],
    )["ContentLength"] == len(VALID_PNG)


@pytest.mark.parametrize(
    "fault",
    [
        "source-stat",
        "bounded-read",
    ],
)
def test_finalize_recovers_from_each_storage_io_boundary(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    del minio_client
    storage = BoundaryFaultStorage(build_object_storage(upload_settings), fault=fault)
    monkeypatch.setattr(api_container, "build_object_storage", lambda _settings: storage)

    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key=f"create-boundary-{fault}-0001",
        )
        _direct_upload(session)
        storage.arm()
        finalize_path = f"/api/v1/upload-sessions/{session['id']}:finalize"
        finalize_headers = _headers(f"finalize-boundary-{fault}-0001")
        finalize_body = {"expected_version": session["version"]}

        unavailable = client.post(
            finalize_path,
            headers=finalize_headers,
            json=finalize_body,
        )
        assert unavailable.status_code == 503, unavailable.text
        assert unavailable.json()["code"] == "STORAGE_UNAVAILABLE"

        with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
            before_retry = (
                connection.execute(
                    text(
                        "SELECT "
                        "(SELECT state FROM upload_sessions "
                        "WHERE id = :upload_session_id) AS session_state, "
                        "(SELECT COUNT(*) FROM asset_versions "
                        "WHERE upload_session_id = :upload_session_id) AS versions, "
                        "(SELECT COUNT(*) FROM assets "
                        "WHERE id = :asset_id) AS assets"
                    ),
                    {
                        "asset_id": session["reserved_asset_id"],
                        "upload_session_id": session["id"],
                    },
                )
                .mappings()
                .one()
            )
        assert before_retry == {"session_state": "OPEN", "versions": 0, "assets": 0}

        recovered = client.post(
            finalize_path,
            headers=finalize_headers,
            json=finalize_body,
        )
        assert recovered.status_code == 202, recovered.text
        assert recovered.json()["upload_session"]["status"] == "FINALIZED"


def test_upload_promoter_recovers_after_process_crash_following_object_copy(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = CrashAfterFirstCopyStorage(build_object_storage(upload_settings))
    monkeypatch.setattr(api_container, "build_object_storage", lambda _settings: storage)

    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-copy-crash-recovery-0001",
        )
        _direct_upload(session)
        with SqlAlchemyAssetUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        ) as uow:
            upload_session = uow.upload_sessions.get(
                workspace_id="upload-workspace",
                upload_session_id=str(session["id"]),
            )
        assert upload_session is not None
        verifier = ImageUploadIntegrityVerifier(
            storage=storage,
            transaction_active=is_unit_of_work_active,
            maximum_bytes=upload_settings.upload_max_bytes,
            maximum_dimension=upload_settings.upload_max_image_dimension,
            maximum_pixels=upload_settings.upload_max_image_pixels,
            maximum_frames=upload_settings.upload_max_image_frames,
            maximum_metadata_bytes=upload_settings.upload_max_metadata_bytes,
        )
        promoter = UploadPromoter(storage=storage, verifier=verifier)

        with pytest.raises(SimulatedFinalizeCrash):
            promoter.verify_and_promote(upload_session)
        recovery_storage = build_object_storage(upload_settings)
        try:
            recovery_verifier = ImageUploadIntegrityVerifier(
                storage=recovery_storage,
                transaction_active=is_unit_of_work_active,
                maximum_bytes=upload_settings.upload_max_bytes,
                maximum_dimension=upload_settings.upload_max_image_dimension,
                maximum_pixels=upload_settings.upload_max_image_pixels,
                maximum_frames=upload_settings.upload_max_image_frames,
                maximum_metadata_bytes=upload_settings.upload_max_metadata_bytes,
            )
            recovered = UploadPromoter(
                storage=recovery_storage,
                verifier=recovery_verifier,
            ).verify_and_promote(upload_session)
        finally:
            close_object_storage(recovery_storage)

    assert recovered.stat.reference.location == upload_session.destination_location
    assert recovered.stat.reference.key == upload_session.destination_key
    assert minio_client.head_object(  # type: ignore[attr-defined]
        Bucket=upload_session.destination_bucket,
        Key=upload_session.destination_key,
    )["ContentLength"] == len(VALID_PNG)
    with pytest.raises(ClientError) as missing_source:
        minio_client.head_object(  # type: ignore[attr-defined]
            Bucket=upload_session.storage_bucket,
            Key=upload_session.storage_key,
        )
    assert missing_source.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404


def test_task_finalize_cannot_commit_after_retention_expires_during_verification(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = BoundaryFaultStorage(build_object_storage(upload_settings))
    monkeypatch.setattr(api_container, "build_object_storage", lambda _settings: storage)

    with TestClient(create_app(upload_settings)) as client:
        workflow = client.post(
            "/api/v1/workflows",
            headers=_headers("create-expiring-finalize-workflow-0001"),
            json={
                "workflow_type": "FIXTURE_IMAGE_GENERATION",
                "input_data": {"fixture_config": {"count": 1}},
                "retention_hours": 72,
            },
        )
        assert workflow.status_code == 202, workflow.text
        session = _create_session(
            client,
            idempotency_key="create-expiring-finalize-upload-0001",
            retention_class="TASK",
            workflow_id=workflow.json()["id"],
        )
        source = _object_location(session)
        _direct_upload(session)
        finalize_path = f"/api/v1/upload-sessions/{session['id']}:finalize"
        finalize_headers = _headers("finalize-expiring-finalize-upload-0001")
        finalize_body = {"expected_version": session["version"]}

        def expire_workflow_during_verification() -> None:
            expired_at = (datetime.now(UTC) - timedelta(seconds=1)).replace(tzinfo=None)
            with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
                connection.execute(
                    text("UPDATE workflows SET expires_at = :expired_at WHERE id = :workflow_id"),
                    {
                        "expired_at": expired_at,
                        "workflow_id": workflow.json()["id"],
                    },
                )

        storage.arm(first_io_observer=expire_workflow_during_verification)
        expired = client.post(
            finalize_path,
            headers=finalize_headers,
            json=finalize_body,
        )
        assert expired.status_code == 410, expired.text

        with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
            facts = (
                connection.execute(
                    text(
                        "SELECT "
                        "(SELECT state FROM upload_sessions "
                        "WHERE id = :upload_session_id) AS session_state, "
                        "(SELECT cleanup_operation_id FROM upload_sessions "
                        "WHERE id = :upload_session_id) AS cleanup_operation_id, "
                        "(SELECT COUNT(*) FROM assets WHERE id = :asset_id) AS assets, "
                        "(SELECT COUNT(*) FROM asset_versions "
                        "WHERE upload_session_id = :upload_session_id) AS versions, "
                        "(SELECT COUNT(*) FROM durable_operations "
                        "WHERE target_id IN (SELECT id FROM asset_versions "
                        "WHERE upload_session_id = :upload_session_id)) AS operations, "
                        "(SELECT COUNT(*) FROM durable_operations "
                        "WHERE target_type = 'UPLOAD_SESSION' "
                        "AND target_id = :upload_session_id "
                        "AND kind = 'ASSET_DELETION') AS cleanup_operations, "
                        "(SELECT COUNT(*) FROM outbox_events AS oe "
                        "JOIN durable_operations AS op ON op.id = oe.aggregate_id "
                        "WHERE op.target_type = 'UPLOAD_SESSION' "
                        "AND op.target_id = :upload_session_id "
                        "AND oe.event_type = 'asset.delete.requested') AS cleanup_events"
                    ),
                    {
                        "upload_session_id": session["id"],
                        "asset_id": session["reserved_asset_id"],
                    },
                )
                .mappings()
                .one()
            )
        assert facts == {
            "session_state": "EXPIRED",
            "cleanup_operation_id": facts["cleanup_operation_id"],
            "assets": 0,
            "versions": 0,
            "operations": 0,
            "cleanup_operations": 1,
            "cleanup_events": 1,
        }
        assert facts["cleanup_operation_id"] is not None

        assert minio_client.head_object(  # type: ignore[attr-defined]
            Bucket=source[0],
            Key=source[1],
        )["ContentLength"] == len(VALID_PNG)


def test_cleanup_worker_recovers_after_expiry_transaction_without_sync_deletion(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-durable-expiry-cleanup-0001",
        )
        source = _object_location(session)
        _direct_upload(session)

        expired_at = (
            datetime.now(UTC)
            - timedelta(seconds=upload_settings.upload_cleanup_presign_grace_seconds + 1)
        ).replace(tzinfo=None)
        with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
            connection.execute(
                text(
                    "UPDATE upload_sessions "
                    "SET expires_at = :expired_at "
                    "WHERE id = :upload_session_id"
                ),
                {
                    "expired_at": expired_at,
                    "upload_session_id": session["id"],
                },
            )

        expired = client.get(
            f"/api/v1/upload-sessions/{session['id']}",
            headers=_read_headers(),
        )
        assert expired.status_code == 200
        assert expired.json()["status"] == "EXPIRED"

    object_locations = (source,)
    present = minio_client.head_object(  # type: ignore[attr-defined]
        Bucket=source[0],
        Key=source[1],
    )
    assert present["ContentLength"] == len(VALID_PNG)

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        cleanup_operation_id = connection.execute(
            text("SELECT cleanup_operation_id FROM upload_sessions WHERE id = :upload_session_id"),
            {"upload_session_id": session["id"]},
        ).scalar_one()
    with SqlAlchemyUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        events = uow.outbox.list_for_aggregate(cleanup_operation_id)
    cleanup_events = [
        event for event in events if event.envelope.event_type == "asset.delete.requested"
    ]
    assert len(cleanup_events) == 1

    worker = WorkerRuntime.build(upload_settings)
    try:
        event_id = cleanup_events[0].envelope.event_id
        assert worker.process_event(event_id) == "processed"
        assert worker.process_event(event_id) == "duplicate"
    finally:
        worker.close()

    with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
        cleanup_state = connection.execute(
            text(
                "SELECT state FROM durable_operations "
                "WHERE id = (SELECT cleanup_operation_id FROM upload_sessions "
                "WHERE id = :upload_session_id)"
            ),
            {"upload_session_id": session["id"]},
        ).scalar_one()
    assert cleanup_state == "RECONCILING"
    for bucket, key in object_locations:
        with pytest.raises(ClientError) as missing:
            minio_client.head_object(Bucket=bucket, Key=key)  # type: ignore[attr-defined]
        assert missing.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404


def test_cleanup_storage_outage_uses_durable_retry_and_recovery_event(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-cleanup-outage-upload-0001",
        )
        source = _object_location(session)
        _direct_upload(session)
        with integration_database.engine.begin() as connection:  # type: ignore[attr-defined]
            connection.execute(
                text(
                    "UPDATE upload_sessions SET expires_at = :expires_at "
                    "WHERE id = :upload_session_id"
                ),
                {
                    "expires_at": (
                        datetime.now(UTC)
                        - timedelta(
                            seconds=(upload_settings.upload_cleanup_presign_grace_seconds + 1)
                        )
                    ).replace(tzinfo=None),
                    "upload_session_id": session["id"],
                },
            )
        expired = client.get(
            f"/api/v1/upload-sessions/{session['id']}",
            headers=_read_headers(),
        )
        assert expired.status_code == 200
        cleanup_operation_id = expired.json()["cleanup_operation_id"]

    with SqlAlchemyUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        cleanup_event = next(
            event
            for event in uow.outbox.list_for_aggregate(cleanup_operation_id)
            if event.envelope.event_type == "asset.delete.requested"
        )
    unavailable_settings = upload_settings.model_copy(
        update={
            "object_store_endpoint": "http://127.0.0.1:19999",
            "object_store_presign_endpoint": "http://127.0.0.1:19999",
            "object_store_connect_timeout_seconds": 0.2,
            "object_store_read_timeout_seconds": 0.2,
        }
    )
    unavailable_worker = WorkerRuntime.build(unavailable_settings)
    try:
        assert unavailable_worker.process_event(cleanup_event.envelope.event_id) == "processed"
    finally:
        unavailable_worker.close()

    with SqlAlchemyOperationUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        failed = uow.operations.get(
            cleanup_operation_id,
            workspace_id="upload-workspace",
        )
    assert failed is not None
    assert failed.state.value == "RETRYABLE_FAILED"
    assert failed.next_attempt_at is not None
    assert failed.error is not None
    assert failed.error.code == "UPLOAD_CLEANUP_STORAGE_UNAVAILABLE"

    scanner = OperationRecoveryService(
        uow_factory=lambda: SqlAlchemyOperationUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        ),
        batch_size=10,
    )
    assert scanner.recover_once(now=failed.next_attempt_at) == 1
    with SqlAlchemyUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        recovery_event = next(
            event
            for event in uow.outbox.list_for_aggregate(cleanup_operation_id)
            if event.envelope.event_type == "operation.recovery.requested"
        )

    retry_delay = (failed.next_attempt_at - datetime.now(UTC)).total_seconds()
    if retry_delay > 0:
        time.sleep(retry_delay + 0.05)
    recovered_worker = WorkerRuntime.build(upload_settings)
    try:
        assert recovered_worker.process_event(recovery_event.envelope.event_id) == "processed"
    finally:
        recovered_worker.close()

    with SqlAlchemyOperationUnitOfWork(
        integration_database.session_factory  # type: ignore[attr-defined]
    ) as uow:
        recovered = uow.operations.get(
            cleanup_operation_id,
            workspace_id="upload-workspace",
        )
    assert recovered is not None
    assert recovered.state.value == "RECONCILING"
    assert recovered.reconciliation_required is True
    with pytest.raises(ClientError) as missing:
        minio_client.head_object(Bucket=source[0], Key=source[1])  # type: ignore[attr-defined]
    assert missing.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404


def test_finalize_isolated_from_an_untrusted_destination_object(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(
            client,
            idempotency_key="create-isolated-destination-0001",
        )
        _direct_upload(session)
        finalize_path = f"/api/v1/upload-sessions/{session['id']}:finalize"
        finalize_headers = _headers("finalize-isolated-destination-0001")
        finalize_body = {"expected_version": session["version"]}

        with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
            destination = (
                connection.execute(
                    text(
                        "SELECT destination_bucket, destination_key FROM upload_sessions "
                        "WHERE id = :upload_session_id"
                    ),
                    {"upload_session_id": session["id"]},
                )
                .mappings()
                .one()
            )
        minio_client.put_object(  # type: ignore[attr-defined]
            Bucket=destination["destination_bucket"],
            Key=destination["destination_key"],
            Body=b"conflicting object",
            ContentType="image/png",
            Metadata={
                "sha256": hashlib.sha256(b"conflicting object").hexdigest(),
                "upload-session-id": "another-session",
            },
        )

        finalized = client.post(
            finalize_path,
            headers=finalize_headers,
            json=finalize_body,
        )
        assert finalized.status_code == 202, finalized.text
        assert finalized.json()["upload_session"]["cleanup_operation_id"] is None
        with integration_database.engine.connect() as connection:  # type: ignore[attr-defined]
            facts = (
                connection.execute(
                    text(
                        "SELECT "
                        "(SELECT state FROM upload_sessions "
                        "WHERE id = :upload_session_id) AS session_state, "
                        "(SELECT location FROM asset_objects "
                        "WHERE asset_version_id = (SELECT id FROM asset_versions "
                        "WHERE upload_session_id = :upload_session_id)) AS object_location"
                    ),
                    {"upload_session_id": session["id"]},
                )
                .mappings()
                .one()
            )
        assert facts == {
            "session_state": "FINALIZED",
            "object_location": "QUARANTINE",
        }
    assert minio_client.head_object(  # type: ignore[attr-defined]
        Bucket=destination["destination_bucket"],
        Key=destination["destination_key"],
    )["ContentLength"] == len(b"conflicting object")


def test_minio_copy_retry_after_lost_response_returns_same_destination(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del integration_database
    storage = build_object_storage(upload_settings)
    content = VALID_PNG
    sha256 = hashlib.sha256(content).hexdigest()
    source = ObjectReference(
        location=StorageLocationClass.QUARANTINE,
        key=f"integration/copy-source/{new_uuid7()}",
    )
    destination = ObjectReference(
        location=StorageLocationClass.FOUNDATION,
        key=f"integration/copy-destination/{new_uuid7()}",
    )
    minio_client.put_object(  # type: ignore[attr-defined]
        Bucket=upload_settings.object_store_quarantine_bucket,
        Key=source.key,
        Body=content,
        ContentType="image/png",
        Metadata={"sha256": sha256},
    )
    source_stat = storage.stat(source)
    request = ConditionalCopyRequest(
        source=source_stat.reference,
        destination=destination,
        source_etag=source_stat.etag,
        expected_content_length=len(content),
        expected_sha256=sha256,
        content_type="image/png",
        upload_session_id="019f8a00-0000-7000-8000-000000000001",
    )

    first = storage.copy_if_absent(request)
    recovered = storage.copy_if_absent(request)

    assert recovered.etag == first.etag
    assert recovered.content_length == len(content)
    assert recovered.metadata["sha256"] == sha256
    assert recovered.metadata["upload-session-id"] == request.upload_session_id

    temporary = storage.temporary_read(
        TemporaryReadRequest(
            reference=recovered.reference,
            expires_at=datetime.now(UTC) + timedelta(seconds=30),
            expected_etag=recovered.etag,
        )
    )
    downloaded = httpx.get(temporary.url, headers=temporary.required_headers)
    assert downloaded.status_code == 200
    assert downloaded.content == content

    with pytest.raises(StoragePreconditionError):
        storage.delete_if_match(
            ConditionalDeleteRequest(
                reference=recovered.reference,
                expected_etag='"different-etag"',
            )
        )
    assert storage.delete_if_match(
        ConditionalDeleteRequest(
            reference=recovered.reference,
            expected_etag=recovered.etag,
        )
    )
    with pytest.raises(UploadObjectMissingError):
        storage.stat(recovered.reference)


def test_validation_event_crosses_durable_worker_and_duplicate_delivery_seam(
    integration_database: object,
    upload_settings: Settings,
    minio_client: object,
) -> None:
    del minio_client
    with TestClient(create_app(upload_settings)) as client:
        session = _create_session(client, idempotency_key="create-worker-event-0001")
        _direct_upload(session)
        finalized = client.post(
            f"/api/v1/upload-sessions/{session['id']}:finalize",
            headers=_headers("finalize-worker-event-0001"),
            json={"expected_version": session["version"]},
        )
        assert finalized.status_code == 202, finalized.text
        operation_id = finalized.json()["validation_operation"]["id"]

        with SqlAlchemyUnitOfWork(
            integration_database.session_factory  # type: ignore[attr-defined]
        ) as uow:
            events = uow.outbox.list_for_aggregate(operation_id)
        validation_events = [
            event for event in events if event.envelope.event_type == "asset.validation.requested"
        ]
        assert len(validation_events) == 1

        executor = SuccessfulAssetValidationExecutor()
        worker = WorkerRuntime.build(
            upload_settings,
            operation_executors={OperationKind.ASSET_VALIDATION: executor},
        )
        try:
            event_id = validation_events[0].envelope.event_id
            assert worker.process_event(event_id) == "processed"
            assert worker.process_event(event_id) == "duplicate"
        finally:
            worker.close()

        operation = client.get(
            f"/api/v1/operations/{operation_id}",
            headers=_read_headers(),
        )
        assert operation.status_code == 200, operation.text
        assert operation.json()["state"] == "SUCCEEDED"
        assert len(executor.requests) == 1
        assert executor.requests[0].input_ref == (
            f"mysql://asset-versions/{finalized.json()['asset_version']['id']}"
        )
