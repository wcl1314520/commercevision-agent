from __future__ import annotations

import base64
import hashlib
import subprocess
import sys
import textwrap
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
from threading import Event, Lock
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import oss2
import pytest
from alibabacloud_credentials.exceptions import CredentialException
from botocore.exceptions import ClientError, ReadTimeoutError
from commercevision_contracts import Settings
from commercevision_contracts.object_storage import (
    BoundedReadRequest,
    ConditionalCopyRequest,
    ConditionalDeleteRequest,
    DeleteMarkerRequest,
    ObjectReference,
    ObjectVersionListRequest,
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
from commercevision_object_storage import (
    AlibabaCloudCredentialsProvider,
    MinioObjectStorage,
    ObjectStorageReadinessError,
    OssObjectStorage,
    build_object_storage,
)
from commercevision_object_storage.credentials import create_oss_credentials_provider
from Tea.exceptions import TeaException


@pytest.mark.parametrize(
    ("backend", "endpoint", "expected_backend"),
    [
        ("minio", "http://127.0.0.1:19000", StorageBackend.MINIO),
        ("oss", "https://oss-cn-hangzhou.aliyuncs.com", StorageBackend.OSS),
    ],
)
def test_object_storage_adapters_issue_one_constrained_put(
    backend: str,
    endpoint: str,
    expected_backend: StorageBackend,
) -> None:
    storage = build_object_storage(
        Settings(
            environment="ci",
            object_store_backend=backend,
            object_store_endpoint=endpoint,
            object_store_presign_endpoint=endpoint,
            object_store_access_key="access-key",
            object_store_secret_key="secret-key",
            object_store_region="cn-hangzhou" if backend == "oss" else "us-east-1",
        )
    )
    payload = b"known object"
    digest = hashlib.sha256(payload).digest()
    expires_at = datetime.now(UTC) + timedelta(minutes=5)

    result = storage.presign_put(
        PresignPutRequest(
            reference=ObjectReference(
                location=StorageLocationClass.QUARANTINE,
                key="q/server-generated-key",
            ),
            content_type="image/png",
            content_length=len(payload),
            checksum_sha256_base64=base64.b64encode(digest).decode("ascii"),
            upload_session_id="019f8a00-0000-7000-8000-000000000001",
            expires_at=expires_at,
        )
    )

    assert storage.backend == expected_backend
    assert result.method == "PUT"
    assert result.expires_at == expires_at
    assert urlsplit(result.url).scheme in {"http", "https"}
    assert result.required_headers["Content-Type"] == "image/png"
    assert result.required_headers["Content-Length"] == str(len(payload))
    assert base64.b64encode(digest).decode("ascii") in result.required_headers.values()
    assert "Authorization" not in result.required_headers
    if backend == "minio":
        assert result.required_headers["If-None-Match"] == "*"
    else:
        assert result.required_headers["x-oss-forbid-overwrite"] == "true"


@dataclass
class _StoredObject:
    body: bytes
    content_type: str
    etag: str
    metadata: dict[str, str]
    version_id: str | None = None


class _S3Body:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self.closed = False

    def iter_chunks(self, chunk_size: int):
        for offset in range(0, len(self._body), chunk_size):
            yield self._body[offset : offset + chunk_size]

    def close(self) -> None:
        self.closed = True


class _FailingS3Body(_S3Body):
    def iter_chunks(self, chunk_size: int):
        yield self._body[: min(chunk_size, 1)]
        raise ReadTimeoutError(endpoint_url="https://storage.invalid")


class _S3Client:
    def __init__(self, objects: dict[tuple[str, str], _StoredObject]) -> None:
        self.objects = objects
        self.copy_collision: _StoredObject | None = None
        self.fail_stream = False
        self.last_read_version_id: str | None = None
        self.head_version_ids: list[str | None] = []
        self.versioning_status: dict[str, str] = {}
        self.encryption_algorithm: dict[str, str | None] = {}
        self.readiness_calls: list[tuple[str, str]] = []
        self.version_listing_pages: list[dict[str, object]] = []
        self.version_listing_requests: list[dict[str, object]] = []
        self.deleted_markers: list[tuple[str, str, str]] = []

    @staticmethod
    def _missing(operation: str) -> ClientError:
        return ClientError(
            {
                "Error": {"Code": "NoSuchKey"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            operation,
        )

    @staticmethod
    def _precondition(operation: str) -> ClientError:
        return ClientError(
            {
                "Error": {"Code": "PreconditionFailed"},
                "ResponseMetadata": {"HTTPStatusCode": 412},
            },
            operation,
        )

    def generate_presigned_url(
        self,
        operation: str,
        Params: dict[str, object],
        ExpiresIn: int,
        HttpMethod: str,
    ) -> str:
        assert ExpiresIn > 0
        assert HttpMethod in {"GET", "PUT"}
        version = f"&versionId={Params['VersionId']}" if Params.get("VersionId") else ""
        return f"https://signed.invalid/{Params['Bucket']}/{Params['Key']}?op={operation}{version}"

    def head_object(
        self,
        *,
        Bucket: str,
        Key: str,
        VersionId: str | None = None,
    ) -> dict[str, object]:
        self.head_version_ids.append(VersionId)
        stored = self.objects.get((Bucket, Key))
        if stored is None:
            raise self._missing("HeadObject")
        if VersionId is not None:
            assert VersionId == stored.version_id
        return {
            "ContentLength": len(stored.body),
            "ContentType": stored.content_type,
            "ETag": stored.etag,
            "VersionId": stored.version_id,
            "Metadata": stored.metadata,
            "LastModified": datetime(2026, 7, 21, 12, tzinfo=UTC),
        }

    def get_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Range: str,
        IfMatch: str | None = None,
        VersionId: str | None = None,
    ) -> dict[str, _S3Body]:
        stored = self.objects.get((Bucket, Key))
        if stored is None:
            raise self._missing("GetObject")
        self.last_read_version_id = VersionId
        if VersionId is not None:
            assert VersionId == stored.version_id
        if IfMatch is not None and IfMatch != stored.etag:
            raise self._precondition("GetObject")
        maximum = int(Range.partition("-")[2]) + 1
        body_type = _FailingS3Body if self.fail_stream else _S3Body
        return {"Body": body_type(stored.body[:maximum])}

    def copy_object(self, **params: object) -> dict[str, object]:
        bucket = str(params["Bucket"])
        key = str(params["Key"])
        source = params["CopySource"]
        assert isinstance(source, dict)
        source_object = self.objects[(str(source["Bucket"]), str(source["Key"]))]
        assert source.get("VersionId") == source_object.version_id
        if params["CopySourceIfMatch"] != source_object.etag:
            raise self._precondition("CopyObject")
        if self.copy_collision is not None:
            self.objects[(bucket, key)] = self.copy_collision
            self.copy_collision = None
        if params.get("IfNoneMatch") == "*" and (bucket, key) in self.objects:
            raise self._precondition("CopyObject")
        metadata = params["Metadata"]
        assert isinstance(metadata, dict)
        self.objects[(bucket, key)] = _StoredObject(
            body=source_object.body,
            content_type=str(params["ContentType"]),
            etag='"opaque-copy-etag"',
            metadata={str(name): str(value) for name, value in metadata.items()},
            version_id="copy-version-1",
        )
        return {
            "CopyObjectResult": {"ETag": '"opaque-copy-etag"'},
            "VersionId": "copy-version-1",
        }

    def delete_object(self, **params: object) -> dict[str, object]:
        bucket = str(params["Bucket"])
        key = str(params["Key"])
        if "IfMatch" not in params:
            self.deleted_markers.append((bucket, key, str(params["VersionId"])))
            return {}
        stored = self.objects.get((bucket, key))
        if stored is None:
            raise self._missing("DeleteObject")
        assert params.get("VersionId") == stored.version_id
        if params.get("IfMatch") != stored.etag:
            raise self._precondition("DeleteObject")
        del self.objects[(bucket, key)]
        return {}

    def list_object_versions(self, **params: object) -> dict[str, object]:
        self.version_listing_requests.append(params)
        if self.version_listing_pages:
            return self.version_listing_pages.pop(0)
        return {"Versions": [], "DeleteMarkers": [], "IsTruncated": False}

    def head_bucket(self, *, Bucket: str) -> dict[str, object]:
        self.readiness_calls.append(("head", Bucket))
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def get_bucket_versioning(self, *, Bucket: str) -> dict[str, object]:
        self.readiness_calls.append(("versioning", Bucket))
        return {"Status": self.versioning_status.get(Bucket, "Enabled")}

    def get_bucket_encryption(self, *, Bucket: str) -> dict[str, object]:
        self.readiness_calls.append(("encryption", Bucket))
        algorithm = self.encryption_algorithm.get(Bucket, "AES256")
        if algorithm is None:
            raise ClientError(
                {
                    "Error": {"Code": "ServerSideEncryptionConfigurationNotFoundError"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "GetBucketEncryption",
            )
        return {
            "ServerSideEncryptionConfiguration": {
                "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": algorithm}}]
            }
        }


def test_minio_adapter_contract_covers_bounded_read_copy_delete_and_temporary_read() -> None:
    payload = b"adapter-contract-payload"
    sha256 = hashlib.sha256(payload).hexdigest()
    names = {
        StorageLocationClass.QUARANTINE: "quarantine",
        StorageLocationClass.TASK: "task",
        StorageLocationClass.FOUNDATION: "foundation",
        StorageLocationClass.PROVIDER_RESULT: "provider",
    }
    objects = {
        ("quarantine", "q/source"): _StoredObject(
            body=payload,
            content_type="image/png",
            etag='"opaque/source-etag"',
            metadata={"sha256": sha256},
            version_id="source-version-1",
        )
    }
    client = _S3Client(objects)
    storage = MinioObjectStorage(
        endpoint="https://minio.internal.invalid",
        presign_endpoint="https://minio.invalid",
        access_key="access-key",
        secret_key="secret-key",
        session_token=None,
        region="us-east-1",
        buckets=names,
        verify_tls=True,
        force_path_style=True,
        require_encryption=True,
        connect_timeout=1,
        read_timeout=1,
        client=client,
        signer=client,
    )
    source = ObjectReference(location=StorageLocationClass.QUARANTINE, key="q/source")
    destination = ObjectReference(location=StorageLocationClass.TASK, key="task/destination")
    source_stat = storage.stat(source)
    assert source_stat.reference.version_id == "source-version-1"
    with storage.open_bounded_read(
        BoundedReadRequest(
            reference=source,
            maximum_bytes=len(payload),
            expected_etag=source_stat.etag,
        )
    ) as chunks:
        assert b"".join(chunks) == payload
    assert client.last_read_version_id == source_stat.reference.version_id

    copy_request = ConditionalCopyRequest(
        source=source_stat.reference,
        destination=destination,
        source_etag=source_stat.etag,
        expected_content_length=len(payload),
        expected_sha256=sha256,
        content_type="image/png",
        upload_session_id="019f8a00-0000-7000-8000-000000000001",
    )
    first_copy = storage.copy_if_absent(copy_request)
    assert first_copy.reference.version_id == "copy-version-1"
    assert client.head_version_ids[-1] == "copy-version-1"
    assert storage.copy_if_absent(copy_request).etag == first_copy.etag
    assert first_copy.metadata["upload-session-id"] == copy_request.upload_session_id

    collision_destination = ObjectReference(
        location=StorageLocationClass.TASK,
        key="task/collision",
    )
    client.copy_collision = _StoredObject(
        body=b"different",
        content_type="image/png",
        etag='"collision-etag"',
        metadata={"sha256": hashlib.sha256(b"different").hexdigest()},
    )
    with pytest.raises(StoragePreconditionError):
        storage.copy_if_absent(
            copy_request.model_copy(update={"destination": collision_destination})
        )
    assert objects[("task", "task/collision")].body == b"different"

    temporary = storage.temporary_read(
        TemporaryReadRequest(
            reference=first_copy.reference,
            expires_at=datetime.now(UTC) + timedelta(seconds=30),
            expected_etag=first_copy.etag,
        )
    )
    assert temporary.required_headers == {"If-Match": first_copy.etag}
    assert "copy-version-1" in temporary.url
    with pytest.raises(StoragePreconditionError):
        storage.delete_if_match(
            ConditionalDeleteRequest(
                reference=destination,
                expected_etag='"another-etag"',
            )
        )
    assert storage.delete_if_match(
        ConditionalDeleteRequest(reference=destination, expected_etag=first_copy.etag)
    )
    assert storage.delete_if_match(
        ConditionalDeleteRequest(reference=destination, expected_etag=first_copy.etag)
    )
    client.version_listing_pages = [
        {
            "Versions": [
                {"Key": "task/destination", "VersionId": "version-3"},
                {"Key": "task/destination-suffix", "VersionId": "other-version"},
            ],
            "DeleteMarkers": [{"Key": "task/destination", "VersionId": "delete-marker-2"}],
            "IsTruncated": True,
            "NextKeyMarker": "task/destination",
            "NextVersionIdMarker": "delete-marker-2",
        },
        {
            "Versions": [{"Key": "task/destination", "VersionId": "version-1"}],
            "DeleteMarkers": [],
            "IsTruncated": False,
        },
    ]
    first_page = storage.list_versions(ObjectVersionListRequest(reference=destination, page_size=2))
    assert [(entry.kind, entry.reference.version_id) for entry in first_page.entries] == [
        ("OBJECT", "version-3"),
        ("DELETE_MARKER", "delete-marker-2"),
    ]
    assert first_page.continuation_token is not None
    second_page = storage.list_versions(
        ObjectVersionListRequest(
            reference=destination,
            page_size=2,
            continuation_token=first_page.continuation_token,
        )
    )
    assert [entry.reference.version_id for entry in second_page.entries] == ["version-1"]
    assert second_page.continuation_token is None
    assert client.version_listing_requests[1]["KeyMarker"] == "task/destination"
    assert client.version_listing_requests[1]["VersionIdMarker"] == "delete-marker-2"
    with pytest.raises(StoragePreconditionError, match="continuation token"):
        storage.list_versions(
            ObjectVersionListRequest(
                reference=destination,
                page_size=2,
                continuation_token="a",
            )
        )
    assert storage.delete_marker(
        DeleteMarkerRequest(
            reference=destination.model_copy(update={"version_id": "delete-marker-2"})
        )
    )
    assert client.deleted_markers == [("task", "task/destination", "delete-marker-2")]
    client.fail_stream = True
    with (
        pytest.raises(StorageUnavailableError),
        storage.open_bounded_read(
            BoundedReadRequest(
                reference=source_stat.reference,
                maximum_bytes=len(payload),
                expected_etag=source_stat.etag,
            )
        ) as chunks,
    ):
        b"".join(chunks)


@pytest.mark.parametrize("reported_version_id", [None, "another-version"])
def test_minio_stat_rejects_an_inexact_requested_version(
    reported_version_id: str | None,
) -> None:
    class InexactVersionClient(_S3Client):
        def head_object(self, **params: object) -> dict[str, object]:
            response = super().head_object(**params)  # type: ignore[arg-type]
            response["VersionId"] = reported_version_id
            return response

    stored = _StoredObject(
        body=b"versioned",
        content_type="image/png",
        etag='"opaque"',
        metadata={},
        version_id="expected-version",
    )
    client = InexactVersionClient({("quarantine", "q/source"): stored})
    storage = MinioObjectStorage(
        endpoint="https://minio.internal.invalid",
        presign_endpoint="https://minio.invalid",
        access_key="access-key",
        secret_key="secret-key",
        session_token=None,
        region="us-east-1",
        buckets={StorageLocationClass.QUARANTINE: "quarantine"},
        verify_tls=True,
        force_path_style=True,
        require_encryption=True,
        connect_timeout=1,
        read_timeout=1,
        client=client,
        signer=client,
    )

    with pytest.raises(StoragePreconditionError, match="version"):
        storage.stat(
            ObjectReference(
                location=StorageLocationClass.QUARANTINE,
                key="q/source",
                version_id="expected-version",
            )
        )


def test_minio_adapter_does_not_misclassify_a_missing_bucket_as_a_missing_object() -> None:
    class MissingBucketClient(_S3Client):
        def head_object(
            self,
            *,
            Bucket: str,
            Key: str,
            VersionId: str | None = None,
        ) -> dict[str, object]:
            del Bucket, Key, VersionId
            raise ClientError(
                {
                    "Error": {"Code": "NoSuchBucket"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "HeadObject",
            )

    names = {
        StorageLocationClass.QUARANTINE: "quarantine",
        StorageLocationClass.TASK: "task",
        StorageLocationClass.FOUNDATION: "foundation",
        StorageLocationClass.PROVIDER_RESULT: "provider",
    }
    client = MissingBucketClient({})
    storage = MinioObjectStorage(
        endpoint="https://minio.internal.invalid",
        presign_endpoint="https://minio.invalid",
        access_key="access-key",
        secret_key="secret-key",
        session_token=None,
        region="us-east-1",
        buckets=names,
        verify_tls=True,
        force_path_style=True,
        require_encryption=True,
        connect_timeout=1,
        read_timeout=1,
        client=client,
        signer=client,
    )

    with pytest.raises(StorageUnavailableError):
        storage.stat(
            ObjectReference(
                location=StorageLocationClass.QUARANTINE,
                key="q/source",
            )
        )


def test_minio_readiness_requires_all_unique_buckets_versioned_and_encrypted() -> None:
    names = {
        StorageLocationClass.QUARANTINE: "quarantine",
        StorageLocationClass.TASK: "retained",
        StorageLocationClass.FOUNDATION: "retained",
        StorageLocationClass.PROVIDER_RESULT: "provider",
    }
    client = _S3Client({})
    storage = MinioObjectStorage(
        endpoint="https://minio.internal.invalid",
        presign_endpoint="https://minio.invalid",
        access_key="access-key",
        secret_key="secret-key",
        session_token=None,
        region="us-east-1",
        buckets=names,
        verify_tls=True,
        force_path_style=True,
        require_encryption=True,
        connect_timeout=1,
        read_timeout=5,
        client=client,
        signer=client,
    )

    storage.assert_ready()
    assert client.readiness_calls.count(("head", "retained")) == 1
    assert client.readiness_calls.count(("versioning", "retained")) == 1
    assert client.readiness_calls.count(("encryption", "retained")) == 1

    client.versioning_status["retained"] = "Suspended"
    with pytest.raises(ObjectStorageReadinessError, match="versioning"):
        storage.assert_ready()

    client.versioning_status["retained"] = "Enabled"
    client.encryption_algorithm["provider"] = None
    with pytest.raises(ObjectStorageReadinessError, match="encryption"):
        storage.assert_ready()


def test_minio_readiness_probes_only_the_locations_required_by_the_process() -> None:
    names = {
        StorageLocationClass.QUARANTINE: "quarantine",
        StorageLocationClass.TASK: "retained",
        StorageLocationClass.FOUNDATION: "retained",
        StorageLocationClass.PROVIDER_RESULT: "provider",
    }
    client = _S3Client({})
    storage = MinioObjectStorage(
        endpoint="https://minio.internal.invalid",
        presign_endpoint="https://minio.invalid",
        access_key="access-key",
        secret_key="secret-key",
        session_token=None,
        region="us-east-1",
        buckets=names,
        verify_tls=True,
        force_path_style=True,
        require_encryption=True,
        connect_timeout=1,
        read_timeout=5,
        client=client,
        signer=client,
    )
    client.versioning_status["provider"] = "Suspended"

    storage.assert_ready(
        required_locations=(
            StorageLocationClass.QUARANTINE,
            StorageLocationClass.TASK,
            StorageLocationClass.FOUNDATION,
        )
    )

    assert ("head", "quarantine") in client.readiness_calls
    assert ("head", "retained") in client.readiness_calls
    assert ("head", "provider") not in client.readiness_calls


def test_minio_readiness_rejects_an_empty_bucket_topology() -> None:
    client = _S3Client({})
    storage = MinioObjectStorage(
        endpoint="https://minio.internal.invalid",
        presign_endpoint="https://minio.invalid",
        access_key="access-key",
        secret_key="secret-key",
        session_token=None,
        region="us-east-1",
        buckets={},
        verify_tls=True,
        force_path_style=True,
        require_encryption=True,
        connect_timeout=1,
        read_timeout=5,
        client=client,
        signer=client,
    )

    with pytest.raises(ObjectStorageReadinessError, match="bucket mapping"):
        storage.assert_ready()


def test_minio_readiness_does_not_accept_a_missing_bucket() -> None:
    class MissingBucketClient(_S3Client):
        def head_bucket(self, *, Bucket: str) -> dict[str, object]:
            raise ClientError(
                {
                    "Error": {"Code": "NoSuchBucket"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                f"HeadBucket:{Bucket}",
            )

    names = {
        StorageLocationClass.QUARANTINE: "quarantine",
        StorageLocationClass.TASK: "task",
        StorageLocationClass.FOUNDATION: "foundation",
        StorageLocationClass.PROVIDER_RESULT: "provider",
    }
    client = MissingBucketClient({})
    storage = MinioObjectStorage(
        endpoint="https://minio.internal.invalid",
        presign_endpoint="https://minio.invalid",
        access_key="access-key",
        secret_key="secret-key",
        session_token=None,
        region="us-east-1",
        buckets=names,
        verify_tls=True,
        force_path_style=True,
        require_encryption=True,
        connect_timeout=1,
        read_timeout=5,
        client=client,
        signer=client,
    )

    with pytest.raises(ObjectStorageReadinessError, match="not accessible"):
        storage.assert_ready()


class _OssReadResult:
    def __init__(self, body: bytes, *, fail: bool = False) -> None:
        self._stream = BytesIO(body)
        self.closed = False
        self.fail = fail

    def read(self, size: int) -> bytes:
        if self.fail:
            raise oss2.exceptions.RequestError(TimeoutError("stream timed out"))
        return self._stream.read(size)

    def close(self) -> None:
        self.closed = True


_UNSET = object()


class _OssBucket:
    def __init__(
        self,
        name: str,
        objects: dict[tuple[str, str], _StoredObject],
    ) -> None:
        self.name = name
        self.objects = objects
        self.versions: dict[tuple[str, str, str], _StoredObject] = {
            (bucket, key, stored.version_id): stored
            for (bucket, key), stored in objects.items()
            if stored.version_id is not None
        }
        self.copy_collision: _StoredObject | None = None
        self.copy_count = 0
        self.copy_result_version_override: object = _UNSET
        self.fail_stream = False
        self.last_read_version_id: str | None = None
        self.omit_version_headers: set[str] = set()
        self.versioning_status = "Enabled"
        self.encryption_algorithm: str | None = "AES256"
        self.readiness_calls: list[str] = []
        self.version_listing_pages: list[SimpleNamespace] = []
        self.version_listing_requests: list[dict[str, object]] = []
        self.deleted_markers: list[tuple[str, str]] = []

    def get_bucket_versioning(self) -> SimpleNamespace:
        self.readiness_calls.append("versioning")
        return SimpleNamespace(status=self.versioning_status)

    def get_bucket_encryption(self) -> SimpleNamespace:
        self.readiness_calls.append("encryption")
        return SimpleNamespace(sse_algorithm=self.encryption_algorithm)

    def sign_url(
        self,
        method: str,
        key: str,
        expires: int,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        **_: Any,
    ) -> str:
        assert expires > 0
        suffix = f"?versionId={params['versionId']}" if params else ""
        return f"https://signed.invalid/{self.name}/{key}{suffix}"

    def head_object(
        self,
        key: str,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> SimpleNamespace:
        del headers
        version_id = params.get("versionId") if params is not None else None
        stored = (
            self.versions.get((self.name, key, version_id))
            if version_id is not None
            else self.objects.get((self.name, key))
        )
        if stored is None:
            raise oss2.exceptions.NoSuchKey(404, {}, b"", {})
        response_headers = {
            "content-length": str(len(stored.body)),
            "content-type": stored.content_type,
            "etag": stored.etag,
            "last-modified": "Tue, 21 Jul 2026 12:00:00 GMT",
            **{f"x-oss-meta-{name}": value for name, value in stored.metadata.items()},
        }
        if stored.version_id is not None and stored.version_id not in self.omit_version_headers:
            response_headers["x-oss-version-id"] = stored.version_id
        return SimpleNamespace(
            headers=response_headers,
            etag=stored.etag,
            content_length=len(stored.body),
            content_type=stored.content_type,
            last_modified=1784635200,
        )

    def get_object(
        self,
        key: str,
        byte_range: tuple[int, int],
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> _OssReadResult:
        version_id = params.get("versionId") if params is not None else None
        stored = (
            self.versions[(self.name, key, version_id)]
            if version_id is not None
            else self.objects[(self.name, key)]
        )
        self.last_read_version_id = version_id
        if headers and headers.get("If-Match") != stored.etag:
            raise oss2.exceptions.PreconditionFailed(412, {}, b"", {})
        start, end = byte_range
        return _OssReadResult(
            stored.body[start : end + 1],
            fail=self.fail_stream,
        )

    def copy_object(
        self,
        source_bucket_name: str,
        source_key: str,
        target_key: str,
        headers: dict[str, str],
        params: dict[str, str] | None = None,
    ) -> SimpleNamespace:
        source = self.objects[(source_bucket_name, source_key)]
        assert params == (
            {"versionId": source.version_id} if source.version_id is not None else None
        )
        if headers["x-oss-copy-source-if-match"] != source.etag:
            raise oss2.exceptions.PreconditionFailed(412, {}, b"", {})
        if self.copy_collision is not None:
            self.objects[(self.name, target_key)] = self.copy_collision
            if self.copy_collision.version_id is not None:
                self.versions[(self.name, target_key, self.copy_collision.version_id)] = (
                    self.copy_collision
                )
            self.copy_collision = None
        if (
            headers.get("x-oss-forbid-overwrite") == "true"
            and (self.name, target_key) in self.objects
        ):
            raise oss2.exceptions.OssError(
                409,
                {},
                b"",
                {
                    "Code": "FileAlreadyExists",
                    "Message": "destination already exists",
                },
            )
        self.copy_count += 1
        version_id = f"copy-version-{self.copy_count}"
        copied = _StoredObject(
            body=source.body,
            content_type=headers["Content-Type"],
            etag=f'"opaque-copy-etag-{self.copy_count}"',
            metadata={
                "sha256": headers["x-oss-meta-sha256"],
                "upload-session-id": headers["x-oss-meta-upload-session-id"],
            },
            version_id=version_id,
        )
        self.objects[(self.name, target_key)] = copied
        self.versions[(self.name, target_key, version_id)] = copied
        result_version_id = (
            version_id
            if self.copy_result_version_override is _UNSET
            else self.copy_result_version_override
        )
        return SimpleNamespace(etag=copied.etag, versionid=result_version_id)

    def delete_object(
        self,
        key: str,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> SimpleNamespace:
        version_id = params.get("versionId") if params is not None else None
        if (
            headers is None
            and version_id is not None
            and (self.name, key, version_id) not in self.versions
        ):
            self.deleted_markers.append((key, version_id))
            return SimpleNamespace(status=204)
        stored = (
            self.versions[(self.name, key, version_id)]
            if version_id is not None
            else self.objects[(self.name, key)]
        )
        if headers and headers.get("If-Match") != stored.etag:
            raise oss2.exceptions.PreconditionFailed(412, {}, b"", {})
        if version_id is not None:
            del self.versions[(self.name, key, version_id)]
            if self.objects.get((self.name, key)) is stored:
                del self.objects[(self.name, key)]
        else:
            del self.objects[(self.name, key)]
        return SimpleNamespace(status=204)

    def list_object_versions(self, **params: object) -> SimpleNamespace:
        self.version_listing_requests.append(params)
        if self.version_listing_pages:
            return self.version_listing_pages.pop(0)
        return SimpleNamespace(
            versions=[],
            delete_marker=[],
            is_truncated=False,
            next_key_marker="",
            next_versionid_marker="",
        )


def test_oss_adapter_contract_covers_bounded_read_copy_delete_and_temporary_read() -> None:
    payload = b"adapter-contract-payload"
    sha256 = hashlib.sha256(payload).hexdigest()
    names = {
        StorageLocationClass.QUARANTINE: "quarantine",
        StorageLocationClass.TASK: "task",
        StorageLocationClass.FOUNDATION: "foundation",
        StorageLocationClass.PROVIDER_RESULT: "provider",
    }
    objects = {
        ("quarantine", "q/source"): _StoredObject(
            body=payload,
            content_type="image/png",
            etag='"opaque/source-etag"',
            metadata={"sha256": sha256},
            version_id="source-version-1",
        )
    }
    clients = {location: _OssBucket(name, objects) for location, name in names.items()}
    storage = OssObjectStorage(
        endpoint="https://oss.internal.invalid",
        presign_endpoint="https://oss.invalid",
        access_key="access-key",
        secret_key="secret-key",
        session_token=None,
        region="cn-hangzhou",
        buckets=names,
        force_path_style=False,
        require_encryption=True,
        connect_timeout=1,
        clients=clients,
        signers=clients,
    )
    source = ObjectReference(
        location=StorageLocationClass.QUARANTINE,
        key="q/source",
    )
    destination = ObjectReference(
        location=StorageLocationClass.TASK,
        key="task/destination",
    )

    source_stat = storage.stat(source)
    assert source_stat.etag == '"opaque/source-etag"'
    assert source_stat.reference.version_id == "source-version-1"
    with storage.open_bounded_read(
        BoundedReadRequest(
            reference=source,
            maximum_bytes=len(payload),
            expected_etag=source_stat.etag,
        )
    ) as chunks:
        assert b"".join(chunks) == payload
    assert (
        clients[StorageLocationClass.QUARANTINE].last_read_version_id
        == source_stat.reference.version_id
    )
    with (
        pytest.raises(ObjectMismatchError),
        storage.open_bounded_read(
            BoundedReadRequest(reference=source, maximum_bytes=len(payload) - 1)
        ),
    ):
        pass

    copy_request = ConditionalCopyRequest(
        source=source_stat.reference,
        destination=destination,
        source_etag=source_stat.etag,
        expected_content_length=len(payload),
        expected_sha256=sha256,
        content_type="image/png",
        upload_session_id="019f8a00-0000-7000-8000-000000000001",
    )
    first_copy = storage.copy_if_absent(copy_request)
    retry_after_lost_response = storage.copy_if_absent(copy_request)
    assert first_copy.etag == retry_after_lost_response.etag
    assert retry_after_lost_response.content_length == len(payload)
    assert retry_after_lost_response.metadata["upload-session-id"] == copy_request.upload_session_id

    collision_destination = ObjectReference(
        location=StorageLocationClass.TASK,
        key="task/collision",
    )
    clients[StorageLocationClass.TASK].copy_collision = _StoredObject(
        body=b"different",
        content_type="image/png",
        etag='"collision-etag"',
        metadata={"sha256": hashlib.sha256(b"different").hexdigest()},
        version_id="concurrent-version",
    )
    with pytest.raises(StoragePreconditionError):
        storage.copy_if_absent(
            copy_request.model_copy(update={"destination": collision_destination})
        )
    assert (
        clients[StorageLocationClass.TASK]
        .versions[("task", "task/collision", "concurrent-version")]
        .body
        == b"different"
    )
    assert (
        clients[StorageLocationClass.TASK].objects[("task", "task/collision")].version_id
        == "concurrent-version"
    )

    matching_collision_destination = ObjectReference(
        location=StorageLocationClass.TASK,
        key="task/matching-collision",
    )
    clients[StorageLocationClass.TASK].copy_collision = _StoredObject(
        body=payload,
        content_type="image/png",
        etag='"matching-collision-etag"',
        metadata={
            "sha256": sha256,
            "upload-session-id": copy_request.upload_session_id,
        },
        version_id="matching-concurrent-version",
    )
    matching_race = storage.copy_if_absent(
        copy_request.model_copy(update={"destination": matching_collision_destination})
    )
    assert matching_race.reference.version_id == "matching-concurrent-version"
    assert clients[StorageLocationClass.TASK].copy_count == 1

    temporary = storage.temporary_read(
        TemporaryReadRequest(
            reference=first_copy.reference,
            expires_at=datetime.now(UTC) + timedelta(seconds=30),
            expected_etag=first_copy.etag,
        )
    )
    assert temporary.method == "GET"
    assert temporary.required_headers == {"If-Match": first_copy.etag}
    assert "versionId=copy-version-1" in temporary.url

    with pytest.raises(StoragePreconditionError):
        storage.delete_if_match(
            ConditionalDeleteRequest(
                reference=destination,
                expected_etag='"another-etag"',
            )
        )
    assert storage.delete_if_match(
        ConditionalDeleteRequest(
            reference=destination,
            expected_etag=first_copy.etag,
        )
    )
    assert storage.delete_if_match(
        ConditionalDeleteRequest(
            reference=destination,
            expected_etag=first_copy.etag,
        )
    )
    task_client = clients[StorageLocationClass.TASK]
    task_client.version_listing_pages = [
        SimpleNamespace(
            versions=[
                SimpleNamespace(key="task/destination", versionid="version-3"),
                SimpleNamespace(key="task/destination-suffix", versionid="other-version"),
            ],
            delete_marker=[SimpleNamespace(key="task/destination", versionid="delete-marker-2")],
            is_truncated=True,
            next_key_marker="task/destination",
            next_versionid_marker="delete-marker-2",
        ),
        SimpleNamespace(
            versions=[SimpleNamespace(key="task/destination", versionid="version-1")],
            delete_marker=[],
            is_truncated=False,
            next_key_marker="",
            next_versionid_marker="",
        ),
    ]
    first_page = storage.list_versions(ObjectVersionListRequest(reference=destination, page_size=2))
    assert [(entry.kind, entry.reference.version_id) for entry in first_page.entries] == [
        ("OBJECT", "version-3"),
        ("DELETE_MARKER", "delete-marker-2"),
    ]
    assert first_page.continuation_token is not None
    second_page = storage.list_versions(
        ObjectVersionListRequest(
            reference=destination,
            page_size=2,
            continuation_token=first_page.continuation_token,
        )
    )
    assert [entry.reference.version_id for entry in second_page.entries] == ["version-1"]
    assert second_page.continuation_token is None
    assert task_client.version_listing_requests[1]["key_marker"] == "task/destination"
    assert task_client.version_listing_requests[1]["versionid_marker"] == "delete-marker-2"
    with pytest.raises(StoragePreconditionError, match="continuation token"):
        storage.list_versions(
            ObjectVersionListRequest(
                reference=destination,
                page_size=2,
                continuation_token="a",
            )
        )
    assert storage.delete_marker(
        DeleteMarkerRequest(
            reference=destination.model_copy(update={"version_id": "delete-marker-2"})
        )
    )
    assert task_client.deleted_markers == [("task/destination", "delete-marker-2")]
    clients[StorageLocationClass.QUARANTINE].fail_stream = True
    with (
        pytest.raises(StorageUnavailableError),
        storage.open_bounded_read(
            BoundedReadRequest(
                reference=source_stat.reference,
                maximum_bytes=len(payload),
                expected_etag=source_stat.etag,
            )
        ) as chunks,
    ):
        b"".join(chunks)


def _oss_copy_contract(
    *,
    source_version_id: str | None = "source-version-1",
) -> tuple[
    OssObjectStorage,
    dict[StorageLocationClass, _OssBucket],
    ConditionalCopyRequest,
]:
    payload = b"adapter-contract-payload"
    sha256 = hashlib.sha256(payload).hexdigest()
    names = {
        StorageLocationClass.QUARANTINE: "quarantine",
        StorageLocationClass.TASK: "task",
        StorageLocationClass.FOUNDATION: "foundation",
        StorageLocationClass.PROVIDER_RESULT: "provider",
    }
    objects = {
        ("quarantine", "q/source"): _StoredObject(
            body=payload,
            content_type="image/png",
            etag='"opaque/source-etag"',
            metadata={"sha256": sha256},
            version_id=source_version_id,
        )
    }
    clients = {location: _OssBucket(name, objects) for location, name in names.items()}
    storage = OssObjectStorage(
        endpoint="https://oss.internal.invalid",
        presign_endpoint="https://oss.invalid",
        access_key="access-key",
        secret_key="secret-key",
        session_token=None,
        region="cn-hangzhou",
        buckets=names,
        force_path_style=False,
        require_encryption=True,
        connect_timeout=1,
        clients=clients,
        signers=clients,
    )
    source = storage.stat(
        ObjectReference(
            location=StorageLocationClass.QUARANTINE,
            key="q/source",
        )
    )
    request = ConditionalCopyRequest(
        source=source.reference,
        destination=ObjectReference(
            location=StorageLocationClass.TASK,
            key="task/destination",
        ),
        source_etag=source.etag,
        expected_content_length=len(payload),
        expected_sha256=sha256,
        content_type="image/png",
        upload_session_id="019f8a00-0000-7000-8000-000000000001",
    )
    return storage, clients, request


@pytest.mark.parametrize("source_version_id", [None, "", "null", " NULL "])
def test_oss_copy_requires_an_exact_source_version(
    source_version_id: str | None,
) -> None:
    storage, _, request = _oss_copy_contract(
        source_version_id=source_version_id,
    )

    with pytest.raises(StoragePreconditionError, match="source version"):
        storage.copy_if_absent(request)


def test_oss_copy_rejects_an_unversioned_matching_destination() -> None:
    storage, clients, request = _oss_copy_contract()
    clients[StorageLocationClass.TASK].objects[("task", request.destination.key)] = _StoredObject(
        body=b"adapter-contract-payload",
        content_type=request.content_type,
        etag='"matching-but-unversioned"',
        metadata={
            "sha256": request.expected_sha256,
            "upload-session-id": request.upload_session_id,
        },
        version_id=None,
    )

    with pytest.raises(StoragePreconditionError, match="destination version"):
        storage.copy_if_absent(request)


@pytest.mark.parametrize("result_version_id", [None, "", "null", " NULL "])
def test_oss_copy_rejects_a_missing_result_version(
    result_version_id: str | None,
) -> None:
    storage, clients, request = _oss_copy_contract()
    clients[StorageLocationClass.TASK].copy_result_version_override = result_version_id

    with pytest.raises(StoragePreconditionError, match="result version"):
        storage.copy_if_absent(request)


def test_oss_copy_rejects_a_post_copy_head_without_the_exact_version() -> None:
    storage, clients, request = _oss_copy_contract()
    clients[StorageLocationClass.TASK].omit_version_headers.add("copy-version-1")

    with pytest.raises(StoragePreconditionError, match="copied object version"):
        storage.copy_if_absent(request)


def test_oss_readiness_requires_all_unique_buckets_versioned_and_encrypted() -> None:
    names = {
        StorageLocationClass.QUARANTINE: "quarantine",
        StorageLocationClass.TASK: "retained",
        StorageLocationClass.FOUNDATION: "retained",
        StorageLocationClass.PROVIDER_RESULT: "provider",
    }
    clients_by_name = {name: _OssBucket(name, {}) for name in set(names.values())}
    clients = {location: clients_by_name[name] for location, name in names.items()}
    storage = OssObjectStorage(
        endpoint="https://oss.internal.invalid",
        presign_endpoint="https://oss.invalid",
        access_key="access-key",
        secret_key="secret-key",
        session_token=None,
        region="cn-hangzhou",
        buckets=names,
        force_path_style=False,
        require_encryption=True,
        connect_timeout=1,
        read_timeout=5,
        clients=clients,
        signers=clients,
    )

    storage.assert_ready()
    assert clients_by_name["retained"].readiness_calls == [
        "versioning",
        "encryption",
    ]

    clients_by_name["retained"].versioning_status = "Suspended"
    with pytest.raises(ObjectStorageReadinessError, match="versioning"):
        storage.assert_ready()

    clients_by_name["retained"].versioning_status = "Enabled"
    clients_by_name["provider"].encryption_algorithm = None
    provider_probe_count = len(clients_by_name["provider"].readiness_calls)
    storage.assert_ready(
        {
            StorageLocationClass.QUARANTINE,
            StorageLocationClass.TASK,
            StorageLocationClass.FOUNDATION,
        }
    )
    assert len(clients_by_name["provider"].readiness_calls) == provider_probe_count
    with pytest.raises(ObjectStorageReadinessError, match="encryption"):
        storage.assert_ready()


def test_oss_readiness_rejects_an_empty_bucket_topology() -> None:
    storage = OssObjectStorage(
        endpoint="https://oss.internal.invalid",
        presign_endpoint="https://oss.invalid",
        access_key="access-key",
        secret_key="secret-key",
        session_token=None,
        region="cn-hangzhou",
        buckets={},
        force_path_style=False,
        require_encryption=True,
        connect_timeout=1,
        read_timeout=5,
        clients={},
        signers={},
    )

    with pytest.raises(ObjectStorageReadinessError, match="bucket mapping"):
        storage.assert_ready()


def test_oss_readiness_does_not_accept_a_missing_bucket() -> None:
    class MissingOssBucket(_OssBucket):
        def get_bucket_versioning(self) -> SimpleNamespace:
            raise oss2.exceptions.NoSuchBucket(404, {}, b"", {})

    names = {
        StorageLocationClass.QUARANTINE: "quarantine",
        StorageLocationClass.TASK: "task",
        StorageLocationClass.FOUNDATION: "foundation",
        StorageLocationClass.PROVIDER_RESULT: "provider",
    }
    clients = {location: MissingOssBucket(bucket, {}) for location, bucket in names.items()}
    storage = OssObjectStorage(
        endpoint="https://oss.internal.invalid",
        presign_endpoint="https://oss.invalid",
        access_key="access-key",
        secret_key="secret-key",
        session_token=None,
        region="cn-hangzhou",
        buckets=names,
        force_path_style=False,
        require_encryption=True,
        connect_timeout=1,
        read_timeout=5,
        clients=clients,
        signers=clients,
    )

    with pytest.raises(ObjectStorageReadinessError, match="not accessible"):
        storage.assert_ready()


def test_oss_no_such_version_is_an_object_missing_error() -> None:
    class MissingVersionOssBucket(_OssBucket):
        def head_object(
            self,
            key: str,
            headers: dict[str, str] | None = None,
            params: dict[str, str] | None = None,
        ) -> SimpleNamespace:
            del key, headers, params
            raise oss2.exceptions.OssError(
                404,
                {},
                b"",
                {"Code": "NoSuchVersion", "Message": "version does not exist"},
            )

    names = {
        StorageLocationClass.QUARANTINE: "quarantine",
    }
    clients = {
        StorageLocationClass.QUARANTINE: MissingVersionOssBucket(
            "quarantine",
            {},
        )
    }
    storage = OssObjectStorage(
        endpoint="https://oss.internal.invalid",
        presign_endpoint="https://oss.invalid",
        access_key="access-key",
        secret_key="secret-key",
        session_token=None,
        region="cn-hangzhou",
        buckets=names,
        force_path_style=False,
        require_encryption=True,
        connect_timeout=1,
        clients=clients,
        signers=clients,
    )

    with pytest.raises(UploadObjectMissingError):
        storage.stat(
            ObjectReference(
                location=StorageLocationClass.QUARANTINE,
                key="q/source",
                version_id="missing-version",
            )
        )


@pytest.mark.parametrize("reported_version_id", [None, "another-version"])
def test_oss_stat_rejects_an_inexact_requested_version(
    reported_version_id: str | None,
) -> None:
    class InexactVersionBucket(_OssBucket):
        def head_object(
            self,
            key: str,
            headers: dict[str, str] | None = None,
            params: dict[str, str] | None = None,
        ) -> SimpleNamespace:
            response = super().head_object(key, headers=headers, params=params)
            if reported_version_id is None:
                response.headers.pop("x-oss-version-id", None)
            else:
                response.headers["x-oss-version-id"] = reported_version_id
            return response

    stored = _StoredObject(
        body=b"versioned",
        content_type="image/png",
        etag='"opaque"',
        metadata={},
        version_id="expected-version",
    )
    client = InexactVersionBucket(
        "quarantine",
        {("quarantine", "q/source"): stored},
    )
    storage = OssObjectStorage(
        endpoint="https://oss.internal.invalid",
        presign_endpoint="https://oss.invalid",
        access_key="access-key",
        secret_key="secret-key",
        session_token=None,
        region="cn-hangzhou",
        buckets={StorageLocationClass.QUARANTINE: "quarantine"},
        force_path_style=False,
        require_encryption=True,
        connect_timeout=1,
        clients={StorageLocationClass.QUARANTINE: client},
        signers={StorageLocationClass.QUARANTINE: client},
    )

    with pytest.raises(StoragePreconditionError, match="version"):
        storage.stat(
            ObjectReference(
                location=StorageLocationClass.QUARANTINE,
                key="q/source",
                version_id="expected-version",
            )
        )


def test_oss_factory_applies_distinct_connect_and_read_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_timeouts: list[object] = []

    class BucketProbe:
        def __init__(
            self,
            *_args: object,
            connect_timeout: object,
            **_kwargs: object,
        ) -> None:
            observed_timeouts.append(connect_timeout)

    monkeypatch.setattr(oss2, "Bucket", BucketProbe)
    build_object_storage(
        Settings(
            environment="ci",
            object_store_backend="oss",
            object_store_endpoint="https://oss-cn-hangzhou.aliyuncs.com",
            object_store_presign_endpoint="https://oss-cn-hangzhou.aliyuncs.com",
            object_store_access_key="access-key",
            object_store_secret_key="secret-key",
            object_store_region="cn-hangzhou",
            object_store_force_path_style=False,
            object_store_connect_timeout_seconds=2,
            object_store_read_timeout_seconds=17,
        )
    )

    assert (2, 17) in observed_timeouts
    assert 2 not in observed_timeouts


def test_alibaba_credentials_provider_reads_fresh_credentials_per_request() -> None:
    class RotatingClient:
        def __init__(self) -> None:
            self.calls = 0

        def get_credential(self) -> SimpleNamespace:
            self.calls += 1
            return SimpleNamespace(
                access_key_id=f"access-{self.calls}",
                access_key_secret=f"secret-{self.calls}",
                security_token=f"token-{self.calls}",
            )

    client = RotatingClient()
    provider = AlibabaCloudCredentialsProvider(client)

    first = provider.get_credentials()
    second = provider.get_credentials()

    assert first.get_access_key_id() == "access-1"
    assert first.get_security_token() == "token-1"
    assert second.get_access_key_id() == "access-2"
    assert second.get_access_key_secret() == "secret-2"
    assert client.calls == 2


def test_alibaba_credentials_provider_serializes_sdk_refreshes() -> None:
    class ConcurrentClient:
        def __init__(self) -> None:
            self._state_lock = Lock()
            self.active_calls = 0
            self.maximum_active_calls = 0
            self.calls = 0

        def get_credential(self) -> SimpleNamespace:
            with self._state_lock:
                self.active_calls += 1
                self.maximum_active_calls = max(
                    self.maximum_active_calls,
                    self.active_calls,
                )
                self.calls += 1
                call = self.calls
            time.sleep(0.02)
            with self._state_lock:
                self.active_calls -= 1
            return SimpleNamespace(
                access_key_id=f"access-{call}",
                access_key_secret=f"secret-{call}",
                security_token=f"token-{call}",
            )

    client = ConcurrentClient()
    provider = AlibabaCloudCredentialsProvider(client)

    with ThreadPoolExecutor(max_workers=8) as executor:
        credentials = list(executor.map(lambda _: provider.get_credentials(), range(16)))

    assert len(credentials) == 16
    assert client.calls == 16
    assert client.maximum_active_calls == 1


def test_alibaba_credentials_provider_bounds_and_deduplicates_a_stuck_refresh() -> None:
    class BlockingClient:
        def __init__(self) -> None:
            self.calls = 0
            self.started = Event()
            self.release = Event()

        def get_credential(self) -> SimpleNamespace:
            self.calls += 1
            self.started.set()
            self.release.wait(timeout=1)
            return SimpleNamespace(
                access_key_id="access",
                access_key_secret="secret",
                security_token="token",
            )

    client = BlockingClient()
    provider = AlibabaCloudCredentialsProvider(
        client,
        refresh_timeout_seconds=0.02,
    )
    try:
        with pytest.raises(StorageUnavailableError, match="timed out"):
            provider.get_credentials()
        assert client.started.wait(timeout=1)
        with pytest.raises(StorageUnavailableError, match="timed out"):
            provider.get_credentials()
        assert client.calls == 1

        client.release.set()
        credentials = provider.get_credentials()
        assert credentials.get_access_key_id() == "access"
        assert client.calls == 1
    finally:
        client.release.set()
        provider.close()


def test_alibaba_credentials_provider_does_not_cache_a_completed_timeout() -> None:
    class TimeoutClient:
        def __init__(self) -> None:
            self.calls = 0

        def get_credential(self) -> SimpleNamespace:
            self.calls += 1
            raise TimeoutError("credential endpoint timed out")

    client = TimeoutClient()
    provider = AlibabaCloudCredentialsProvider(client)
    try:
        for _ in range(2):
            with pytest.raises(StorageUnavailableError, match="timed out"):
                provider.get_credentials()
        assert client.calls == 2
    finally:
        provider.close()


def test_timed_out_credential_refresh_does_not_block_process_exit() -> None:
    script = textwrap.dedent(
        """
        from threading import Event

        from commercevision_domain import StorageUnavailableError
        from commercevision_object_storage.credentials import (
            AlibabaCloudCredentialsProvider,
        )

        class BlockingClient:
            def get_credential(self):
                Event().wait()

        provider = AlibabaCloudCredentialsProvider(
            BlockingClient(),
            refresh_timeout_seconds=0.01,
        )
        try:
            provider.get_credentials()
        except StorageUnavailableError:
            pass
        finally:
            provider.close()
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        check=False,
        text=True,
        timeout=5,
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "failure",
    [
        CredentialException("credential refresh failed"),
        TeaException({"code": "CredentialError", "message": "tea refresh failed"}),
        OSError("token file disappeared"),
    ],
)
def test_alibaba_credentials_provider_normalizes_refresh_failures(
    failure: Exception,
) -> None:
    class FailingClient:
        def get_credential(self) -> SimpleNamespace:
            raise failure

    provider = AlibabaCloudCredentialsProvider(FailingClient())

    with pytest.raises(StorageUnavailableError, match="credential refresh failed"):
        provider.get_credentials()


@pytest.mark.parametrize(
    "credential",
    [
        None,
        SimpleNamespace(
            access_key_id=None,
            access_key_secret="secret",
            security_token="token",
        ),
        SimpleNamespace(
            access_key_id="access",
            access_key_secret=None,
            security_token="token",
        ),
    ],
)
def test_alibaba_credentials_provider_rejects_malformed_snapshots(
    credential: object,
) -> None:
    class MalformedClient:
        def get_credential(self) -> object:
            return credential

    provider = AlibabaCloudCredentialsProvider(MalformedClient())  # type: ignore[arg-type]

    with pytest.raises(StorageUnavailableError, match="incomplete credentials"):
        provider.get_credentials()


def test_ecs_workload_identity_disables_imdsv1_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    class ClientProbe:
        def __init__(self, config: object) -> None:
            captured.append(config)

    monkeypatch.setattr(
        "commercevision_object_storage.credentials.AlibabaCredentialsClient",
        ClientProbe,
    )

    create_oss_credentials_provider(
        Settings(
            environment="ci",
            object_store_backend="oss",
            object_store_credential_mode="ecs_ram_role",
            object_store_ram_role_name="commercevision-assets",
        )
    )

    assert len(captured) == 1
    config = captured[0]
    assert config.enable_imds_v2 is True  # type: ignore[attr-defined]
    assert config.disable_imds_v1 is True  # type: ignore[attr-defined]


def test_oidc_workload_identity_uses_the_configured_sts_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    class ClientProbe:
        def __init__(self, config: object) -> None:
            captured.append(config)

    monkeypatch.setattr(
        "commercevision_object_storage.credentials.AlibabaCredentialsClient",
        ClientProbe,
    )
    create_oss_credentials_provider(
        Settings(
            environment="ci",
            object_store_backend="oss",
            object_store_credential_mode="oidc_role_arn",
            object_store_oidc_role_arn="acs:ram::1234567890123456:role/assets",
            object_store_oidc_provider_arn=("acs:ram::1234567890123456:oidc-provider/assets"),
            object_store_oidc_token_file_path="/var/run/secrets/aliyun/token",
            object_store_sts_endpoint="sts-vpc.cn-hangzhou.aliyuncs.com",
        )
    )

    assert len(captured) == 1
    assert captured[0].sts_endpoint == "sts-vpc.cn-hangzhou.aliyuncs.com"  # type: ignore[attr-defined]


def test_oss_factory_uses_renewable_provider_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_auth: list[object] = []
    credential_provider = object()

    class BucketProbe:
        def __init__(self, auth: object, *_args: object, **_kwargs: object) -> None:
            observed_auth.append(auth)

    monkeypatch.setattr(oss2, "Bucket", BucketProbe)
    monkeypatch.setattr(
        "commercevision_object_storage.object_storage.create_oss_credentials_provider",
        lambda _settings: credential_provider,
    )

    build_object_storage(
        Settings(
            environment="ci",
            object_store_backend="oss",
            object_store_credential_mode="ecs_ram_role",
            object_store_endpoint="https://oss-cn-hangzhou.aliyuncs.com",
            object_store_presign_endpoint="https://oss-cn-hangzhou.aliyuncs.com",
            object_store_region="cn-hangzhou",
            object_store_force_path_style=False,
        )
    )

    assert observed_auth
    assert all(isinstance(auth, oss2.ProviderAuthV4) for auth in observed_auth)
    assert all(auth.credentials_provider is credential_provider for auth in observed_auth)


def test_oss_adapter_does_not_misclassify_a_missing_bucket_as_a_missing_object() -> None:
    class MissingBucketClient(_OssBucket):
        def head_object(
            self,
            key: str,
            headers: dict[str, str] | None = None,
            params: dict[str, str] | None = None,
        ) -> SimpleNamespace:
            del key, headers, params
            raise oss2.exceptions.NoSuchBucket(404, {}, b"", {})

    names = {
        StorageLocationClass.QUARANTINE: "quarantine",
        StorageLocationClass.TASK: "task",
        StorageLocationClass.FOUNDATION: "foundation",
        StorageLocationClass.PROVIDER_RESULT: "provider",
    }
    clients = {location: MissingBucketClient(name, {}) for location, name in names.items()}
    storage = OssObjectStorage(
        endpoint="https://oss.internal.invalid",
        presign_endpoint="https://oss.invalid",
        access_key="access-key",
        secret_key="secret-key",
        session_token=None,
        region="cn-hangzhou",
        buckets=names,
        force_path_style=False,
        require_encryption=True,
        connect_timeout=1,
        clients=clients,
        signers=clients,
    )

    with pytest.raises(StorageUnavailableError):
        storage.stat(
            ObjectReference(
                location=StorageLocationClass.QUARANTINE,
                key="q/source",
            )
        )


def test_oss_conditional_delete_requires_a_provider_version_id() -> None:
    names = {
        StorageLocationClass.QUARANTINE: "quarantine",
        StorageLocationClass.TASK: "task",
        StorageLocationClass.FOUNDATION: "foundation",
        StorageLocationClass.PROVIDER_RESULT: "provider",
    }
    objects = {
        ("quarantine", "q/unversioned"): _StoredObject(
            body=b"unversioned",
            content_type="image/png",
            etag='"opaque-etag"',
            metadata={"upload-session-id": "session-1"},
            version_id=None,
        )
    }
    clients = {location: _OssBucket(name, objects) for location, name in names.items()}
    storage = OssObjectStorage(
        endpoint="https://oss.internal.invalid",
        presign_endpoint="https://oss.invalid",
        access_key="access-key",
        secret_key="secret-key",
        session_token=None,
        region="cn-hangzhou",
        buckets=names,
        force_path_style=False,
        require_encryption=True,
        connect_timeout=1,
        clients=clients,
        signers=clients,
    )

    with pytest.raises(StoragePreconditionError, match="versioning"):
        storage.delete_if_match(
            ConditionalDeleteRequest(
                reference=ObjectReference(
                    location=StorageLocationClass.QUARANTINE,
                    key="q/unversioned",
                ),
                expected_etag='"opaque-etag"',
            )
        )
    assert ("quarantine", "q/unversioned") in objects
