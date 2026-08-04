"""Deterministic retrieval metrics, confidence intervals, and release gates."""

from __future__ import annotations

import math
import random
from typing import Any

from .models import (
    BootstrapConfig,
    ConfidenceInterval,
    EvaluationConfidenceIntervals,
    EvaluationGate,
    EvaluationMetrics,
    EvaluationQuery,
    EvaluationThresholds,
    RetrievalEvaluationReport,
    RetrievalEvaluationSuite,
    RetrievalObservation,
)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _ndcg(ranking: tuple[str, ...], grades: dict[str, int], k: int) -> float:
    def dcg(values: list[int]) -> float:
        return sum((2**grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(values, 1))

    actual = dcg([grades.get(asset_id, 0) for asset_id in ranking[:k]])
    ideal = dcg(sorted(grades.values(), reverse=True)[:k])
    return actual / ideal if ideal else 0.0


def _evaluate_query(
    query: EvaluationQuery,
    observation: RetrievalObservation,
    ks: tuple[int, ...],
) -> dict[str, Any]:
    relevant = {asset_id for asset_id, grade in query.relevance_grades.items() if grade > 0}
    first_relevant = next(
        (
            rank
            for rank, asset_id in enumerate(observation.retrieved_asset_version_ids, 1)
            if asset_id in relevant
        ),
        None,
    )
    unauthorized = tuple(
        asset_id
        for asset_id in observation.retrieved_asset_version_ids
        if asset_id not in query.authorized_asset_version_ids
    )
    return {
        "category": query.category,
        "vector_kind": query.vector_kind,
        "recall_at": {
            k: len(set(observation.retrieved_asset_version_ids[:k]) & relevant) / len(relevant)
            if relevant
            else 0.0
            for k in ks
        },
        "precision_at": {
            k: len(set(observation.retrieved_asset_version_ids[:k]) & relevant) / k for k in ks
        },
        "mrr": 1.0 / first_relevant if first_relevant is not None else 0.0,
        "ndcg_at": {
            k: _ndcg(observation.retrieved_asset_version_ids, query.relevance_grades, k) for k in ks
        },
        "ann_recall_at": {
            k: len(
                set(observation.ann_asset_version_ids[:k])
                & set(query.exact_flat_asset_version_ids[:k])
            )
            / len(query.exact_flat_asset_version_ids[:k])
            if query.exact_flat_asset_version_ids[:k]
            else 0.0
            for k in ks
        },
        "latency_ms": observation.latency_ms,
        "unauthorized_recall_at": {
            k: len(
                set(observation.retrieved_asset_version_ids[:k])
                & query.unauthorized_asset_version_ids
            )
            / len(query.unauthorized_asset_version_ids)
            if query.unauthorized_asset_version_ids
            else 0.0
            for k in ks
        },
        "unauthorized_return_count": len(unauthorized),
        "has_unauthorized": bool(unauthorized),
    }


def _aggregate(query_metrics: list[dict[str, Any]], ks: tuple[int, ...]) -> EvaluationMetrics:
    latencies = [metrics["latency_ms"] for metrics in query_metrics]
    return EvaluationMetrics(
        query_count=len(query_metrics),
        recall_at={k: _mean([metrics["recall_at"][k] for metrics in query_metrics]) for k in ks},
        precision_at={
            k: _mean([metrics["precision_at"][k] for metrics in query_metrics]) for k in ks
        },
        mrr=_mean([metrics["mrr"] for metrics in query_metrics]),
        ndcg_at={k: _mean([metrics["ndcg_at"][k] for metrics in query_metrics]) for k in ks},
        ann_recall_at={
            k: _mean([metrics["ann_recall_at"][k] for metrics in query_metrics]) for k in ks
        },
        p50_latency_ms=_percentile(latencies, 0.50),
        p95_latency_ms=_percentile(latencies, 0.95),
        unauthorized_recall_at={
            k: _mean([metrics["unauthorized_recall_at"][k] for metrics in query_metrics])
            for k in ks
        },
        unauthorized_return_count=sum(
            metrics["unauthorized_return_count"] for metrics in query_metrics
        ),
        queries_with_unauthorized_results=sum(
            metrics["has_unauthorized"] for metrics in query_metrics
        ),
    )


def _confidence_interval(
    values: list[float],
    *,
    estimate: float,
    confidence_level: float,
) -> ConfidenceInterval:
    tail = (1.0 - confidence_level) / 2.0
    return ConfidenceInterval(
        lower=_percentile(values, tail),
        estimate=estimate,
        upper=_percentile(values, 1.0 - tail),
    )


def _bootstrap_intervals(
    query_metrics: list[dict[str, Any]],
    *,
    ks: tuple[int, ...],
    config: BootstrapConfig,
    estimate: EvaluationMetrics,
) -> EvaluationConfidenceIntervals:
    generator = random.Random(config.seed)
    sampled_metrics = [
        _aggregate(
            [generator.choice(query_metrics) for _ in range(len(query_metrics))],
            ks,
        )
        for _ in range(config.samples)
    ]

    def interval(values: list[float], point: float) -> ConfidenceInterval:
        return _confidence_interval(
            values,
            estimate=point,
            confidence_level=config.confidence_level,
        )

    return EvaluationConfidenceIntervals(
        confidence_level=config.confidence_level,
        samples=config.samples,
        recall_at={
            k: interval(
                [metrics.recall_at[k] for metrics in sampled_metrics], estimate.recall_at[k]
            )
            for k in ks
        },
        precision_at={
            k: interval(
                [metrics.precision_at[k] for metrics in sampled_metrics],
                estimate.precision_at[k],
            )
            for k in ks
        },
        mrr=interval([metrics.mrr for metrics in sampled_metrics], estimate.mrr),
        ndcg_at={
            k: interval([metrics.ndcg_at[k] for metrics in sampled_metrics], estimate.ndcg_at[k])
            for k in ks
        },
        ann_recall_at={
            k: interval(
                [metrics.ann_recall_at[k] for metrics in sampled_metrics],
                estimate.ann_recall_at[k],
            )
            for k in ks
        },
        p50_latency_ms=interval(
            [metrics.p50_latency_ms for metrics in sampled_metrics],
            estimate.p50_latency_ms,
        ),
        p95_latency_ms=interval(
            [metrics.p95_latency_ms for metrics in sampled_metrics],
            estimate.p95_latency_ms,
        ),
        unauthorized_recall_at={
            k: interval(
                [metrics.unauthorized_recall_at[k] for metrics in sampled_metrics],
                estimate.unauthorized_recall_at[k],
            )
            for k in ks
        },
        unauthorized_return_count=interval(
            [float(metrics.unauthorized_return_count) for metrics in sampled_metrics],
            float(estimate.unauthorized_return_count),
        ),
        queries_with_unauthorized_results=interval(
            [float(metrics.queries_with_unauthorized_results) for metrics in sampled_metrics],
            float(estimate.queries_with_unauthorized_results),
        ),
    )


def _gate(
    metrics: EvaluationMetrics,
    thresholds: EvaluationThresholds,
    intervals: EvaluationConfidenceIntervals,
) -> EvaluationGate:
    failures: list[str] = []
    use_bounds = thresholds.basis == "confidence-bound"
    for k, minimum in thresholds.minimum_recall_at.items():
        value = intervals.recall_at[k].lower if use_bounds else metrics.recall_at[k]
        if value < minimum:
            failures.append(f"recall@{k}")
    for k, minimum in thresholds.minimum_precision_at.items():
        value = intervals.precision_at[k].lower if use_bounds else metrics.precision_at[k]
        if value < minimum:
            failures.append(f"precision@{k}")
    mrr = intervals.mrr.lower if use_bounds else metrics.mrr
    if mrr < thresholds.minimum_mrr:
        failures.append("mrr")
    for k, minimum in thresholds.minimum_ndcg_at.items():
        value = intervals.ndcg_at[k].lower if use_bounds else metrics.ndcg_at[k]
        if value < minimum:
            failures.append(f"ndcg@{k}")
    for k, minimum in thresholds.minimum_ann_recall_at.items():
        value = intervals.ann_recall_at[k].lower if use_bounds else metrics.ann_recall_at[k]
        if value < minimum:
            failures.append(f"ann-recall@{k}")
    p95_latency = intervals.p95_latency_ms.upper if use_bounds else metrics.p95_latency_ms
    if p95_latency > thresholds.maximum_p95_latency_ms:
        failures.append("p95-latency")
    for k, maximum in thresholds.maximum_unauthorized_recall_at.items():
        if metrics.unauthorized_recall_at[k] > maximum:
            failures.append(f"unauthorized-recall@{k}")
    if metrics.unauthorized_return_count > thresholds.maximum_unauthorized_return_count:
        failures.append("unauthorized-return-count")
    if (
        metrics.queries_with_unauthorized_results
        > thresholds.maximum_queries_with_unauthorized_results
    ):
        failures.append("queries-with-unauthorized-results")
    return EvaluationGate(passed=not failures, failures=tuple(failures))


def evaluate_retrieval(suite: RetrievalEvaluationSuite) -> RetrievalEvaluationReport:
    query_by_id = {query.query_id: query for query in suite.queries}
    observation_by_id = {observation.query_id: observation for observation in suite.observations}
    if set(query_by_id) != set(observation_by_id):
        raise ValueError("evaluation observations must exactly cover manifest queries")
    ks = tuple(sorted(suite.thresholds.minimum_recall_at))
    query_metrics = [
        _evaluate_query(query, observation_by_id[query_id], ks)
        for query_id, query in query_by_id.items()
    ]
    overall = _aggregate(query_metrics, ks)
    per_category = {
        category: _aggregate(
            [metrics for metrics in query_metrics if metrics["category"] == category],
            ks,
        )
        for category in sorted({metrics["category"] for metrics in query_metrics})
    }
    per_vector_kind = {
        vector_kind: _aggregate(
            [metrics for metrics in query_metrics if metrics["vector_kind"] == vector_kind],
            ks,
        )
        for vector_kind in sorted({metrics["vector_kind"] for metrics in query_metrics})
    }
    confidence_intervals = _bootstrap_intervals(
        query_metrics,
        ks=ks,
        config=suite.bootstrap,
        estimate=overall,
    )
    return RetrievalEvaluationReport(
        identity=suite.identity,
        bootstrap=suite.bootstrap,
        thresholds=suite.thresholds,
        overall=overall,
        confidence_intervals=confidence_intervals,
        per_category=per_category,
        per_vector_kind=per_vector_kind,
        gate=_gate(overall, suite.thresholds, confidence_intervals),
    )
