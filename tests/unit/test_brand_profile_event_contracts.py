from __future__ import annotations

import pytest
from commercevision_contracts.events import (
    ASSET_DELETE_COMPLETED_V1,
    BRAND_PROFILE_PUBLISHED_V1,
    AssetDeleteCompletedPayload,
    BrandProfilePublishedPayload,
    EventHandling,
    EventQueue,
    EventType,
    event_contract_for,
)
from pydantic import ValidationError

PROFILE_ID = "018f5f4d-7c11-7d11-8a11-222222222222"
PROFILE_VERSION_ID = "018f5f4d-7c11-7d11-8a11-333333333333"
ASSET_ID = "018f5f4d-7c11-7d11-8a11-444444444444"
ASSET_VERSION_ID = "018f5f4d-7c11-7d11-8a11-555555555555"


def _published_payload() -> dict[str, object]:
    return {
        "workspace_id": "workspace-a",
        "profile_id": PROFILE_ID,
        "profile_version_id": PROFILE_VERSION_ID,
        "profile_version_number": 3,
        "content_sha256": "a" * 64,
        "member_count": 4,
        "published_by": "brand-admin",
    }


def test_brand_profile_publication_is_a_strict_typed_asset_observation() -> None:
    contract = event_contract_for(EventType.BRAND_PROFILE_PUBLISHED, 1)

    payload = contract.validate_payload(_published_payload())

    assert contract is BRAND_PROFILE_PUBLISHED_V1
    assert isinstance(payload, BrandProfilePublishedPayload)
    assert payload.profile_version_number == 3
    assert payload.member_count == 4
    assert contract.queue is EventQueue.ASSET
    assert contract.handling is EventHandling.OBSERVATION


def test_brand_profile_publication_rejects_unversioned_or_sensitive_extras() -> None:
    contract = event_contract_for(EventType.BRAND_PROFILE_PUBLISHED, 1)
    payload = _published_payload()
    payload["selected_object_keys"] = ["private/object/key"]

    with pytest.raises(ValidationError):
        contract.validate_payload(payload)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("profile_id", PROFILE_ID.upper()),
        ("profile_id", "not-a-uuid"),
        ("profile_version_id", PROFILE_VERSION_ID.upper()),
        ("profile_version_id", "not-a-uuid"),
        ("published_by", " brand-admin"),
        ("published_by", "brand-admin "),
        ("published_by", "brand\nadmin"),
        ("published_by", "brand\u0085admin"),
    ],
)
def test_brand_profile_publication_requires_canonical_ids_and_safe_actor_text(
    field: str,
    invalid_value: str,
) -> None:
    payload = _published_payload()
    payload[field] = invalid_value

    with pytest.raises(ValidationError):
        BRAND_PROFILE_PUBLISHED_V1.validate_payload(payload)


def test_foundation_asset_deletion_is_a_forward_compatible_typed_observation() -> None:
    contract = event_contract_for(EventType.ASSET_DELETE_COMPLETED, 1)

    payload = contract.validate_payload(
        {
            "workspace_id": "workspace-a",
            "asset_id": ASSET_ID,
            "asset_version_id": ASSET_VERSION_ID,
            "retention_class": "FOUNDATION",
            "deletion_generation": 4,
            "future_convergence_receipt": "ignored-by-v1",
        }
    )

    assert contract is ASSET_DELETE_COMPLETED_V1
    assert isinstance(payload, AssetDeleteCompletedPayload)
    assert payload.asset_version_id == ASSET_VERSION_ID
    assert payload.deletion_generation == 4
    assert "future_convergence_receipt" not in payload.model_dump()
    assert contract.queue is EventQueue.MAINTENANCE
    assert contract.handling is EventHandling.OBSERVATION


@pytest.mark.parametrize("generation", [0, True, 1.0, "1"])
def test_foundation_asset_deletion_requires_a_strict_positive_generation(
    generation: object,
) -> None:
    with pytest.raises(ValidationError):
        ASSET_DELETE_COMPLETED_V1.validate_payload(
            {
                "workspace_id": "workspace-a",
                "asset_id": ASSET_ID,
                "asset_version_id": ASSET_VERSION_ID,
                "retention_class": "FOUNDATION",
                "deletion_generation": generation,
            }
        )


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("asset_id", ASSET_ID.upper()),
        ("asset_id", "not-a-uuid"),
        ("asset_version_id", ASSET_VERSION_ID.upper()),
        ("asset_version_id", "not-a-uuid"),
    ],
)
def test_foundation_asset_deletion_requires_canonical_lowercase_uuid_lineage(
    field: str,
    invalid_value: str,
) -> None:
    payload: dict[str, object] = {
        "workspace_id": "workspace-a",
        "asset_id": ASSET_ID,
        "asset_version_id": ASSET_VERSION_ID,
        "retention_class": "FOUNDATION",
        "deletion_generation": 4,
    }
    payload[field] = invalid_value

    with pytest.raises(ValidationError):
        ASSET_DELETE_COMPLETED_V1.validate_payload(payload)
