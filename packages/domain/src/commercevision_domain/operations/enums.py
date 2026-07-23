"""Durable operation lifecycle values."""

from enum import StrEnum


class OperationKind(StrEnum):
    ASSET_VALIDATION = "ASSET_VALIDATION"
    PRODUCT_BRIEF_ANALYSIS = "PRODUCT_BRIEF_ANALYSIS"
    ASSET_INDEXING = "ASSET_INDEXING"
    ASSET_DELETION = "ASSET_DELETION"
    RECONCILIATION = "RECONCILIATION"
    COLLECTION_REBUILD = "COLLECTION_REBUILD"


class OperationState(StrEnum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    RECONCILING = "RECONCILING"
    WAITING_HUMAN = "WAITING_HUMAN"
    RETRYABLE_FAILED = "RETRYABLE_FAILED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self in {
            OperationState.SUCCEEDED,
            OperationState.FAILED,
            OperationState.CANCELLED,
        }


class ReconciliationOutcome(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    CONFIRMED_SUCCESS = "CONFIRMED_SUCCESS"
    CONFIRMED_FAILURE = "CONFIRMED_FAILURE"
    NOT_FOUND = "NOT_FOUND"
