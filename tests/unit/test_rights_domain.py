from datetime import UTC, datetime, timedelta

import pytest
from commercevision_domain import (
    Asset,
    AssetKind,
    AssetState,
    RetentionClass,
    RightsDecisionCode,
    RightsRecord,
    RightsRecordDecision,
    evaluate_current_usability,
    new_uuid7,
)

NOW = datetime(2026, 7, 27, 8, 30, tzinfo=UTC)


def _asset(
    *,
    status: AssetState = AssetState.PENDING_RIGHTS,
    retention_deadline: datetime | None = None,
) -> Asset:
    asset = Asset.create_quarantined(
        asset_id=new_uuid7(),
        workspace_id="rights-domain",
        retention_class=(
            RetentionClass.TASK if retention_deadline is not None else RetentionClass.FOUNDATION
        ),
        kind=AssetKind.IMAGE,
        workflow_id=new_uuid7() if retention_deadline is not None else None,
        product_id=None,
        sku_id=None,
        current_version_id=new_uuid7(),
        retention_deadline=retention_deadline,
        now=NOW,
    )
    asset.status = status
    return asset


def _rights(asset: Asset, **changes: object) -> RightsRecord:
    values: dict[str, object] = {
        "id": new_uuid7(),
        "workspace_id": asset.workspace_id,
        "asset_id": asset.id,
        "asset_version_id": asset.current_version_id,
        "version_number": 3,
        "decision": RightsRecordDecision.GRANT,
        "owner_reference": "brand-owner-42",
        "source": "brand-dam",
        "license_reference": "license-2026-07",
        "allowed_uses": frozenset({"RETRIEVAL", "VISION_ANALYSIS"}),
        "allowed_providers": frozenset({"qwen-vl", "milvus"}),
        "derivative_allowed": False,
        "public_demo_allowed": False,
        "evidence_reference": "evidence://rights/42",
        "terms_sha256": "a" * 64,
        "valid_from": NOW,
        "valid_until": NOW + timedelta(days=30),
        "perpetual": False,
        "supersedes_record_id": new_uuid7(),
        "created_by": "compliance-user",
        "created_at": NOW,
    }
    values.update(changes)
    rights = RightsRecord(**values)  # type: ignore[arg-type]
    asset.current_rights_record_id = rights.id
    return rights


def _decision(
    asset: Asset,
    rights: RightsRecord,
    *,
    at: datetime = NOW,
    purpose: str = "RETRIEVAL",
    provider: str = "milvus",
    requires_derivative: bool = False,
):
    return evaluate_current_usability(
        asset=asset,
        rights_record=rights,
        asset_version_id=asset.current_version_id,
        purpose=purpose,
        provider=provider,
        requires_derivative=requires_derivative,
        decision_time=at,
    )


def test_current_usability_uses_exclusive_valid_until_and_reports_exact_record() -> None:
    asset = _asset(status=AssetState.AVAILABLE)
    rights = _rights(asset, valid_until=NOW + timedelta(microseconds=1))

    allowed = _decision(asset, rights, at=NOW)
    expired = _decision(asset, rights, at=NOW + timedelta(microseconds=1))

    assert allowed.authorized is True
    assert allowed.reason_code == RightsDecisionCode.AUTHORIZED
    assert allowed.rights_record_id == rights.id
    assert allowed.rights_record_version == 3
    assert expired.authorized is False
    assert expired.reason_code == RightsDecisionCode.RIGHTS_EXPIRED
    assert expired.rights_record_version == 3


def test_rights_never_extend_task_retention_at_the_exact_boundary() -> None:
    deadline = NOW + timedelta(microseconds=1)
    asset = _asset(status=AssetState.AVAILABLE, retention_deadline=deadline)
    rights = _rights(asset, perpetual=True, valid_until=None)

    before = _decision(asset, rights, at=NOW)
    at_deadline = _decision(asset, rights, at=deadline)

    assert before.authorized is True
    assert at_deadline.authorized is False
    assert at_deadline.reason_code == RightsDecisionCode.ASSET_RETENTION_EXPIRED


def test_perpetual_rights_are_explicit_and_cannot_hide_a_valid_until() -> None:
    asset = _asset()

    with pytest.raises(ValueError, match="perpetual"):
        _rights(asset, perpetual=False, valid_until=None)
    with pytest.raises(ValueError, match="valid_until"):
        _rights(asset, perpetual=True, valid_until=NOW + timedelta(days=1))

    perpetual = _rights(asset, perpetual=True, valid_until=None)
    assert perpetual.perpetual is True


@pytest.mark.parametrize(
    ("changes", "purpose", "provider", "requires_derivative", "reason"),
    [
        ({"allowed_uses": frozenset()}, "RETRIEVAL", "milvus", False, "USE_NOT_ALLOWED"),
        (
            {"allowed_providers": frozenset()},
            "RETRIEVAL",
            "milvus",
            False,
            "PROVIDER_NOT_ALLOWED",
        ),
        ({}, "GENERATION", "milvus", False, "USE_NOT_ALLOWED"),
        ({}, "RETRIEVAL", "other-provider", False, "PROVIDER_NOT_ALLOWED"),
        ({}, "RETRIEVAL", "milvus", True, "DERIVATIVE_NOT_ALLOWED"),
    ],
)
def test_current_usability_denies_empty_or_missing_permissions(
    changes: dict[str, object],
    purpose: str,
    provider: str,
    requires_derivative: bool,
    reason: str,
) -> None:
    asset = _asset(status=AssetState.AVAILABLE)
    decision = _decision(
        asset,
        _rights(asset, **changes),
        purpose=purpose,
        provider=provider,
        requires_derivative=requires_derivative,
    )

    assert decision.authorized is False
    assert decision.reason_code.value == reason
    assert decision.rights_record_version == 3


def test_rights_cannot_make_an_unvalidated_asset_usable() -> None:
    asset = _asset(status=AssetState.PENDING_RIGHTS)

    decision = _decision(asset, _rights(asset))

    assert decision.authorized is False
    assert decision.reason_code == RightsDecisionCode.ASSET_NOT_AVAILABLE


def test_revocation_record_is_an_immutable_denial() -> None:
    asset = _asset(status=AssetState.BLOCKED)
    asset.block_reason = "RIGHTS_REVOKED"
    revoked = _rights(asset, decision=RightsRecordDecision.REVOKE)

    decision = _decision(asset, revoked)

    assert decision.authorized is False
    assert decision.reason_code == RightsDecisionCode.RIGHTS_REVOKED
    assert decision.rights_record_version == revoked.version_number


def test_administrator_block_is_the_strongest_reason_after_asset_identity() -> None:
    asset = _asset(status=AssetState.BLOCKED)
    asset.block_reason = "ADMINISTRATIVELY_BLOCKED"

    without_rights = evaluate_current_usability(
        asset=asset,
        rights_record=None,
        asset_version_id=asset.current_version_id,
        purpose="RETRIEVAL",
        provider="milvus",
        requires_derivative=False,
        decision_time=NOW,
    )
    wrong_asset_version = evaluate_current_usability(
        asset=asset,
        rights_record=None,
        asset_version_id=new_uuid7(),
        purpose="RETRIEVAL",
        provider="milvus",
        requires_derivative=False,
        decision_time=NOW,
    )
    expired_rights = _rights(asset, valid_from=NOW - timedelta(days=2), valid_until=NOW)

    assert wrong_asset_version.reason_code == RightsDecisionCode.ASSET_VERSION_NOT_CURRENT
    assert without_rights.reason_code == RightsDecisionCode.ADMINISTRATIVELY_BLOCKED
    assert _decision(asset, expired_rights).reason_code == (
        RightsDecisionCode.ADMINISTRATIVELY_BLOCKED
    )
