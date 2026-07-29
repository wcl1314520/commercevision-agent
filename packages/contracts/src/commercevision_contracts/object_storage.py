"""Typed internal contracts shared by object-storage adapters."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import AbstractContextManager
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from commercevision_domain import StorageBackend, StorageLocationClass
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StorageContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ObjectReference(StorageContract):
    location: StorageLocationClass
    key: str = Field(min_length=1, max_length=1024)
    version_id: str | None = Field(default=None, max_length=256)


class PresignPutRequest(StorageContract):
    reference: ObjectReference
    content_type: str = Field(min_length=1, max_length=128)
    content_length: int = Field(ge=1)
    checksum_sha256_base64: str = Field(min_length=44, max_length=44)
    upload_session_id: str = Field(min_length=1, max_length=36)
    expires_at: datetime


class PresignedRequest(StorageContract):
    method: str
    url: str
    required_headers: dict[str, str]
    expires_at: datetime


class ServerSideEncryptionState(StrEnum):
    NONE = "NONE"
    AES256 = "AES256"
    KMS = "KMS"
    KMS_DSSE = "KMS_DSSE"
    SM4 = "SM4"
    UNKNOWN = "UNKNOWN"


class ObjectStat(StorageContract):
    reference: ObjectReference
    backend: StorageBackend
    bucket: str
    etag: str
    content_length: int = Field(ge=0)
    content_type: str | None
    checksum_sha256_base64: str | None
    metadata: dict[str, str]
    last_modified: datetime | None
    server_side_encryption: ServerSideEncryptionState = ServerSideEncryptionState.NONE


class BoundedReadRequest(StorageContract):
    reference: ObjectReference
    maximum_bytes: int = Field(ge=1)
    expected_etag: str | None = None


class ConditionalCopyRequest(StorageContract):
    source: ObjectReference
    destination: ObjectReference
    source_etag: str
    expected_content_length: int = Field(ge=0)
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_type: str = Field(min_length=1, max_length=128)
    upload_session_id: str = Field(min_length=1, max_length=36)


class ConditionalWriteRequest(StorageContract):
    reference: ObjectReference
    payload: bytes = Field(max_length=2 * 1024 * 1024, repr=False)
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_type: str = Field(min_length=1, max_length=128)
    metadata: dict[str, str] = Field(default_factory=dict)
    require_encryption: bool = False

    @model_validator(mode="after")
    def require_unversioned_destination(self) -> ConditionalWriteRequest:
        if self.reference.version_id is not None:
            raise ValueError("conditional writes require an unversioned destination key")
        if len(self.metadata) > 16 or any(
            not 1 <= len(name) <= 64 or not 1 <= len(value) <= 512
            for name, value in self.metadata.items()
        ):
            raise ValueError("conditional write metadata exceeds its bounds")
        return self


class ConditionalDeleteRequest(StorageContract):
    reference: ObjectReference
    expected_etag: str


class ObjectVersionListRequest(StorageContract):
    reference: ObjectReference
    page_size: int = Field(ge=1, le=1000)
    continuation_token: str | None = Field(default=None, min_length=1, max_length=4096)

    @model_validator(mode="after")
    def require_unversioned_key(self) -> ObjectVersionListRequest:
        if self.reference.version_id is not None:
            raise ValueError("version listing requires an unversioned exact key")
        return self


class ObjectVersionEntry(StorageContract):
    reference: ObjectReference
    kind: Literal["OBJECT", "DELETE_MARKER"]

    @model_validator(mode="after")
    def require_exact_version(self) -> ObjectVersionEntry:
        if self.reference.version_id is None:
            raise ValueError("listed object versions require an exact provider version")
        return self


class ObjectVersionPage(StorageContract):
    entries: tuple[ObjectVersionEntry, ...]
    continuation_token: str | None = Field(default=None, min_length=1, max_length=4096)


class DeleteMarkerRequest(StorageContract):
    reference: ObjectReference

    @model_validator(mode="after")
    def require_exact_version(self) -> DeleteMarkerRequest:
        if self.reference.version_id is None:
            raise ValueError("delete marker removal requires an exact provider version")
        return self


class TemporaryReadRequest(StorageContract):
    reference: ObjectReference
    expires_at: datetime
    expected_etag: str | None = None
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_exact_content_identity(self) -> TemporaryReadRequest:
        if self.expected_sha256 is not None and (
            self.reference.version_id is None or self.expected_etag is None
        ):
            raise ValueError("content-verified temporary reads require an exact version and ETag")
        return self


class ObjectStorage(Protocol):
    @property
    def backend(self) -> StorageBackend: ...

    def presign_put(self, request: PresignPutRequest) -> PresignedRequest: ...

    def stat(self, reference: ObjectReference) -> ObjectStat: ...

    def open_bounded_read(
        self,
        request: BoundedReadRequest,
    ) -> AbstractContextManager[Iterable[bytes]]: ...

    def copy_if_absent(self, request: ConditionalCopyRequest) -> ObjectStat: ...

    def write_if_absent(self, request: ConditionalWriteRequest) -> ObjectStat: ...

    def delete_if_match(self, request: ConditionalDeleteRequest) -> bool: ...

    def list_versions(self, request: ObjectVersionListRequest) -> ObjectVersionPage: ...

    def delete_marker(self, request: DeleteMarkerRequest) -> bool: ...

    def temporary_read(self, request: TemporaryReadRequest) -> PresignedRequest: ...
