from __future__ import annotations

import pytest
from commercevision_contracts.events import (
    ASSET_VALIDATION_COMPLETED_V1,
    ASSET_VALIDATION_FAILED_V1,
    AssetValidationCompletedPayload,
    AssetValidationFailedPayload,
    EventHandling,
    EventQueue,
)
from pydantic import ValidationError

COMMON = {
    "workspace_id": "catalog-workspace",
    "asset_id": "asset-1",
    "asset_version_id": "asset-version-1",
    "operation_id": "operation-1",
    "attempt_number": 2,
}


@pytest.mark.parametrize("outcome", ["PENDING_RIGHTS", "PENDING_REVIEW"])
def test_asset_validation_completed_v1_is_a_strict_typed_observation(
    outcome: str,
) -> None:
    payload = AssetValidationCompletedPayload(
        **COMMON,
        outcome=outcome,
        reason_code=("CONTENT_SAFETY_REVIEW" if outcome == "PENDING_REVIEW" else None),
    )

    assert (
        ASSET_VALIDATION_COMPLETED_V1.validate_payload(payload.model_dump(mode="json")) == payload
    )
    assert ASSET_VALIDATION_COMPLETED_V1.queue == EventQueue.ASSET
    assert ASSET_VALIDATION_COMPLETED_V1.handling == EventHandling.OBSERVATION


@pytest.mark.parametrize("outcome", ["BLOCKED", "FAILED"])
def test_asset_validation_failed_v1_is_a_strict_typed_observation(
    outcome: str,
) -> None:
    payload = AssetValidationFailedPayload(
        **COMMON,
        outcome=outcome,
        reason_code=("MALWARE_DETECTED" if outcome == "BLOCKED" else "PROVIDER_HTTP_403"),
    )

    assert ASSET_VALIDATION_FAILED_V1.validate_payload(payload.model_dump(mode="json")) == payload
    assert ASSET_VALIDATION_FAILED_V1.queue == EventQueue.ASSET
    assert ASSET_VALIDATION_FAILED_V1.handling == EventHandling.OBSERVATION


def test_asset_validation_failed_v1_accepts_zero_execution_attempt() -> None:
    payload = AssetValidationFailedPayload(
        **{**COMMON, "attempt_number": 0},
        outcome="FAILED",
        reason_code="OPERATION_MAXIMUM_ELAPSED",
    )

    assert ASSET_VALIDATION_FAILED_V1.validate_payload(payload.model_dump(mode="json")) == payload


@pytest.mark.parametrize(
    ("contract", "payload"),
    [
        (
            ASSET_VALIDATION_COMPLETED_V1,
            {**COMMON, "outcome": "AVAILABLE", "reason_code": None},
        ),
        (
            ASSET_VALIDATION_FAILED_V1,
            {**COMMON, "outcome": "BLOCKED", "reason_code": "contains spaces"},
        ),
        (
            ASSET_VALIDATION_FAILED_V1,
            {
                **COMMON,
                "outcome": "FAILED",
                "reason_code": "PROVIDER_HTTP_403",
                "raw_provider_payload": {"secret": "must-not-pass"},
            },
        ),
        (
            ASSET_VALIDATION_FAILED_V1,
            {
                **COMMON,
                "attempt_number": -1,
                "outcome": "FAILED",
                "reason_code": "OPERATION_MAXIMUM_ELAPSED",
            },
        ),
    ],
)
def test_asset_validation_terminal_v1_rejects_invalid_or_extra_payload(
    contract: object,
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        contract.validate_payload(payload)  # type: ignore[attr-defined]
