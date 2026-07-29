"""Encrypted object-storage sink for raw provider request and response artifacts."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass

from commercevision_contracts.object_storage import (
    ConditionalWriteRequest,
    ObjectReference,
    ObjectStat,
    ObjectStorage,
    ObjectVersionListRequest,
    ObjectVersionPage,
)
from commercevision_contracts.product_briefs import (
    PROVIDER_ARTIFACT_KEY_SCHEMA_VERSION,
    PreparedProviderArtifact,
    ProviderArtifactPhysicalTarget,
    ProviderArtifactReference,
    ProviderArtifactWrite,
    ProviderArtifactWriteOutcomeUnknownError,
    ProviderArtifactWriteSafeToRetryError,
)
from commercevision_domain import (
    StorageLocationClass,
    StoragePreconditionError,
    StorageUnavailableError,
    StorageWriteOutcomeUnknownError,
    StorageWriteSafeToRetryError,
)

from .object_storage_common import has_verified_server_side_encryption


@dataclass(frozen=True, slots=True)
class ObjectStorageProviderArtifactTarget:
    storage: ObjectStorage
    bucket: str
    location: StorageLocationClass = StorageLocationClass.PROVIDER_RESULT

    def __post_init__(self) -> None:
        if not self.bucket or len(self.bucket) > 255:
            raise ValueError("provider artifact bucket must contain 1-255 characters")
        configured_bucket = getattr(self.storage, "configured_bucket", None)
        if not callable(configured_bucket):
            raise TypeError(
                "provider artifact reconciliation requires a physical-target storage adapter"
            )
        if configured_bucket(self.location) != self.bucket:
            raise ValueError("provider artifact target bucket does not match storage adapter")

    @property
    def physical_target(self) -> ProviderArtifactPhysicalTarget:
        return ProviderArtifactPhysicalTarget(
            storage_backend=self.storage.backend,
            location=self.location,
            bucket=self.bucket,
        )


class ObjectStorageProviderArtifactTargetRegistry:
    """Route reconciliation to an explicitly registered physical storage target."""

    def __init__(self, targets: Iterable[ObjectStorageProviderArtifactTarget]) -> None:
        self._targets: dict[
            tuple[str, StorageLocationClass, str],
            ObjectStorageProviderArtifactTarget,
        ] = {}
        for target in targets:
            identity = (
                target.storage.backend.value,
                target.location,
                target.bucket,
            )
            if identity in self._targets:
                raise ValueError("duplicate provider artifact physical target")
            self._targets[identity] = target

    def list_versions(
        self,
        target: PreparedProviderArtifact,
        *,
        page_size: int,
        continuation_token: str | None,
    ) -> ObjectVersionPage:
        registered = self._resolve(target)
        return registered.storage.list_versions(
            ObjectVersionListRequest(
                reference=ObjectReference(
                    location=target.location,
                    key=target.key,
                ),
                page_size=page_size,
                continuation_token=continuation_token,
            )
        )

    def stat(
        self,
        target: PreparedProviderArtifact,
        reference: ObjectReference,
    ) -> ObjectStat:
        if (
            reference.location != target.location
            or reference.key != target.key
            or reference.version_id is None
        ):
            raise StoragePreconditionError(
                "provider artifact reconciliation reference changed from its frozen target"
            )
        registered = self._resolve(target)
        stat = registered.storage.stat(reference)
        if (
            stat.backend.value != target.storage_backend
            or stat.reference.location != target.location
            or stat.reference.key != target.key
            or stat.bucket != target.bucket
        ):
            raise StoragePreconditionError(
                "provider artifact reconciliation resolved a different physical target"
            )
        return stat

    def _resolve(
        self,
        target: PreparedProviderArtifact,
    ) -> ObjectStorageProviderArtifactTarget:
        identity = (
            target.storage_backend,
            target.location,
            target.bucket,
        )
        try:
            return self._targets[identity]
        except KeyError as exc:
            raise StoragePreconditionError(
                "provider artifact physical target is not registered"
            ) from exc


class ObjectStorageProviderArtifactSink:
    def __init__(self, storage: ObjectStorage, *, bucket: str) -> None:
        if not bucket or len(bucket) > 255:
            raise ValueError("provider artifact bucket must contain 1-255 characters")
        self._storage = storage
        self._bucket = bucket

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
        target_sha256 = _target_sha256(
            storage_backend=self._storage.backend.value,
            location=StorageLocationClass.PROVIDER_RESULT,
            bucket=self._bucket,
            key=key,
        )
        return PreparedProviderArtifact(
            ledger_id=ledger_id,
            key_schema_version=PROVIDER_ARTIFACT_KEY_SCHEMA_VERSION,
            storage_backend=self._storage.backend.value,
            location=StorageLocationClass.PROVIDER_RESULT,
            bucket=self._bucket,
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
        expected_target = self.prepare(
            artifact,
            ledger_id=target.ledger_id,
            write_fence=target.write_fence,
        )
        if expected_target != target:
            raise StoragePreconditionError(
                "provider artifact write changed after its target was frozen"
            )
        retention_deadline = (
            artifact.retention_deadline.isoformat()
            if artifact.retention_deadline is not None
            else "foundation"
        )
        try:
            stat = self._storage.write_if_absent(
                ConditionalWriteRequest(
                    reference=ObjectReference(
                        location=target.location,
                        key=target.key,
                    ),
                    payload=artifact.payload,
                    expected_sha256=artifact.sha256,
                    content_type=artifact.content_type,
                    require_encryption=True,
                    metadata={
                        "artifact-kind": artifact.kind.value,
                        "artifact-key-schema-version": target.key_schema_version,
                        "artifact-ledger-id": target.ledger_id,
                        "artifact-target-sha256": target.target_sha256,
                        "artifact-write-fence": target.write_fence,
                        "call-index": str(artifact.call_index),
                        "operation-attempt": str(artifact.operation_attempt),
                        "operation-id": artifact.operation_id,
                        "retention-class": artifact.retention_class.value,
                        "retention-deadline": retention_deadline,
                    },
                )
            )
        except StorageWriteSafeToRetryError as exc:
            raise ProviderArtifactWriteSafeToRetryError(
                f"provider artifact storage unavailable before write: {exc}"
            ) from exc
        except StorageWriteOutcomeUnknownError as exc:
            raise ProviderArtifactWriteOutcomeUnknownError(
                f"provider artifact write outcome is unknown: {exc}"
            ) from exc
        except StorageUnavailableError as exc:
            raise ProviderArtifactWriteOutcomeUnknownError(
                f"provider artifact write outcome could not be proven: {exc}"
            ) from exc
        if stat.reference.version_id is None:
            raise StoragePreconditionError(
                "provider artifact storage requires exact object versioning"
            )
        if not has_verified_server_side_encryption(stat):
            raise StoragePreconditionError(
                "provider artifact storage requires verified server-side encryption"
            )
        if not self.stat_matches(target, stat):
            raise StoragePreconditionError(
                "stored provider artifact does not match its frozen target"
            )
        return ProviderArtifactReference(
            storage_backend=stat.backend.value,
            location=stat.reference.location,
            bucket=stat.bucket,
            key=stat.reference.key,
            provider_version_id=stat.reference.version_id,
            etag=stat.etag,
            sha256=artifact.sha256,
            byte_size=stat.content_length,
            retention_class=artifact.retention_class,
            retention_deadline=artifact.retention_deadline,
        )

    def stat_matches(
        self,
        target: PreparedProviderArtifact,
        stat: ObjectStat,
    ) -> bool:
        expected_metadata = {
            "artifact-key-schema-version": target.key_schema_version,
            "artifact-ledger-id": target.ledger_id,
            "artifact-target-sha256": target.target_sha256,
            "artifact-write-fence": target.write_fence,
            "sha256": target.expected_sha256,
        }
        return (
            stat.backend.value == target.storage_backend
            and stat.reference.location == target.location
            and stat.reference.key == target.key
            and stat.reference.version_id is not None
            and stat.bucket == target.bucket
            and bool(stat.etag)
            and stat.content_length == target.expected_byte_size
            and stat.content_type == target.content_type
            and all(stat.metadata.get(name) == value for name, value in expected_metadata.items())
            and has_verified_server_side_encryption(stat)
        )

    def write(self, artifact: ProviderArtifactWrite) -> ProviderArtifactReference:
        raise RuntimeError("provider artifact writes require a durable ledger coordinator")


def _target_sha256(
    *,
    storage_backend: str,
    location: StorageLocationClass,
    bucket: str,
    key: str,
) -> str:
    identity = "\0".join((storage_backend, location.value, bucket, key))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()
