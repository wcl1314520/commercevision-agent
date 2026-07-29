"""Object-storage adapters and provisioning boundary."""

from .credentials import AlibabaCloudCredentialsProvider
from .object_storage import MinioObjectStorage, OssObjectStorage, build_object_storage
from .provider_artifacts import (
    ObjectStorageProviderArtifactSink,
    ObjectStorageProviderArtifactTarget,
    ObjectStorageProviderArtifactTargetRegistry,
)
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
    "ObjectStorageProviderArtifactSink",
    "ObjectStorageProviderArtifactTarget",
    "ObjectStorageProviderArtifactTargetRegistry",
    "OssObjectStorage",
    "build_object_storage",
    "close_object_storage",
]
