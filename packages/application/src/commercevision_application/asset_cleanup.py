"""Transaction-free deletion of objects owned by a terminal Upload Session."""

from commercevision_contracts.object_storage import (
    ConditionalDeleteRequest,
    ObjectReference,
    ObjectStat,
    ObjectStorage,
)
from commercevision_domain import (
    ObjectMismatchError,
    UploadObjectMissingError,
    UploadSession,
    UploadSessionState,
)


class UploadObjectCleaner:
    """Delete obsolete upload objects with exact ownership checks."""

    def __init__(self, storage: ObjectStorage) -> None:
        self._storage = storage

    def cleanup(self, upload_session: UploadSession) -> None:
        for reference, expected_bucket in self._locations(upload_session):
            self._delete_owned(
                upload_session,
                reference=reference,
                expected_bucket=expected_bucket,
            )

    def _delete_owned(
        self,
        upload_session: UploadSession,
        *,
        reference: ObjectReference,
        expected_bucket: str,
    ) -> None:
        try:
            stat = self._storage.stat(reference)
        except UploadObjectMissingError:
            return
        self._assert_owned(
            upload_session,
            stat=stat,
            reference=reference,
            expected_bucket=expected_bucket,
        )
        try:
            self._storage.delete_if_match(
                ConditionalDeleteRequest(
                    reference=stat.reference,
                    expected_etag=stat.etag,
                )
            )
        except UploadObjectMissingError:
            return

    @staticmethod
    def _locations(
        upload_session: UploadSession,
    ) -> tuple[tuple[ObjectReference, str], ...]:
        source = (
            ObjectReference(
                location=upload_session.storage_location,
                key=upload_session.storage_key,
            ),
            upload_session.storage_bucket,
        )
        if upload_session.state == UploadSessionState.FINALIZED:
            return (source,)
        destination = (
            ObjectReference(
                location=upload_session.destination_location,
                key=upload_session.destination_key,
            ),
            upload_session.destination_bucket,
        )
        return destination, source

    @staticmethod
    def _assert_owned(
        upload_session: UploadSession,
        *,
        stat: ObjectStat,
        reference: ObjectReference,
        expected_bucket: str,
    ) -> None:
        if (
            stat.backend != upload_session.storage_backend
            or stat.reference.location != reference.location
            or stat.reference.key != reference.key
            or stat.bucket != expected_bucket
            or stat.metadata.get("upload-session-id") != upload_session.id
            or not stat.etag
        ):
            raise ObjectMismatchError(
                "stored object does not match the Upload Session cleanup ownership facts"
            )
