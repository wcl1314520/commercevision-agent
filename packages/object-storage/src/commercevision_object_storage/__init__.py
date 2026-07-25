"""Object-storage adapters and provisioning boundary."""

from .credentials import AlibabaCloudCredentialsProvider
from .object_storage import MinioObjectStorage, OssObjectStorage, build_object_storage
from .readiness import (
    ObjectStorageReadiness,
    ObjectStorageReadinessError,
    close_object_storage,
)

__all__ = [
    "AlibabaCloudCredentialsProvider",
    "MinioObjectStorage",
    "ObjectStorageReadiness",
    "ObjectStorageReadinessError",
    "OssObjectStorage",
    "build_object_storage",
    "close_object_storage",
]
