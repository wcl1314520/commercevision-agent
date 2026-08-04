"""Strict loader for versioned retrieval evaluation evidence."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from .models import (
    BootstrapConfig,
    EvaluationIdentity,
    EvaluationQuery,
    EvaluationThresholds,
    RetrievalEvaluationSuite,
    RetrievalObservation,
)

_MANIFEST_SCHEMA = "commercevision.retrieval-evaluation.v1"
_OBSERVATIONS_SCHEMA = "commercevision.retrieval-observations.v1"
_EVALUATION_KS = frozenset({5, 10, 20})
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_MAX_DOCUMENT_BYTES = 64 * 1024 * 1024
_MAX_ASSETS = 10_000
_MAX_QUERIES = 2_000
_MAX_RANKING = 1_000
_MAX_BOOTSTRAP_WORK = 5_000_000


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("evaluation JSON object keys must be unique")
        value[key] = item
    return value


def _read_json(path: str | Path) -> dict[str, Any]:
    document_path = Path(path)
    if document_path.stat().st_size > _MAX_DOCUMENT_BYTES:
        raise ValueError("evaluation document exceeds its size limit")
    with document_path.open(encoding="utf-8") as stream:
        value = json.load(stream, object_pairs_hook=_reject_duplicate_keys)
    if not isinstance(value, dict):
        raise ValueError("evaluation document must be a JSON object")
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


def _require_integer(value: Any, *, field: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
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


def _require_sequence(value: Any, *, field: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a JSON array")
    return value


def _require_metric_map(value: Any, *, field: str, exact_zero: bool = False) -> None:
    if not isinstance(value, dict) or {int(key) for key in value} != _EVALUATION_KS:
        raise ValueError(f"{field} must define K=5/10/20")
    for metric in value.values():
        normalized = _require_number(metric, field=field, minimum=0.0, maximum=1.0)
        if exact_zero and normalized != 0.0:
            raise ValueError(f"{field} unauthorized threshold must equal zero")


def _validate_bootstrap(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("evaluation bootstrap must be an object")
    _require_keys(value, {"samples", "confidence_level", "seed"}, field="bootstrap")
    samples = _require_integer(value["samples"], field="bootstrap samples", minimum=100)
    if samples > 10_000:
        raise ValueError("bootstrap samples is invalid")
    _require_number(
        value["confidence_level"],
        field="bootstrap confidence level",
        minimum=0.80,
        maximum=0.999,
    )
    _require_integer(value["seed"], field="bootstrap seed")


def _validate_thresholds(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("evaluation thresholds must be an object")
    _require_keys(
        value,
        {
            "basis",
            "minimum_recall_at",
            "minimum_precision_at",
            "minimum_mrr",
            "minimum_ndcg_at",
            "minimum_ann_recall_at",
            "maximum_p95_latency_ms",
            "maximum_unauthorized_recall_at",
            "maximum_unauthorized_return_count",
            "maximum_queries_with_unauthorized_results",
        },
        field="evaluation thresholds",
    )
    if value["basis"] not in {"point-estimate", "confidence-bound"}:
        raise ValueError("evaluation threshold basis is invalid")
    for field in (
        "minimum_recall_at",
        "minimum_precision_at",
        "minimum_ndcg_at",
        "minimum_ann_recall_at",
    ):
        _require_metric_map(value[field], field=field)
    _require_number(value["minimum_mrr"], field="minimum MRR", minimum=0, maximum=1)
    _require_number(
        value["maximum_p95_latency_ms"],
        field="maximum P95 latency",
        minimum=0,
        maximum=3_600_000,
    )
    _require_metric_map(
        value["maximum_unauthorized_recall_at"],
        field="maximum unauthorized recall",
        exact_zero=True,
    )
    for field in (
        "maximum_unauthorized_return_count",
        "maximum_queries_with_unauthorized_results",
    ):
        if _require_integer(value[field], field=field) != 0:
            raise ValueError(f"{field} unauthorized threshold must equal zero")


def _validate_assets(value: Any) -> dict[str, dict[str, Any]]:
    assets = _require_sequence(value, field="evaluation assets")
    if not assets:
        raise ValueError("evaluation assets must not be empty")
    if len(assets) > _MAX_ASSETS:
        raise ValueError("evaluation assets exceed their limit")
    asset_by_id: dict[str, dict[str, Any]] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            raise ValueError("evaluation asset must be an object")
        _require_keys(
            asset,
            {
                "asset_version_id",
                "category",
                "source_documentation",
                "license",
                "rights_record_id",
                "rights_record_version",
            },
            field="evaluation asset",
        )
        asset_id = _require_uuid(asset["asset_version_id"], field="asset version ID")
        if asset_id in asset_by_id:
            raise ValueError("evaluation asset identities must be unique")
        _require_token(asset["category"], field="asset category")
        source_documentation = asset["source_documentation"]
        if (
            not isinstance(source_documentation, str)
            or not source_documentation.startswith("docs/evaluation/")
            or ".." in Path(source_documentation).parts
            or not source_documentation.endswith(".md")
        ):
            raise ValueError("asset source documentation is invalid")
        if not isinstance(asset["license"], str) or not asset["license"].strip():
            raise ValueError("asset license is required")
        _require_uuid(asset["rights_record_id"], field="asset Rights Record ID")
        _require_integer(
            asset["rights_record_version"],
            field="asset Rights Record version",
            minimum=1,
        )
        asset_by_id[asset_id] = asset
    return asset_by_id


def _validate_query(query: Any, asset_by_id: Mapping[str, dict[str, Any]]) -> str:
    if not isinstance(query, dict):
        raise ValueError("evaluation query must be an object")
    _require_keys(
        query,
        {
            "query_id",
            "query_text",
            "category",
            "vector_kind",
            "purpose",
            "provider",
            "relevance_grades",
            "rights_snapshot",
            "exact_flat_asset_version_ids",
        },
        field="evaluation query",
    )
    query_id = _require_token(query["query_id"], field="query ID")
    query_text = query["query_text"]
    if (
        not isinstance(query_text, str)
        or not query_text
        or query_text != " ".join(query_text.split())
        or unicodedata.normalize("NFKC", query_text) != query_text
        or not query_text.isprintable()
        or len(query_text.encode("utf-8")) > 4096
    ):
        raise ValueError("query text is invalid")
    category = _require_token(query["category"], field="query category")
    if category not in {asset["category"] for asset in asset_by_id.values()}:
        raise ValueError("query category has no assets in the candidate universe")
    if query["vector_kind"] not in {"IMAGE", "PRODUCT_FUSED"}:
        raise ValueError("query vector kind is invalid")
    _require_token(query["purpose"], field="query purpose")
    _require_token(query["provider"], field="query provider")

    relevance = query["relevance_grades"]
    if not isinstance(relevance, dict) or not relevance:
        raise ValueError("query relevance grades must not be empty")
    if not set(relevance) <= set(asset_by_id):
        raise ValueError("query relevance references the candidate universe")
    for grade in relevance.values():
        if type(grade) is not int or not 0 <= grade <= 3:
            raise ValueError("query relevance grade must be an integer from 0 to 3")
    if not any(grade > 0 for grade in relevance.values()):
        raise ValueError("query relevance requires at least one relevant candidate")

    rights_snapshot = _require_sequence(query["rights_snapshot"], field="rights snapshot")
    rights_by_asset: dict[str, dict[str, Any]] = {}
    for decision in rights_snapshot:
        if not isinstance(decision, dict):
            raise ValueError("rights snapshot decision must be an object")
        _require_keys(
            decision,
            {
                "asset_version_id",
                "rights_record_id",
                "rights_record_version",
                "authorized",
            },
            field="rights snapshot decision",
        )
        asset_id = _require_uuid(
            decision["asset_version_id"],
            field="rights snapshot Asset Version ID",
        )
        if asset_id in rights_by_asset or asset_id not in asset_by_id:
            raise ValueError("rights snapshot candidate identity is invalid")
        asset = asset_by_id[asset_id]
        if (
            decision["rights_record_id"] != asset["rights_record_id"]
            or decision["rights_record_version"] != asset["rights_record_version"]
        ):
            raise ValueError("rights snapshot does not match the asset Rights Record")
        if type(decision["authorized"]) is not bool:
            raise ValueError("rights snapshot authorization must be a boolean")
        rights_by_asset[asset_id] = decision
    if set(rights_by_asset) != set(asset_by_id):
        raise ValueError("rights snapshot must cover the complete candidate universe")

    exact_flat = _require_sequence(
        query["exact_flat_asset_version_ids"],
        field="exact FLAT reference",
    )
    if not exact_flat or len(exact_flat) != len(set(exact_flat)):
        raise ValueError("exact FLAT reference must be non-empty and unique")
    if not set(exact_flat) <= {
        asset_id for asset_id, decision in rights_by_asset.items() if decision["authorized"]
    }:
        raise ValueError("exact FLAT reference must remain inside the authorized universe")
    return query_id


def _validate_queries(
    value: Any,
    asset_by_id: Mapping[str, dict[str, Any]],
) -> set[str]:
    queries = _require_sequence(value, field="evaluation queries")
    if not queries:
        raise ValueError("evaluation queries must not be empty")
    if len(queries) > _MAX_QUERIES:
        raise ValueError("evaluation queries exceed their limit")
    query_ids: set[str] = set()
    for query in queries:
        query_id = _validate_query(query, asset_by_id)
        if query_id in query_ids:
            raise ValueError("evaluation query identities must be unique")
        query_ids.add(query_id)
    return query_ids


def _validate_observations(
    observations: dict[str, Any],
    *,
    asset_ids: set[str],
    query_ids: set[str],
) -> None:
    _require_keys(
        observations,
        {
            "schema_version",
            "suite_version",
            "retrieval_policy_version",
            "embedding_model_version",
            "collection_version",
            "queries",
        },
        field="evaluation observations",
    )
    if observations["schema_version"] != _OBSERVATIONS_SCHEMA:
        raise ValueError("evaluation observations schema version is unsupported")
    observation_queries = _require_sequence(observations["queries"], field="observations queries")
    observed_ids: set[str] = set()
    for observation in observation_queries:
        if not isinstance(observation, dict):
            raise ValueError("evaluation observation must be an object")
        _require_keys(
            observation,
            {
                "query_id",
                "latency_ms",
                "retrieved_asset_version_ids",
                "ann_asset_version_ids",
            },
            field="evaluation observation",
        )
        query_id = _require_token(observation["query_id"], field="observation query ID")
        if query_id in observed_ids:
            raise ValueError("evaluation observation query identities must be unique")
        observed_ids.add(query_id)
        _require_number(
            observation["latency_ms"],
            field="retrieval latency",
            minimum=0,
            maximum=3_600_000,
        )
        for field in ("retrieved_asset_version_ids", "ann_asset_version_ids"):
            ranking = _require_sequence(observation[field], field=field)
            if len(ranking) > _MAX_RANKING:
                raise ValueError("evaluation ranking limit exceeded")
            if len(ranking) != len(set(ranking)):
                raise ValueError("evaluation ranking identities must be unique")
            if not set(ranking) <= asset_ids:
                raise ValueError("evaluation result is outside the candidate universe")
    if observed_ids != query_ids:
        raise ValueError("evaluation observations must exactly cover manifest queries")


def _validate_manifest_and_observations(
    manifest: dict[str, Any],
    observations: dict[str, Any],
    *,
    requested_profile: str,
) -> None:
    _require_keys(
        manifest,
        {
            "schema_version",
            "suite_version",
            "profile",
            "split",
            "candidate_universe_version",
            "rights_snapshot_version",
            "retrieval_policy_version",
            "embedding_model_version",
            "collection_version",
            "bootstrap",
            "thresholds",
            "assets",
            "queries",
        },
        field="evaluation manifest",
    )
    if manifest["schema_version"] != _MANIFEST_SCHEMA:
        raise ValueError("evaluation manifest schema version is unsupported")
    profile = _require_token(manifest["profile"], field="evaluation profile")
    if profile != requested_profile or profile not in {"daily", "release"}:
        raise ValueError("evaluation profile does not match the requested profile")
    split = _require_token(manifest["split"], field="evaluation split")
    allowed_splits = {"development", "validation"} if profile == "daily" else {"hidden-release"}
    if split not in allowed_splits:
        raise ValueError("evaluation split is invalid for its profile")
    for field in (
        "suite_version",
        "candidate_universe_version",
        "rights_snapshot_version",
        "retrieval_policy_version",
        "embedding_model_version",
        "collection_version",
    ):
        _require_token(manifest[field], field=field)
    _validate_bootstrap(manifest["bootstrap"])
    _validate_thresholds(manifest["thresholds"])
    if profile == "release" and manifest["thresholds"]["basis"] != "confidence-bound":
        raise ValueError("release evaluation requires confidence-bound thresholds")
    asset_by_id = _validate_assets(manifest["assets"])
    query_ids = _validate_queries(manifest["queries"], asset_by_id)
    if manifest["bootstrap"]["samples"] * len(query_ids) > _MAX_BOOTSTRAP_WORK:
        raise ValueError("evaluation bootstrap work exceeds its limit")
    _validate_observations(
        observations,
        asset_ids=set(asset_by_id),
        query_ids=query_ids,
    )


def _metric_map(value: dict[str, Any]) -> dict[int, float]:
    return {int(key): float(metric) for key, metric in value.items()}


def load_retrieval_evaluation(
    manifest_path: str | Path,
    observations_path: str | Path,
    *,
    profile: str,
) -> RetrievalEvaluationSuite:
    manifest = _read_json(manifest_path)
    observations_document = _read_json(observations_path)
    _validate_manifest_and_observations(
        manifest,
        observations_document,
        requested_profile=profile,
    )
    identity = EvaluationIdentity(
        suite_version=manifest["suite_version"],
        profile=manifest["profile"],
        split=manifest["split"],
        candidate_universe_version=manifest["candidate_universe_version"],
        rights_snapshot_version=manifest["rights_snapshot_version"],
        retrieval_policy_version=manifest["retrieval_policy_version"],
        embedding_model_version=manifest["embedding_model_version"],
        collection_version=manifest["collection_version"],
        manifest_sha256=_canonical_sha256(manifest),
        observations_sha256=_canonical_sha256(observations_document),
    )
    for field in (
        "suite_version",
        "retrieval_policy_version",
        "embedding_model_version",
        "collection_version",
    ):
        if observations_document[field] != getattr(identity, field):
            raise ValueError(f"evaluation observations {field} does not match the manifest")
    bootstrap = manifest["bootstrap"]
    threshold_data = manifest["thresholds"]
    queries = tuple(
        EvaluationQuery(
            query_id=query["query_id"],
            query_text=query["query_text"],
            category=query["category"],
            vector_kind=query["vector_kind"],
            purpose=query["purpose"],
            provider=query["provider"],
            relevance_grades=dict(query["relevance_grades"]),
            authorized_asset_version_ids=frozenset(
                decision["asset_version_id"]
                for decision in query["rights_snapshot"]
                if decision["authorized"]
            ),
            unauthorized_asset_version_ids=frozenset(
                decision["asset_version_id"]
                for decision in query["rights_snapshot"]
                if not decision["authorized"]
            ),
            exact_flat_asset_version_ids=tuple(query["exact_flat_asset_version_ids"]),
        )
        for query in manifest["queries"]
    )
    observations = tuple(
        RetrievalObservation(
            query_id=observation["query_id"],
            latency_ms=float(observation["latency_ms"]),
            retrieved_asset_version_ids=tuple(observation["retrieved_asset_version_ids"]),
            ann_asset_version_ids=tuple(observation["ann_asset_version_ids"]),
        )
        for observation in observations_document["queries"]
    )
    return RetrievalEvaluationSuite(
        identity=identity,
        bootstrap=BootstrapConfig(
            samples=bootstrap["samples"],
            confidence_level=float(bootstrap["confidence_level"]),
            seed=bootstrap["seed"],
        ),
        thresholds=EvaluationThresholds(
            basis=threshold_data["basis"],
            minimum_recall_at=_metric_map(threshold_data["minimum_recall_at"]),
            minimum_precision_at=_metric_map(threshold_data["minimum_precision_at"]),
            minimum_mrr=float(threshold_data["minimum_mrr"]),
            minimum_ndcg_at=_metric_map(threshold_data["minimum_ndcg_at"]),
            minimum_ann_recall_at=_metric_map(threshold_data["minimum_ann_recall_at"]),
            maximum_p95_latency_ms=float(threshold_data["maximum_p95_latency_ms"]),
            maximum_unauthorized_recall_at=_metric_map(
                threshold_data["maximum_unauthorized_recall_at"]
            ),
            maximum_unauthorized_return_count=threshold_data["maximum_unauthorized_return_count"],
            maximum_queries_with_unauthorized_results=threshold_data[
                "maximum_queries_with_unauthorized_results"
            ],
        ),
        queries=queries,
        observations=observations,
    )
