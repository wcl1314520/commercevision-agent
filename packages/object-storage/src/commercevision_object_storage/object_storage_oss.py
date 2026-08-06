"""Alibaba OSS adapter for the typed object-storage seam."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Literal

import oss2
from commercevision_contracts.object_storage import (
    BoundedReadRequest,
    ConditionalCopyRequest,
    ConditionalDeleteRequest,
    ConditionalWriteRequest,
    DeleteMarkerRequest,
    GenerationMediaWriteRequest,
    ObjectReference,
    ObjectStat,
    ObjectVersionEntry,
    ObjectVersionListRequest,
    ObjectVersionPage,
    PresignedRequest,
    PresignPutRequest,
    TemporaryReadRequest,
)
from commercevision_domain import (
    ObjectMismatchError,
    StorageBackend,
    StorageLocationClass,
    StoragePreconditionError,
    StorageUnavailableError,
    StorageWriteSafeToRetryError,
    UploadObjectMissingError,
)

from .object_storage_common import (
    READ_CHUNK_BYTES,
    close_resources_best_effort,
    decode_version_cursor,
    encode_version_cursor,
    metadata_matches,
    normalize_server_side_encryption,
    seconds_until,
    select_storage_locations,
    validated_response_version,
    written_object_matches,
)
from .readiness import ObjectStorageReadinessError


def _require_copy_version_id(value: object, *, role: str) -> str:
    if not isinstance(value, str):
        raise StoragePreconditionError(f"OSS copy {role} version identifier is missing")
    version_id = value.strip()
    if not version_id or version_id.lower() == "null":
        raise StoragePreconditionError(f"OSS copy {role} version identifier is missing")
    return version_id


def _raise_oss_error(exc: Exception) -> None:
    if isinstance(exc, oss2.exceptions.NoSuchBucket):
        raise StorageUnavailableError("object storage bucket is unavailable") from exc
    if isinstance(exc, oss2.exceptions.NoSuchKey) or (
        isinstance(exc, oss2.exceptions.OssError) and exc.code in {"NoSuchKey", "NoSuchVersion"}
    ):
        raise UploadObjectMissingError("object storage key was not found") from exc
    if isinstance(
        exc,
        (oss2.exceptions.ObjectAlreadyExists, oss2.exceptions.PreconditionFailed),
    ) or (isinstance(exc, oss2.exceptions.OssError) and exc.code == "FileAlreadyExists"):
        raise StoragePreconditionError("object storage precondition failed") from exc
    raise StorageUnavailableError("object storage request failed") from exc


def _oss_adapter_resources(adapters: Iterable[object | None]) -> Iterator[object | None]:
    for adapter in adapters:
        yield adapter
        session = getattr(adapter, "session", None)
        yield session
        yield getattr(session, "session", None)


class OssObjectStorage:
    """Alibaba OSS implementation using Signature V4 and bounded range reads."""

    def __init__(
        self,
        *,
        endpoint: str,
        presign_endpoint: str,
        access_key: str,
        secret_key: str,
        session_token: str | None,
        region: str,
        buckets: Mapping[StorageLocationClass, str],
        force_path_style: bool,
        require_encryption: bool,
        connect_timeout: float,
        read_timeout: float | None = None,
        readiness_timeout: float = 1.0,
        credential_provider: oss2.credentials.CredentialsProvider | None = None,
        clients: Mapping[StorageLocationClass, Any] | None = None,
        signers: Mapping[StorageLocationClass, Any] | None = None,
        readiness_clients: Mapping[StorageLocationClass, Any] | None = None,
    ) -> None:
        self._bucket_names: dict[StorageLocationClass, str] = dict(buckets)
        self._require_encryption = require_encryption
        self._credential_provider = credential_provider
        request_timeout = (connect_timeout, read_timeout or connect_timeout)
        readiness_request_timeout = (readiness_timeout, readiness_timeout)
        if credential_provider is not None:
            auth = oss2.ProviderAuthV4(credential_provider)
        elif session_token is None:
            auth = oss2.AuthV4(access_key, secret_key)
        else:
            auth = oss2.StsAuth(access_key, secret_key, session_token, auth_version="v4")
        self._clients = dict(
            clients
            or {
                location: oss2.Bucket(
                    auth,
                    endpoint,
                    bucket,
                    connect_timeout=request_timeout,
                    region=region,
                    is_path_style=force_path_style,
                )
                for location, bucket in self._bucket_names.items()
            }
        )
        self._signers = dict(
            signers
            or {
                location: oss2.Bucket(
                    auth,
                    presign_endpoint,
                    bucket,
                    connect_timeout=request_timeout,
                    region=region,
                    is_path_style=force_path_style,
                )
                for location, bucket in self._bucket_names.items()
            }
        )
        self._readiness_clients = dict(
            readiness_clients
            or clients
            or {
                location: oss2.Bucket(
                    auth,
                    endpoint,
                    bucket,
                    connect_timeout=readiness_request_timeout,
                    region=region,
                    is_path_style=force_path_style,
                )
                for location, bucket in self._bucket_names.items()
            }
        )

    @property
    def backend(self) -> StorageBackend:
        return StorageBackend.OSS

    def assert_ready(
        self,
        required_locations: Iterable[StorageLocationClass] | None = None,
    ) -> None:
        if not self._bucket_names:
            raise ObjectStorageReadinessError("object storage bucket mapping is not configured")
        locations = select_storage_locations(self._bucket_names, required_locations)
        clients_by_bucket: dict[str, Any] = {}
        for location in locations:
            bucket = self._bucket_names[location]
            clients_by_bucket.setdefault(bucket, self._readiness_clients[location])
        items = sorted(clients_by_bucket.items())
        with ThreadPoolExecutor(max_workers=min(4, len(items))) as executor:
            list(executor.map(lambda item: self._assert_bucket_ready(*item), items))

    def close(self) -> None:
        adapters = (
            *self._clients.values(),
            *self._signers.values(),
            *self._readiness_clients.values(),
            self._credential_provider,
        )
        close_resources_best_effort(
            _oss_adapter_resources(adapters),
            message="object storage client shutdown failed",
        )

    def presign_put(self, request: PresignPutRequest) -> PresignedRequest:
        headers = {
            "Content-Type": request.content_type,
            "Content-Length": str(request.content_length),
            "x-oss-forbid-overwrite": "true",
            "x-oss-meta-upload-session-id": request.upload_session_id,
            "x-oss-meta-sha256": request.checksum_sha256_base64,
        }
        if self._require_encryption:
            headers["x-oss-server-side-encryption"] = "AES256"
        try:
            url = self._signer(request.reference.location).sign_url(
                "PUT",
                request.reference.key,
                seconds_until(request.expires_at),
                headers=headers,
                additional_headers=list(headers),
            )
        except (oss2.exceptions.OssError, oss2.exceptions.RequestError, ValueError) as exc:
            _raise_oss_error(exc)
        return PresignedRequest(
            method="PUT",
            url=url,
            required_headers=headers,
            expires_at=request.expires_at,
        )

    def stat(self, reference: ObjectReference) -> ObjectStat:
        params = {"versionId": reference.version_id} if reference.version_id else None
        try:
            result = self._client(reference.location).head_object(
                reference.key,
                params=params,
            )
        except (oss2.exceptions.OssError, oss2.exceptions.RequestError) as exc:
            _raise_oss_error(exc)
        headers = {str(key).lower(): str(value) for key, value in result.headers.items()}
        version_id = validated_response_version(
            reference,
            headers.get("x-oss-version-id"),
        )
        metadata = {
            key.removeprefix("x-oss-meta-"): value
            for key, value in headers.items()
            if key.startswith("x-oss-meta-")
        }
        return ObjectStat(
            reference=reference.model_copy(update={"version_id": version_id}),
            backend=self.backend,
            bucket=self._bucket_names[reference.location],
            etag=str(result.etag),
            content_length=int(result.content_length),
            content_type=result.content_type,
            checksum_sha256_base64=metadata.get("sha256"),
            metadata=metadata,
            last_modified=(
                datetime.fromtimestamp(result.last_modified, tz=UTC)
                if result.last_modified is not None
                else None
            ),
            server_side_encryption=normalize_server_side_encryption(
                headers.get("x-oss-server-side-encryption")
            ),
        )

    @contextmanager
    def open_bounded_read(
        self,
        request: BoundedReadRequest,
    ) -> Iterator[Iterable[bytes]]:
        current = self.stat(request.reference)
        if current.content_length > request.maximum_bytes:
            raise ObjectMismatchError("object exceeds the bounded read limit")
        headers = {"If-Match": request.expected_etag} if request.expected_etag else None
        params = (
            {"versionId": current.reference.version_id} if current.reference.version_id else None
        )
        try:
            result = self._client(request.reference.location).get_object(
                current.reference.key,
                byte_range=(0, request.maximum_bytes),
                headers=headers,
                params=params,
            )
        except (oss2.exceptions.OssError, oss2.exceptions.RequestError) as exc:
            _raise_oss_error(exc)
        try:
            yield self._read_oss_chunks(result, request.maximum_bytes)
        finally:
            result.close()

    def copy_if_absent(self, request: ConditionalCopyRequest) -> ObjectStat:
        source_version_id = _require_copy_version_id(
            request.source.version_id,
            role="source",
        )
        existing = self._stat_if_present(request.destination)
        if existing is not None:
            if not metadata_matches(
                existing,
                expected_length=request.expected_content_length,
                expected_sha256=request.expected_sha256,
                expected_upload_session_id=request.upload_session_id,
                expected_content_type=request.content_type,
            ):
                raise StoragePreconditionError("copy destination already contains another object")
            _require_copy_version_id(
                existing.reference.version_id,
                role="destination",
            )
            return existing
        headers = {
            "x-oss-copy-source-if-match": request.source_etag,
            "x-oss-forbid-overwrite": "true",
            "x-oss-metadata-directive": "REPLACE",
            "x-oss-meta-sha256": request.expected_sha256,
            "x-oss-meta-upload-session-id": request.upload_session_id,
            "Content-Type": request.content_type,
        }
        if self._require_encryption:
            headers["x-oss-server-side-encryption"] = "AES256"
        params = {"versionId": source_version_id}
        try:
            result = self._client(request.destination.location).copy_object(
                self._bucket_names[request.source.location],
                request.source.key,
                request.destination.key,
                headers=headers,
                params=params,
            )
        except (oss2.exceptions.OssError, oss2.exceptions.RequestError) as exc:
            existing = self._stat_if_present(request.destination)
            if existing is not None and metadata_matches(
                existing,
                expected_length=request.expected_content_length,
                expected_sha256=request.expected_sha256,
                expected_upload_session_id=request.upload_session_id,
                expected_content_type=request.content_type,
            ):
                _require_copy_version_id(
                    existing.reference.version_id,
                    role="destination",
                )
                return existing
            _raise_oss_error(exc)
        version_id = _require_copy_version_id(
            getattr(result, "versionid", None),
            role="result",
        )
        try:
            copied = self.stat(request.destination.model_copy(update={"version_id": version_id}))
        except StoragePreconditionError as exc:
            raise StoragePreconditionError(
                "OSS copy copied object version did not match the result version"
            ) from exc
        copied_version_id = _require_copy_version_id(
            copied.reference.version_id,
            role="copied object",
        )
        if copied_version_id != version_id:
            raise StoragePreconditionError(
                "OSS copy copied object version did not match the result version"
            )
        if not metadata_matches(
            copied,
            expected_length=request.expected_content_length,
            expected_sha256=request.expected_sha256,
            expected_upload_session_id=request.upload_session_id,
            expected_content_type=request.content_type,
        ):
            raise StoragePreconditionError("copied object did not match the expected facts")
        return copied

    def write_if_absent(self, request: ConditionalWriteRequest) -> ObjectStat:
        return self._write_if_absent(
            request,
            metadata=request.metadata,
            require_encryption=request.require_encryption,
        )

    def _write_if_absent(
        self,
        request: ConditionalWriteRequest | GenerationMediaWriteRequest,
        *,
        metadata: Mapping[str, str],
        require_encryption: bool,
    ) -> ObjectStat:
        try:
            existing = self._stat_if_present(request.reference)
        except StorageUnavailableError as exc:
            raise StorageWriteSafeToRetryError(
                "object storage became unavailable before conditional write"
            ) from exc
        expected_metadata = {**metadata, "sha256": request.expected_sha256}
        require_encryption = self._require_encryption or require_encryption
        if existing is not None:
            if not written_object_matches(
                existing,
                expected_length=len(request.payload),
                expected_sha256=request.expected_sha256,
                expected_content_type=request.content_type,
                expected_metadata=expected_metadata,
                require_encryption=require_encryption,
            ):
                raise StoragePreconditionError(
                    "write destination already contains another object "
                    "or lacks required server-side encryption"
                )
            _require_copy_version_id(existing.reference.version_id, role="written object")
            return existing
        if hashlib.sha256(request.payload).hexdigest() != request.expected_sha256:
            raise ObjectMismatchError("conditional write payload SHA-256 does not match")
        headers = {
            "Content-Type": request.content_type,
            "x-oss-forbid-overwrite": "true",
            **{f"x-oss-meta-{name}": value for name, value in expected_metadata.items()},
        }
        if require_encryption:
            headers["x-oss-server-side-encryption"] = "AES256"
        try:
            result = self._client(request.reference.location).put_object(
                request.reference.key,
                request.payload,
                headers=headers,
            )
        except (oss2.exceptions.OssError, oss2.exceptions.RequestError) as exc:
            existing = self._stat_if_present(request.reference)
            if existing is not None and written_object_matches(
                existing,
                expected_length=len(request.payload),
                expected_sha256=request.expected_sha256,
                expected_content_type=request.content_type,
                expected_metadata=expected_metadata,
                require_encryption=require_encryption,
            ):
                _require_copy_version_id(existing.reference.version_id, role="written object")
                return existing
            _raise_oss_error(exc)
        version_id = _require_copy_version_id(
            getattr(result, "versionid", None),
            role="result",
        )
        exact_reference = request.reference.model_copy(update={"version_id": version_id})
        cleanup_etag = "*"
        try:
            result_etag = getattr(result, "etag", None)
            if isinstance(result_etag, str) and result_etag:
                cleanup_etag = result_etag
            written = self.stat(exact_reference)
            if not written_object_matches(
                written,
                expected_length=len(request.payload),
                expected_sha256=request.expected_sha256,
                expected_content_type=request.content_type,
                expected_metadata=expected_metadata,
                require_encryption=require_encryption,
            ):
                raise StoragePreconditionError(
                    "written object did not match expected facts or encryption requirement"
                )
        except Exception as exc:
            self._cleanup_failed_write(
                reference=exact_reference,
                expected_etag=cleanup_etag,
            )
            if isinstance(
                exc,
                (
                    StoragePreconditionError,
                    StorageUnavailableError,
                    UploadObjectMissingError,
                ),
            ):
                raise
            raise StoragePreconditionError("written object verification failed") from exc
        return written

    def write_generation_media_if_absent(
        self,
        request: GenerationMediaWriteRequest,
    ) -> ObjectStat:
        return self._write_if_absent(
            request,
            metadata={
                "durable-operation-id": request.durable_operation_id,
                "candidate-slot-id": request.candidate_slot_id,
                "provider-call-id": request.provider_call_id,
            },
            require_encryption=True,
        )

    def _cleanup_failed_write(
        self,
        *,
        reference: ObjectReference,
        expected_etag: str,
    ) -> None:
        if reference.version_id is None:
            raise StoragePreconditionError("failed write exact-version cleanup could not be proven")
        try:
            self._client(reference.location).delete_object(
                reference.key,
                params={"versionId": reference.version_id},
                headers={"If-Match": expected_etag},
            )
        except (oss2.exceptions.OssError, oss2.exceptions.RequestError) as exc:
            try:
                _raise_oss_error(exc)
            except UploadObjectMissingError:
                pass
            except (StoragePreconditionError, StorageUnavailableError) as cleanup_exc:
                raise StoragePreconditionError(
                    "failed write exact-version cleanup could not be proven"
                ) from cleanup_exc
        except Exception as exc:
            raise StoragePreconditionError(
                "failed write exact-version cleanup could not be proven"
            ) from exc
        try:
            self.stat(reference)
        except UploadObjectMissingError:
            return
        except Exception as exc:
            raise StoragePreconditionError(
                "failed write exact-version cleanup could not be proven"
            ) from exc
        raise StoragePreconditionError("failed write exact version remains readable")

    def delete_if_match(self, request: ConditionalDeleteRequest) -> bool:
        try:
            current = self.stat(request.reference)
        except UploadObjectMissingError:
            return True
        if current.etag != request.expected_etag:
            raise StoragePreconditionError("object changed before conditional delete")
        if current.reference.version_id is None:
            raise StoragePreconditionError("OSS conditional delete requires bucket versioning")
        params = {"versionId": current.reference.version_id}
        try:
            self._client(request.reference.location).delete_object(
                request.reference.key,
                params=params,
            )
        except (oss2.exceptions.OssError, oss2.exceptions.RequestError) as exc:
            _raise_oss_error(exc)
        return True

    def list_versions(self, request: ObjectVersionListRequest) -> ObjectVersionPage:
        key_marker, version_marker = decode_version_cursor(
            request.continuation_token,
            provider="oss",
        )
        try:
            result = self._client(request.reference.location).list_object_versions(
                prefix=request.reference.key,
                key_marker=key_marker,
                max_keys=request.page_size,
                versionid_marker=version_marker,
            )
        except (oss2.exceptions.OssError, oss2.exceptions.RequestError) as exc:
            _raise_oss_error(exc)
        entries: list[ObjectVersionEntry] = []
        version_groups: tuple[tuple[list[Any], Literal["OBJECT", "DELETE_MARKER"]], ...] = (
            (result.versions, "OBJECT"),
            (result.delete_marker, "DELETE_MARKER"),
        )
        for items, kind in version_groups:
            for item in items:
                if item.key != request.reference.key:
                    continue
                version_id = item.versionid
                if (
                    not isinstance(version_id, str)
                    or not version_id
                    or version_id.lower() == "null"
                ):
                    raise StoragePreconditionError(
                        "object version listing returned an inexact provider version"
                    )
                entries.append(
                    ObjectVersionEntry(
                        reference=request.reference.model_copy(update={"version_id": version_id}),
                        kind=kind,
                    )
                )
        continuation_token = None
        if result.is_truncated:
            if not isinstance(result.next_key_marker, str) or not isinstance(
                result.next_versionid_marker,
                str,
            ):
                raise StoragePreconditionError(
                    "object version listing omitted its continuation markers"
                )
            continuation_token = encode_version_cursor(
                provider="oss",
                key_marker=result.next_key_marker,
                version_marker=result.next_versionid_marker,
            )
        return ObjectVersionPage(
            entries=tuple(entries),
            continuation_token=continuation_token,
        )

    def delete_marker(self, request: DeleteMarkerRequest) -> bool:
        assert request.reference.version_id is not None
        try:
            self._client(request.reference.location).delete_object(
                request.reference.key,
                params={"versionId": request.reference.version_id},
            )
        except (oss2.exceptions.OssError, oss2.exceptions.RequestError) as exc:
            try:
                _raise_oss_error(exc)
            except UploadObjectMissingError:
                return True
        return True

    def temporary_read(self, request: TemporaryReadRequest) -> PresignedRequest:
        current = self.stat(request.reference)
        if request.expected_etag is not None and current.etag != request.expected_etag:
            raise StoragePreconditionError("temporary read ETag does not match")
        if (
            request.expected_sha256 is not None
            and current.metadata.get("sha256") != request.expected_sha256
        ):
            raise StoragePreconditionError("temporary read content identity does not match")
        params = (
            {"versionId": current.reference.version_id} if current.reference.version_id else None
        )
        try:
            url = self._signer(current.reference.location).sign_url(
                "GET",
                current.reference.key,
                seconds_until(request.expires_at),
                params=params,
            )
        except (oss2.exceptions.OssError, oss2.exceptions.RequestError, ValueError) as exc:
            _raise_oss_error(exc)
        return PresignedRequest(
            method="GET",
            url=url,
            required_headers={},
            expires_at=request.expires_at,
        )

    def _stat_if_present(self, reference: ObjectReference) -> ObjectStat | None:
        try:
            return self.stat(reference)
        except UploadObjectMissingError:
            return None

    def _assert_bucket_ready(self, bucket_name: str, client: Any) -> None:
        try:
            versioning = client.get_bucket_versioning()
        except (oss2.exceptions.OssError, oss2.exceptions.RequestError) as exc:
            raise ObjectStorageReadinessError(
                f"object storage bucket {bucket_name} is not accessible"
            ) from exc
        if getattr(versioning, "status", None) != "Enabled":
            raise ObjectStorageReadinessError(
                f"object storage bucket {bucket_name} versioning is not enabled"
            )
        if not self._require_encryption:
            return
        try:
            encryption = client.get_bucket_encryption()
        except oss2.exceptions.NoSuchServerSideEncryptionRule:
            encryption = None
        except (oss2.exceptions.OssError, oss2.exceptions.RequestError) as exc:
            raise ObjectStorageReadinessError(
                f"object storage bucket {bucket_name} encryption cannot be inspected"
            ) from exc
        algorithm = str(getattr(encryption, "sse_algorithm", ""))
        if algorithm not in {"AES256", "KMS", "SM4"}:
            raise ObjectStorageReadinessError(
                f"object storage bucket {bucket_name} encryption is not configured"
            )

    def configured_bucket(self, location: StorageLocationClass) -> str:
        try:
            return self._bucket_names[location]
        except KeyError as exc:
            raise ValueError(f"storage location {location.value} is not configured") from exc

    def _client(self, location: StorageLocationClass) -> Any:
        try:
            return self._clients[location]
        except KeyError as exc:
            raise ValueError(f"storage location {location.value} is not configured") from exc

    def _signer(self, location: StorageLocationClass) -> Any:
        try:
            return self._signers[location]
        except KeyError as exc:
            raise ValueError(f"storage location {location.value} is not configured") from exc

    @staticmethod
    def _read_oss_chunks(result: Any, maximum_bytes: int) -> Iterator[bytes]:
        consumed = 0
        try:
            while True:
                chunk = result.read(READ_CHUNK_BYTES)
                if not chunk:
                    return
                consumed += len(chunk)
                if consumed > maximum_bytes:
                    raise ObjectMismatchError("object exceeds the bounded read limit")
                yield chunk
        except (oss2.exceptions.OssError, oss2.exceptions.RequestError) as exc:
            _raise_oss_error(exc)
