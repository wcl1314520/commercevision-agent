"""Indexing lifecycle vocabulary."""

from enum import StrEnum


class VectorKind(StrEnum):
    IMAGE = "IMAGE"
    PRODUCT_FUSED = "PRODUCT_FUSED"


class CollectionState(StrEnum):
    PLANNED = "PLANNED"
    CREATING = "CREATING"
    BACKFILLING = "BACKFILLING"
    VERIFYING = "VERIFYING"
    READY = "READY"
    ACTIVE = "ACTIVE"
    RETIRING = "RETIRING"
    RETIRED = "RETIRED"
    FAILED = "FAILED"


class EmbeddingState(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    INDEXED = "INDEXED"
    RETRYABLE_FAILED = "RETRYABLE_FAILED"
    PERMANENT_FAILED = "PERMANENT_FAILED"
    STALE = "STALE"
    DELETE_PENDING = "DELETE_PENDING"
    DELETED = "DELETED"
