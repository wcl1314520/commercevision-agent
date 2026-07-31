from __future__ import annotations

from datetime import UTC, datetime

import pytest
from commercevision_contracts import (
    BrandColorV1,
    BrandProfileCreateRequestV1,
    BrandProfileDraftV1,
    BrandProfileListResponseV1,
    BrandProfileMemberSelectionV1,
    BrandProfilePublishedMemberV1,
    BrandProfilePublishRequestV1,
    BrandProfileUpdateDraftRequestV1,
    BrandProfileValidateRequestV1,
    BrandProfileVersionResponseV1,
    BrandRuleV1,
)
from commercevision_domain import (
    BrandProfileMemberRole,
    BrandRuleScope,
    RightsDecisionCode,
)
from pydantic import ValidationError

ASSET_ID = "018f5f4d-7c11-7d11-8a11-111111111111"
ASSET_VERSION_ID = "018f5f4d-7c11-7d11-8a11-222222222222"
PROFILE_ID = "018f5f4d-7c11-7d11-8a11-333333333333"
PROFILE_VERSION_ID = "018f5f4d-7c11-7d11-8a11-444444444444"
RIGHTS_ID = "018f5f4d-7c11-7d11-8a11-555555555555"
NOW = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)


def _draft() -> BrandProfileDraftV1:
    return BrandProfileDraftV1(
        rules=[
            BrandRuleV1(
                code="logo.clear-space",
                scope=BrandRuleScope.VISUAL,
                instruction="Keep one mark-width of clear space.",
            )
        ],
        approved_colors=[BrandColorV1(name="Primary", value="#0A5CFF")],
        required_marks=["CommerceVision wordmark"],
        prohibited_elements=["Competitor marks"],
        tone_constraints=["Confident"],
        copy_constraints=["No unverified superlatives"],
        purpose="commerce.image-generation",
        provider="alibaba",
        requires_derivative=True,
        selected_assets=[
            BrandProfileMemberSelectionV1(
                asset_version_id=ASSET_VERSION_ID,
                role=BrandProfileMemberRole.LOGO,
            )
        ],
    )


def test_brand_profile_contracts_reject_unknown_nested_fields() -> None:
    payload = {
        "brand": "CommerceVision",
        "profile_key": "cn-primary",
        "draft": {
            **_draft().model_dump(mode="json"),
            "untrusted_override": True,
        },
    }

    with pytest.raises(ValidationError):
        BrandProfileCreateRequestV1.model_validate(payload)


def test_brand_profile_draft_rejects_duplicate_asset_version_selection() -> None:
    selection = BrandProfileMemberSelectionV1(
        asset_version_id=ASSET_VERSION_ID,
        role=BrandProfileMemberRole.LOGO,
    )

    with pytest.raises(ValidationError, match="selected Asset Version"):
        BrandProfileDraftV1(
            **{
                **_draft().model_dump(),
                "selected_assets": [selection, selection],
            }
        )


def test_publish_request_requires_optimistic_version_and_forbids_extra_fields() -> None:
    assert BrandProfilePublishRequestV1(expected_version=3).expected_version == 3

    with pytest.raises(ValidationError):
        BrandProfilePublishRequestV1.model_validate({"expected_version": 3, "force": True})


@pytest.mark.parametrize(
    "request_type",
    [
        BrandProfilePublishRequestV1,
        BrandProfileValidateRequestV1,
    ],
)
@pytest.mark.parametrize("invalid_version", [True, "3", 3.0])
def test_commands_reject_coerced_optimistic_versions(
    request_type: type[BrandProfilePublishRequestV1 | BrandProfileValidateRequestV1],
    invalid_version: object,
) -> None:
    with pytest.raises(ValidationError):
        request_type.model_validate({"expected_version": invalid_version})

    with pytest.raises(ValidationError):
        BrandProfileUpdateDraftRequestV1.model_validate(
            {
                "expected_version": invalid_version,
                "draft": _draft().model_dump(mode="json"),
            }
        )


@pytest.mark.parametrize("invalid_derivative_flag", [0, 1, "false", "true"])
def test_draft_rejects_coerced_derivative_flags(invalid_derivative_flag: object) -> None:
    with pytest.raises(ValidationError):
        BrandProfileDraftV1.model_validate(
            {
                **_draft().model_dump(mode="json"),
                "requires_derivative": invalid_derivative_flag,
            }
        )


def test_published_version_separates_frozen_rights_from_current_usability() -> None:
    response = BrandProfileVersionResponseV1(
        id=PROFILE_VERSION_ID,
        workspace_id="workspace-a",
        profile_id=PROFILE_ID,
        version_number=1,
        draft=_draft(),
        content_sha256="a" * 64,
        published_by="brand-admin",
        published_at=NOW,
        members=[
            BrandProfilePublishedMemberV1(
                ordinal=0,
                asset_id=ASSET_ID,
                asset_version_id=ASSET_VERSION_ID,
                role=BrandProfileMemberRole.LOGO,
                published_rights_record_id=RIGHTS_ID,
                published_rights_record_version=1,
                currently_usable=False,
                current_reason_code=RightsDecisionCode.RIGHTS_REVOKED,
                current_rights_record_id="018f5f4d-7c11-7d11-8a11-666666666666",
                current_rights_record_version=2,
                decided_at=NOW,
            )
        ],
    )

    member = response.members[0]
    assert member.published_rights_record_id == RIGHTS_ID
    assert member.published_rights_record_version == 1
    assert member.currently_usable is False
    assert member.current_reason_code == RightsDecisionCode.RIGHTS_REVOKED


def test_cursor_transport_accepts_only_bounded_v1_signed_envelopes() -> None:
    valid = "v1.current.cGF5bG9hZA.c2lnbmF0dXJl"

    assert BrandProfileListResponseV1(items=[], next_cursor=valid).next_cursor == valid
    for invalid in (
        "eyJraW5kIjoicHJvZmlsZSJ9",
        "v1.current.payload",
        "v1.current.payload.signature.extra",
        "v1.bad$key.payload.signature",
        "v1.current.payload.signature=",
        "v1.current.payload.signature\n",
        "v1.current.payload.signaturé",
        f"v1.{'k' * 64}.{'p' * 145}.{'s' * 43}",
    ):
        with pytest.raises(ValidationError):
            BrandProfileListResponseV1(items=[], next_cursor=invalid)
