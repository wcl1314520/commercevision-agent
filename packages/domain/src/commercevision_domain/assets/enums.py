"""Asset Registry lifecycle values."""

from enum import StrEnum


class UploadSessionState(StrEnum):
    OPEN = "OPEN"
    FINALIZING = "FINALIZING"
    FINALIZED = "FINALIZED"
    EXPIRED = "EXPIRED"
    ABORTED = "ABORTED"


class RetentionClass(StrEnum):
    TASK = "TASK"
    FOUNDATION = "FOUNDATION"


class AssetKind(StrEnum):
    IMAGE = "IMAGE"
    LORA = "LORA"
    PROMPT_TEMPLATE = "PROMPT_TEMPLATE"
    MODEL_CONFIGURATION = "MODEL_CONFIGURATION"


class AssetState(StrEnum):
    QUARANTINED = "QUARANTINED"
    VALIDATING = "VALIDATING"
    PENDING_RIGHTS = "PENDING_RIGHTS"
    PENDING_REVIEW = "PENDING_REVIEW"
    AVAILABLE = "AVAILABLE"
    BLOCKED = "BLOCKED"
    RIGHTS_EXPIRED = "RIGHTS_EXPIRED"
    DELETING = "DELETING"
    DELETED = "DELETED"
    FAILED = "FAILED"


class AssetObjectState(StrEnum):
    QUARANTINED = "QUARANTINED"
    CONTROLLED = "CONTROLLED"
    DELETE_PENDING = "DELETE_PENDING"
    DELETED = "DELETED"


class StorageLocationClass(StrEnum):
    QUARANTINE = "QUARANTINE"
    TASK = "TASK"
    FOUNDATION = "FOUNDATION"
    PROVIDER_RESULT = "PROVIDER_RESULT"


class StorageBackend(StrEnum):
    MINIO = "MINIO"
    OSS = "OSS"
