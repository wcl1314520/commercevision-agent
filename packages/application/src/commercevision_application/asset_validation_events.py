"""Typed terminal events for the Asset Validation lifecycle."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from commercevision_contracts.events import (
    AssetValidationCompletedPayload,
    AssetValidationFailedPayload,
    EventType,
)
from commercevision_domain import Asset
from commercevision_domain.messaging import EventEnvelope, OutboxEvent

from .operations import OperationExecutionRequest

ValidationCompletedOutcome = Literal["PENDING_RIGHTS", "PENDING_REVIEW"]
ValidationFailedOutcome = Literal["BLOCKED", "FAILED"]


def build_validation_completed_event(
    *,
    request: OperationExecutionRequest,
    asset: Asset,
    outcome: ValidationCompletedOutcome,
    reason_code: str | None,
    now: datetime,
) -> OutboxEvent:
    payload = AssetValidationCompletedPayload(
        workspace_id=asset.workspace_id,
        asset_id=asset.id,
        asset_version_id=request.target_id,
        operation_id=request.operation_id,
        attempt_number=request.attempt_count,
        outcome=outcome,
        reason_code=reason_code,
    )
    return _terminal_event(
        request=request,
        asset=asset,
        event_type=EventType.ASSET_VALIDATION_COMPLETED,
        payload=payload.model_dump(mode="json"),
        now=now,
    )


def build_validation_failed_event(
    *,
    request: OperationExecutionRequest,
    asset: Asset,
    outcome: ValidationFailedOutcome,
    reason_code: str,
    now: datetime,
) -> OutboxEvent:
    payload = AssetValidationFailedPayload(
        workspace_id=asset.workspace_id,
        asset_id=asset.id,
        asset_version_id=request.target_id,
        operation_id=request.operation_id,
        attempt_number=request.attempt_count,
        outcome=outcome,
        reason_code=reason_code,
    )
    return _terminal_event(
        request=request,
        asset=asset,
        event_type=EventType.ASSET_VALIDATION_FAILED,
        payload=payload.model_dump(mode="json"),
        now=now,
    )


def _terminal_event(
    *,
    request: OperationExecutionRequest,
    asset: Asset,
    event_type: EventType,
    payload: dict[str, object],
    now: datetime,
) -> OutboxEvent:
    if asset.current_version_id != request.target_id:
        raise ValueError("validation terminal event does not match the current Asset Version")
    return OutboxEvent(
        envelope=EventEnvelope.create(
            event_type=event_type.value,
            aggregate_type="Asset",
            aggregate_id=asset.id,
            aggregate_version=asset.version,
            trace_id=f"validation:{request.operation_id}:{request.attempt_count}",
            payload=payload,
            now=now,
        ),
        available_at=now,
        workspace_id=asset.workspace_id,
    )
