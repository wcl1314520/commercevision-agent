"""MinIO adapter and object-storage factory."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError
from commercevision_contracts import Settings
from commercevision_contracts.object_storage import (
    BoundedReadRequest,
    ConditionalCopyRequest,
    ConditionalDeleteRequest,
    ObjectReference,
    ObjectStat,
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
    UploadObjectMissingError,
)

from .credentials import create_oss_credentials_provider
from .object_storage_common import (
    READ_CHUNK_BYTES,
    metadata_matches,
    seconds_until,
    select_storage_locations,
    validated_response_version,
)
from .object_storage_oss import OssObjectStorage
from .readiness import ObjectStorageReadinessError


def _raise_s3_error(exc: Exception) -> None:
    if isinstance(exc, ClientError):
        response = exc.response
        error = response.get("Error", {})
        code = str(error.get("Code", ""))
        status = int(response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
        if code == "NoSuchBucket":
            raise StorageUnavailableError("object storage bucket is unavailable") from exc
        if code in {"NoSuchKey", "NotFound", "404"} or (status == 404 and not code):
            raise UploadObjectMissingError("object storage key was not found") from exc
        if code in {"PreconditionFailed", "412", "ConditionalRequestConflict"} or status in {
            409,
            412,
        }:
            raise StoragePreconditionError("object storage precondition failed") from exc
    raise StorageUnavailableError("object storage request failed") from exc


class MinioObjectStorage:
    """S3-compatible MinIO implementation with constrained SigV4 requests."""

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
        verify_tls: bool,
        force_path_style: bool,
        require_encryption: bool,
        connect_timeout: float,
        read_timeout: float,
        readiness_timeout: float = 1.0,
        client: Any | None = None,
        signer: Any | None = None,
        readiness_client: Any | None = None,
    ) -> None:
        self._buckets = dict(buckets)
        self._require_encryption = require_encryption
        boto_config = Config(
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            retries={"max_attempts": 1, "mode": "standard"},
            s3={
                "addressing_style": "path" if force_path_style else "virtual",
                "payload_signing_enabled": True,
            },
            signature_version="s3v4",
        )
        client_options = {
            "service_name": "s3",
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
            "aws_session_token": session_token,
            "region_name": region,
            "verify": verify_tls,
            "config": boto_config,
        }
        self._client = client or boto3.client(endpoint_url=endpoint, **client_options)
        self._signer = signer or boto3.client(
            endpoint_url=presign_endpoint,
            **client_options,
        )
        readiness_config = Config(
            connect_timeout=readiness_timeout,
            read_timeout=readiness_timeout,
            retries={"max_attempts": 1, "mode": "standard"},
            s3={
                "addressing_style": "path" if force_path_style else "virtual",
                "payload_signing_enabled": True,
            },
            signature_version="s3v4",
        )
        self._readiness_client = (
            readiness_client
            or client
            or boto3.client(
                endpoint_url=endpoint,
                **{**client_options, "config": readiness_config},
            )
        )

    @property
    def backend(self) -> StorageBackend:
        return StorageBackend.MINIO

    def assert_ready(
        self,
        required_locations: Iterable[StorageLocationClass] | None = None,
    ) -> None:
        if not self._buckets:
            raise ObjectStorageReadinessError("object storage bucket mapping is not configured")
        locations = select_storage_locations(self._buckets, required_locations)
        bucket_names = sorted({self._buckets[location] for location in locations})
        with ThreadPoolExecutor(max_workers=min(4, len(bucket_names))) as executor:
            list(executor.map(self._assert_bucket_ready, bucket_names))

    def close(self) -> None:
        seen: set[int] = set()
        for client in (self._client, self._signer, self._readiness_client):
            if id(client) in seen:
                continue
            seen.add(id(client))
            close = getattr(client, "close", None)
            if callable(close):
                close()

    def presign_put(self, request: PresignPutRequest) -> PresignedRequest:
        bucket = self._bucket(request.reference.location)
        metadata = {
            "upload-session-id": request.upload_session_id,
            "sha256": request.checksum_sha256_base64,
        }
        params: dict[str, object] = {
            "Bucket": bucket,
            "Key": request.reference.key,
            "ContentType": request.content_type,
            "ContentLength": request.content_length,
            "ChecksumSHA256": request.checksum_sha256_base64,
            "IfNoneMatch": "*",
            "Metadata": metadata,
        }
        headers = {
            "Content-Type": request.content_type,
            "Content-Length": str(request.content_length),
            "x-amz-checksum-sha256": request.checksum_sha256_base64,
            "If-None-Match": "*",
            "x-amz-meta-upload-session-id": request.upload_session_id,
            "x-amz-meta-sha256": request.checksum_sha256_base64,
        }
        if self._require_encryption:
            params["ServerSideEncryption"] = "AES256"
            headers["x-amz-server-side-encryption"] = "AES256"
        try:
            url = self._signer.generate_presigned_url(
                "put_object",
                Params=params,
                ExpiresIn=seconds_until(request.expires_at),
                HttpMethod="PUT",
            )
        except (BotoCoreError, ClientError, ValueError) as exc:
            _raise_s3_error(exc)
        return PresignedRequest(
            method="PUT",
            url=url,
            required_headers=headers,
            expires_at=request.expires_at,
        )

    def stat(self, reference: ObjectReference) -> ObjectStat:
        params: dict[str, object] = {
            "Bucket": self._bucket(reference.location),
            "Key": reference.key,
        }
        if reference.version_id is not None:
            params["VersionId"] = reference.version_id
        try:
            response = self._client.head_object(**params)
        except (BotoCoreError, ClientError) as exc:
            _raise_s3_error(exc)
        version_id = validated_response_version(
            reference,
            response.get("VersionId"),
        )
        return ObjectStat(
            reference=reference.model_copy(update={"version_id": version_id}),
            backend=self.backend,
            bucket=self._bucket(reference.location),
            etag=str(response.get("ETag", "")),
            content_length=int(response["ContentLength"]),
            content_type=response.get("ContentType"),
            checksum_sha256_base64=response.get("ChecksumSHA256"),
            metadata={
                str(key).lower(): str(value) for key, value in response.get("Metadata", {}).items()
            },
            last_modified=response.get("LastModified"),
        )

    @contextmanager
    def open_bounded_read(
        self,
        request: BoundedReadRequest,
    ) -> Iterator[Iterable[bytes]]:
        current = self.stat(request.reference)
        if current.content_length > request.maximum_bytes:
            raise ObjectMismatchError("object exceeds the bounded read limit")
        params: dict[str, object] = {
            "Bucket": current.bucket,
            "Key": current.reference.key,
            "Range": f"bytes=0-{request.maximum_bytes}",
        }
        if current.reference.version_id is not None:
            params["VersionId"] = current.reference.version_id
        if request.expected_etag is not None:
            params["IfMatch"] = request.expected_etag
        try:
            response = self._client.get_object(**params)
        except (BotoCoreError, ClientError) as exc:
            _raise_s3_error(exc)
        body = response["Body"]
        try:
            yield self._bounded_chunks(
                body.iter_chunks(chunk_size=READ_CHUNK_BYTES),
                request.maximum_bytes,
            )
        finally:
            body.close()

    def copy_if_absent(self, request: ConditionalCopyRequest) -> ObjectStat:
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
            return existing
        params: dict[str, object] = {
            "Bucket": self._bucket(request.destination.location),
            "Key": request.destination.key,
            "CopySource": {
                "Bucket": self._bucket(request.source.location),
                "Key": request.source.key,
            },
            "CopySourceIfMatch": request.source_etag,
            "IfNoneMatch": "*",
            "ContentType": request.content_type,
            "Metadata": {
                "sha256": request.expected_sha256,
                "upload-session-id": request.upload_session_id,
            },
            "MetadataDirective": "REPLACE",
        }
        if request.source.version_id is not None:
            params["CopySource"]["VersionId"] = request.source.version_id
        if self._require_encryption:
            params["ServerSideEncryption"] = "AES256"
        try:
            self._client.copy_object(**params)
        except (BotoCoreError, ClientError) as exc:
            try:
                _raise_s3_error(exc)
            except StoragePreconditionError as conflict:
                existing = self._stat_if_present(request.destination)
                if existing is not None and metadata_matches(
                    existing,
                    expected_length=request.expected_content_length,
                    expected_sha256=request.expected_sha256,
                    expected_upload_session_id=request.upload_session_id,
                    expected_content_type=request.content_type,
                ):
                    return existing
                raise conflict
        copied = self.stat(request.destination)
        if not metadata_matches(
            copied,
            expected_length=request.expected_content_length,
            expected_sha256=request.expected_sha256,
            expected_upload_session_id=request.upload_session_id,
            expected_content_type=request.content_type,
        ):
            raise StoragePreconditionError("copied object did not match the expected facts")
        return copied

    def delete_if_match(self, request: ConditionalDeleteRequest) -> bool:
        try:
            current = self.stat(request.reference)
        except UploadObjectMissingError:
            return True
        if current.etag != request.expected_etag:
            raise StoragePreconditionError("object changed before conditional delete")
        params: dict[str, object] = {
            "Bucket": current.bucket,
            "Key": request.reference.key,
            "IfMatch": request.expected_etag,
        }
        if current.reference.version_id is not None:
            params["VersionId"] = current.reference.version_id
        try:
            self._client.delete_object(**params)
        except (BotoCoreError, ClientError) as exc:
            _raise_s3_error(exc)
        return True

    def temporary_read(self, request: TemporaryReadRequest) -> PresignedRequest:
        params: dict[str, object] = {
            "Bucket": self._bucket(request.reference.location),
            "Key": request.reference.key,
        }
        if request.reference.version_id is not None:
            params["VersionId"] = request.reference.version_id
        if request.expected_etag is not None:
            params["IfMatch"] = request.expected_etag
        try:
            url = self._signer.generate_presigned_url(
                "get_object",
                Params=params,
                ExpiresIn=seconds_until(request.expires_at),
                HttpMethod="GET",
            )
        except (BotoCoreError, ClientError, ValueError) as exc:
            _raise_s3_error(exc)
        headers = {"If-Match": request.expected_etag} if request.expected_etag else {}
        return PresignedRequest(
            method="GET",
            url=url,
            required_headers=headers,
            expires_at=request.expires_at,
        )

    def _stat_if_present(self, reference: ObjectReference) -> ObjectStat | None:
        try:
            return self.stat(reference)
        except UploadObjectMissingError:
            return None

    def _assert_bucket_ready(self, bucket: str) -> None:
        try:
            response = self._readiness_client.head_bucket(Bucket=bucket)
            status = int(response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
            if status != 200:
                raise ObjectStorageReadinessError(
                    f"object storage bucket {bucket} is not accessible"
                )
            versioning = self._readiness_client.get_bucket_versioning(Bucket=bucket)
        except ObjectStorageReadinessError:
            raise
        except (BotoCoreError, ClientError) as exc:
            raise ObjectStorageReadinessError(
                f"object storage bucket {bucket} is not accessible"
            ) from exc
        if versioning.get("Status") != "Enabled":
            raise ObjectStorageReadinessError(
                f"object storage bucket {bucket} versioning is not enabled"
            )
        if not self._require_encryption:
            return
        try:
            encryption = self._readiness_client.get_bucket_encryption(Bucket=bucket)
        except (BotoCoreError, ClientError) as exc:
            raise ObjectStorageReadinessError(
                f"object storage bucket {bucket} encryption is not configured"
            ) from exc
        rules = encryption.get("ServerSideEncryptionConfiguration", {}).get("Rules", [])
        algorithms = {
            str(
                rule.get("ApplyServerSideEncryptionByDefault", {}).get(
                    "SSEAlgorithm",
                    "",
                )
            )
            for rule in rules
            if isinstance(rule, dict)
        }
        if not algorithms.intersection({"AES256", "aws:kms", "aws:kms:dsse"}):
            raise ObjectStorageReadinessError(
                f"object storage bucket {bucket} encryption is not configured"
            )

    def _bucket(self, location: StorageLocationClass) -> str:
        try:
            return self._buckets[location]
        except KeyError as exc:
            raise ValueError(f"storage location {location.value} is not configured") from exc

    @staticmethod
    def _bounded_chunks(chunks: Iterable[bytes], maximum_bytes: int) -> Iterator[bytes]:
        consumed = 0
        try:
            for chunk in chunks:
                if not chunk:
                    continue
                consumed += len(chunk)
                if consumed > maximum_bytes:
                    raise ObjectMismatchError("object exceeds the bounded read limit")
                yield chunk
        except (BotoCoreError, ClientError) as exc:
            _raise_s3_error(exc)


def build_object_storage(settings: Settings) -> MinioObjectStorage | OssObjectStorage:
    common = {
        "endpoint": settings.object_store_endpoint,
        "presign_endpoint": (
            settings.object_store_presign_endpoint or settings.object_store_endpoint
        ),
        "region": settings.object_store_region,
        "buckets": settings.object_store_buckets,
        "force_path_style": settings.object_store_force_path_style,
        "require_encryption": settings.object_store_require_encryption,
        "connect_timeout": settings.object_store_connect_timeout_seconds,
        "read_timeout": settings.object_store_read_timeout_seconds,
        "readiness_timeout": settings.object_store_readiness_timeout_seconds,
    }
    if settings.object_store_backend == "minio":
        return MinioObjectStorage(
            **common,
            access_key=settings.object_store_access_key,
            secret_key=settings.object_store_secret_key.get_secret_value(),
            session_token=(
                settings.object_store_session_token.get_secret_value()
                if settings.object_store_session_token is not None
                else None
            ),
            verify_tls=settings.object_store_tls_verify,
        )
    credential_provider = (
        None
        if settings.object_store_credential_mode == "static"
        else create_oss_credentials_provider(settings)
    )
    return OssObjectStorage(
        **common,
        access_key=settings.object_store_access_key,
        secret_key=settings.object_store_secret_key.get_secret_value(),
        session_token=(
            settings.object_store_session_token.get_secret_value()
            if settings.object_store_session_token is not None
            else None
        ),
        credential_provider=credential_provider,
    )
