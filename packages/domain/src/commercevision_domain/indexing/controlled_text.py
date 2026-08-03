"""Canonical controlled text for PRODUCT_FUSED indexing and lexical search."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from commercevision_domain.ids import canonicalize_uuid

from .enums import VectorKind

_MAXIMUM_ITEM_BYTES = 2048
_MAXIMUM_ITEMS = 64
_MAXIMUM_DOCUMENT_BYTES = 32 * 1024
_SAFE_FIELD_KINDS = {
    "common.identity": "IDENTITY",
    "common.category": "CATEGORY",
    "common.brand": "TEXT",
    "common.product_type": "TEXT",
    "common.package_or_part_form": "TEXT",
    "common.material": "TEXT",
    "common.colors": "TEXT_LIST",
    "common.visible_text_summary": "TEXT",
    "common.visual_features": "TEXT_LIST",
    "common.usage_context": "TEXT",
    "beauty.package_type": "TEXT",
    "beauty.cosmetic_form": "TEXT",
    "beauty.finish": "TEXT",
    "beauty.texture": "TEXT",
    "automotive.part_type": "TEXT",
    "automotive.placement": "TEXT",
    "automotive.material": "TEXT",
    "automotive.finish": "TEXT",
}


def _normalize_text(value: str, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    normalized = unicodedata.normalize("NFKC", value)
    safe = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in normalized
    )
    collapsed = " ".join(safe.split()).casefold()
    if len(collapsed.encode("utf-8")) > _MAXIMUM_ITEM_BYTES:
        raise ValueError(f"{field} exceeds the controlled text byte limit")
    return collapsed


def _normalized_set(values: Iterable[str], *, field: str) -> tuple[str, ...]:
    normalized = {_normalize_text(value, field=field) for value in values}
    normalized.discard("")
    if len(normalized) > _MAXIMUM_ITEMS:
        raise ValueError(f"{field} exceeds the controlled item limit")
    return tuple(sorted(normalized))


def _require_keys(
    value: Mapping[str, Any],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> None:
    if set(value) - required - optional or required - set(value):
        raise ValueError("controlled ProductBrief field has an invalid shape")


def _field_terms(path: str, value: object) -> tuple[str, ...]:
    expected_kind = _SAFE_FIELD_KINDS[path]
    if not isinstance(value, Mapping) or value.get("kind") != expected_kind:
        raise ValueError(f"controlled ProductBrief field {path} has an invalid kind")
    if expected_kind == "TEXT":
        _require_keys(value, required=frozenset({"kind", "text"}))
        normalized = _normalize_text(value["text"], field=path)
        return (normalized,) if normalized else ()
    if expected_kind == "TEXT_LIST":
        _require_keys(value, required=frozenset({"kind", "items"}))
        items = value["items"]
        if not isinstance(items, list):
            raise ValueError(f"controlled ProductBrief field {path} must contain an item list")
        return _normalized_set(items, field=path)
    if expected_kind == "IDENTITY":
        _require_keys(
            value,
            required=frozenset({"kind", "display_name"}),
            optional=frozenset({"model_number", "variant"}),
        )
        return _normalized_set(
            (
                value["display_name"],
                value.get("model_number") or "",
                value.get("variant") or "",
            ),
            field=path,
        )
    if expected_kind == "CATEGORY":
        _require_keys(value, required=frozenset({"kind", "code", "label"}))
        return _normalized_set((value["code"], value["label"]), field=path)
    raise AssertionError(f"unsupported controlled ProductBrief kind: {expected_kind}")


@dataclass(frozen=True, slots=True)
class ControlledProductText:
    """One immutable, safe-to-index text snapshot from approved facts only."""

    confirmed_product_brief_version_id: str
    title: str
    labels: tuple[str, ...]
    ocr_summary: str
    product_brief_summary: str
    notes: tuple[str, ...]
    canonical_text: str
    content_sha256: str


def serialize_controlled_product_sections(
    *,
    title: str,
    labels: Iterable[str],
    ocr_summary: str,
    product_brief_summary: str,
    notes: Iterable[str],
) -> str:
    """Serialize already-controlled sections for durable Provider replay."""
    return json.dumps(
        {
            "labels": tuple(labels),
            "notes": tuple(notes),
            "ocr_summary": ocr_summary,
            "product_brief_summary": product_brief_summary,
            "title": title,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def build_controlled_product_text(
    *,
    confirmed_product_brief_version_id: str,
    confirmed_fields: Mapping[str, object],
    approved_labels: Iterable[str] = (),
    approved_notes: Iterable[str] = (),
) -> ControlledProductText:
    """Build the shared PRODUCT_FUSED/FULLTEXT document from an explicit allowlist."""

    canonicalize_uuid(confirmed_product_brief_version_id)
    identity = confirmed_fields.get("common.identity")
    if not isinstance(identity, Mapping) or identity.get("kind") != "IDENTITY":
        raise ValueError("confirmed ProductBrief identity is required for the controlled title")
    _require_keys(
        identity,
        required=frozenset({"kind", "display_name"}),
        optional=frozenset({"model_number", "variant"}),
    )
    normalized_title = _normalize_text(
        identity["display_name"],
        field="common.identity.display_name",
    )
    if not normalized_title:
        raise ValueError("controlled product title must not be empty")
    if len(normalized_title) > 512:
        raise ValueError("controlled product title exceeds the search document column limit")
    labels = _normalized_set(approved_labels, field="approved_labels")
    notes = _normalized_set(approved_notes, field="approved_notes")
    summary_parts: list[str] = []
    ocr_summary = ""
    for path in sorted(set(confirmed_fields) & set(_SAFE_FIELD_KINDS)):
        terms = _field_terms(path, confirmed_fields[path])
        if path == "common.visible_text_summary":
            ocr_summary = " ".join(terms)
            continue
        if terms:
            summary_parts.append(f"{path}={' | '.join(terms)}")
    product_brief_summary = "\n".join(summary_parts)
    canonical_text = serialize_controlled_product_sections(
        title=normalized_title,
        labels=labels,
        ocr_summary=ocr_summary,
        product_brief_summary=product_brief_summary,
        notes=notes,
    )
    if len(canonical_text.encode("utf-8")) > _MAXIMUM_DOCUMENT_BYTES:
        raise ValueError("controlled product text exceeds the document byte limit")
    return ControlledProductText(
        confirmed_product_brief_version_id=confirmed_product_brief_version_id,
        title=normalized_title,
        labels=labels,
        ocr_summary=ocr_summary,
        product_brief_summary=product_brief_summary,
        notes=notes,
        canonical_text=canonical_text,
        content_sha256=hashlib.sha256(canonical_text.encode("utf-8")).hexdigest(),
    )


def compute_product_fused_input_hash(
    *,
    product_brief_id: str,
    content_sha256: str,
    controlled_text_sha256: str,
    provider: str,
    preprocessing_version: str,
    model_configuration_version: str,
    vector_kind: VectorKind,
) -> str:
    """Bind image bytes, controlled text and every PRODUCT_FUSED transformation input."""

    canonical_product_brief_id = canonicalize_uuid(product_brief_id)
    for value, field in (
        (content_sha256, "content_sha256"),
        (controlled_text_sha256, "controlled_text_sha256"),
    ):
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError(f"{field} must be a lowercase SHA-256")
    if vector_kind is not VectorKind.PRODUCT_FUSED:
        raise ValueError("product fused input hash requires PRODUCT_FUSED vector kind")
    for value, field in (
        (provider, "provider"),
        (preprocessing_version, "preprocessing_version"),
        (model_configuration_version, "model_configuration_version"),
    ):
        if not value or value != value.strip() or "\0" in value:
            raise ValueError(f"{field} must be a non-empty canonical identity")
    canonical = "\0".join(
        (
            vector_kind.value,
            canonical_product_brief_id,
            content_sha256,
            controlled_text_sha256,
            provider,
            preprocessing_version,
            model_configuration_version,
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
