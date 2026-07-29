"""Immutable ProductBrief versions and mutable version-selection head."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from commercevision_domain.assets import RetentionClass
from commercevision_domain.ids import new_uuid7
from commercevision_domain.workflow.errors import ConcurrencyError, InvalidTransitionError
from commercevision_domain.workspace_identity import validate_workspace_id

from .enums import (
    ProductBriefCategory,
    ProductBriefEvidenceKind,
    ProductBriefFieldConflict,
    ProductBriefFieldSource,
    ProductBriefState,
    ProductBriefVersionSource,
)
from .schemas import (
    assert_product_brief_schema,
    product_brief_field_paths,
    validate_product_brief_field_value,
)

_EVIDENCE_REFERENCE_PREFIXES = {
    ProductBriefEvidenceKind.IMAGE_REGION: "asset-region://",
    ProductBriefEvidenceKind.VISIBLE_TEXT: "asset-text://",
    ProductBriefEvidenceKind.PRODUCT_DATA: "product-data://",
    ProductBriefEvidenceKind.HUMAN_NOTE: "human-note://",
}


def _require_utc(value: datetime | None, field_name: str) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() != timedelta(0)):
        raise ValueError(f"{field_name} must be timezone-aware UTC")


def validate_product_brief_evidence_reference(
    kind: ProductBriefEvidenceKind | str,
    reference: str,
) -> str:
    selected_kind = ProductBriefEvidenceKind(kind)
    if not any(reference.startswith(prefix) for prefix in _EVIDENCE_REFERENCE_PREFIXES.values()):
        raise ValueError("ProductBrief evidence requires a controlled internal reference")
    expected_prefix = _EVIDENCE_REFERENCE_PREFIXES[selected_kind]
    if not reference.startswith(expected_prefix):
        raise ValueError("ProductBrief evidence reference does not match its kind")
    suffix = reference.removeprefix(expected_prefix)
    if len(suffix) != 64 or any(character not in "0123456789abcdef" for character in suffix):
        raise ValueError("ProductBrief evidence reference is invalid")
    return reference


def _canonical_json(value: object) -> str:
    stack: list[tuple[object, int]] = [(value, 0)]
    visited = 0
    while stack:
        current, depth = stack.pop()
        visited += 1
        if visited > 512:
            raise ValueError("ProductBrief field value exceeds the node limit")
        if depth > 12:
            raise ValueError("ProductBrief field value exceeds the depth limit")
        if isinstance(current, str) and len(current) > 4096:
            raise ValueError("ProductBrief field string exceeds the length limit")
        if isinstance(current, dict):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, (list, tuple)):
            stack.extend((item, depth + 1) for item in current)
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (RecursionError, TypeError, ValueError) as exc:
        raise ValueError("ProductBrief values must be finite JSON values") from exc
    if len(serialized.encode()) > 16 * 1024:
        raise ValueError("ProductBrief field value exceeds the byte limit")
    return serialized


def _contains_claim(value: object) -> bool:
    stack = [value]
    while stack:
        current = stack.pop()
        if current is None or current is False:
            continue
        if isinstance(current, str):
            if current.strip():
                return True
            continue
        if isinstance(current, (int, float, Decimal)) and not isinstance(current, bool):
            if current != 0:
                return True
            continue
        if isinstance(current, dict):
            stack.extend(item for key, item in current.items() if key != "kind")
            continue
        if isinstance(current, (list, tuple)):
            stack.extend(current)
            continue
        if current:
            return True
    return False


def _confidence(value: Decimal | str | int | float) -> Decimal:
    try:
        normalized = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("ProductBrief confidence must be numeric") from exc
    if not Decimal("0") <= normalized <= Decimal("1"):
        raise ValueError("ProductBrief confidence must be between 0 and 1")
    return normalized.quantize(Decimal("0.0001"))


@dataclass(frozen=True, slots=True)
class ProductBriefEvidence:
    id: str
    source_asset_version_id: str
    kind: ProductBriefEvidenceKind
    reference: str
    region: tuple[float, float, float, float] | None
    excerpt_sha256: str | None

    def __post_init__(self) -> None:
        if not self.source_asset_version_id:
            raise ValueError("ProductBrief evidence requires a source Asset Version")
        if not 1 <= len(self.reference) <= 512:
            raise ValueError("ProductBrief evidence reference is invalid")
        object.__setattr__(self, "kind", ProductBriefEvidenceKind(self.kind))
        validate_product_brief_evidence_reference(self.kind, self.reference)
        if self.region is not None:
            if len(self.region) != 4 or any(
                isinstance(value, bool) or not 0 <= value <= 1 for value in self.region
            ):
                raise ValueError("ProductBrief evidence region must contain normalized bounds")
            left, top, right, bottom = self.region
            if left >= right or top >= bottom:
                raise ValueError("ProductBrief evidence region bounds are invalid")
        if self.excerpt_sha256 is not None and (
            len(self.excerpt_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.excerpt_sha256)
        ):
            raise ValueError("ProductBrief evidence excerpt hash must be lowercase SHA-256")

    @classmethod
    def create(
        cls,
        *,
        source_asset_version_id: str,
        kind: ProductBriefEvidenceKind | str,
        reference: str,
        region: tuple[float, float, float, float] | None = None,
        excerpt_sha256: str | None = None,
    ) -> ProductBriefEvidence:
        return cls(
            id=new_uuid7(),
            source_asset_version_id=source_asset_version_id,
            kind=ProductBriefEvidenceKind(kind),
            reference=reference,
            region=region,
            excerpt_sha256=excerpt_sha256,
        )


@dataclass(frozen=True, slots=True)
class ProductBriefField:
    id: str
    path: str
    value: Any
    confidence: Decimal
    source: ProductBriefFieldSource
    conflict: ProductBriefFieldConflict
    review_required: bool
    sensitive: bool
    evidence: tuple[ProductBriefEvidence, ...]

    def __post_init__(self) -> None:
        if not 1 <= len(self.path) <= 160:
            raise ValueError("ProductBrief field path is invalid")
        _canonical_json(self.value)
        validate_product_brief_field_value(self.path, self.value)
        object.__setattr__(self, "confidence", _confidence(self.confidence))
        object.__setattr__(self, "source", ProductBriefFieldSource(self.source))
        object.__setattr__(self, "conflict", ProductBriefFieldConflict(self.conflict))
        if not self.evidence:
            raise ValueError("each ProductBrief field requires at least one evidence record")
        if len(self.evidence) > 32:
            raise ValueError("ProductBrief field evidence exceeds the bounded maximum")

    @classmethod
    def create(
        cls,
        *,
        path: str,
        value: Any,
        confidence: Decimal | str | int | float,
        source: ProductBriefFieldSource | str,
        conflict: ProductBriefFieldConflict | str,
        review_required: bool,
        sensitive: bool,
        evidence: tuple[ProductBriefEvidence, ...],
    ) -> ProductBriefField:
        return cls(
            id=new_uuid7(),
            path=path,
            value=value,
            confidence=_confidence(confidence),
            source=ProductBriefFieldSource(source),
            conflict=ProductBriefFieldConflict(conflict),
            review_required=review_required,
            sensitive=sensitive,
            evidence=evidence,
        )


@dataclass(frozen=True, slots=True)
class ProductBriefReviewDecision:
    policy_version: str
    confidence_threshold: Decimal
    confirmation_required: bool
    unresolved_field_count: int
    reasons_by_path: dict[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class ProductBriefReviewPolicy:
    policy_version: str
    confidence_threshold: Decimal | str | int | float
    mandatory_review_paths: frozenset[str] = frozenset()
    sensitive_claim_paths: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not 1 <= len(self.policy_version) <= 64:
            raise ValueError("ProductBrief policy version is invalid")
        object.__setattr__(
            self,
            "confidence_threshold",
            _confidence(self.confidence_threshold),
        )
        known_paths = set(product_brief_field_paths(ProductBriefCategory.BEAUTY)) | set(
            product_brief_field_paths(ProductBriefCategory.AUTOMOTIVE)
        )
        mandatory_paths = frozenset(self.mandatory_review_paths)
        sensitive_paths = frozenset(self.sensitive_claim_paths)
        unknown_paths = (mandatory_paths | sensitive_paths) - known_paths
        if unknown_paths:
            raise ValueError(
                "ProductBrief review policy contains unknown fields: "
                + ", ".join(sorted(unknown_paths))
            )
        object.__setattr__(self, "mandatory_review_paths", mandatory_paths)
        object.__setattr__(self, "sensitive_claim_paths", sensitive_paths)

    @property
    def snapshot_sha256(self) -> str:
        return hashlib.sha256(
            _canonical_json(
                {
                    "confidence_threshold": str(self.confidence_threshold),
                    "mandatory_review_paths": sorted(self.mandatory_review_paths),
                    "policy_version": self.policy_version,
                    "sensitive_claim_paths": sorted(self.sensitive_claim_paths),
                }
            ).encode()
        ).hexdigest()

    def enforce_risk_floor(
        self,
        fields: tuple[ProductBriefField, ...],
    ) -> tuple[ProductBriefField, ...]:
        return tuple(
            replace(
                field,
                review_required=(
                    field.review_required or field.path in self.mandatory_review_paths
                ),
                sensitive=(
                    field.sensitive
                    or (field.path in self.sensitive_claim_paths and _contains_claim(field.value))
                ),
            )
            for field in fields
        )

    def evaluate(
        self,
        fields: tuple[ProductBriefField, ...],
    ) -> ProductBriefReviewDecision:
        confidence_threshold = _confidence(self.confidence_threshold)
        reasons: dict[str, tuple[str, ...]] = {}
        for field in fields:
            field_reasons: list[str] = []
            if field.confidence < confidence_threshold:
                field_reasons.append("LOW_CONFIDENCE")
            if field.conflict == ProductBriefFieldConflict.CONFLICTING:
                field_reasons.append("SOURCE_CONFLICT")
            if field.sensitive:
                field_reasons.append("SENSITIVE_CLAIM")
            if field.path in self.mandatory_review_paths:
                field_reasons.append("MANDATORY_REVIEW")
            elif field.review_required:
                field_reasons.append("PROVIDER_REVIEW")
            if field_reasons:
                reasons[field.path] = tuple(field_reasons)
        return ProductBriefReviewDecision(
            policy_version=self.policy_version,
            confidence_threshold=confidence_threshold,
            confirmation_required=bool(reasons),
            unresolved_field_count=len(reasons),
            reasons_by_path=reasons,
        )


def _payload_hash(
    *,
    category: ProductBriefCategory,
    common_schema_version: str,
    category_schema_version: str,
    fields: tuple[ProductBriefField, ...],
) -> str:
    payload = {
        "category": category.value,
        "category_schema_version": category_schema_version,
        "common_schema_version": common_schema_version,
        "fields": [
            {
                "confidence": str(field.confidence),
                "conflict": field.conflict.value,
                "evidence": [
                    {
                        "excerpt_sha256": evidence.excerpt_sha256,
                        "kind": evidence.kind.value,
                        "reference": evidence.reference,
                        "region": evidence.region,
                        "source_asset_version_id": evidence.source_asset_version_id,
                    }
                    for evidence in sorted(
                        field.evidence,
                        key=lambda item: (
                            item.source_asset_version_id,
                            item.kind.value,
                            item.reference,
                            item.region or (),
                            item.excerpt_sha256 or "",
                        ),
                    )
                ],
                "path": field.path,
                "review_required": field.review_required,
                "sensitive": field.sensitive,
                "source": field.source.value,
                "value": field.value,
            }
            for field in sorted(fields, key=lambda item: item.path)
        ],
    }
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ProductBriefVersion:
    id: str
    workspace_id: str
    product_brief_id: str
    version_number: int
    supersedes_version_id: str | None
    category: ProductBriefCategory
    common_schema_version: str
    category_schema_version: str
    fields: tuple[ProductBriefField, ...]
    changed_field_paths: tuple[str, ...]
    payload_sha256: str
    confirmation_required: bool
    unresolved_field_count: int
    review_policy_version: str
    source: ProductBriefVersionSource
    prompt_version: str | None
    provider_call_id: str | None
    actor_id: str
    revision_reason: str | None
    retention_class: RetentionClass
    retention_deadline: datetime | None
    created_at: datetime

    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)
        if self.version_number < 1:
            raise ValueError("ProductBrief version number must be positive")
        object.__setattr__(self, "category", ProductBriefCategory(self.category))
        object.__setattr__(self, "source", ProductBriefVersionSource(self.source))
        object.__setattr__(self, "retention_class", RetentionClass(self.retention_class))
        _require_utc(self.created_at, "created_at")
        _require_utc(self.retention_deadline, "retention_deadline")
        if self.retention_class == RetentionClass.TASK and self.retention_deadline is None:
            raise ValueError("Task ProductBrief requires a retention deadline")
        if (
            self.retention_class == RetentionClass.FOUNDATION
            and self.retention_deadline is not None
        ):
            raise ValueError("Foundation ProductBrief cannot have a task retention deadline")
        assert_product_brief_schema(
            category=self.category,
            common_schema_version=self.common_schema_version,
            category_schema_version=self.category_schema_version,
            paths=tuple(field.path for field in self.fields),
        )
        field_paths = {field.path for field in self.fields}
        changed_paths = tuple(sorted(self.changed_field_paths))
        if (
            not changed_paths
            or len(changed_paths) != len(set(changed_paths))
            or any(path not in field_paths for path in changed_paths)
        ):
            raise ValueError("ProductBrief changed field paths are invalid")
        object.__setattr__(self, "changed_field_paths", changed_paths)
        if self.source == ProductBriefVersionSource.MODEL:
            if not self.prompt_version or not self.provider_call_id:
                raise ValueError(
                    "model ProductBrief versions require prompt and provider provenance"
                )
            if set(changed_paths) != field_paths:
                raise ValueError("model ProductBrief versions must record every generated field")
        elif not self.revision_reason:
            raise ValueError("human ProductBrief versions require a revision reason")
        elif any(
            field.source != ProductBriefFieldSource.HUMAN
            for field in self.fields
            if field.path in changed_paths
        ):
            raise ValueError("human ProductBrief changes must record HUMAN field source")
        if self.payload_sha256 != _payload_hash(
            category=self.category,
            common_schema_version=self.common_schema_version,
            category_schema_version=self.category_schema_version,
            fields=self.fields,
        ):
            raise ValueError("ProductBrief payload hash does not match immutable fields")

    @classmethod
    def create(
        cls,
        *,
        workspace_id: str,
        product_brief_id: str,
        version_number: int,
        supersedes_version_id: str | None,
        category: ProductBriefCategory | str,
        common_schema_version: str,
        category_schema_version: str,
        fields: tuple[ProductBriefField, ...],
        changed_field_paths: tuple[str, ...] | None = None,
        review_decision: ProductBriefReviewDecision,
        source: ProductBriefVersionSource | str,
        prompt_version: str | None,
        provider_call_id: str | None,
        actor_id: str,
        revision_reason: str | None,
        retention_class: RetentionClass | str,
        retention_deadline: datetime | None,
        now: datetime | None = None,
    ) -> ProductBriefVersion:
        category_value = ProductBriefCategory(category)
        source_value = ProductBriefVersionSource(source)
        return cls(
            id=new_uuid7(),
            workspace_id=workspace_id,
            product_brief_id=product_brief_id,
            version_number=version_number,
            supersedes_version_id=supersedes_version_id,
            category=category_value,
            common_schema_version=common_schema_version,
            category_schema_version=category_schema_version,
            fields=fields,
            changed_field_paths=(
                tuple(field.path for field in fields)
                if source_value == ProductBriefVersionSource.MODEL
                else (changed_field_paths or ())
            ),
            payload_sha256=_payload_hash(
                category=category_value,
                common_schema_version=common_schema_version,
                category_schema_version=category_schema_version,
                fields=fields,
            ),
            confirmation_required=(
                review_decision.confirmation_required
                or source_value == ProductBriefVersionSource.HUMAN
            ),
            unresolved_field_count=review_decision.unresolved_field_count,
            review_policy_version=review_decision.policy_version,
            source=source_value,
            prompt_version=prompt_version,
            provider_call_id=provider_call_id,
            actor_id=actor_id,
            revision_reason=revision_reason,
            retention_class=RetentionClass(retention_class),
            retention_deadline=retention_deadline,
            created_at=now or datetime.now(UTC),
        )


@dataclass(slots=True)
class ProductBrief:
    id: str
    workspace_id: str
    workflow_id: str
    product_id: str
    created_by: str
    state: ProductBriefState
    current_version_id: str | None
    confirmed_version_id: str | None
    version: int
    retention_class: RetentionClass
    retention_deadline: datetime | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)
        self.state = ProductBriefState(self.state)
        self.retention_class = RetentionClass(self.retention_class)
        _require_utc(self.created_at, "created_at")
        _require_utc(self.updated_at, "updated_at")
        _require_utc(self.retention_deadline, "retention_deadline")
        if self.retention_class == RetentionClass.TASK and self.retention_deadline is None:
            raise ValueError("Task ProductBrief requires a retention deadline")
        if (
            self.retention_class == RetentionClass.FOUNDATION
            and self.retention_deadline is not None
        ):
            raise ValueError("Foundation ProductBrief cannot have a task retention deadline")

    @classmethod
    def create(
        cls,
        *,
        workspace_id: str,
        workflow_id: str,
        product_id: str,
        created_by: str,
        retention_class: RetentionClass | str,
        retention_deadline: datetime | None,
        now: datetime | None = None,
    ) -> ProductBrief:
        created_at = now or datetime.now(UTC)
        return cls(
            id=new_uuid7(),
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            product_id=product_id,
            created_by=created_by,
            state=ProductBriefState.DRAFT,
            current_version_id=None,
            confirmed_version_id=None,
            version=1,
            retention_class=RetentionClass(retention_class),
            retention_deadline=retention_deadline,
            created_at=created_at,
            updated_at=created_at,
        )

    def assert_version(self, expected_version: int) -> None:
        if self.version != expected_version:
            raise ConcurrencyError(
                f"ProductBrief {self.id} version is {self.version}, expected {expected_version}"
            )

    def publish_version(
        self,
        product_brief_version: ProductBriefVersion,
        *,
        expected_version: int,
        now: datetime | None = None,
    ) -> None:
        self.assert_version(expected_version)
        if product_brief_version.product_brief_id != self.id:
            raise ValueError("ProductBrief version belongs to another ProductBrief")
        if product_brief_version.workspace_id != self.workspace_id:
            raise ValueError("ProductBrief version belongs to another workspace")
        if self.current_version_id is None:
            if (
                product_brief_version.version_number != 1
                or product_brief_version.supersedes_version_id is not None
            ):
                raise ConcurrencyError("first ProductBrief version must be version 1")
        elif product_brief_version.supersedes_version_id != self.current_version_id:
            raise ConcurrencyError("ProductBrief version does not supersede the current version")
        if (
            product_brief_version.retention_class != self.retention_class
            or product_brief_version.retention_deadline != self.retention_deadline
        ):
            raise ValueError("ProductBrief version must inherit the ProductBrief retention")
        self.current_version_id = product_brief_version.id
        if product_brief_version.confirmation_required:
            self.state = ProductBriefState.AWAITING_CONFIRMATION
        else:
            self.state = ProductBriefState.CONFIRMED
            self.confirmed_version_id = product_brief_version.id
        self.version += 1
        self.updated_at = now or datetime.now(UTC)

    def reopen_for_analysis(
        self,
        *,
        expected_version: int,
        now: datetime | None = None,
    ) -> None:
        self.assert_version(expected_version)
        if (
            self.state != ProductBriefState.CONFIRMED
            or self.current_version_id is None
            or self.current_version_id != self.confirmed_version_id
        ):
            raise InvalidTransitionError(
                "only an exact current confirmed ProductBrief can be reanalyzed"
            )
        self.state = ProductBriefState.DRAFT
        self.version += 1
        self.updated_at = now or datetime.now(UTC)

    def confirm(
        self,
        *,
        product_brief_version_id: str,
        expected_version: int,
        now: datetime | None = None,
    ) -> None:
        self.assert_version(expected_version)
        if product_brief_version_id != self.current_version_id:
            raise ConcurrencyError(
                "confirmation must target the exact current ProductBrief version"
            )
        if self.state == ProductBriefState.CONFIRMED:
            if self.confirmed_version_id == product_brief_version_id:
                return
            raise InvalidTransitionError("ProductBrief confirmation state is inconsistent")
        if self.state != ProductBriefState.AWAITING_CONFIRMATION:
            raise InvalidTransitionError(
                f"ProductBrief cannot be confirmed from {self.state.value}"
            )
        self.confirmed_version_id = product_brief_version_id
        self.state = ProductBriefState.CONFIRMED
        self.version += 1
        self.updated_at = now or datetime.now(UTC)
