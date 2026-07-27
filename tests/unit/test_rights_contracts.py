from datetime import UTC, datetime

import pytest
from commercevision_contracts import (
    AssetAdministratorBlockRequestV1,
    RightsRecordMutationRequestV1,
    RightsRecordResponseV1,
    RightsRecordRevokeRequestV1,
    RightsUsabilityRequestV1,
)
from commercevision_contracts.events import (
    ASSET_RIGHTS_EXPIRED_V1,
    AssetRightsChangedPayload,
    EventHandling,
    EventQueue,
    EventType,
    event_contract_for,
)
from pydantic import ValidationError

NOW = datetime(2026, 7, 27, 9, 0, tzinfo=UTC)


def _mutation(**changes: object) -> RightsRecordMutationRequestV1:
    values: dict[str, object] = {
        "expected_asset_version": 3,
        "asset_version_id": "019c0000-0000-7000-8000-000000000001",
        "owner_reference": "brand-owner-1",
        "source": "brand-dam",
        "license_reference": "enterprise-license",
        "allowed_uses": ["RETRIEVAL"],
        "allowed_providers": ["milvus"],
        "derivative_allowed": False,
        "public_demo_allowed": False,
        "evidence_reference": "evidence://rights/1",
        "terms_sha256": "a" * 64,
        "valid_from": NOW,
        "valid_until": None,
        "perpetual": True,
    }
    values.update(changes)
    return RightsRecordMutationRequestV1.model_validate(values)


def test_rights_mutation_preserves_visible_deny_by_default_sets() -> None:
    request = _mutation(allowed_uses=[], allowed_providers=[])

    assert request.allowed_uses == []
    assert request.allowed_providers == []


def test_rights_mutation_rejects_implicit_perpetual_and_duplicate_permissions() -> None:
    with pytest.raises(ValidationError, match="valid_until"):
        _mutation(perpetual=False, valid_until=None)
    with pytest.raises(ValidationError, match="duplicate"):
        _mutation(allowed_uses=["RETRIEVAL", "RETRIEVAL"])


@pytest.mark.parametrize(
    ("contract", "field", "value"),
    [
        (RightsRecordRevokeRequestV1, "reason", " license withdrawn"),
        (RightsRecordRevokeRequestV1, "evidence_reference", "evidence://1\n"),
        (AssetAdministratorBlockRequestV1, "reason", "legal\x00hold"),
        (AssetAdministratorBlockRequestV1, "evidence_reference", " evidence://2"),
    ],
)
def test_blocking_inputs_reject_untrimmed_or_control_text(
    contract: type[RightsRecordRevokeRequestV1 | AssetAdministratorBlockRequestV1],
    field: str,
    value: str,
) -> None:
    data = {
        "expected_asset_version": 3,
        "reason": "license withdrawn",
        "evidence_reference": "evidence://rights/revocation",
        field: value,
    }

    with pytest.raises(ValidationError, match="trimmed|controls"):
        contract.model_validate(data)


def test_usability_request_requires_exact_version_purpose_provider_and_time() -> None:
    request = RightsUsabilityRequestV1(
        asset_version_id="019c0000-0000-7000-8000-000000000001",
        purpose="VISION_ANALYSIS",
        provider="qwen-vl",
        requires_derivative=True,
        decision_time=NOW,
    )

    assert request.decision_time == NOW
    assert request.requires_derivative is True


def test_rights_response_contains_auditable_evidence_and_exact_version() -> None:
    response_fields = set(RightsRecordResponseV1.model_fields)

    assert {
        "id",
        "version_number",
        "evidence_reference",
        "terms_sha256",
        "allowed_uses",
        "allowed_providers",
        "valid_until",
        "perpetual",
    }.issubset(response_fields)


def test_rights_changed_is_a_typed_asset_observation_with_convergence_action() -> None:
    contract = event_contract_for(EventType.ASSET_RIGHTS_CHANGED, 1)
    payload = contract.validate_payload(
        {
            "workspace_id": "workspace-a",
            "asset_id": "019c0000-0000-7000-8000-000000000001",
            "asset_version_id": "019c0000-0000-7000-8000-000000000002",
            "rights_record_id": "019c0000-0000-7000-8000-000000000003",
            "rights_record_version": 2,
            "change": "REPLACED",
            "resulting_asset_state": "AVAILABLE",
            "required_convergence": "REINDEX",
        }
    )

    assert isinstance(payload, AssetRightsChangedPayload)
    assert contract.queue == EventQueue.ASSET
    assert contract.handling == EventHandling.OBSERVATION


def test_deny_by_default_registration_has_a_typed_repair_event() -> None:
    payload = AssetRightsChangedPayload(
        workspace_id="workspace-a",
        asset_id="019c0000-0000-7000-8000-000000000001",
        asset_version_id="019c0000-0000-7000-8000-000000000002",
        rights_record_id="019c0000-0000-7000-8000-000000000003",
        rights_record_version=1,
        change="REGISTERED",
        resulting_asset_state="PENDING_RIGHTS",
        required_convergence="REMOVE_EXTERNAL_DERIVATIVES",
    )

    assert payload.resulting_asset_state == "PENDING_RIGHTS"


def test_scheduled_activation_has_a_typed_reindex_event() -> None:
    payload = AssetRightsChangedPayload(
        workspace_id="workspace-a",
        asset_id="019c0000-0000-7000-8000-000000000001",
        asset_version_id="019c0000-0000-7000-8000-000000000002",
        rights_record_id="019c0000-0000-7000-8000-000000000003",
        rights_record_version=1,
        change="ACTIVATED",
        resulting_asset_state="AVAILABLE",
        required_convergence="REINDEX",
    )

    assert payload.change == "ACTIVATED"


@pytest.mark.parametrize(
    "changes",
    [
        {
            "resulting_asset_state": "AVAILABLE",
            "required_convergence": "REMOVE_EXTERNAL_DERIVATIVES",
        },
        {
            "change": "REVOKED",
            "resulting_asset_state": "RIGHTS_EXPIRED",
            "required_convergence": "REMOVE_EXTERNAL_DERIVATIVES",
        },
        {
            "change": "REGISTERED",
            "rights_record_id": None,
            "rights_record_version": None,
        },
    ],
)
def test_rights_event_rejects_contradictory_state_and_convergence(
    changes: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "workspace_id": "workspace-a",
        "asset_id": "019c0000-0000-7000-8000-000000000001",
        "asset_version_id": "019c0000-0000-7000-8000-000000000002",
        "rights_record_id": "019c0000-0000-7000-8000-000000000003",
        "rights_record_version": 1,
        "change": "ACTIVATED",
        "resulting_asset_state": "AVAILABLE",
        "required_convergence": "REINDEX",
    }
    values.update(changes)

    with pytest.raises(ValidationError):
        AssetRightsChangedPayload.model_validate(values)


def test_rights_expired_event_accepts_only_the_expiry_transition() -> None:
    with pytest.raises(ValidationError):
        ASSET_RIGHTS_EXPIRED_V1.validate_payload(
            {
                "workspace_id": "workspace-a",
                "asset_id": "019c0000-0000-7000-8000-000000000001",
                "asset_version_id": "019c0000-0000-7000-8000-000000000002",
                "rights_record_id": "019c0000-0000-7000-8000-000000000003",
                "rights_record_version": 1,
                "change": "REGISTERED",
                "resulting_asset_state": "AVAILABLE",
                "required_convergence": "REINDEX",
            }
        )
