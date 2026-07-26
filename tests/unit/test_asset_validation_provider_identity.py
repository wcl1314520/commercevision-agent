from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from commercevision_application.asset_validation_evidence import (
    AssetValidationEvidenceError,
)
from commercevision_application.asset_validation_identity import (
    NON_IMAGE_CONTENT_SAFETY_IDENTITY,
    NON_IMAGE_PROVENANCE_IDENTITY,
    assert_content_safety_stage_identity,
    assert_provenance_stage_identity,
)
from commercevision_contracts.validation import (
    ContentSafetyConfiguredIdentity,
    ProvenanceConfiguredIdentity,
)
from commercevision_domain import (
    AssetKind,
    AssetValidationResult,
    ValidationStage,
    ValidationVerdict,
    new_uuid7,
)

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)

CONTENT_IDENTITY = ContentSafetyConfiguredIdentity(
    provider="alibaba-green20220302",
    endpoint="green-cip.cn-shanghai.aliyuncs.com",
    service="postImageCheckByVL_ec",
    sdk_version="3.2.4",
    policy_version="content-policy-v1",
    mapping_version="risk-map-v1",
)
PROVENANCE_IDENTITY = ProvenanceConfiguredIdentity(
    validator="c2pa",
    sdk_version="0.36.0",
    trust_config_version="trust-v1",
    trust_config_sha256="a" * 64,
)


def _result(
    *,
    stage: ValidationStage,
    validator_name: str,
    validator_version: str,
    verdict: ValidationVerdict,
    evidence: dict[str, object],
) -> AssetValidationResult:
    return AssetValidationResult.create(
        workspace_id="identity-workspace",
        operation_id=new_uuid7(),
        asset_version_id=new_uuid7(),
        asset_object_id=new_uuid7(),
        attempt_number=1,
        stage=stage,
        validator_name=validator_name,
        validator_version=validator_version,
        policy_version="asset-validation-v1",
        verdict=verdict,
        reason_code=None,
        object_provider_version_id="source-version-1",
        object_etag='"source-etag"',
        content_sha256="f" * 64,
        evidence=evidence,
        retention_deadline=None,
        now=NOW,
    )


def _content_pass() -> AssetValidationResult:
    identity = CONTENT_IDENTITY
    return _result(
        stage=ValidationStage.CONTENT_SAFETY,
        validator_name=identity.provider,
        validator_version=identity.sdk_version,
        verdict=ValidationVerdict.PASS,
        evidence={
            "asset_kind": AssetKind.IMAGE.value,
            "endpoint": identity.endpoint,
            "mapping_version": identity.mapping_version,
            "outcome": ValidationVerdict.PASS.value,
            "policy_version": identity.policy_version,
            "provider": identity.provider,
            "sdk_version": identity.sdk_version,
            "service": identity.service,
        },
    )


def _provenance_pass() -> AssetValidationResult:
    identity = PROVENANCE_IDENTITY
    return _result(
        stage=ValidationStage.PROVENANCE,
        validator_name=identity.validator,
        validator_version=identity.sdk_version,
        verdict=ValidationVerdict.PASS,
        evidence={
            "asset_kind": AssetKind.IMAGE.value,
            "outcome": "EVIDENCE",
            "sdk_version": identity.sdk_version,
            "trust_config_sha256": identity.trust_config_sha256,
            "trust_config_version": identity.trust_config_version,
            "validator": identity.validator,
        },
    )


@pytest.mark.parametrize(
    "rotation",
    [
        {"policy_version": "content-policy-v2"},
        {"mapping_version": "risk-map-v2"},
    ],
)
def test_prior_content_pass_fails_closed_after_policy_identity_rotation(
    rotation: dict[str, str],
) -> None:
    prior_pass = _content_pass()

    assert_content_safety_stage_identity(
        result=prior_pass,
        asset_kind=AssetKind.IMAGE,
        configured_identity=CONTENT_IDENTITY,
    )
    with pytest.raises(AssetValidationEvidenceError) as failed:
        assert_content_safety_stage_identity(
            result=prior_pass,
            asset_kind=AssetKind.IMAGE,
            configured_identity=replace(CONTENT_IDENTITY, **rotation),
        )

    assert failed.value.code == "CONTENT_SAFETY_EVIDENCE_IDENTITY_MISMATCH"
    assert failed.value.retryable is False


@pytest.mark.parametrize(
    "rotation",
    [
        {"trust_config_version": "trust-v2"},
        {"trust_config_sha256": "b" * 64},
    ],
)
def test_prior_provenance_pass_fails_closed_after_trust_identity_rotation(
    rotation: dict[str, str],
) -> None:
    prior_pass = _provenance_pass()

    assert_provenance_stage_identity(
        result=prior_pass,
        asset_kind=AssetKind.IMAGE,
        configured_identity=PROVENANCE_IDENTITY,
    )
    with pytest.raises(AssetValidationEvidenceError) as failed:
        assert_provenance_stage_identity(
            result=prior_pass,
            asset_kind=AssetKind.IMAGE,
            configured_identity=replace(PROVENANCE_IDENTITY, **rotation),
        )

    assert failed.value.code == "PROVENANCE_EVIDENCE_IDENTITY_MISMATCH"
    assert failed.value.retryable is False


@pytest.mark.parametrize(
    ("stage", "identity", "assert_identity"),
    [
        (
            ValidationStage.CONTENT_SAFETY,
            NON_IMAGE_CONTENT_SAFETY_IDENTITY,
            assert_content_safety_stage_identity,
        ),
        (
            ValidationStage.PROVENANCE,
            NON_IMAGE_PROVENANCE_IDENTITY,
            assert_provenance_stage_identity,
        ),
    ],
)
def test_non_image_not_applicable_requires_canonical_local_identity(
    stage: ValidationStage,
    identity: ContentSafetyConfiguredIdentity | ProvenanceConfiguredIdentity,
    assert_identity,
) -> None:
    validator_name = (
        identity.provider
        if isinstance(identity, ContentSafetyConfiguredIdentity)
        else identity.validator
    )
    evidence = {
        "asset_kind": AssetKind.LORA.value,
        "outcome": ValidationVerdict.NOT_APPLICABLE.value,
        "sdk_version": identity.sdk_version,
    }
    if isinstance(identity, ContentSafetyConfiguredIdentity):
        evidence.update(
            {
                "endpoint": identity.endpoint,
                "mapping_version": identity.mapping_version,
                "policy_version": identity.policy_version,
                "provider": identity.provider,
                "service": identity.service,
            }
        )
        active_identity = CONTENT_IDENTITY
    else:
        evidence.update(
            {
                "trust_config_sha256": identity.trust_config_sha256,
                "trust_config_version": identity.trust_config_version,
                "validator": identity.validator,
            }
        )
        active_identity = PROVENANCE_IDENTITY
    result = _result(
        stage=stage,
        validator_name=validator_name,
        validator_version=identity.sdk_version,
        verdict=ValidationVerdict.NOT_APPLICABLE,
        evidence=evidence,
    )

    assert_identity(
        result=result,
        asset_kind=AssetKind.LORA,
        configured_identity=active_identity,
    )
    stale_result = _result(
        stage=stage,
        validator_name=validator_name,
        validator_version="stale-not-applicable-v0",
        verdict=ValidationVerdict.NOT_APPLICABLE,
        evidence=dict(result.evidence),
    )
    with pytest.raises(AssetValidationEvidenceError):
        assert_identity(
            result=stale_result,
            asset_kind=AssetKind.LORA,
            configured_identity=active_identity,
        )
