from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta

import pytest
from commercevision_domain import (
    BrandColor,
    BrandProfile,
    BrandProfileDraft,
    BrandProfileMemberRole,
    BrandProfileMemberSelection,
    BrandProfilePublishedMember,
    BrandProfileState,
    BrandRule,
    BrandRuleScope,
    ConcurrencyError,
    new_uuid7,
)

NOW = datetime(2026, 7, 30, 9, 0, tzinfo=UTC)


def _draft(
    *,
    asset_version_id: str | None = None,
    instruction: str = "Keep the logo clear of product claims.",
) -> BrandProfileDraft:
    selected_assets = (
        (
            BrandProfileMemberSelection(
                asset_version_id=asset_version_id,
                role=BrandProfileMemberRole.LOGO,
            ),
        )
        if asset_version_id is not None
        else ()
    )
    return BrandProfileDraft(
        rules=(
            BrandRule(
                code="logo-clear-space",
                scope=BrandRuleScope.VISUAL,
                instruction=instruction,
            ),
        ),
        approved_colors=(BrandColor(name="Northstar blue", value="#1457FF"),),
        required_marks=("Northstar wordmark",),
        prohibited_elements=("competitor marks",),
        tone_constraints=("calm", "precise"),
        copy_constraints=("Do not make unsupported efficacy claims.",),
        purpose="BRAND_CONTEXT",
        provider="qwen-vl",
        requires_derivative=True,
        selected_assets=selected_assets,
    )


def _published_member(
    *,
    asset_version_id: str,
    rights_record_id: str | None = None,
    rights_record_version: int = 4,
) -> BrandProfilePublishedMember:
    return BrandProfilePublishedMember(
        ordinal=0,
        asset_id=new_uuid7(),
        asset_version_id=asset_version_id,
        role=BrandProfileMemberRole.LOGO,
        rights_record_id=rights_record_id or new_uuid7(),
        rights_record_version=rights_record_version,
    )


def test_publication_is_immutable_and_hashes_exact_asset_and_rights_facts() -> None:
    asset_version_id = new_uuid7()
    profile = BrandProfile.create(
        workspace_id="Brand-Workspace",
        brand="Northstar Labs",
        profile_key="primary",
        draft=_draft(asset_version_id=asset_version_id),
        actor_id="brand-admin",
        now=NOW,
    )
    member = _published_member(asset_version_id=asset_version_id)

    version = profile.publish(
        expected_version=1,
        members=(member,),
        actor_id="brand-admin",
        now=NOW + timedelta(seconds=1),
    )

    assert profile.state == BrandProfileState.ACTIVE
    assert profile.current_version_id == version.id
    assert profile.current_version_number == 1
    assert profile.version == 2
    assert version.profile_id == profile.id
    assert version.workspace_id == profile.workspace_id
    assert version.version_number == 1
    assert version.members == (member,)
    assert len(version.content_sha256) == 64

    different_rights = _published_member(
        asset_version_id=asset_version_id,
        rights_record_id=new_uuid7(),
        rights_record_version=5,
    )
    assert version.calculate_content_sha256(members=(different_rights,)) != version.content_sha256

    with pytest.raises(FrozenInstanceError):
        version.content_sha256 = "0" * 64  # type: ignore[misc]


def test_draft_update_uses_optimistic_version_without_deactivating_publication() -> None:
    asset_version_id = new_uuid7()
    profile = BrandProfile.create(
        workspace_id="brand-workspace",
        brand="Northstar Labs",
        profile_key="primary",
        draft=_draft(asset_version_id=asset_version_id),
        actor_id="brand-admin",
        now=NOW,
    )
    profile.publish(
        expected_version=1,
        members=(_published_member(asset_version_id=asset_version_id),),
        actor_id="brand-admin",
        now=NOW + timedelta(seconds=1),
    )

    profile.update_draft(
        expected_version=2,
        draft=_draft(
            asset_version_id=asset_version_id,
            instruction="Use the compact mark below 320 px.",
        ),
        actor_id="brand-editor",
        now=NOW + timedelta(seconds=2),
    )

    assert profile.version == 3
    assert profile.state == BrandProfileState.ACTIVE
    assert profile.current_version_number == 1
    assert profile.draft.rules[0].instruction == "Use the compact mark below 320 px."
    with pytest.raises(ConcurrencyError):
        profile.update_draft(
            expected_version=2,
            draft=_draft(asset_version_id=asset_version_id),
            actor_id="stale-editor",
            now=NOW + timedelta(seconds=3),
        )


def test_stale_rights_event_only_marks_the_same_current_publication() -> None:
    asset_version_id = new_uuid7()
    profile = BrandProfile.create(
        workspace_id="brand-workspace",
        brand="Northstar Labs",
        profile_key="primary",
        draft=_draft(asset_version_id=asset_version_id),
        actor_id="brand-admin",
        now=NOW,
    )
    first = profile.publish(
        expected_version=1,
        members=(_published_member(asset_version_id=asset_version_id),),
        actor_id="brand-admin",
        now=NOW + timedelta(seconds=1),
    )

    assert (
        profile.mark_needs_republish(
            expected_current_version_id=new_uuid7(),
            stale_at=NOW + timedelta(seconds=2),
        )
        is False
    )
    assert profile.state == BrandProfileState.ACTIVE
    assert (
        profile.mark_needs_republish(
            expected_current_version_id=first.id,
            stale_at=NOW + timedelta(seconds=2),
        )
        is True
    )
    assert profile.state == BrandProfileState.NEEDS_REPUBLISH
    assert profile.version == 3

    second = profile.publish(
        expected_version=3,
        members=(_published_member(asset_version_id=asset_version_id),),
        actor_id="brand-admin",
        now=NOW + timedelta(seconds=3),
    )
    assert second.version_number == 2
    assert profile.state == BrandProfileState.ACTIVE
    assert profile.stale_at is None

    assert (
        profile.mark_needs_republish(
            expected_current_version_id=first.id,
            stale_at=NOW + timedelta(seconds=4),
        )
        is False
    )
    assert profile.state == BrandProfileState.ACTIVE
    assert profile.current_version_id == second.id


@pytest.mark.parametrize(
    ("workspace_id", "brand", "profile_key"),
    [
        (" workspace", "Northstar", "primary"),
        ("brand-workspace", " Northstar", "primary"),
        ("brand-workspace", "Northstar", "Primary Key"),
        ("brand-workspace", "Northstar", "品牌"),
    ],
)
def test_brand_profile_identity_rejects_normalization_and_ambiguous_keys(
    workspace_id: str,
    brand: str,
    profile_key: str,
) -> None:
    with pytest.raises(ValueError):
        BrandProfile.create(
            workspace_id=workspace_id,
            brand=brand,
            profile_key=profile_key,
            draft=_draft(),
            actor_id="brand-admin",
            now=NOW,
        )


def test_brand_profile_rejects_unicode_control_characters_in_actor_identity() -> None:
    profile = BrandProfile.create(
        workspace_id="brand-workspace",
        brand="Northstar",
        profile_key="primary",
        draft=_draft(),
        actor_id="brand-admin",
        now=NOW,
    )

    with pytest.raises(ValueError, match="publisher"):
        profile.publish(
            expected_version=1,
            members=(),
            actor_id="brand\u0085admin",
            now=NOW + timedelta(seconds=1),
        )


def test_draft_rejects_duplicate_asset_versions_and_mutable_collections() -> None:
    asset_version_id = new_uuid7()
    selection = BrandProfileMemberSelection(
        asset_version_id=asset_version_id,
        role=BrandProfileMemberRole.LOGO,
    )

    with pytest.raises(ValueError, match="selected Asset Version"):
        BrandProfileDraft(
            rules=(),
            approved_colors=(),
            required_marks=(),
            prohibited_elements=(),
            tone_constraints=(),
            copy_constraints=(),
            purpose="BRAND_CONTEXT",
            provider="qwen-vl",
            requires_derivative=False,
            selected_assets=(selection, selection),
        )

    with pytest.raises(ValueError, match="immutable tuple"):
        BrandProfileDraft(
            rules=[],  # type: ignore[arg-type]
            approved_colors=(),
            required_marks=(),
            prohibited_elements=(),
            tone_constraints=(),
            copy_constraints=(),
            purpose="BRAND_CONTEXT",
            provider="qwen-vl",
            requires_derivative=False,
            selected_assets=(),
        )


def test_archive_records_the_archiver_and_authoritative_timestamp() -> None:
    profile = BrandProfile.create(
        workspace_id="brand-workspace",
        brand="Northstar Labs",
        profile_key="primary",
        draft=_draft(),
        actor_id="brand-admin",
        now=NOW,
    )
    archived_at = NOW + timedelta(seconds=5)

    profile.archive(
        expected_version=1,
        actor_id="brand-archiver",
        now=archived_at,
    )

    assert profile.state == BrandProfileState.ARCHIVED
    assert profile.version == 2
    assert profile.updated_by == "brand-archiver"
    assert profile.updated_at == archived_at


@pytest.mark.parametrize("invalid_version", [True, 1.5, "1"])
def test_profile_and_publication_reject_non_integer_versions(
    invalid_version: object,
) -> None:
    asset_version_id = new_uuid7()
    profile = BrandProfile.create(
        workspace_id="brand-workspace",
        brand="Northstar Labs",
        profile_key="primary",
        draft=_draft(asset_version_id=asset_version_id),
        actor_id="brand-admin",
        now=NOW,
    )
    publication = profile.publish(
        expected_version=1,
        members=(_published_member(asset_version_id=asset_version_id),),
        actor_id="brand-admin",
        now=NOW + timedelta(seconds=1),
    )

    with pytest.raises(ValueError, match="optimistic version"):
        replace(profile, version=invalid_version)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Version number"):
        replace(publication, version_number=invalid_version)  # type: ignore[arg-type]
