from datetime import UTC, datetime

from commercevision_contracts.object_storage import (
    ObjectReference,
    ObjectStat,
    ObjectVersionEntry,
    ObjectVersionPage,
)
from commercevision_contracts.product_briefs import PreparedProviderArtifact
from commercevision_domain import (
    RetentionClass,
    StorageBackend,
    StorageLocationClass,
    UploadObjectMissingError,
)
from commercevision_persistence.asset_provider_artifact_deletion import (
    ProviderArtifactDeletionConverger,
    ProviderArtifactDeletionTarget,
)


class _ProviderStore:
    def __init__(self) -> None:
        self.pages = [
            ObjectVersionPage(
                entries=(
                    ObjectVersionEntry(
                        reference=ObjectReference(
                            location=StorageLocationClass.PROVIDER_RESULT,
                            key="provider/key",
                            version_id="object-version",
                        ),
                        kind="OBJECT",
                    ),
                    ObjectVersionEntry(
                        reference=ObjectReference(
                            location=StorageLocationClass.PROVIDER_RESULT,
                            key="provider/key",
                            version_id="delete-marker",
                        ),
                        kind="DELETE_MARKER",
                    ),
                ),
                continuation_token=None,
            ),
            ObjectVersionPage(entries=(), continuation_token=None),
        ]
        self.deleted_objects: list[str] = []
        self.deleted_markers: list[str] = []
        self.list_calls = 0
        self.missing_versions: set[str] = set()

    def list_versions(self, *_args: object, **_kwargs: object) -> ObjectVersionPage:
        self.list_calls += 1
        if self.pages:
            return self.pages.pop(0)
        return ObjectVersionPage(entries=(), continuation_token=None)

    def stat(
        self,
        _target: PreparedProviderArtifact,
        reference: ObjectReference,
    ) -> ObjectStat:
        if reference.version_id in self.missing_versions:
            raise UploadObjectMissingError("already deleted")
        return ObjectStat(
            reference=reference,
            backend=StorageBackend.MINIO,
            bucket="provider-results",
            etag='"etag"',
            content_length=2,
            content_type="application/json",
            checksum_sha256_base64=None,
            metadata={},
            last_modified=datetime(2026, 8, 4, tzinfo=UTC),
        )

    def delete_if_match(
        self,
        _target: PreparedProviderArtifact,
        reference: ObjectReference,
        *,
        expected_etag: str,
    ) -> bool:
        assert expected_etag == '"etag"'
        assert reference.version_id is not None
        self.deleted_objects.append(reference.version_id)
        return True

    def delete_marker(
        self,
        _target: PreparedProviderArtifact,
        reference: ObjectReference,
    ) -> bool:
        assert reference.version_id is not None
        self.deleted_markers.append(reference.version_id)
        return True


def test_unknown_provider_artifact_sweeps_versions_until_two_stable_empty_passes() -> None:
    provider = _ProviderStore()
    converger = ProviderArtifactDeletionConverger(
        store=provider,
        version_page_size=10,
        max_version_pages=10,
        max_versions=10,
        stable_empty_passes=2,
    )
    target = PreparedProviderArtifact(
        ledger_id="019fc8b5-82b7-7000-8000-000000000001",
        key_schema_version="provider-artifact-v1",
        storage_backend="MINIO",
        location=StorageLocationClass.PROVIDER_RESULT,
        bucket="provider-results",
        key="provider/key",
        target_sha256="a" * 64,
        content_type="application/json",
        expected_sha256="b" * 64,
        expected_byte_size=2,
        retention_class=RetentionClass.TASK,
        retention_deadline=datetime(2026, 8, 4, tzinfo=UTC),
        write_fence="c" * 64,
    )

    converger.converge(
        ProviderArtifactDeletionTarget(
            id=target.ledger_id,
            state="UNKNOWN",
            target=target,
            provider_version_id=None,
            etag=None,
        )
    )

    assert provider.deleted_objects == ["object-version"]
    assert provider.deleted_markers == ["delete-marker"]
    assert provider.pages == []
    assert provider.list_calls == 3


def test_unknown_provider_artifact_treats_version_missing_after_listing_as_converged() -> None:
    provider = _ProviderStore()
    provider.missing_versions.add("object-version")
    converger = ProviderArtifactDeletionConverger(
        store=provider,
        version_page_size=10,
        max_version_pages=10,
        max_versions=10,
        stable_empty_passes=2,
    )
    target = PreparedProviderArtifact(
        ledger_id="019fc8b5-82b7-7000-8000-000000000001",
        key_schema_version="provider-artifact-v1",
        storage_backend="MINIO",
        location=StorageLocationClass.PROVIDER_RESULT,
        bucket="provider-results",
        key="provider/key",
        target_sha256="a" * 64,
        content_type="application/json",
        expected_sha256="b" * 64,
        expected_byte_size=2,
        retention_class=RetentionClass.TASK,
        retention_deadline=datetime(2026, 8, 4, tzinfo=UTC),
        write_fence="c" * 64,
    )

    converger.converge(
        ProviderArtifactDeletionTarget(
            id=target.ledger_id,
            state="INTENDED",
            target=target,
            provider_version_id=None,
            etag=None,
        )
    )

    assert provider.deleted_objects == []
    assert provider.deleted_markers == ["delete-marker"]
    assert provider.list_calls == 3
