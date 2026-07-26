"""Idempotent promotion of verified uploads into retained object storage."""

from __future__ import annotations

import base64

from commercevision_contracts.object_storage import (
    ConditionalCopyRequest,
    ConditionalDeleteRequest,
    DeleteMarkerRequest,
    ObjectReference,
    ObjectStat,
    ObjectStorage,
    ObjectVersionListRequest,
)
from commercevision_domain import (
    ObjectMismatchError,
    StoragePreconditionError,
    StorageUnavailableError,
    UploadObjectMissingError,
    UploadSession,
)

from .asset_integrity import UploadIntegrityVerifier, VerifiedUpload


class UploadPromoter:
    """Verify, conditionally copy, and recover uploads without a MySQL transaction."""

    def __init__(
        self,
        *,
        storage: ObjectStorage,
        verifier: UploadIntegrityVerifier,
        retention_version_page_size: int = 100,
        retention_max_version_pages: int = 50,
        retention_max_versions: int = 1000,
        retention_stable_empty_passes: int = 2,
    ) -> None:
        if not 1 <= retention_version_page_size <= 1000:
            raise ValueError("retention version page size must be between 1 and 1000")
        if retention_max_version_pages < retention_stable_empty_passes:
            raise ValueError("retention maximum version pages must cover stable empty scans")
        if retention_max_versions < 1:
            raise ValueError("retention maximum versions must be positive")
        if retention_stable_empty_passes < 2:
            raise ValueError("retention cleanup requires at least two empty scans")
        self._storage = storage
        self._verifier = verifier
        self._retention_version_page_size = retention_version_page_size
        self._retention_max_version_pages = retention_max_version_pages
        self._retention_max_versions = retention_max_versions
        self._retention_stable_empty_passes = retention_stable_empty_passes

    def verify_and_promote(self, upload_session: UploadSession) -> VerifiedUpload:
        source = ObjectReference(
            location=upload_session.storage_location,
            key=upload_session.storage_key,
        )
        destination = ObjectReference(
            location=upload_session.destination_location,
            key=upload_session.destination_key,
        )
        try:
            destination_stat = self._storage.stat(destination)
        except UploadObjectMissingError:
            source_verified = self._verifier.verify(
                upload_session,
                reference=source,
                expected_bucket=upload_session.storage_bucket,
            )
            destination_stat = self._storage.copy_if_absent(
                ConditionalCopyRequest(
                    source=source_verified.stat.reference,
                    destination=destination,
                    source_etag=source_verified.stat.etag,
                    expected_content_length=source_verified.byte_size,
                    expected_sha256=source_verified.sha256,
                    content_type=upload_session.declared_mime,
                    upload_session_id=upload_session.id,
                )
            )
            self._assert_destination_stat(upload_session, destination_stat)
            verified = VerifiedUpload(
                stat=destination_stat,
                sha256=source_verified.sha256,
                byte_size=source_verified.byte_size,
                detected_mime=source_verified.detected_mime,
                image_format=source_verified.image_format,
                width=source_verified.width,
                height=source_verified.height,
                frame_count=source_verified.frame_count,
            )
            source_stat = source_verified.stat
        else:
            self._assert_destination_stat(upload_session, destination_stat)
            try:
                verified = self._verifier.verify(
                    upload_session,
                    reference=destination_stat.reference,
                    expected_bucket=upload_session.destination_bucket,
                )
            except ObjectMismatchError as exc:
                raise StoragePreconditionError(
                    "promoted object no longer matches the verified upload"
                ) from exc
            source_stat = None

        self._delete_source(upload_session, source_stat=source_stat)
        return verified

    def discard_for_retention(self, upload_session: UploadSession) -> None:
        """Idempotently remove every exact object owned by an expired Task Asset."""

        self._discard_owned_versions(
            upload_session,
            reference=ObjectReference(
                location=upload_session.destination_location,
                key=upload_session.destination_key,
            ),
            expected_bucket=upload_session.destination_bucket,
        )
        self._discard_owned_versions(
            upload_session,
            reference=ObjectReference(
                location=upload_session.storage_location,
                key=upload_session.storage_key,
            ),
            expected_bucket=upload_session.storage_bucket,
        )

    def _discard_owned_versions(
        self,
        upload_session: UploadSession,
        *,
        reference: ObjectReference,
        expected_bucket: str,
    ) -> None:
        continuation_token: str | None = None
        pages_scanned = 0
        versions_deleted = 0
        empty_scans = 0
        scan_had_entries = False
        seen_tokens: set[str] = set()
        while pages_scanned < self._retention_max_version_pages:
            page = self._storage.list_versions(
                ObjectVersionListRequest(
                    reference=reference,
                    page_size=self._retention_version_page_size,
                    continuation_token=continuation_token,
                )
            )
            pages_scanned += 1
            scan_had_entries = scan_had_entries or bool(page.entries)
            for entry in page.entries:
                if (
                    entry.reference.location != reference.location
                    or entry.reference.key != reference.key
                    or entry.reference.version_id is None
                ):
                    raise StoragePreconditionError(
                        "object version listing escaped the owned exact key"
                    )
                if versions_deleted >= self._retention_max_versions:
                    raise StorageUnavailableError(
                        "retention version cleanup reached its bounded delete budget"
                    )
                if entry.kind == "DELETE_MARKER":
                    self._storage.delete_marker(DeleteMarkerRequest(reference=entry.reference))
                else:
                    self._delete_owned_object(
                        upload_session,
                        reference=entry.reference,
                        expected_bucket=expected_bucket,
                        known_stat=None,
                    )
                versions_deleted += 1
            if page.continuation_token is not None:
                if (
                    page.continuation_token == continuation_token
                    or page.continuation_token in seen_tokens
                ):
                    raise StoragePreconditionError(
                        "object version listing repeated its continuation token"
                    )
                seen_tokens.add(page.continuation_token)
                continuation_token = page.continuation_token
                continue
            continuation_token = None
            seen_tokens.clear()
            if scan_had_entries:
                scan_had_entries = False
                empty_scans = 0
                continue
            empty_scans += 1
            if empty_scans >= self._retention_stable_empty_passes:
                return
        raise StorageUnavailableError("retention version cleanup reached its bounded page budget")

    def reconcile_concurrent_destination(
        self,
        upload_session: UploadSession,
        *,
        canonical_reference: ObjectReference,
        observed: VerifiedUpload,
    ) -> VerifiedUpload:
        """Verify the MySQL winner and remove one same-content duplicate version."""

        if (
            canonical_reference.location != upload_session.destination_location
            or canonical_reference.key != upload_session.destination_key
            or canonical_reference.version_id is None
            or observed.stat.reference.location != canonical_reference.location
            or observed.stat.reference.key != canonical_reference.key
            or observed.stat.reference.version_id is None
            or observed.stat.reference.version_id == canonical_reference.version_id
        ):
            raise StoragePreconditionError(
                "concurrent promotion versions cannot be reconciled safely"
            )
        canonical = self._verifier.verify(
            upload_session,
            reference=canonical_reference,
            expected_bucket=upload_session.destination_bucket,
        )
        self._assert_destination_stat(upload_session, canonical.stat)
        self._assert_destination_stat(upload_session, observed.stat)
        if canonical.sha256 != observed.sha256 or canonical.byte_size != observed.byte_size:
            raise StoragePreconditionError(
                "concurrent promotion versions do not contain the same verified content"
            )
        self._delete_owned_object(
            upload_session,
            reference=observed.stat.reference,
            expected_bucket=upload_session.destination_bucket,
            known_stat=observed.stat,
        )
        return canonical

    @staticmethod
    def _assert_destination_stat(
        upload_session: UploadSession,
        stat: ObjectStat,
    ) -> None:
        if (
            stat.backend != upload_session.storage_backend
            or stat.reference.location != upload_session.destination_location
            or stat.reference.key != upload_session.destination_key
            or stat.bucket != upload_session.destination_bucket
            or stat.content_length != upload_session.expected_byte_length
            or stat.content_type is None
            or stat.content_type.partition(";")[0].strip().lower()
            != upload_session.declared_mime.lower()
            or stat.metadata.get("sha256") != upload_session.expected_sha256
            or stat.metadata.get("upload-session-id") != upload_session.id
            or not stat.etag
        ):
            raise StoragePreconditionError("promoted object does not match the Upload Session")

    def _delete_source(
        self,
        upload_session: UploadSession,
        *,
        source_stat: ObjectStat | None,
    ) -> None:
        self._delete_owned_object(
            upload_session,
            reference=ObjectReference(
                location=upload_session.storage_location,
                key=upload_session.storage_key,
            ),
            expected_bucket=upload_session.storage_bucket,
            known_stat=source_stat,
        )

    def _delete_owned_object(
        self,
        upload_session: UploadSession,
        *,
        reference: ObjectReference,
        expected_bucket: str,
        known_stat: ObjectStat | None,
    ) -> None:
        object_stat = known_stat
        if object_stat is None:
            try:
                object_stat = self._storage.stat(reference)
            except UploadObjectMissingError:
                return
        expected_sha256_values = {
            upload_session.expected_sha256,
            base64.b64encode(bytes.fromhex(upload_session.expected_sha256)).decode("ascii"),
        }
        if (
            object_stat.reference.location != reference.location
            or object_stat.reference.key != reference.key
            or object_stat.bucket != expected_bucket
            or object_stat.content_length != upload_session.expected_byte_length
            or object_stat.metadata.get("upload-session-id") != upload_session.id
            or object_stat.metadata.get("sha256") not in expected_sha256_values
        ):
            raise StoragePreconditionError("quarantine object changed before promotion cleanup")
        try:
            self._storage.delete_if_match(
                ConditionalDeleteRequest(
                    reference=object_stat.reference,
                    expected_etag=object_stat.etag,
                )
            )
        except UploadObjectMissingError:
            return
