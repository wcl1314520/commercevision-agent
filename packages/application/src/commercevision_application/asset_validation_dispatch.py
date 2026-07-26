"""Durable validation operation and event construction for finalized uploads."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from commercevision_contracts.events import (
    AssetUploadFinalizedPayload,
    AssetValidationRequestedPayload,
    EventType,
)
from commercevision_domain import (
    Asset,
    AssetObject,
    AssetVersion,
    DurableOperation,
    OperationKind,
    UploadSession,
)
from commercevision_domain.messaging import EventEnvelope, OutboxEvent

from .asset_validation_target import asset_validation_input_hash


@dataclass(frozen=True, slots=True)
class AssetValidationPolicy:
    policy_version: str
    max_attempts: int
    execution_max_elapsed: timedelta

    def __post_init__(self) -> None:
        if not self.policy_version or len(self.policy_version) > 64:
            raise ValueError("Asset validation policy version is invalid")
        if self.max_attempts < 1:
            raise ValueError("Asset validation max_attempts must be positive")
        if self.execution_max_elapsed <= timedelta(0):
            raise ValueError("Asset validation execution budget must be positive")


def build_validation_operation(
    *,
    asset: Asset,
    asset_version: AssetVersion,
    object_fact: AssetObject,
    policy: AssetValidationPolicy,
    now: datetime,
) -> DurableOperation:
    return DurableOperation.create(
        workspace_id=asset_version.workspace_id,
        kind=OperationKind.ASSET_VALIDATION,
        target_type="ASSET_VERSION",
        target_id=asset_version.id,
        target_version=asset_version.version_number,
        input_hash=asset_validation_input_hash(asset, asset_version, object_fact),
        input_ref=f"mysql://asset-versions/{asset_version.id}",
        max_attempts=policy.max_attempts,
        execution_max_elapsed=policy.execution_max_elapsed,
        now=now,
    )


def build_validation_event(
    *,
    operation: DurableOperation,
    asset: Asset,
    asset_version: AssetVersion,
    object_fact: AssetObject,
    trace_id: str,
    now: datetime,
) -> OutboxEvent:
    payload = AssetValidationRequestedPayload(
        operation_id=operation.id,
        workspace_id=operation.workspace_id,
        asset_id=asset.id,
        asset_version_id=asset_version.id,
        object_fact_id=object_fact.id,
        integrity_policy_version=asset_version.integrity_policy_version,
        validation_policy_version=asset_version.validation_policy_version,
        content_sha256=asset_version.sha256,
    )
    return OutboxEvent(
        envelope=EventEnvelope.create(
            event_type=EventType.ASSET_VALIDATION_REQUESTED.value,
            aggregate_type="DurableOperation",
            aggregate_id=operation.id,
            aggregate_version=operation.version,
            trace_id=trace_id,
            payload=payload.model_dump(mode="json"),
            now=now,
        ),
        available_at=now,
        workspace_id=operation.workspace_id,
    )


def build_upload_finalized_event(
    *,
    upload_session: UploadSession,
    asset: Asset,
    asset_version: AssetVersion,
    object_fact: AssetObject,
    operation: DurableOperation,
    trace_id: str,
    now: datetime,
) -> OutboxEvent:
    payload = AssetUploadFinalizedPayload(
        workspace_id=asset.workspace_id,
        upload_session_id=upload_session.id,
        asset_id=asset.id,
        asset_version_id=asset_version.id,
        object_fact_id=object_fact.id,
        validation_operation_id=operation.id,
    )
    return OutboxEvent(
        envelope=EventEnvelope.create(
            event_type=EventType.ASSET_UPLOAD_FINALIZED.value,
            aggregate_type="Asset",
            aggregate_id=asset.id,
            aggregate_version=asset.version,
            trace_id=trace_id,
            payload=payload.model_dump(mode="json"),
            now=now,
        ),
        available_at=now,
        workspace_id=asset.workspace_id,
    )
