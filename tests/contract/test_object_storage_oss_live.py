from __future__ import annotations

import base64
import hashlib
import os
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx
import pytest
from commercevision_contracts import Settings
from commercevision_contracts.object_storage import (
    BoundedReadRequest,
    ConditionalCopyRequest,
    ConditionalDeleteRequest,
    ObjectReference,
    PresignPutRequest,
    TemporaryReadRequest,
)
from commercevision_domain import (
    OperationKind,
    StorageBackend,
    StorageLocationClass,
    StoragePreconditionError,
    UploadObjectMissingError,
    new_uuid7,
)
from commercevision_object_storage import build_object_storage, close_object_storage

pytestmark = [pytest.mark.integration, pytest.mark.live_oss]

_REQUIRED_ENVIRONMENT = (
    "CV_OBJECT_STORE_BACKEND",
    "CV_OBJECT_STORE_CREDENTIAL_MODE",
    "CV_OBJECT_STORE_ENDPOINT",
    "CV_OBJECT_STORE_PRESIGN_ENDPOINT",
    "CV_OBJECT_STORE_REGION",
    "CV_OBJECT_STORE_FORCE_PATH_STYLE",
    "CV_OBJECT_STORE_REQUIRE_ENCRYPTION",
    "CV_OBJECT_STORE_QUARANTINE_BUCKET",
    "CV_OBJECT_STORE_TASK_BUCKET",
    "CV_OBJECT_STORE_FOUNDATION_BUCKET",
    "CV_OBJECT_STORE_PROVIDER_RESULT_BUCKET",
)


def _live_settings() -> Settings:
    if os.getenv("CV_TEST_OSS_LIVE") != "1":
        pytest.skip("set CV_TEST_OSS_LIVE=1 to run the real Alibaba OSS contract")
    missing = [name for name in _REQUIRED_ENVIRONMENT if not os.getenv(name)]
    if missing:
        pytest.fail("live OSS gate is missing environment: " + ", ".join(missing))
    if os.environ["CV_OBJECT_STORE_BACKEND"] != "oss":
        pytest.fail("live OSS gate requires CV_OBJECT_STORE_BACKEND=oss")
    credential_mode = os.environ["CV_OBJECT_STORE_CREDENTIAL_MODE"]
    identity_environment = {
        "ecs_ram_role": (),
        "oidc_role_arn": (
            "CV_OBJECT_STORE_OIDC_ROLE_ARN",
            "CV_OBJECT_STORE_OIDC_PROVIDER_ARN",
            "CV_OBJECT_STORE_OIDC_TOKEN_FILE_PATH",
            "CV_OBJECT_STORE_STS_ENDPOINT",
        ),
    }
    if credential_mode not in identity_environment:
        pytest.fail("live OSS gate requires ecs_ram_role or oidc_role_arn credentials")
    missing_identity = [
        name for name in identity_environment[credential_mode] if not os.getenv(name)
    ]
    if missing_identity:
        pytest.fail(
            "live OSS gate is missing workload identity environment: " + ", ".join(missing_identity)
        )
    settings = Settings(
        environment="production",
        object_store_backend="oss",
        worker_required_operation_kinds=[OperationKind.ASSET_VALIDATION],
    )
    internal = urlsplit(settings.object_store_endpoint)
    browser = urlsplit(settings.object_store_presign_endpoint or settings.object_store_endpoint)
    assert internal.scheme == "https"
    assert browser.scheme == "https"
    assert (internal.scheme, internal.netloc) != (browser.scheme, browser.netloc)
    assert len(set(settings.object_store_buckets.values())) == 4
    assert not settings.object_store_force_path_style
    assert settings.object_store_require_encryption
    return settings


def _direct_put(
    client: httpx.Client,
    *,
    storage: object,
    reference: ObjectReference,
    payload: bytes,
    upload_session_id: str,
) -> None:
    checksum = base64.b64encode(hashlib.sha256(payload).digest()).decode("ascii")
    signed = storage.presign_put(  # type: ignore[attr-defined]
        PresignPutRequest(
            reference=reference,
            content_type="image/png",
            content_length=len(payload),
            checksum_sha256_base64=checksum,
            upload_session_id=upload_session_id,
            expires_at=datetime.now(UTC) + timedelta(minutes=2),
        )
    )
    headers = {
        name: value
        for name, value in signed.required_headers.items()
        if name.lower() != "content-length"
    }
    response = client.put(signed.url, headers=headers, content=payload)
    assert 200 <= response.status_code < 300


def test_real_oss_adapter_contract_is_an_explicit_production_gate(
    request: pytest.FixtureRequest,
) -> None:
    settings = _live_settings()
    storage = build_object_storage(settings)
    request.addfinalizer(lambda: close_object_storage(storage))
    assert storage.backend == StorageBackend.OSS
    storage.assert_ready()

    payload = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    digest = hashlib.sha256(payload).hexdigest()
    session_id = new_uuid7()
    prefix = f"ticket04-live/{session_id}"
    source = ObjectReference(
        location=StorageLocationClass.QUARANTINE,
        key=f"{prefix}/source",
    )
    destination = ObjectReference(
        location=StorageLocationClass.FOUNDATION,
        key=f"{prefix}/destination",
    )
    conflict = ObjectReference(
        location=StorageLocationClass.FOUNDATION,
        key=f"{prefix}/conflict",
    )
    cleanup: list[tuple[ObjectReference, str]] = []

    with httpx.Client(timeout=30) as client:
        try:
            _direct_put(
                client,
                storage=storage,
                reference=source,
                payload=payload,
                upload_session_id=session_id,
            )
            source_stat = storage.stat(source)
            cleanup.append((source_stat.reference, source_stat.etag))
            assert source_stat.reference.version_id
            with storage.open_bounded_read(
                BoundedReadRequest(
                    reference=source,
                    maximum_bytes=len(payload),
                    expected_etag=source_stat.etag,
                )
            ) as chunks:
                assert b"".join(chunks) == payload

            copied = storage.copy_if_absent(
                ConditionalCopyRequest(
                    source=source_stat.reference,
                    destination=destination,
                    source_etag=source_stat.etag,
                    expected_content_length=len(payload),
                    expected_sha256=digest,
                    content_type="image/png",
                    upload_session_id=session_id,
                )
            )
            cleanup.append((copied.reference, copied.etag))
            assert copied.reference.version_id
            assert (
                storage.copy_if_absent(
                    ConditionalCopyRequest(
                        source=source_stat.reference,
                        destination=destination,
                        source_etag=source_stat.etag,
                        expected_content_length=len(payload),
                        expected_sha256=digest,
                        content_type="image/png",
                        upload_session_id=session_id,
                    )
                ).reference
                == copied.reference
            )

            _direct_put(
                client,
                storage=storage,
                reference=conflict,
                payload=payload,
                upload_session_id=new_uuid7(),
            )
            conflict_stat = storage.stat(conflict)
            cleanup.append((conflict_stat.reference, conflict_stat.etag))
            with pytest.raises(StoragePreconditionError):
                storage.copy_if_absent(
                    ConditionalCopyRequest(
                        source=source_stat.reference,
                        destination=conflict,
                        source_etag=source_stat.etag,
                        expected_content_length=len(payload),
                        expected_sha256=digest,
                        content_type="image/png",
                        upload_session_id=session_id,
                    )
                )

            temporary = storage.temporary_read(
                TemporaryReadRequest(
                    reference=copied.reference,
                    expected_etag=copied.etag,
                    expires_at=datetime.now(UTC) + timedelta(seconds=30),
                )
            )
            downloaded = client.get(
                temporary.url,
                headers=temporary.required_headers,
            )
            assert downloaded.status_code == 200
            assert downloaded.content == payload

            assert storage.delete_if_match(
                ConditionalDeleteRequest(
                    reference=source_stat.reference,
                    expected_etag=source_stat.etag,
                )
            )
            cleanup.remove((source_stat.reference, source_stat.etag))
            with pytest.raises(UploadObjectMissingError):
                storage.stat(source_stat.reference)
        finally:
            for reference, etag in reversed(cleanup):
                with suppress(Exception):
                    storage.delete_if_match(
                        ConditionalDeleteRequest(
                            reference=reference,
                            expected_etag=etag,
                        )
                    )
