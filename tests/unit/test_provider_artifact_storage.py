from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from commercevision_contracts.object_storage import (
    ConditionalDeleteRequest,
    ConditionalWriteRequest,
    DeleteMarkerRequest,
    ObjectReference,
    ObjectStat,
    ServerSideEncryptionState,
)
from commercevision_contracts.product_briefs import (
    PROVIDER_ARTIFACT_KEY_SCHEMA_VERSION,
    ProviderArtifactKind,
    ProviderArtifactWrite,
    ProviderArtifactWriteSafeToRetryError,
)
from commercevision_domain import (
    RetentionClass,
    StorageBackend,
    StorageLocationClass,
    StoragePreconditionError,
    StorageWriteSafeToRetryError,
)
from commercevision_object_storage import (
    ObjectStorageProviderArtifactSink,
    ObjectStorageProviderArtifactTarget,
    ObjectStorageProviderArtifactTargetRegistry,
)


class CapturingStorage:
    backend = StorageBackend.MINIO
    server_side_encryption = ServerSideEncryptionState.AES256

    def __init__(self) -> None:
        self.request: ConditionalWriteRequest | None = None
        self.delete_request: ConditionalDeleteRequest | None = None
        self.delete_marker_request: DeleteMarkerRequest | None = None

    def configured_bucket(self, location: StorageLocationClass) -> str:
        assert location == StorageLocationClass.PROVIDER_RESULT
        return "provider-results"

    def delete_if_match(self, request: ConditionalDeleteRequest) -> bool:
        self.delete_request = request
        return True

    def delete_marker(self, request: DeleteMarkerRequest) -> bool:
        self.delete_marker_request = request
        return True

    def write_if_absent(self, request: ConditionalWriteRequest) -> ObjectStat:
        self.request = request
        return ObjectStat(
            reference=request.reference.model_copy(update={"version_id": "version-1"}),
            backend=self.backend,
            bucket="provider-results",
            etag='"provider-etag"',
            content_length=len(request.payload),
            content_type=request.content_type,
            checksum_sha256_base64=None,
            metadata={**request.metadata, "sha256": request.expected_sha256},
            last_modified=datetime(2026, 7, 28, tzinfo=UTC),
            server_side_encryption=self.server_side_encryption,
        )


class UnversionedStorage(CapturingStorage):
    def write_if_absent(self, request: ConditionalWriteRequest) -> ObjectStat:
        stat = super().write_if_absent(request)
        return stat.model_copy(
            update={
                "reference": stat.reference.model_copy(update={"version_id": None}),
            }
        )


class PlaintextStorage(CapturingStorage):
    server_side_encryption = ServerSideEncryptionState.NONE


class PreWriteUnavailableStorage(CapturingStorage):
    def write_if_absent(self, request: ConditionalWriteRequest) -> ObjectStat:
        del request
        raise StorageWriteSafeToRetryError("storage preflight failed")


@pytest.mark.parametrize(
    ("retention_class", "retention_deadline", "expected_deadline"),
    [
        (
            RetentionClass.TASK,
            datetime(2026, 7, 31, tzinfo=UTC),
            "2026-07-31T00:00:00+00:00",
        ),
        (RetentionClass.FOUNDATION, None, "foundation"),
    ],
)
def test_provider_artifact_sink_requires_encryption_and_preserves_retention(
    retention_class: RetentionClass,
    retention_deadline: datetime | None,
    expected_deadline: str,
) -> None:
    payload = b'{"provider":"raw"}'
    storage = CapturingStorage()
    sink = ObjectStorageProviderArtifactSink(  # type: ignore[arg-type]
        storage,
        bucket="provider-results",
    )
    artifact = ProviderArtifactWrite(
        operation_id="019f9aaa-0000-7000-8000-000000000001",
        operation_attempt=2,
        call_index=1,
        kind=ProviderArtifactKind.RESPONSE,
        content_type="application/json",
        payload=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
        retention_class=retention_class,
        retention_deadline=retention_deadline,
    )

    target = sink.prepare(
        artifact,
        ledger_id="019f9aaa-0000-7000-8000-000000000099",
        write_fence="a" * 64,
    )
    assert storage.request is None
    assert target.key_schema_version == PROVIDER_ARTIFACT_KEY_SCHEMA_VERSION
    assert target.bucket == "provider-results"
    assert target.expected_sha256 == artifact.sha256
    assert target.expected_byte_size == len(payload)

    reference = sink.write_prepared(artifact, target)

    assert storage.request is not None
    assert storage.request.require_encryption is True
    assert storage.request.reference == ObjectReference(
        location=StorageLocationClass.PROVIDER_RESULT,
        key=("product-brief/019f9aaa-0000-7000-8000-000000000001/attempt-2/call-1/response.json"),
    )
    assert storage.request.metadata["call-index"] == "1"
    assert storage.request.metadata["retention-class"] == retention_class.value
    assert storage.request.metadata["retention-deadline"] == expected_deadline
    assert storage.request.metadata["artifact-ledger-id"] == "019f9aaa-0000-7000-8000-000000000099"
    assert (
        storage.request.metadata["artifact-key-schema-version"]
        == PROVIDER_ARTIFACT_KEY_SCHEMA_VERSION
    )
    assert storage.request.metadata["artifact-write-fence"] == "a" * 64
    assert storage.request.metadata["artifact-target-sha256"] == target.target_sha256
    assert reference.retention_class == retention_class
    assert reference.retention_deadline == retention_deadline
    assert reference.provider_version_id == "version-1"


def test_provider_artifact_sink_rejects_storage_without_an_exact_object_version() -> None:
    payload = b'{"provider":"raw"}'
    sink = ObjectStorageProviderArtifactSink(  # type: ignore[arg-type]
        UnversionedStorage(),
        bucket="provider-results",
    )
    artifact = ProviderArtifactWrite(
        operation_id="019f9aaa-0000-7000-8000-000000000001",
        operation_attempt=1,
        call_index=0,
        kind=ProviderArtifactKind.REQUEST,
        content_type="application/json",
        payload=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
        retention_class=RetentionClass.TASK,
        retention_deadline=datetime(2026, 7, 31, tzinfo=UTC),
    )
    target = sink.prepare(
        artifact,
        ledger_id="019f9aaa-0000-7000-8000-000000000099",
        write_fence="b" * 64,
    )

    with pytest.raises(StoragePreconditionError, match="exact object versioning"):
        sink.write_prepared(artifact, target)


def test_provider_artifact_sink_rejects_storage_without_encryption_evidence() -> None:
    payload = b'{"provider":"raw"}'
    sink = ObjectStorageProviderArtifactSink(  # type: ignore[arg-type]
        PlaintextStorage(),
        bucket="provider-results",
    )
    artifact = ProviderArtifactWrite(
        operation_id="019f9aaa-0000-7000-8000-000000000001",
        operation_attempt=1,
        call_index=0,
        kind=ProviderArtifactKind.REQUEST,
        content_type="application/json",
        payload=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
        retention_class=RetentionClass.TASK,
        retention_deadline=datetime(2026, 7, 31, tzinfo=UTC),
    )
    target = sink.prepare(
        artifact,
        ledger_id="019f9aaa-0000-7000-8000-000000000099",
        write_fence="c" * 64,
    )

    with pytest.raises(StoragePreconditionError, match="server-side encryption"):
        sink.write_prepared(artifact, target)


def test_provider_artifact_sink_preserves_proven_safe_pre_write_failure() -> None:
    payload = b'{"provider":"raw"}'
    sink = ObjectStorageProviderArtifactSink(  # type: ignore[arg-type]
        PreWriteUnavailableStorage(),
        bucket="provider-results",
    )
    artifact = ProviderArtifactWrite(
        operation_id="019f9aaa-0000-7000-8000-000000000001",
        operation_attempt=1,
        call_index=0,
        kind=ProviderArtifactKind.REQUEST,
        content_type="application/json",
        payload=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
        retention_class=RetentionClass.TASK,
        retention_deadline=datetime(2026, 7, 31, tzinfo=UTC),
    )
    target = sink.prepare(
        artifact,
        ledger_id="019f9aaa-0000-7000-8000-000000000099",
        write_fence="d" * 64,
    )

    with pytest.raises(
        ProviderArtifactWriteSafeToRetryError,
        match="before write",
    ):
        sink.write_prepared(artifact, target)


def test_provider_artifact_registry_deletes_only_the_frozen_exact_target() -> None:
    storage = CapturingStorage()
    sink = ObjectStorageProviderArtifactSink(storage, bucket="provider-results")  # type: ignore[arg-type]
    artifact = ProviderArtifactWrite(
        operation_id="019f9aaa-0000-7000-8000-000000000001",
        operation_attempt=1,
        call_index=0,
        kind=ProviderArtifactKind.REQUEST,
        content_type="application/json",
        payload=b"{}",
        sha256=hashlib.sha256(b"{}").hexdigest(),
        retention_class=RetentionClass.TASK,
        retention_deadline=datetime(2026, 7, 31, tzinfo=UTC),
    )
    target = sink.prepare(
        artifact,
        ledger_id="019f9aaa-0000-7000-8000-000000000099",
        write_fence="e" * 64,
    )
    registry = ObjectStorageProviderArtifactTargetRegistry(
        (ObjectStorageProviderArtifactTarget(storage=storage, bucket="provider-results"),)  # type: ignore[arg-type]
    )
    exact = ObjectReference(
        location=target.location,
        key=target.key,
        version_id="version-1",
    )

    assert registry.delete_if_match(target, exact, expected_etag='"etag"') is True
    assert registry.delete_marker(target, exact) is True
    assert storage.delete_request == ConditionalDeleteRequest(
        reference=exact,
        expected_etag='"etag"',
    )
    assert storage.delete_marker_request == DeleteMarkerRequest(reference=exact)

    with pytest.raises(StoragePreconditionError, match="frozen target"):
        registry.delete_if_match(
            target,
            exact.model_copy(update={"key": "different"}),
            expected_etag='"etag"',
        )
