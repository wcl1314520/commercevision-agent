"""Versioned ProductBrief field catalogs."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from .enums import ProductBriefCategory

COMMON_PRODUCT_BRIEF_FIELDS = (
    "common.identity",
    "common.category",
    "common.brand",
    "common.product_type",
    "common.package_or_part_form",
    "common.material",
    "common.colors",
    "common.visible_text_summary",
    "common.visual_features",
    "common.usage_context",
    "common.prohibited_assumptions",
    "common.sensitive_claims",
    "common.source_conflicts",
)

BEAUTY_PRODUCT_BRIEF_FIELDS = (
    "beauty.package_type",
    "beauty.cosmetic_form",
    "beauty.finish",
    "beauty.texture",
    "beauty.shade_evidence",
    "beauty.ingredient_claim_evidence",
    "beauty.skin_hair_claim_flags",
    "beauty.medical_like_claim_flags",
    "beauty.packaging_compliance_notes",
)

AUTOMOTIVE_PRODUCT_BRIEF_FIELDS = (
    "automotive.part_type",
    "automotive.placement",
    "automotive.compatibility_evidence",
    "automotive.material",
    "automotive.finish",
    "automotive.dimensions_evidence",
    "automotive.installation_evidence",
    "automotive.safety_critical_claim_flags",
    "automotive.certification_marks",
)

DEFAULT_PRODUCT_BRIEF_SENSITIVE_CLAIM_PATHS = (
    "common.sensitive_claims",
    "beauty.ingredient_claim_evidence",
    "beauty.skin_hair_claim_flags",
    "beauty.medical_like_claim_flags",
    "automotive.compatibility_evidence",
    "automotive.safety_critical_claim_flags",
    "automotive.certification_marks",
)

COMMON_SCHEMA_VERSION = "product-brief-common-v1"
CATEGORY_SCHEMA_VERSIONS = {
    ProductBriefCategory.BEAUTY: "product-brief-beauty-v1",
    ProductBriefCategory.AUTOMOTIVE: "product-brief-automotive-v1",
}


class ProductBriefFieldValueKind(StrEnum):
    IDENTITY = "IDENTITY"
    CATEGORY = "CATEGORY"
    TEXT = "TEXT"
    TEXT_LIST = "TEXT_LIST"
    STATEMENT_LIST = "STATEMENT_LIST"
    FLAG_LIST = "FLAG_LIST"
    DIMENSION_LIST = "DIMENSION_LIST"


_PRODUCT_BRIEF_FIELD_VALUE_KINDS = {
    "common.identity": ProductBriefFieldValueKind.IDENTITY,
    "common.category": ProductBriefFieldValueKind.CATEGORY,
    "common.brand": ProductBriefFieldValueKind.TEXT,
    "common.product_type": ProductBriefFieldValueKind.TEXT,
    "common.package_or_part_form": ProductBriefFieldValueKind.TEXT,
    "common.material": ProductBriefFieldValueKind.TEXT,
    "common.colors": ProductBriefFieldValueKind.TEXT_LIST,
    "common.visible_text_summary": ProductBriefFieldValueKind.TEXT,
    "common.visual_features": ProductBriefFieldValueKind.TEXT_LIST,
    "common.usage_context": ProductBriefFieldValueKind.TEXT,
    "common.prohibited_assumptions": ProductBriefFieldValueKind.TEXT_LIST,
    "common.sensitive_claims": ProductBriefFieldValueKind.STATEMENT_LIST,
    "common.source_conflicts": ProductBriefFieldValueKind.STATEMENT_LIST,
    "beauty.package_type": ProductBriefFieldValueKind.TEXT,
    "beauty.cosmetic_form": ProductBriefFieldValueKind.TEXT,
    "beauty.finish": ProductBriefFieldValueKind.TEXT,
    "beauty.texture": ProductBriefFieldValueKind.TEXT,
    "beauty.shade_evidence": ProductBriefFieldValueKind.STATEMENT_LIST,
    "beauty.ingredient_claim_evidence": ProductBriefFieldValueKind.STATEMENT_LIST,
    "beauty.skin_hair_claim_flags": ProductBriefFieldValueKind.FLAG_LIST,
    "beauty.medical_like_claim_flags": ProductBriefFieldValueKind.FLAG_LIST,
    "beauty.packaging_compliance_notes": ProductBriefFieldValueKind.TEXT,
    "automotive.part_type": ProductBriefFieldValueKind.TEXT,
    "automotive.placement": ProductBriefFieldValueKind.TEXT,
    "automotive.compatibility_evidence": ProductBriefFieldValueKind.STATEMENT_LIST,
    "automotive.material": ProductBriefFieldValueKind.TEXT,
    "automotive.finish": ProductBriefFieldValueKind.TEXT,
    "automotive.dimensions_evidence": ProductBriefFieldValueKind.DIMENSION_LIST,
    "automotive.installation_evidence": ProductBriefFieldValueKind.STATEMENT_LIST,
    "automotive.safety_critical_claim_flags": ProductBriefFieldValueKind.FLAG_LIST,
    "automotive.certification_marks": ProductBriefFieldValueKind.STATEMENT_LIST,
}

_FIELD_TEXT_MAXIMUM_BYTES = 2048
_FIELD_LIST_MAXIMUM_ITEMS = 32
_DIMENSION_LIST_MAXIMUM_ITEMS = 16


def product_brief_field_value_kind(path: str) -> ProductBriefFieldValueKind:
    try:
        return _PRODUCT_BRIEF_FIELD_VALUE_KINDS[path]
    except KeyError as exc:
        raise ValueError(f"unknown ProductBrief field path: {path}") from exc


def product_brief_field_value_kinds() -> dict[str, str]:
    return {path: kind.value for path, kind in _PRODUCT_BRIEF_FIELD_VALUE_KINDS.items()}


def validate_product_brief_field_value(path: str, value: Any) -> None:
    expected_kind = product_brief_field_value_kind(path)
    if not isinstance(value, dict):
        raise ValueError("ProductBrief field value must be a versioned object")
    if value.get("kind") != expected_kind.value:
        raise ValueError(f"ProductBrief field value for {path} must use kind {expected_kind.value}")

    if expected_kind == ProductBriefFieldValueKind.IDENTITY:
        _validate_object_keys(
            value,
            required={"kind", "display_name"},
            optional={"model_number", "variant"},
        )
        display_name = _validate_text(value["display_name"], allow_empty=True)
        model_number = _validate_optional_text(value.get("model_number"))
        variant = _validate_optional_text(value.get("variant"))
        if not any((display_name, model_number, variant)):
            raise ValueError("ProductBrief field value identity must contain one fact")
        return
    if expected_kind == ProductBriefFieldValueKind.CATEGORY:
        _validate_object_keys(value, required={"kind", "code", "label"})
        _validate_text(value["code"], allow_empty=False)
        _validate_text(value["label"], allow_empty=False)
        return
    if expected_kind == ProductBriefFieldValueKind.TEXT:
        _validate_object_keys(value, required={"kind", "text"})
        _validate_text(value["text"], allow_empty=True)
        return
    if expected_kind == ProductBriefFieldValueKind.TEXT_LIST:
        _validate_object_keys(value, required={"kind", "items"})
        _validate_text_collection(value["items"], field_name="items")
        return
    if expected_kind == ProductBriefFieldValueKind.STATEMENT_LIST:
        _validate_object_keys(value, required={"kind", "statements"})
        _validate_text_collection(value["statements"], field_name="statements")
        return
    if expected_kind == ProductBriefFieldValueKind.FLAG_LIST:
        _validate_object_keys(value, required={"kind", "flags"})
        _validate_text_collection(value["flags"], field_name="flags")
        return
    if expected_kind == ProductBriefFieldValueKind.DIMENSION_LIST:
        _validate_object_keys(value, required={"kind", "dimensions"})
        _validate_dimensions(value["dimensions"])
        return
    raise AssertionError(f"unhandled ProductBrief field value kind: {expected_kind}")


def _validate_object_keys(
    value: dict[str, Any],
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    actual = set(value)
    missing = required - actual
    unknown = actual - required - optional
    if missing or unknown:
        raise ValueError("ProductBrief field value has missing or unknown properties")


def _validate_text(value: Any, *, allow_empty: bool) -> str:
    if not isinstance(value, str):
        raise ValueError("ProductBrief field value text must be a string")
    if not allow_empty and not value:
        raise ValueError("ProductBrief field value text must not be empty")
    if len(value.encode("utf-8")) > _FIELD_TEXT_MAXIMUM_BYTES:
        raise ValueError("ProductBrief field value text exceeds the byte limit")
    return value


def _validate_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return _validate_text(value, allow_empty=True)


def _validate_text_collection(value: Any, *, field_name: str) -> None:
    if not isinstance(value, list):
        raise ValueError(f"ProductBrief field value {field_name} must be an array")
    if len(value) > _FIELD_LIST_MAXIMUM_ITEMS:
        raise ValueError(f"ProductBrief field value {field_name} exceeds the item limit")
    normalized = [_validate_text(item, allow_empty=False) for item in value]
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"ProductBrief field value {field_name} must be unique")


def _validate_dimensions(value: Any) -> None:
    if not isinstance(value, list):
        raise ValueError("ProductBrief field value dimensions must be an array")
    if len(value) > _DIMENSION_LIST_MAXIMUM_ITEMS:
        raise ValueError("ProductBrief field value dimensions exceeds the item limit")
    names: list[str] = []
    for dimension in value:
        if not isinstance(dimension, dict):
            raise ValueError("ProductBrief field value dimension must be an object")
        _validate_object_keys(
            dimension,
            required={"name", "value"},
            optional={"unit", "raw_text"},
        )
        names.append(_validate_text(dimension["name"], allow_empty=False))
        _validate_text(dimension["value"], allow_empty=False)
        _validate_optional_text(dimension.get("unit"))
        _validate_optional_text(dimension.get("raw_text"))
    if len(set(names)) != len(names):
        raise ValueError("ProductBrief field value dimension names must be unique")


def product_brief_field_paths(category: ProductBriefCategory | str) -> tuple[str, ...]:
    selected = ProductBriefCategory(category)
    extension = {
        ProductBriefCategory.BEAUTY: BEAUTY_PRODUCT_BRIEF_FIELDS,
        ProductBriefCategory.AUTOMOTIVE: AUTOMOTIVE_PRODUCT_BRIEF_FIELDS,
    }[selected]
    return (*COMMON_PRODUCT_BRIEF_FIELDS, *extension)


def assert_product_brief_schema(
    *,
    category: ProductBriefCategory,
    common_schema_version: str,
    category_schema_version: str,
    paths: tuple[str, ...],
) -> None:
    if common_schema_version != COMMON_SCHEMA_VERSION:
        raise ValueError(f"unsupported common ProductBrief schema version: {common_schema_version}")
    expected_category_version = CATEGORY_SCHEMA_VERSIONS[category]
    if category_schema_version != expected_category_version:
        raise ValueError(
            f"unsupported {category.value} ProductBrief schema version: {category_schema_version}"
        )
    duplicates = sorted(path for path in set(paths) if paths.count(path) > 1)
    if duplicates:
        raise ValueError("duplicate ProductBrief fields: " + ", ".join(duplicates))
    expected = set(product_brief_field_paths(category))
    actual = set(paths)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        raise ValueError("missing required ProductBrief fields: " + ", ".join(missing))
    if unknown:
        raise ValueError("unknown ProductBrief fields: " + ", ".join(unknown))
