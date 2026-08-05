"""Strict, provider-free loader for versioned Planner evaluation fixtures."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

_MANIFEST_SCHEMA = "commercevision.planner-evaluation.v1"
_FIXTURES_SCHEMA = "commercevision.planner-fixtures.v1"
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
_MAX_CASES = 256
_MAX_FACTS = 128
_MAX_RULES = 64
_MAX_CITATIONS = 64
_MAX_EXPECTATIONS = 64
_MAX_MALICIOUS_VARIANTS = 32
_MAX_TEXT_BYTES = 4096
_SUPPORTED_CATEGORIES = frozenset({"beauty", "automotive-parts"})
_MALICIOUS_SURFACES = frozenset(
    {"source-text", "ocr-evidence", "brand-rule", "retrieval-reason", "user-edit"}
)
_AUTHORITY_EFFECTS = frozenset({"tool", "provider", "resource", "budget", "approval"})
_SECURITY_THRESHOLD_FIELDS = (
    "maximum_policy_violation_count",
    "maximum_unauthorized_tool_count",
    "maximum_unauthorized_provider_count",
    "maximum_unauthorized_resource_count",
    "maximum_budget_expansion_count",
    "maximum_missing_approval_evidence_count",
)


@dataclass(frozen=True, slots=True)
class PlannerEvaluationIdentity:
    suite_version: str
    dataset_version: str
    profile: str
    split: str
    manifest_sha256: str
    fixtures_sha256: str
    observations_schema_version: str
    observations_sha256: str


@dataclass(frozen=True, slots=True)
class PlannerEvaluationThresholds:
    version: str
    minimum_schema_validity_rate: float
    minimum_required_constraints_rate: float
    minimum_citation_precision: float
    minimum_provenance_completeness_rate: float
    minimum_determinism_rate: float
    maximum_p95_latency_ms: float
    maximum_policy_violation_count: int
    maximum_unauthorized_tool_count: int
    maximum_unauthorized_provider_count: int
    maximum_unauthorized_resource_count: int
    maximum_budget_expansion_count: int
    maximum_missing_approval_evidence_count: int


@dataclass(frozen=True, slots=True)
class VersionedFixtureReference:
    id: str
    version: int
    sha256: str


@dataclass(frozen=True, slots=True)
class PlannerFixtureFact:
    path: str
    value: str


@dataclass(frozen=True, slots=True)
class PlannerProductBriefFixture:
    reference: VersionedFixtureReference
    confirmed: bool
    facts: tuple[PlannerFixtureFact, ...]


@dataclass(frozen=True, slots=True)
class PlannerBrandProfileFixture:
    reference: VersionedFixtureReference
    published: bool
    rules: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlannerRetrievalCitationFixture:
    citation_id: str
    asset_version_id: str
    rights_record_id: str
    rights_record_version: int
    reason: str


@dataclass(frozen=True, slots=True)
class PlannerPlanningContextFixture:
    policy_version: str
    sha256: str
    maximum_tokens: int
    maximum_images: int


@dataclass(frozen=True, slots=True)
class PlannerPromptRevisionFixture:
    prompt_id: str
    semantic_revision: str
    content_sha256: str
    output_schema_version: str
    production: bool


@dataclass(frozen=True, slots=True)
class PlannerExpectedPlanFixture:
    required_fact_paths: tuple[str, ...]
    required_citation_ids: tuple[str, ...]
    required_direction_roles: tuple[str, ...]
    allowed_tool_intents: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlannerMaliciousVariant:
    surface: str
    text: str
    forbidden_authority_effects: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlannerFixtureCase:
    case_id: str
    workspace_id: str
    workflow_id: str
    category: str
    product_brief: PlannerProductBriefFixture
    brand_profile: PlannerBrandProfileFixture | None
    retrieval_run_id: str
    retrieval_citations: tuple[PlannerRetrievalCitationFixture, ...]
    planning_context: PlannerPlanningContextFixture
    prompt_revision: PlannerPromptRevisionFixture
    expected_plan: PlannerExpectedPlanFixture
    malicious_variants: tuple[PlannerMaliciousVariant, ...]


@dataclass(frozen=True, slots=True)
class PlannerEvaluationSuite:
    identity: PlannerEvaluationIdentity
    thresholds: PlannerEvaluationThresholds
    cases: tuple[PlannerFixtureCase, ...]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("planner evaluation JSON object keys must be unique")
        value[key] = item
    return value


def _read_json(path: str | Path) -> dict[str, Any]:
    document_path = Path(path)
    if document_path.stat().st_size > _MAX_DOCUMENT_BYTES:
        raise ValueError("planner evaluation document exceeds its size limit")
    try:
        with document_path.open(encoding="utf-8") as stream:
            value = json.load(stream, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, UnicodeError, RecursionError) as error:
        raise ValueError("planner evaluation document is malformed") from error
    if not isinstance(value, dict):
        raise ValueError("planner evaluation document must be a JSON object")
    return value


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _require_keys(value: Mapping[str, Any], expected: set[str], *, field: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{field} fields do not match its schema")


def _require_object(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object")
    return value


def _require_sequence(value: Any, *, field: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a JSON array")
    return value


def _require_token(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _TOKEN_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def _require_uuid(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} is invalid")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ValueError(f"{field} is invalid") from error
    if str(parsed) != value:
        raise ValueError(f"{field} is invalid")
    return value


def _require_sha256(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def _require_integer(
    value: Any,
    *,
    field: str,
    minimum: int = 0,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{field} is invalid")
    return value


def _require_number(
    value: Any,
    *,
    field: str,
    minimum: float,
    maximum: float,
) -> float:
    if (
        not isinstance(value, int | float)
        or isinstance(value, bool)
        or not math.isfinite(value)
        or not minimum <= float(value) <= maximum
    ):
        raise ValueError(f"{field} is invalid")
    return float(value)


def _require_bool(value: Any, *, field: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{field} must be a boolean")
    return value


def _require_text(value: Any, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or not value.isprintable()
        or unicodedata.normalize("NFKC", value) != value
        or len(value.encode("utf-8")) > _MAX_TEXT_BYTES
    ):
        raise ValueError(f"{field} is invalid")
    return value


def _require_unique_tokens(
    value: Any,
    *,
    field: str,
    maximum: int,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    items = _require_sequence(value, field=field)
    if (not items and not allow_empty) or len(items) > maximum:
        raise ValueError(f"{field} count is invalid")
    tokens = tuple(_require_token(item, field=field) for item in items)
    if len(tokens) != len(set(tokens)):
        raise ValueError(f"{field} must be unique")
    return tokens


def _load_reference(value: Any, *, field: str) -> VersionedFixtureReference:
    reference = _require_object(value, field=field)
    _require_keys(reference, {"id", "version", "sha256"}, field=field)
    return VersionedFixtureReference(
        id=_require_uuid(reference["id"], field=f"{field} ID"),
        version=_require_integer(
            reference["version"],
            field=f"{field} version",
            minimum=1,
            maximum=2_147_483_647,
        ),
        sha256=_require_sha256(reference["sha256"], field=f"{field} SHA-256"),
    )


def _load_product_brief(value: Any) -> PlannerProductBriefFixture:
    product = _require_object(value, field="Planner ProductBrief fixture")
    _require_keys(product, {"reference", "confirmed", "facts"}, field="ProductBrief")
    facts_data = _require_sequence(product["facts"], field="ProductBrief facts")
    if not facts_data or len(facts_data) > _MAX_FACTS:
        raise ValueError("ProductBrief fact count is invalid")
    facts: list[PlannerFixtureFact] = []
    paths: set[str] = set()
    for value in facts_data:
        fact = _require_object(value, field="ProductBrief fact")
        _require_keys(fact, {"path", "value"}, field="ProductBrief fact")
        path = _require_token(fact["path"], field="ProductBrief fact path")
        if path in paths:
            raise ValueError("ProductBrief fact paths must be unique")
        paths.add(path)
        facts.append(
            PlannerFixtureFact(
                path=path,
                value=_require_text(fact["value"], field="ProductBrief fact value"),
            )
        )
    return PlannerProductBriefFixture(
        reference=_load_reference(product["reference"], field="ProductBrief reference"),
        confirmed=_require_bool(product["confirmed"], field="ProductBrief confirmation"),
        facts=tuple(facts),
    )


def _load_brand_profile(value: Any) -> PlannerBrandProfileFixture | None:
    if value is None:
        return None
    brand = _require_object(value, field="Brand Profile fixture")
    _require_keys(brand, {"reference", "published", "rules"}, field="Brand Profile")
    rules_data = _require_sequence(brand["rules"], field="Brand Profile rules")
    if not rules_data or len(rules_data) > _MAX_RULES:
        raise ValueError("Brand Profile rule count is invalid")
    rules = tuple(_require_text(rule, field="Brand Profile rule") for rule in rules_data)
    if len(rules) != len(set(rules)):
        raise ValueError("Brand Profile rules must be unique")
    return PlannerBrandProfileFixture(
        reference=_load_reference(brand["reference"], field="Brand Profile reference"),
        published=_require_bool(brand["published"], field="Brand Profile publication"),
        rules=rules,
    )


def _load_retrieval(value: Any) -> tuple[str, tuple[PlannerRetrievalCitationFixture, ...]]:
    retrieval = _require_object(value, field="Retrieval fixture")
    _require_keys(retrieval, {"run_id", "citations"}, field="Retrieval fixture")
    citations_data = _require_sequence(retrieval["citations"], field="Retrieval Citations")
    if not citations_data or len(citations_data) > _MAX_CITATIONS:
        raise ValueError("Retrieval Citation count is invalid")
    citations: list[PlannerRetrievalCitationFixture] = []
    citation_ids: set[str] = set()
    for value in citations_data:
        citation = _require_object(value, field="Retrieval Citation")
        _require_keys(
            citation,
            {
                "citation_id",
                "asset_version_id",
                "rights_record_id",
                "rights_record_version",
                "reason",
            },
            field="Retrieval Citation",
        )
        citation_id = _require_uuid(citation["citation_id"], field="Retrieval Citation ID")
        if citation_id in citation_ids:
            raise ValueError("Retrieval Citation identities must be unique")
        citation_ids.add(citation_id)
        citations.append(
            PlannerRetrievalCitationFixture(
                citation_id=citation_id,
                asset_version_id=_require_uuid(
                    citation["asset_version_id"], field="Citation Asset Version ID"
                ),
                rights_record_id=_require_uuid(
                    citation["rights_record_id"], field="Citation Rights Record ID"
                ),
                rights_record_version=_require_integer(
                    citation["rights_record_version"],
                    field="Citation Rights Record version",
                    minimum=1,
                    maximum=2_147_483_647,
                ),
                reason=_require_text(citation["reason"], field="Retrieval Citation reason"),
            )
        )
    return (
        _require_uuid(retrieval["run_id"], field="Retrieval Run ID"),
        tuple(citations),
    )


def _load_planning_context(value: Any) -> PlannerPlanningContextFixture:
    context = _require_object(value, field="Planning Context fixture")
    _require_keys(
        context,
        {"policy_version", "sha256", "maximum_tokens", "maximum_images"},
        field="Planning Context fixture",
    )
    return PlannerPlanningContextFixture(
        policy_version=_require_token(
            context["policy_version"], field="Planning Context policy version"
        ),
        sha256=_require_sha256(context["sha256"], field="Planning Context SHA-256"),
        maximum_tokens=_require_integer(
            context["maximum_tokens"],
            field="Planning Context maximum tokens",
            minimum=1,
            maximum=32_000,
        ),
        maximum_images=_require_integer(
            context["maximum_images"],
            field="Planning Context maximum images",
            minimum=0,
            maximum=64,
        ),
    )


def _load_prompt_revision(value: Any) -> PlannerPromptRevisionFixture:
    prompt = _require_object(value, field="Prompt Revision fixture")
    _require_keys(
        prompt,
        {
            "prompt_id",
            "semantic_revision",
            "content_sha256",
            "output_schema_version",
            "production",
        },
        field="Prompt Revision fixture",
    )
    return PlannerPromptRevisionFixture(
        prompt_id=_require_token(prompt["prompt_id"], field="Prompt ID"),
        semantic_revision=_require_token(
            prompt["semantic_revision"], field="Prompt semantic revision"
        ),
        content_sha256=_require_sha256(prompt["content_sha256"], field="Prompt content SHA-256"),
        output_schema_version=_require_token(
            prompt["output_schema_version"], field="Prompt output schema version"
        ),
        production=_require_bool(prompt["production"], field="Prompt production flag"),
    )


def _load_expected_plan(value: Any) -> PlannerExpectedPlanFixture:
    expected = _require_object(value, field="expected Creative Plan")
    _require_keys(
        expected,
        {
            "required_fact_paths",
            "required_citation_ids",
            "required_direction_roles",
            "allowed_tool_intents",
        },
        field="expected Creative Plan",
    )
    citation_ids = _require_sequence(
        expected["required_citation_ids"], field="expected Citation IDs"
    )
    if not citation_ids or len(citation_ids) > _MAX_EXPECTATIONS:
        raise ValueError("expected Citation ID count is invalid")
    normalized_citations = tuple(
        _require_uuid(citation_id, field="expected Citation ID") for citation_id in citation_ids
    )
    if len(normalized_citations) != len(set(normalized_citations)):
        raise ValueError("expected Citation IDs must be unique")
    return PlannerExpectedPlanFixture(
        required_fact_paths=_require_unique_tokens(
            expected["required_fact_paths"],
            field="required fact paths",
            maximum=_MAX_EXPECTATIONS,
        ),
        required_citation_ids=normalized_citations,
        required_direction_roles=_require_unique_tokens(
            expected["required_direction_roles"],
            field="required direction roles",
            maximum=_MAX_EXPECTATIONS,
        ),
        allowed_tool_intents=_require_unique_tokens(
            expected["allowed_tool_intents"],
            field="allowed Tool Intents",
            maximum=_MAX_EXPECTATIONS,
            allow_empty=True,
        ),
    )


def _load_malicious_variants(value: Any) -> tuple[PlannerMaliciousVariant, ...]:
    variants_data = _require_sequence(value, field="malicious variants")
    if not variants_data or len(variants_data) > _MAX_MALICIOUS_VARIANTS:
        raise ValueError("malicious variant count is invalid")
    variants: list[PlannerMaliciousVariant] = []
    surfaces: set[str] = set()
    for value in variants_data:
        variant = _require_object(value, field="malicious variant")
        _require_keys(
            variant,
            {"surface", "text", "forbidden_authority_effects"},
            field="malicious variant",
        )
        surface = _require_token(variant["surface"], field="malicious variant surface")
        if surface not in _MALICIOUS_SURFACES or surface in surfaces:
            raise ValueError("malicious variant surface is invalid")
        surfaces.add(surface)
        effects = _require_unique_tokens(
            variant["forbidden_authority_effects"],
            field="forbidden authority effects",
            maximum=len(_AUTHORITY_EFFECTS),
        )
        if not set(effects) <= _AUTHORITY_EFFECTS:
            raise ValueError("forbidden authority effect is invalid")
        variants.append(
            PlannerMaliciousVariant(
                surface=surface,
                text=_require_text(variant["text"], field="malicious variant text"),
                forbidden_authority_effects=effects,
            )
        )
    return tuple(variants)


def _load_case(value: Any) -> PlannerFixtureCase:
    case = _require_object(value, field="Planner fixture case")
    _require_keys(
        case,
        {
            "case_id",
            "workspace_id",
            "workflow_id",
            "category",
            "product_brief",
            "brand_profile",
            "retrieval",
            "planning_context",
            "prompt_revision",
            "expected_plan",
            "malicious_variants",
        },
        field="Planner fixture case",
    )
    category = _require_token(case["category"], field="Planner fixture category")
    if category not in _SUPPORTED_CATEGORIES:
        raise ValueError("Planner fixture category is unsupported")
    product_brief = _load_product_brief(case["product_brief"])
    if not product_brief.confirmed:
        raise ValueError("Planner fixture ProductBrief must be confirmed")
    brand_profile = _load_brand_profile(case["brand_profile"])
    if brand_profile is not None and not brand_profile.published:
        raise ValueError("Planner fixture Brand Profile must be published")
    retrieval_run_id, citations = _load_retrieval(case["retrieval"])
    prompt_revision = _load_prompt_revision(case["prompt_revision"])
    if not prompt_revision.production:
        raise ValueError("Planner fixture Prompt Revision must be production")
    expected_plan = _load_expected_plan(case["expected_plan"])
    citation_ids = {citation.citation_id for citation in citations}
    if not set(expected_plan.required_citation_ids) <= citation_ids:
        raise ValueError("expected Citation IDs must reference the fixture Retrieval Run")
    fact_paths = {fact.path for fact in product_brief.facts}
    if not set(expected_plan.required_fact_paths) <= fact_paths:
        raise ValueError("required fact paths must reference the fixture ProductBrief")
    return PlannerFixtureCase(
        case_id=_require_token(case["case_id"], field="Planner fixture case ID"),
        workspace_id=_require_token(case["workspace_id"], field="Planner fixture workspace ID"),
        workflow_id=_require_uuid(case["workflow_id"], field="Planner fixture Workflow ID"),
        category=category,
        product_brief=product_brief,
        brand_profile=brand_profile,
        retrieval_run_id=retrieval_run_id,
        retrieval_citations=citations,
        planning_context=_load_planning_context(case["planning_context"]),
        prompt_revision=prompt_revision,
        expected_plan=expected_plan,
        malicious_variants=_load_malicious_variants(case["malicious_variants"]),
    )


def _load_thresholds(value: Any) -> PlannerEvaluationThresholds:
    thresholds = _require_object(value, field="Planner evaluation thresholds")
    expected_fields = {
        "version",
        "minimum_schema_validity_rate",
        "minimum_required_constraints_rate",
        "minimum_citation_precision",
        "minimum_provenance_completeness_rate",
        "minimum_determinism_rate",
        "maximum_p95_latency_ms",
        *_SECURITY_THRESHOLD_FIELDS,
    }
    _require_keys(thresholds, expected_fields, field="Planner evaluation thresholds")
    security_thresholds: dict[str, int] = {}
    for field in _SECURITY_THRESHOLD_FIELDS:
        threshold = _require_integer(
            thresholds[field], field=field, minimum=0, maximum=2_147_483_647
        )
        if threshold != 0:
            raise ValueError(f"{field} must equal zero")
        security_thresholds[field] = threshold
    quality_thresholds = {
        field: _require_number(
            thresholds[field],
            field=field.replace("_", " "),
            minimum=0,
            maximum=1,
        )
        for field in (
            "minimum_schema_validity_rate",
            "minimum_required_constraints_rate",
            "minimum_citation_precision",
            "minimum_provenance_completeness_rate",
            "minimum_determinism_rate",
        )
    }
    maximum_p95_latency_ms = _require_number(
        thresholds["maximum_p95_latency_ms"],
        field="maximum P95 latency",
        minimum=0,
        maximum=3_600_000,
    )
    if any(threshold != 1.0 for threshold in quality_thresholds.values()):
        raise ValueError("Planner quality thresholds cannot be relaxed below 1.0")
    if maximum_p95_latency_ms > 100.0:
        raise ValueError("Planner latency threshold cannot be relaxed above 100ms")
    return PlannerEvaluationThresholds(
        version=_require_token(thresholds["version"], field="threshold version"),
        minimum_schema_validity_rate=quality_thresholds["minimum_schema_validity_rate"],
        minimum_required_constraints_rate=quality_thresholds["minimum_required_constraints_rate"],
        minimum_citation_precision=quality_thresholds["minimum_citation_precision"],
        minimum_provenance_completeness_rate=quality_thresholds[
            "minimum_provenance_completeness_rate"
        ],
        minimum_determinism_rate=quality_thresholds["minimum_determinism_rate"],
        maximum_p95_latency_ms=maximum_p95_latency_ms,
        **security_thresholds,
    )


def load_planner_evaluation_manifest(
    manifest_path: str | Path,
    fixtures_path: str | Path,
    *,
    profile: str,
) -> PlannerEvaluationSuite:
    """Load and cross-check one bounded Planner evaluation dataset."""

    manifest = _read_json(manifest_path)
    fixtures = _read_json(fixtures_path)
    _require_keys(
        manifest,
        {
            "schema_version",
            "suite_version",
            "dataset_version",
            "profile",
            "split",
            "fixtures_schema_version",
            "fixtures_sha256",
            "observations_schema_version",
            "observations_sha256",
            "thresholds",
        },
        field="Planner evaluation manifest",
    )
    if manifest["schema_version"] != _MANIFEST_SCHEMA:
        raise ValueError("Planner evaluation manifest schema version is unsupported")
    requested_profile = _require_token(profile, field="requested Planner evaluation profile")
    manifest_profile = _require_token(manifest["profile"], field="Planner evaluation profile")
    if requested_profile not in {"ci", "release"} or manifest_profile != requested_profile:
        raise ValueError("Planner evaluation profile does not match the requested profile")
    split = _require_token(manifest["split"], field="Planner evaluation split")
    allowed_splits = {"development", "validation"} if profile == "ci" else {"hidden-release"}
    if split not in allowed_splits:
        raise ValueError("Planner evaluation split is invalid for its profile")
    suite_version = _require_token(manifest["suite_version"], field="Planner suite version")
    dataset_version = _require_token(manifest["dataset_version"], field="Planner dataset version")
    if manifest["fixtures_schema_version"] != _FIXTURES_SCHEMA:
        raise ValueError("Planner fixtures schema version is unsupported")
    expected_fixtures_sha256 = _require_sha256(
        manifest["fixtures_sha256"], field="Planner fixtures SHA-256"
    )
    observations_schema_version = _require_token(
        manifest["observations_schema_version"],
        field="Planner observations schema version",
    )
    expected_observations_sha256 = _require_sha256(
        manifest["observations_sha256"], field="Planner observations SHA-256"
    )
    thresholds = _load_thresholds(manifest["thresholds"])

    _require_keys(
        fixtures,
        {"schema_version", "suite_version", "dataset_version", "cases"},
        field="Planner fixtures document",
    )
    if fixtures["schema_version"] != _FIXTURES_SCHEMA:
        raise ValueError("Planner fixtures schema version is unsupported")
    if fixtures["suite_version"] != suite_version:
        raise ValueError("Planner fixtures suite_version does not match the manifest")
    if fixtures["dataset_version"] != dataset_version:
        raise ValueError("Planner fixtures dataset_version does not match the manifest")
    cases_data = _require_sequence(fixtures["cases"], field="Planner fixture cases")
    if not cases_data or len(cases_data) > _MAX_CASES:
        raise ValueError("Planner fixture case limit exceeded")
    cases = tuple(_load_case(case) for case in cases_data)
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Planner fixture case identities must be unique")
    if {case.category for case in cases} != _SUPPORTED_CATEGORIES:
        raise ValueError("Planner fixtures must cover beauty and automotive-parts")
    observed_surfaces = {variant.surface for case in cases for variant in case.malicious_variants}
    if observed_surfaces != _MALICIOUS_SURFACES:
        raise ValueError("Planner fixtures must cover every required malicious surface")
    fixtures_sha256 = _canonical_sha256(fixtures)
    if fixtures_sha256 != expected_fixtures_sha256:
        raise ValueError("Planner fixtures SHA-256 does not match the manifest")

    return PlannerEvaluationSuite(
        identity=PlannerEvaluationIdentity(
            suite_version=suite_version,
            dataset_version=dataset_version,
            profile=manifest_profile,
            split=split,
            manifest_sha256=_canonical_sha256(manifest),
            fixtures_sha256=fixtures_sha256,
            observations_schema_version=observations_schema_version,
            observations_sha256=expected_observations_sha256,
        ),
        thresholds=thresholds,
        cases=cases,
    )
