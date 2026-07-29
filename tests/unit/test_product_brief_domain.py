from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta

import pytest
from commercevision_contracts.events import ProductBriefAwaitingConfirmationPayload
from commercevision_domain import (
    ProductBrief,
    ProductBriefCategory,
    ProductBriefEvidence,
    ProductBriefEvidenceKind,
    ProductBriefField,
    ProductBriefFieldConflict,
    ProductBriefFieldSource,
    ProductBriefFieldValueKind,
    ProductBriefReviewPolicy,
    ProductBriefState,
    ProductBriefVersion,
    ProductBriefVersionSource,
    product_brief_field_paths,
    product_brief_field_value_kind,
)
from commercevision_domain.workflow.errors import ConcurrencyError

NOW = datetime(2026, 7, 28, 8, 0, tzinfo=UTC)
WORKSPACE_ID = "brief-domain"
WORKFLOW_ID = "019b0000-0000-7000-8000-000000000001"
PRODUCT_ID = "019b0000-0000-7000-8000-000000000002"
ASSET_VERSION_ID = "019b0000-0000-7000-8000-000000000003"


def _field_value(path: str) -> dict[str, object]:
    kind = product_brief_field_value_kind(path)
    if kind == ProductBriefFieldValueKind.IDENTITY:
        return {
            "kind": "IDENTITY",
            "display_name": path,
            "model_number": None,
            "variant": None,
        }
    if kind == ProductBriefFieldValueKind.CATEGORY:
        return {"kind": "CATEGORY", "code": "catalog.category", "label": path}
    if kind == ProductBriefFieldValueKind.TEXT:
        return {"kind": "TEXT", "text": path}
    if kind == ProductBriefFieldValueKind.TEXT_LIST:
        return {"kind": "TEXT_LIST", "items": [path]}
    if kind == ProductBriefFieldValueKind.STATEMENT_LIST:
        return {"kind": "STATEMENT_LIST", "statements": []}
    if kind == ProductBriefFieldValueKind.FLAG_LIST:
        return {"kind": "FLAG_LIST", "flags": []}
    if kind == ProductBriefFieldValueKind.DIMENSION_LIST:
        return {"kind": "DIMENSION_LIST", "dimensions": []}
    raise AssertionError(f"unexpected ProductBrief field kind: {kind}")


def _evidence(kind: ProductBriefEvidenceKind = ProductBriefEvidenceKind.IMAGE_REGION):
    return ProductBriefEvidence.create(
        source_asset_version_id=ASSET_VERSION_ID,
        kind=kind,
        reference=f"asset-region://{'b' * 64}",
        region=(0.1, 0.2, 0.7, 0.8),
        excerpt_sha256="a" * 64,
    )


def _fields(
    category: ProductBriefCategory,
    *,
    low_path: str | None = None,
    conflict_path: str | None = None,
    sensitive_path: str | None = None,
) -> tuple[ProductBriefField, ...]:
    fields: list[ProductBriefField] = []
    for path in product_brief_field_paths(category):
        fields.append(
            ProductBriefField.create(
                path=path,
                value=_field_value(path),
                confidence="0.42" if path == low_path else "0.97",
                source=ProductBriefFieldSource.MODEL,
                conflict=(
                    ProductBriefFieldConflict.CONFLICTING
                    if path == conflict_path
                    else ProductBriefFieldConflict.NONE
                ),
                review_required=False,
                sensitive=path == sensitive_path,
                evidence=(_evidence(),),
            )
        )
    return tuple(fields)


def _version(
    *,
    number: int,
    category: ProductBriefCategory = ProductBriefCategory.BEAUTY,
    fields: tuple[ProductBriefField, ...] | None = None,
    source: ProductBriefVersionSource = ProductBriefVersionSource.MODEL,
) -> ProductBriefVersion:
    selected_fields = fields or _fields(category)
    if source == ProductBriefVersionSource.HUMAN:
        selected_fields = tuple(
            replace(field, source=ProductBriefFieldSource.HUMAN) for field in selected_fields
        )
    decision = ProductBriefReviewPolicy(
        policy_version="brief-review-v1",
        confidence_threshold="0.80",
    ).evaluate(selected_fields)
    return ProductBriefVersion.create(
        workspace_id=WORKSPACE_ID,
        product_brief_id="019b0000-0000-7000-8000-000000000004",
        version_number=number,
        supersedes_version_id=None,
        category=category,
        common_schema_version="product-brief-common-v1",
        category_schema_version=f"product-brief-{category.value.lower()}-v1",
        fields=selected_fields,
        changed_field_paths=tuple(field.path for field in selected_fields),
        review_decision=decision,
        source=source,
        prompt_version="product-brief-prompt-v1"
        if source == ProductBriefVersionSource.MODEL
        else None,
        provider_call_id=(
            "019b0000-0000-7000-8000-000000000005"
            if source == ProductBriefVersionSource.MODEL
            else None
        ),
        actor_id="product-brief-worker"
        if source == ProductBriefVersionSource.MODEL
        else "reviewer",
        revision_reason=None
        if source == ProductBriefVersionSource.MODEL
        else "Correct package type",
        retention_class="TASK",
        retention_deadline=NOW + timedelta(hours=72),
        now=NOW,
    )


def test_category_schema_requires_every_common_and_category_field_with_evidence() -> None:
    version = _version(number=1)

    assert {field.path for field in version.fields} == set(
        product_brief_field_paths(ProductBriefCategory.BEAUTY)
    )
    assert all(field.evidence for field in version.fields)
    assert version.payload_sha256

    with pytest.raises(ValueError, match="missing required ProductBrief fields"):
        replace(version, fields=version.fields[1:])
    with pytest.raises(ValueError, match="at least one evidence"):
        replace(version.fields[0], evidence=())
    with pytest.raises(ValueError, match="controlled internal reference"):
        ProductBriefEvidence.create(
            source_asset_version_id=ASSET_VERSION_ID,
            kind=ProductBriefEvidenceKind.IMAGE_REGION,
            reference="https://signed.invalid/source.png?secret=1",
            region=(0.1, 0.2, 0.7, 0.8),
        )
    with pytest.raises(ValueError, match="does not match its kind"):
        ProductBriefEvidence.create(
            source_asset_version_id=ASSET_VERSION_ID,
            kind=ProductBriefEvidenceKind.VISIBLE_TEXT,
            reference=f"asset-region://{'b' * 64}",
        )


def test_immutable_payload_hash_is_independent_of_persistence_row_order() -> None:
    fields = list(_fields(ProductBriefCategory.BEAUTY))
    second_evidence = ProductBriefEvidence.create(
        source_asset_version_id=ASSET_VERSION_ID,
        kind=ProductBriefEvidenceKind.VISIBLE_TEXT,
        reference=f"asset-text://{'c' * 64}",
        excerpt_sha256="b" * 64,
    )
    fields[0] = replace(
        fields[0],
        evidence=(*fields[0].evidence, second_evidence),
    )
    version = _version(number=1, fields=tuple(fields))

    reconstructed = replace(
        version,
        fields=tuple(
            replace(field, evidence=tuple(reversed(field.evidence)))
            for field in reversed(version.fields)
        ),
    )

    assert reconstructed.payload_sha256 == version.payload_sha256


@pytest.mark.parametrize(
    ("field_variant", "reason"),
    [
        ({"low_path": "common.brand"}, "LOW_CONFIDENCE"),
        ({"conflict_path": "common.colors"}, "SOURCE_CONFLICT"),
        ({"sensitive_path": "beauty.medical_like_claim_flags"}, "SENSITIVE_CLAIM"),
    ],
)
def test_review_policy_requires_human_confirmation_for_mandatory_risk(
    field_variant: dict[str, str],
    reason: str,
) -> None:
    fields = _fields(ProductBriefCategory.BEAUTY, **field_variant)

    decision = ProductBriefReviewPolicy(
        policy_version="brief-review-v1",
        confidence_threshold="0.80",
    ).evaluate(fields)

    assert decision.confirmation_required is True
    assert decision.unresolved_field_count == 1
    assert decision.reasons_by_path[next(iter(field_variant.values()))] == (reason,)


def test_review_policy_derives_sensitive_claims_instead_of_trusting_provider_flags() -> None:
    fields = list(_fields(ProductBriefCategory.BEAUTY))
    path = "beauty.medical_like_claim_flags"
    field_index = next(index for index, field in enumerate(fields) if field.path == path)
    fields[field_index] = replace(
        fields[field_index],
        value={"kind": "FLAG_LIST", "flags": ["repairs skin damage"]},
        sensitive=False,
        review_required=False,
    )
    policy = ProductBriefReviewPolicy(
        policy_version="brief-review-v1",
        confidence_threshold="0.80",
        sensitive_claim_paths=frozenset({path}),
    )

    normalized = policy.enforce_risk_floor(tuple(fields))
    decision = policy.evaluate(normalized)
    selected = next(field for field in normalized if field.path == path)

    assert selected.sensitive is True
    assert decision.confirmation_required is True
    assert decision.reasons_by_path[path] == ("SENSITIVE_CLAIM",)


def test_review_policy_enforces_mandatory_review_without_provider_opt_in() -> None:
    fields = _fields(ProductBriefCategory.BEAUTY)
    policy = ProductBriefReviewPolicy(
        policy_version="brief-review-v1",
        confidence_threshold="0.80",
        mandatory_review_paths=frozenset({"common.identity"}),
    )

    normalized = policy.enforce_risk_floor(fields)
    decision = policy.evaluate(normalized)
    selected = next(field for field in normalized if field.path == "common.identity")

    assert selected.review_required is True
    assert decision.confirmation_required is True
    assert decision.reasons_by_path["common.identity"] == ("MANDATORY_REVIEW",)


def test_review_policy_snapshot_changes_with_threshold_and_risk_rules() -> None:
    baseline = ProductBriefReviewPolicy(
        policy_version="brief-review-v1",
        confidence_threshold="0.80",
        mandatory_review_paths=frozenset({"common.identity"}),
        sensitive_claim_paths=frozenset({"common.sensitive_claims"}),
    )

    assert (
        baseline.snapshot_sha256
        == ProductBriefReviewPolicy(
            policy_version="brief-review-v1",
            confidence_threshold="0.80",
            mandatory_review_paths=frozenset({"common.identity"}),
            sensitive_claim_paths=frozenset({"common.sensitive_claims"}),
        ).snapshot_sha256
    )
    assert (
        baseline.snapshot_sha256
        != ProductBriefReviewPolicy(
            policy_version="brief-review-v1",
            confidence_threshold="0.81",
            mandatory_review_paths=frozenset({"common.identity"}),
            sensitive_claim_paths=frozenset({"common.sensitive_claims"}),
        ).snapshot_sha256
    )


def test_product_brief_field_rejects_recursive_depth_as_a_domain_error() -> None:
    nested: object = "leaf"
    for _ in range(5000):
        nested = {"child": nested}

    with pytest.raises(ValueError, match="depth limit"):
        replace(_fields(ProductBriefCategory.BEAUTY)[0], value=nested)


def test_product_brief_versions_are_immutable_and_confirmation_targets_exact_current_version() -> (
    None
):
    brief = ProductBrief.create(
        workspace_id=WORKSPACE_ID,
        workflow_id=WORKFLOW_ID,
        product_id=PRODUCT_ID,
        created_by="requester",
        retention_class="TASK",
        retention_deadline=NOW + timedelta(hours=72),
        now=NOW,
    )
    version_one = replace(
        _version(
            number=1,
            fields=_fields(ProductBriefCategory.BEAUTY, low_path="common.brand"),
        ),
        product_brief_id=brief.id,
    )
    brief.publish_version(
        version_one,
        expected_version=1,
        now=NOW + timedelta(seconds=1),
    )

    assert brief.state == ProductBriefState.AWAITING_CONFIRMATION
    assert brief.current_version_id == version_one.id
    assert brief.confirmed_version_id is None
    with pytest.raises(FrozenInstanceError):
        version_one.actor_id = "tampered"  # type: ignore[misc]

    with pytest.raises(ConcurrencyError):
        brief.confirm(
            product_brief_version_id="019b0000-0000-7000-8000-000000000099",
            expected_version=brief.version,
            now=NOW + timedelta(seconds=2),
        )

    brief.confirm(
        product_brief_version_id=version_one.id,
        expected_version=brief.version,
        now=NOW + timedelta(seconds=2),
    )
    assert brief.state == ProductBriefState.CONFIRMED
    assert brief.confirmed_version_id == version_one.id

    human_version = replace(
        _version(
            number=2,
            fields=_fields(ProductBriefCategory.BEAUTY),
            source=ProductBriefVersionSource.HUMAN,
        ),
        product_brief_id=brief.id,
        supersedes_version_id=version_one.id,
    )
    brief.publish_version(
        human_version,
        expected_version=brief.version,
        now=NOW + timedelta(seconds=3),
    )

    assert brief.state == ProductBriefState.AWAITING_CONFIRMATION
    assert brief.current_version_id == human_version.id
    assert brief.confirmed_version_id == version_one.id
    assert human_version.confirmation_required is True
    assert human_version.unresolved_field_count == 0
    payload = ProductBriefAwaitingConfirmationPayload(
        workspace_id=WORKSPACE_ID,
        product_brief_id=brief.id,
        product_brief_version=brief.version,
        product_brief_version_id=human_version.id,
        product_brief_version_number=human_version.version_number,
        workflow_id=WORKFLOW_ID,
        operation_id="019b0000-0000-7000-8000-000000000005",
        unresolved_field_count=human_version.unresolved_field_count,
        review_policy_version=human_version.review_policy_version,
    )
    assert payload.unresolved_field_count == 0


def test_foundation_brief_has_no_task_deadline_but_cannot_drop_source_evidence() -> None:
    brief = ProductBrief.create(
        workspace_id=WORKSPACE_ID,
        workflow_id=WORKFLOW_ID,
        product_id=PRODUCT_ID,
        created_by="requester",
        retention_class="FOUNDATION",
        retention_deadline=None,
        now=NOW,
    )

    assert brief.retention_deadline is None
    with pytest.raises(ValueError, match="Task ProductBrief requires a retention deadline"):
        replace(brief, retention_class="TASK")
