"""Idempotent promotion of verified uploads into retained object storage."""

from __future__ import annotations

from commercevision_contracts.object_storage import (
    ConditionalCopyRequest,
    ConditionalDeleteRequest,
    ObjectReference,
    ObjectStat,
    ObjectStorage,
)
from commercevision_domain import (
    ObjectMismatchError,
    StoragePreconditionError,
    StorageUnavailableError,
    UploadObjectMissingError,
    UploadSession,
)

from .asset_integrity import ImageUploadIntegrityVerifier, VerifiedUpload


class UploadPromoter:
    """Verify, conditionally copy, and recover uploads without a MySQL transaction."""

    def __init__(
        self,
        *,
        storage: ObjectStorage,
        verifier: ImageUploadIntegrityVerifier,
    ) -> None:
        self._storage = storage
        self._verifier = verifier

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
                    content_type=source_verified.detected_mime,
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

        self._delete_source_best_effort(upload_session, source_stat=source_stat)
        return verified

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

    def _delete_source_best_effort(
        self,
        upload_session: UploadSession,
        *,
        source_stat: ObjectStat | None,
    ) -> None:
        self._delete_owned_object_best_effort(
            upload_session,
            reference=ObjectReference(
                location=upload_session.storage_location,
                key=upload_session.storage_key,
            ),
            expected_bucket=upload_session.storage_bucket,
            known_stat=source_stat,
        )

    def _delete_owned_object_best_effort(
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
            except (UploadObjectMissingError, StorageUnavailableError):
                return
        if (
            object_stat.reference.location != reference.location
            or object_stat.reference.key != reference.key
            or object_stat.bucket != expected_bucket
            or object_stat.content_length != upload_session.expected_byte_length
            or object_stat.metadata.get("upload-session-id") != upload_session.id
        ):
            return
        try:
            self._storage.delete_if_match(
                ConditionalDeleteRequest(
                    reference=object_stat.reference,
                    expected_etag=object_stat.etag,
                )
            )
        except (
            StoragePreconditionError,
            StorageUnavailableError,
            UploadObjectMissingError,
        ):
            return
