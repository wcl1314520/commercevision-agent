from __future__ import annotations

from decimal import Decimal

import pytest
from commercevision_contracts.product_briefs import (
    ProductBriefEvidenceOutput,
    ProductBriefEvidenceRevisionV1,
    ProductBriefFieldOutput,
    ProductBriefFieldRevisionV1,
)
from commercevision_domain import (
    ProductBriefEvidence,
    ProductBriefEvidenceKind,
    ProductBriefField,
    ProductBriefFieldConflict,
    ProductBriefFieldSource,
)
from pydantic import ValidationError

ASSET_VERSION_ID = "019b0000-0000-7000-8000-000000000003"


def _provider_evidence() -> tuple[ProductBriefEvidenceOutput, ...]:
    return (
        ProductBriefEvidenceOutput(
            source_asset_version_id=ASSET_VERSION_ID,
            kind=ProductBriefEvidenceKind.IMAGE_REGION,
            reference=f"asset-region://{'b' * 64}",
            region=(0.1, 0.2, 0.7, 0.8),
            excerpt_sha256="a" * 64,
        ),
    )


def _revision_evidence() -> tuple[ProductBriefEvidenceRevisionV1, ...]:
    return (
        ProductBriefEvidenceRevisionV1(
            source_asset_version_id=ASSET_VERSION_ID,
            kind=ProductBriefEvidenceKind.IMAGE_REGION,
            reference=f"asset-region://{'b' * 64}",
            region=(0.1, 0.2, 0.7, 0.8),
            excerpt_sha256="a" * 64,
        ),
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (
            "common.identity",
            {
                "kind": "IDENTITY",
                "display_name": "Hydrating Serum",
                "model_number": None,
                "variant": "30 ml",
            },
        ),
        (
            "common.category",
            {"kind": "CATEGORY", "code": "beauty.skincare", "label": "Skin care"},
        ),
        ("common.brand", {"kind": "TEXT", "text": "Northstar Labs"}),
        (
            "common.colors",
            {"kind": "TEXT_LIST", "items": ["clear", "silver"]},
        ),
        (
            "beauty.ingredient_claim_evidence",
            {
                "kind": "STATEMENT_LIST",
                "statements": ["Hyaluronic acid appears on the label"],
            },
        ),
        (
            "beauty.medical_like_claim_flags",
            {"kind": "FLAG_LIST", "flags": ["repairs skin damage"]},
        ),
        (
            "automotive.dimensions_evidence",
            {
                "kind": "DIMENSION_LIST",
                "dimensions": [
                    {
                        "name": "outer_diameter",
                        "value": "76",
                        "unit": "mm",
                        "raw_text": "OD 76 mm",
                    }
                ],
            },
        ),
    ],
)
def test_provider_field_contract_accepts_only_the_versioned_shape_for_each_path(
    path: str,
    value: dict[str, object],
) -> None:
    field = ProductBriefFieldOutput(
        path=path,
        value=value,
        confidence=Decimal("0.9700"),
        conflict=ProductBriefFieldConflict.NONE,
        review_required=False,
        sensitive=False,
        evidence=_provider_evidence(),
    )

    assert field.value.model_dump(mode="json") == value


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("common.brand", ["Northstar Labs"]),
        ("common.brand", {"kind": "FLAG_LIST", "flags": ["Northstar Labs"]}),
        (
            "automotive.dimensions_evidence",
            {"kind": "DIMENSION_LIST", "dimensions": [{"name": "length", "value": True}]},
        ),
        (
            "beauty.medical_like_claim_flags",
            {"kind": "FLAG_LIST", "flags": ["claim"], "unbounded": "not allowed"},
        ),
    ],
)
def test_provider_field_contract_rejects_path_shape_mismatches(
    path: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        ProductBriefFieldOutput(
            path=path,
            value=value,
            confidence=Decimal("0.9700"),
            conflict=ProductBriefFieldConflict.NONE,
            review_required=False,
            sensitive=False,
            evidence=_provider_evidence(),
        )


def test_human_revision_uses_the_same_path_value_contract() -> None:
    with pytest.raises(ValidationError, match="ProductBrief field value"):
        ProductBriefFieldRevisionV1(
            path="common.brand",
            value={"kind": "TEXT_LIST", "items": ["Northstar Labs"]},
            sensitive=False,
            evidence=_revision_evidence(),
        )


@pytest.mark.parametrize(
    "reference",
    [
        "asset-region://https://signed.example/object",
        "asset-region://https%3A%2F%2Fsigned.example%2Fobject",
        "asset-region://aHR0cHM6Ly9zaWduZWQuZXhhbXBsZS9vYmplY3Q=",
    ],
)
def test_evidence_contract_rejects_provider_controlled_locations(reference: str) -> None:
    with pytest.raises(ValidationError, match="evidence reference"):
        ProductBriefEvidenceOutput(
            source_asset_version_id=ASSET_VERSION_ID,
            kind=ProductBriefEvidenceKind.IMAGE_REGION,
            reference=reference,
            region=(0.1, 0.2, 0.7, 0.8),
            excerpt_sha256="a" * 64,
        )


def test_domain_rejects_invalid_field_values_reconstructed_from_persistence() -> None:
    evidence = ProductBriefEvidence.create(
        source_asset_version_id=ASSET_VERSION_ID,
        kind=ProductBriefEvidenceKind.IMAGE_REGION,
        reference=f"asset-region://{'b' * 64}",
        region=(0.1, 0.2, 0.7, 0.8),
        excerpt_sha256="a" * 64,
    )

    with pytest.raises(ValueError, match="ProductBrief field value"):
        ProductBriefField.create(
            path="common.brand",
            value={"kind": "FLAG_LIST", "flags": ["Northstar Labs"]},
            confidence="0.97",
            source=ProductBriefFieldSource.MODEL,
            conflict=ProductBriefFieldConflict.NONE,
            review_required=False,
            sensitive=False,
            evidence=(evidence,),
        )
