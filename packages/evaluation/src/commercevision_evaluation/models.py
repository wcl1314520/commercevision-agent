"""Immutable data model for retrieval evaluation inputs and aggregate evidence."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EvaluationIdentity:
    suite_version: str
    profile: str
    split: str
    candidate_universe_version: str
    rights_snapshot_version: str
    retrieval_policy_version: str
    embedding_model_version: str
    collection_version: str
    manifest_sha256: str
    observations_sha256: str


@dataclass(frozen=True, slots=True)
class BootstrapConfig:
    samples: int
    confidence_level: float
    seed: int


@dataclass(frozen=True, slots=True)
class EvaluationThresholds:
    basis: str
    minimum_recall_at: dict[int, float]
    minimum_precision_at: dict[int, float]
    minimum_mrr: float
    minimum_ndcg_at: dict[int, float]
    minimum_ann_recall_at: dict[int, float]
    maximum_p95_latency_ms: float
    maximum_unauthorized_recall_at: dict[int, float]
    maximum_unauthorized_return_count: int
    maximum_queries_with_unauthorized_results: int


@dataclass(frozen=True, slots=True)
class EvaluationQuery:
    query_id: str
    query_text: str
    category: str
    vector_kind: str
    purpose: str
    provider: str
    relevance_grades: dict[str, int]
    authorized_asset_version_ids: frozenset[str]
    unauthorized_asset_version_ids: frozenset[str]
    exact_flat_asset_version_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RetrievalObservation:
    query_id: str
    latency_ms: float
    retrieved_asset_version_ids: tuple[str, ...]
    ann_asset_version_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RetrievalEvaluationSuite:
    identity: EvaluationIdentity
    bootstrap: BootstrapConfig
    thresholds: EvaluationThresholds
    queries: tuple[EvaluationQuery, ...]
    observations: tuple[RetrievalObservation, ...]


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    query_count: int
    recall_at: dict[int, float]
    precision_at: dict[int, float]
    mrr: float
    ndcg_at: dict[int, float]
    ann_recall_at: dict[int, float]
    p50_latency_ms: float
    p95_latency_ms: float
    unauthorized_recall_at: dict[int, float]
    unauthorized_return_count: int
    queries_with_unauthorized_results: int


@dataclass(frozen=True, slots=True)
class ConfidenceInterval:
    lower: float
    estimate: float
    upper: float


@dataclass(frozen=True, slots=True)
class EvaluationConfidenceIntervals:
    confidence_level: float
    samples: int
    recall_at: dict[int, ConfidenceInterval]
    precision_at: dict[int, ConfidenceInterval]
    mrr: ConfidenceInterval
    ndcg_at: dict[int, ConfidenceInterval]
    ann_recall_at: dict[int, ConfidenceInterval]
    p50_latency_ms: ConfidenceInterval
    p95_latency_ms: ConfidenceInterval
    unauthorized_recall_at: dict[int, ConfidenceInterval]
    unauthorized_return_count: ConfidenceInterval
    queries_with_unauthorized_results: ConfidenceInterval


@dataclass(frozen=True, slots=True)
class EvaluationGate:
    passed: bool
    failures: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RetrievalEvaluationReport:
    identity: EvaluationIdentity
    bootstrap: BootstrapConfig
    thresholds: EvaluationThresholds
    overall: EvaluationMetrics
    confidence_intervals: EvaluationConfidenceIntervals
    per_category: dict[str, EvaluationMetrics]
    per_vector_kind: dict[str, EvaluationMetrics]
    gate: EvaluationGate
