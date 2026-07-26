"""Configured provider identity checks for reusable validation evidence."""

from __future__ import annotations

import hashlib

from commercevision_contracts.validation import (
    ContentSafetyConfiguredIdentity,
    ProvenanceConfiguredIdentity,
    ProvenanceVerificationOutcome,
)
from commercevision_domain import (
    AssetKind,
    AssetValidationResult,
    ValidationStage,
    ValidationVerdict,
)

from .asset_validation_evidence import AssetValidationEvidenceError

_NOT_APPLICABLE_VERSION = "not-applicable-v1"

NON_IMAGE_CONTENT_SAFETY_IDENTITY = ContentSafetyConfiguredIdentity(
    provider="content-safety",
    endpoint="local",
    service="asset-kind-not-applicable",
    sdk_version=_NOT_APPLICABLE_VERSION,
    policy_version=_NOT_APPLICABLE_VERSION,
    mapping_version=_NOT_APPLICABLE_VERSION,
)
NON_IMAGE_PROVENANCE_IDENTITY = ProvenanceConfiguredIdentity(
    validator="c2pa",
    sdk_version=_NOT_APPLICABLE_VERSION,
    trust_config_version=_NOT_APPLICABLE_VERSION,
    trust_config_sha256=hashlib.sha256(_NOT_APPLICABLE_VERSION.encode("ascii")).hexdigest(),
)
_IDENTITY_BOUND_VERDICTS = frozenset(
    {
        ValidationVerdict.PASS,
        ValidationVerdict.REVIEW,
        ValidationVerdict.NOT_APPLICABLE,
    }
)


def assert_content_safety_stage_identity(
    *,
    result: AssetValidationResult,
    asset_kind: AssetKind,
    configured_identity: ContentSafetyConfiguredIdentity,
) -> None:
    if result.verdict not in _IDENTITY_BOUND_VERDICTS:
        return
    expected = (
        NON_IMAGE_CONTENT_SAFETY_IDENTITY
        if result.verdict == ValidationVerdict.NOT_APPLICABLE
        else configured_identity
    )
    kind_matches = (
        asset_kind != AssetKind.IMAGE
        if result.verdict == ValidationVerdict.NOT_APPLICABLE
        else asset_kind == AssetKind.IMAGE
    )
    evidence = result.evidence
    if not all(
        (
            result.stage == ValidationStage.CONTENT_SAFETY,
            kind_matches,
            result.validator_name == expected.provider,
            result.validator_version == expected.sdk_version,
            evidence.get("asset_kind") == asset_kind.value,
            evidence.get("endpoint") == expected.endpoint,
            evidence.get("mapping_version") == expected.mapping_version,
            evidence.get("outcome") == result.verdict.value,
            evidence.get("policy_version") == expected.policy_version,
            evidence.get("provider") == expected.provider,
            evidence.get("sdk_version") == expected.sdk_version,
            evidence.get("service") == expected.service,
        )
    ):
        raise AssetValidationEvidenceError(
            code="CONTENT_SAFETY_EVIDENCE_IDENTITY_MISMATCH",
            message=(
                "content-safety evidence does not match the active provider "
                "policy and mapping identity"
            ),
        )


def assert_provenance_stage_identity(
    *,
    result: AssetValidationResult,
    asset_kind: AssetKind,
    configured_identity: ProvenanceConfiguredIdentity,
) -> None:
    if result.verdict not in _IDENTITY_BOUND_VERDICTS:
        return
    not_applicable = result.verdict == ValidationVerdict.NOT_APPLICABLE
    expected = NON_IMAGE_PROVENANCE_IDENTITY if not_applicable else configured_identity
    expected_outcome = (
        ValidationVerdict.NOT_APPLICABLE.value
        if not_applicable
        else ProvenanceVerificationOutcome.EVIDENCE.value
    )
    kind_matches = (
        asset_kind != AssetKind.IMAGE if not_applicable else asset_kind == AssetKind.IMAGE
    )
    evidence = result.evidence
    if not all(
        (
            result.stage == ValidationStage.PROVENANCE,
            kind_matches,
            result.validator_name == expected.validator,
            result.validator_version == expected.sdk_version,
            evidence.get("asset_kind") == asset_kind.value,
            evidence.get("outcome") == expected_outcome,
            evidence.get("sdk_version") == expected.sdk_version,
            evidence.get("trust_config_sha256") == expected.trust_config_sha256,
            evidence.get("trust_config_version") == expected.trust_config_version,
            evidence.get("validator") == expected.validator,
        )
    ):
        raise AssetValidationEvidenceError(
            code="PROVENANCE_EVIDENCE_IDENTITY_MISMATCH",
            message=(
                "provenance evidence does not match the active validator "
                "and trust configuration identity"
            ),
        )
