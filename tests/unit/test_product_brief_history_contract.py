from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from commercevision_contracts.product_briefs import (
    ProductBriefEvidenceResponseV1,
    ProductBriefFieldResponseV1,
    ProductBriefProviderCallResponseV1,
    ProductBriefVersionListResponseV1,
    ProductBriefVersionResponseV1,
    ProductBriefVersionSummaryResponseV1,
)
from commercevision_domain import (
    CATEGORY_SCHEMA_VERSIONS,
    COMMON_SCHEMA_VERSION,
    ProductBriefCategory,
    ProductBriefEvidence,
    ProductBriefEvidenceKind,
    ProductBriefField,
    ProductBriefFieldConflict,
    ProductBriefFieldSource,
    ProductBriefFieldValueKind,
    ProductBriefReviewDecision,
    ProductBriefState,
    ProductBriefVersion,
    ProductBriefVersionSource,
    RetentionClass,
    product_brief_field_paths,
    product_brief_field_value_kind,
)
from pydantic import ValidationError

_BFF_RESPONSE_LIMIT_BYTES = 2 * 1024 * 1024
_PRODUCT_BRIEF_ID = "019f8a00-0000-7000-8000-000000000201"
_ASSET_VERSION_ID = "019f8a00-0000-7000-8000-000000000202"
_PROVIDER_CALL_ID = "019f8a00-0000-7000-8000-000000000203"
_NOW = datetime(2026, 7, 29, tzinfo=UTC)


def _maximum_text(index: int) -> str:
    prefix = f"{index:02d}"
    return prefix + ("x" * (107 - len(prefix)))


def _maximum_value(path: str) -> dict[str, object]:
    kind = product_brief_field_value_kind(path)
    if kind == ProductBriefFieldValueKind.IDENTITY:
        return {
            "kind": kind.value,
            "display_name": _maximum_text(0),
            "model_number": _maximum_text(1),
            "variant": _maximum_text(2),
        }
    if kind == ProductBriefFieldValueKind.CATEGORY:
        return {
            "kind": kind.value,
            "code": _maximum_text(0),
            "label": _maximum_text(1),
        }
    if kind == ProductBriefFieldValueKind.TEXT:
        return {"kind": kind.value, "text": _maximum_text(0)}
    if kind in {
        ProductBriefFieldValueKind.TEXT_LIST,
        ProductBriefFieldValueKind.STATEMENT_LIST,
        ProductBriefFieldValueKind.FLAG_LIST,
    }:
        collection_name = {
            ProductBriefFieldValueKind.TEXT_LIST: "items",
            ProductBriefFieldValueKind.STATEMENT_LIST: "statements",
            ProductBriefFieldValueKind.FLAG_LIST: "flags",
        }[kind]
        return {
            "kind": kind.value,
            collection_name: [_maximum_text(index) for index in range(4)],
        }
    if kind == ProductBriefFieldValueKind.DIMENSION_LIST:
        return {
            "kind": kind.value,
            "dimensions": [
                {
                    "name": _maximum_text(index),
                    "value": _maximum_text(index + 16),
                    "unit": _maximum_text(index + 32),
                    "raw_text": _maximum_text(index + 48),
                }
                for index in range(16)
            ],
        }
    raise AssertionError(f"unsupported ProductBrief value kind: {kind}")


def _large_fields(version_number: int) -> tuple[ProductBriefField, ...]:
    fields: list[ProductBriefField] = []
    for path_index, path in enumerate(product_brief_field_paths(ProductBriefCategory.BEAUTY)):
        evidence = tuple(
            ProductBriefEvidence.create(
                source_asset_version_id=_ASSET_VERSION_ID,
                kind=ProductBriefEvidenceKind.IMAGE_REGION,
                reference=(
                    "asset-region://"
                    + hashlib.sha256(
                        f"{version_number}:{path}:{evidence_index}".encode()
                    ).hexdigest()
                ),
                region=(0.0, 0.0, 1.0, 1.0),
                excerpt_sha256=hashlib.sha256(
                    f"excerpt:{version_number}:{path_index}:{evidence_index}".encode()
                ).hexdigest(),
            )
            for evidence_index in range(1)
        )
        fields.append(
            ProductBriefField.create(
                path=path,
                value=_maximum_value(path),
                confidence=Decimal("1"),
                source=ProductBriefFieldSource.MODEL,
                conflict=ProductBriefFieldConflict.NONE,
                review_required=False,
                sensitive=False,
                evidence=evidence,
            )
        )
    return tuple(fields)


def _large_version(
    version_number: int,
    supersedes_version_id: str | None,
) -> ProductBriefVersion:
    fields = _large_fields(version_number)
    return ProductBriefVersion.create(
        workspace_id="workspace-history-contract",
        product_brief_id=_PRODUCT_BRIEF_ID,
        version_number=version_number,
        supersedes_version_id=supersedes_version_id,
        category=ProductBriefCategory.BEAUTY,
        common_schema_version=COMMON_SCHEMA_VERSION,
        category_schema_version=CATEGORY_SCHEMA_VERSIONS[ProductBriefCategory.BEAUTY],
        fields=fields,
        review_decision=ProductBriefReviewDecision(
            policy_version="history-contract-v1",
            confidence_threshold=Decimal("0.8"),
            confirmation_required=False,
            unresolved_field_count=0,
            reasons_by_path={},
        ),
        source=ProductBriefVersionSource.MODEL,
        prompt_version="history-contract-v1",
        provider_call_id=_PROVIDER_CALL_ID,
        actor_id="vision-provider",
        revision_reason=None,
        retention_class=RetentionClass.TASK,
        retention_deadline=_NOW + timedelta(hours=72),
        now=_NOW + timedelta(seconds=version_number),
    )


def _detail_response(version: ProductBriefVersion) -> ProductBriefVersionResponseV1:
    return ProductBriefVersionResponseV1(
        id=version.id,
        product_brief_id=version.product_brief_id,
        version_number=version.version_number,
        supersedes_version_id=version.supersedes_version_id,
        effective_state=ProductBriefState.ARCHIVED,
        category=version.category,
        common_schema_version=version.common_schema_version,
        category_schema_version=version.category_schema_version,
        payload_sha256=version.payload_sha256,
        changed_field_paths=version.changed_field_paths,
        confirmation_required=version.confirmation_required,
        unresolved_field_count=version.unresolved_field_count,
        review_policy_version=version.review_policy_version,
        source=version.source,
        prompt_version=version.prompt_version,
        provider_call=ProductBriefProviderCallResponseV1(
            provider="deterministic-vision",
            requested_model="vision-large-v1",
            resolved_model="vision-large-v1",
            latency_ms=100,
        ),
        actor_id=version.actor_id,
        revision_reason=version.revision_reason,
        retention_class=version.retention_class,
        retention_deadline=version.retention_deadline,
        created_at=version.created_at,
        fields=tuple(
            ProductBriefFieldResponseV1(
                id=field.id,
                path=field.path,
                value=field.value,
                confidence=field.confidence,
                source=field.source,
                conflict=field.conflict,
                review_required=field.review_required,
                sensitive=field.sensitive,
                review_reasons=(),
                evidence=tuple(
                    ProductBriefEvidenceResponseV1(
                        id=evidence.id,
                        source_asset_version_id=evidence.source_asset_version_id,
                        kind=evidence.kind,
                        reference=evidence.reference,
                        region=evidence.region,
                        excerpt_sha256=evidence.excerpt_sha256,
                    )
                    for evidence in field.evidence
                ),
            )
            for field in version.fields
        ),
    )


def _summary_response(
    detail: ProductBriefVersionResponseV1,
) -> ProductBriefVersionSummaryResponseV1:
    return ProductBriefVersionSummaryResponseV1.model_validate(
        detail.model_dump(mode="json", exclude={"fields"})
    )


def _public_contract_maximum_text(index: int) -> str:
    prefix = f"{index:02d}"
    return prefix + ("x" * (2048 - len(prefix)))


def _public_contract_maximum_value(path: str) -> dict[str, object]:
    kind = product_brief_field_value_kind(path)
    if kind == ProductBriefFieldValueKind.IDENTITY:
        return {
            "kind": kind.value,
            "display_name": _public_contract_maximum_text(0),
            "model_number": _public_contract_maximum_text(1),
            "variant": _public_contract_maximum_text(2),
        }
    if kind == ProductBriefFieldValueKind.CATEGORY:
        return {
            "kind": kind.value,
            "code": _public_contract_maximum_text(0),
            "label": _public_contract_maximum_text(1),
        }
    if kind == ProductBriefFieldValueKind.TEXT:
        return {"kind": kind.value, "text": _public_contract_maximum_text(0)}
    if kind in {
        ProductBriefFieldValueKind.TEXT_LIST,
        ProductBriefFieldValueKind.STATEMENT_LIST,
        ProductBriefFieldValueKind.FLAG_LIST,
    }:
        collection_name = {
            ProductBriefFieldValueKind.TEXT_LIST: "items",
            ProductBriefFieldValueKind.STATEMENT_LIST: "statements",
            ProductBriefFieldValueKind.FLAG_LIST: "flags",
        }[kind]
        return {
            "kind": kind.value,
            collection_name: [_public_contract_maximum_text(index) for index in range(32)],
        }
    raise AssertionError(f"unexpected BEAUTY ProductBrief value kind: {kind}")


def _public_contract_maximum_detail(
    version_number: int,
) -> ProductBriefVersionResponseV1:
    fields = tuple(
        ProductBriefFieldResponseV1(
            id=f"field-{version_number:02d}-{path_index:02d}",
            path=path,
            value=_public_contract_maximum_value(path),
            confidence=Decimal("1"),
            source=ProductBriefFieldSource.MODEL,
            conflict=ProductBriefFieldConflict.NONE,
            review_required=False,
            sensitive=False,
            review_reasons=(),
            evidence=tuple(
                ProductBriefEvidenceResponseV1(
                    id=f"evidence-{version_number:02d}-{path_index:02d}-{index:02d}",
                    source_asset_version_id=_ASSET_VERSION_ID,
                    kind=ProductBriefEvidenceKind.IMAGE_REGION,
                    reference=(
                        "asset-region://"
                        + hashlib.sha256(
                            f"contract:{version_number}:{path}:{index}".encode()
                        ).hexdigest()
                    ),
                    region=(0.0, 0.0, 1.0, 1.0),
                    excerpt_sha256=hashlib.sha256(
                        f"contract-excerpt:{version_number}:{path}:{index}".encode()
                    ).hexdigest(),
                )
                for index in range(32)
            ),
        )
        for path_index, path in enumerate(product_brief_field_paths(ProductBriefCategory.BEAUTY))
    )
    return ProductBriefVersionResponseV1(
        id=f"public-contract-version-{version_number}",
        product_brief_id=_PRODUCT_BRIEF_ID,
        version_number=version_number,
        supersedes_version_id=(
            None if version_number == 1 else f"public-contract-version-{version_number - 1}"
        ),
        effective_state=ProductBriefState.ARCHIVED,
        category=ProductBriefCategory.BEAUTY,
        common_schema_version=COMMON_SCHEMA_VERSION,
        category_schema_version=CATEGORY_SCHEMA_VERSIONS[ProductBriefCategory.BEAUTY],
        payload_sha256=hashlib.sha256(f"payload:{version_number}".encode()).hexdigest(),
        changed_field_paths=tuple(field.path for field in fields),
        confirmation_required=False,
        unresolved_field_count=0,
        review_policy_version="history-contract-v1",
        source=ProductBriefVersionSource.MODEL,
        prompt_version="history-contract-v1",
        provider_call=ProductBriefProviderCallResponseV1(
            provider="deterministic-vision",
            requested_model="vision-large-v1",
            resolved_model="vision-large-v1",
            latency_ms=100,
        ),
        actor_id="vision-provider",
        revision_reason=None,
        retention_class=RetentionClass.TASK,
        retention_deadline=_NOW + timedelta(hours=72),
        created_at=_NOW + timedelta(seconds=version_number),
        fields=fields,
    )


def test_three_real_domain_versions_have_a_bounded_history_contract() -> None:
    versions: list[ProductBriefVersion] = []
    supersedes_version_id = None
    for version_number in range(1, 4):
        version = _large_version(version_number, supersedes_version_id)
        versions.append(version)
        supersedes_version_id = version.id

    details = tuple(_detail_response(version) for version in reversed(versions))
    history = ProductBriefVersionListResponseV1(
        items=tuple(_summary_response(detail) for detail in details),
        next_cursor=None,
    )
    history_body = history.model_dump_json().encode()

    assert len(history_body) < _BFF_RESPONSE_LIMIT_BYTES
    assert all("fields" not in item for item in history.model_dump(mode="json")["items"])
    assert len(history.items) == 3
    assert all(len(detail.model_dump_json().encode()) > 19_000 for detail in details)
    assert all(len(detail.fields) == 22 for detail in details)


def test_three_public_full_detail_contract_versions_exceed_the_bff_limit() -> None:
    details = tuple(
        _public_contract_maximum_detail(version_number) for version_number in range(1, 4)
    )
    legacy_history_body = json.dumps(
        {
            "items": [detail.model_dump(mode="json") for detail in details],
            "next_cursor": None,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    summary_history = ProductBriefVersionListResponseV1(
        items=tuple(_summary_response(detail) for detail in details),
        next_cursor=None,
    )

    assert len(legacy_history_body) > _BFF_RESPONSE_LIMIT_BYTES
    assert len(summary_history.model_dump_json().encode()) < _BFF_RESPONSE_LIMIT_BYTES
    assert all("fields" not in item for item in summary_history.model_dump(mode="json")["items"])


def test_maximum_summary_page_remains_below_the_bff_limit() -> None:
    changed_paths = tuple(
        f"path-{index:02d}-" + ("x" * (160 - len(f"path-{index:02d}-"))) for index in range(64)
    )
    history = ProductBriefVersionListResponseV1(
        items=tuple(
            ProductBriefVersionSummaryResponseV1(
                id=f"{version_number:036d}",
                product_brief_id=_PRODUCT_BRIEF_ID,
                version_number=version_number,
                supersedes_version_id=(
                    None if version_number == 1 else f"{version_number - 1:036d}"
                ),
                effective_state=ProductBriefState.ARCHIVED,
                category=ProductBriefCategory.BEAUTY,
                common_schema_version="c" * 64,
                category_schema_version="b" * 64,
                payload_sha256="a" * 64,
                changed_field_paths=changed_paths,
                confirmation_required=True,
                unresolved_field_count=64,
                review_policy_version="r" * 64,
                source=ProductBriefVersionSource.HUMAN,
                prompt_version=None,
                provider_call=None,
                actor_id="a" * 128,
                revision_reason="r" * 512,
                retention_class=RetentionClass.TASK,
                retention_deadline=_NOW + timedelta(hours=72),
                created_at=_NOW + timedelta(seconds=version_number),
            )
            for version_number in range(100, 0, -1)
        ),
        next_cursor=1,
    )

    assert len(history.items) == 100
    assert len(history.model_dump_json().encode()) < _BFF_RESPONSE_LIMIT_BYTES

    with pytest.raises(ValidationError, match="at most 100 items"):
        ProductBriefVersionListResponseV1(
            items=(*history.items, history.items[-1]),
            next_cursor=1,
        )
