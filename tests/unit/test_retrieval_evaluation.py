from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from commercevision_evaluation import (
    evaluate_retrieval,
    load_retrieval_evaluation,
    retrieval_report_json,
    write_retrieval_report,
)
from commercevision_evaluation.cli import main as evaluation_cli

ASSET_IDS = tuple(f"0198f4d8-1f7c-7b2d-8da9-214a92a885{index:02d}" for index in range(1, 7))


def test_versioned_suite_reports_relevance_latency_and_exact_flat_ann_recall(tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    observations_path = tmp_path / "observations.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "commercevision.retrieval-evaluation.v1",
                "suite_version": "retrieval-daily-v1",
                "profile": "daily",
                "split": "validation",
                "candidate_universe_version": "catalog-fixture-v1",
                "rights_snapshot_version": "rights-fixture-v1",
                "retrieval_policy_version": "retrieval-policy-v1",
                "embedding_model_version": "qwen3-vl-embedding-fixture-v1",
                "collection_version": "collection-fixture-v1",
                "bootstrap": {"samples": 200, "confidence_level": 0.95, "seed": 20260804},
                "thresholds": {
                    "basis": "point-estimate",
                    "minimum_recall_at": {"5": 1.0, "10": 1.0, "20": 1.0},
                    "minimum_precision_at": {"5": 0.6, "10": 0.3, "20": 0.15},
                    "minimum_mrr": 1.0,
                    "minimum_ndcg_at": {"5": 1.0, "10": 1.0, "20": 1.0},
                    "minimum_ann_recall_at": {"5": 1.0, "10": 1.0, "20": 1.0},
                    "maximum_p95_latency_ms": 150.0,
                    "maximum_unauthorized_recall_at": {"5": 0.0, "10": 0.0, "20": 0.0},
                    "maximum_unauthorized_return_count": 0,
                    "maximum_queries_with_unauthorized_results": 0,
                },
                "assets": [
                    {
                        "asset_version_id": asset_id,
                        "category": "beauty",
                        "source_documentation": "docs/evaluation/retrieval-dataset-sources.md",
                        "license": "CC0-1.0",
                        "rights_record_id": (f"0198f4d8-1f7c-7b2d-8da9-214a92a886{index:02d}"),
                        "rights_record_version": 1,
                    }
                    for index, asset_id in enumerate(ASSET_IDS, start=1)
                ],
                "queries": [
                    {
                        "query_id": "beauty-lipstick-red-01",
                        "query_text": "red lipstick product packshot",
                        "category": "beauty",
                        "vector_kind": "IMAGE",
                        "purpose": "creative-reference",
                        "provider": "alibaba-model-studio",
                        "relevance_grades": {
                            ASSET_IDS[0]: 3,
                            ASSET_IDS[1]: 2,
                            ASSET_IDS[2]: 1,
                        },
                        "rights_snapshot": [
                            {
                                "asset_version_id": asset_id,
                                "rights_record_id": (
                                    f"0198f4d8-1f7c-7b2d-8da9-214a92a886{index:02d}"
                                ),
                                "rights_record_version": 1,
                                "authorized": True,
                            }
                            for index, asset_id in enumerate(ASSET_IDS, start=1)
                        ],
                        "exact_flat_asset_version_ids": list(ASSET_IDS[:5]),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    observations_path.write_text(
        json.dumps(
            {
                "schema_version": "commercevision.retrieval-observations.v1",
                "suite_version": "retrieval-daily-v1",
                "retrieval_policy_version": "retrieval-policy-v1",
                "embedding_model_version": "qwen3-vl-embedding-fixture-v1",
                "collection_version": "collection-fixture-v1",
                "queries": [
                    {
                        "query_id": "beauty-lipstick-red-01",
                        "latency_ms": 100.0,
                        "retrieved_asset_version_ids": list(ASSET_IDS[:5]),
                        "ann_asset_version_ids": list(ASSET_IDS[:5]),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    suite = load_retrieval_evaluation(manifest_path, observations_path, profile="daily")
    report = evaluate_retrieval(suite)

    assert report.identity.suite_version == "retrieval-daily-v1"
    assert report.overall.recall_at == {5: 1.0, 10: 1.0, 20: 1.0}
    assert report.overall.precision_at == {5: 0.6, 10: 0.3, 20: 0.15}
    assert report.overall.mrr == 1.0
    assert report.overall.ndcg_at == {5: 1.0, 10: 1.0, 20: 1.0}
    assert report.overall.ann_recall_at == {5: 1.0, 10: 1.0, 20: 1.0}
    assert report.overall.p50_latency_ms == pytest.approx(100.0)
    assert report.overall.p95_latency_ms == pytest.approx(100.0)
    assert report.gate.passed is True


def test_any_unauthorized_result_fails_all_safety_gates_without_leaking_asset_ids(
    tmp_path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    observations_path = tmp_path / "observations.json"
    thresholds = {
        "basis": "point-estimate",
        "minimum_recall_at": {str(k): 0.0 for k in (5, 10, 20)},
        "minimum_precision_at": {str(k): 0.0 for k in (5, 10, 20)},
        "minimum_mrr": 0.0,
        "minimum_ndcg_at": {str(k): 0.0 for k in (5, 10, 20)},
        "minimum_ann_recall_at": {str(k): 0.0 for k in (5, 10, 20)},
        "maximum_p95_latency_ms": 1_000.0,
        "maximum_unauthorized_recall_at": {str(k): 0.0 for k in (5, 10, 20)},
        "maximum_unauthorized_return_count": 0,
        "maximum_queries_with_unauthorized_results": 0,
    }
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "commercevision.retrieval-evaluation.v1",
                "suite_version": "retrieval-security-v1",
                "profile": "daily",
                "split": "validation",
                "candidate_universe_version": "catalog-fixture-v1",
                "rights_snapshot_version": "rights-fixture-v1",
                "retrieval_policy_version": "retrieval-policy-v1",
                "embedding_model_version": "embedding-fixture-v1",
                "collection_version": "collection-fixture-v1",
                "bootstrap": {"samples": 100, "confidence_level": 0.95, "seed": 17},
                "thresholds": thresholds,
                "assets": [
                    {
                        "asset_version_id": asset_id,
                        "category": "beauty",
                        "source_documentation": "docs/evaluation/retrieval-dataset-sources.md",
                        "license": "CC0-1.0",
                        "rights_record_id": (f"0198f4d8-1f7c-7b2d-8da9-214a92a886{index:02d}"),
                        "rights_record_version": 1,
                    }
                    for index, asset_id in enumerate(ASSET_IDS, start=1)
                ],
                "queries": [
                    {
                        "query_id": "beauty-unauthorized-01",
                        "query_text": "red lipstick unauthorized safety probe",
                        "category": "beauty",
                        "vector_kind": "IMAGE",
                        "purpose": "creative-reference",
                        "provider": "alibaba-model-studio",
                        "relevance_grades": {ASSET_IDS[0]: 3},
                        "rights_snapshot": [
                            {
                                "asset_version_id": asset_id,
                                "rights_record_id": (
                                    f"0198f4d8-1f7c-7b2d-8da9-214a92a886{index:02d}"
                                ),
                                "rights_record_version": 1,
                                "authorized": index < len(ASSET_IDS),
                            }
                            for index, asset_id in enumerate(ASSET_IDS, start=1)
                        ],
                        "exact_flat_asset_version_ids": list(ASSET_IDS[:5]),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    observations_path.write_text(
        json.dumps(
            {
                "schema_version": "commercevision.retrieval-observations.v1",
                "suite_version": "retrieval-security-v1",
                "retrieval_policy_version": "retrieval-policy-v1",
                "embedding_model_version": "embedding-fixture-v1",
                "collection_version": "collection-fixture-v1",
                "queries": [
                    {
                        "query_id": "beauty-unauthorized-01",
                        "latency_ms": 50.0,
                        "retrieved_asset_version_ids": [ASSET_IDS[0], ASSET_IDS[-1]],
                        "ann_asset_version_ids": list(ASSET_IDS[:5]),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_retrieval(
        load_retrieval_evaluation(manifest_path, observations_path, profile="daily")
    )

    assert report.overall.unauthorized_return_count == 1
    assert report.overall.queries_with_unauthorized_results == 1
    assert report.overall.unauthorized_recall_at == {5: 1.0, 10: 1.0, 20: 1.0}
    assert report.gate.passed is False
    assert "unauthorized-return-count" in report.gate.failures
    assert "queries-with-unauthorized-results" in report.gate.failures
    serialized = retrieval_report_json(report)
    assert all(asset_id not in serialized for asset_id in ASSET_IDS)


def test_checked_in_daily_suite_has_breakdowns_and_deterministic_bootstrap_intervals() -> None:
    dataset_root = Path("evaluation/retrieval/daily-v1")
    suite = load_retrieval_evaluation(
        dataset_root / "manifest.json",
        dataset_root / "observations.json",
        profile="daily",
    )

    first = evaluate_retrieval(suite)
    second = evaluate_retrieval(suite)

    assert set(first.per_category) == {"automotive-accessory", "beauty"}
    assert set(first.per_vector_kind) == {"IMAGE", "PRODUCT_FUSED"}
    recall_interval = first.confidence_intervals.recall_at[5]
    assert recall_interval.lower <= recall_interval.estimate <= recall_interval.upper
    assert first.confidence_intervals == second.confidence_intervals
    assert first.gate.passed is True


def test_manifest_and_observations_fail_closed_when_versioned_evidence_is_tampered(
    tmp_path,
) -> None:
    dataset_root = Path("evaluation/retrieval/daily-v1")
    manifest = json.loads((dataset_root / "manifest.json").read_text(encoding="utf-8"))
    observations = json.loads((dataset_root / "observations.json").read_text(encoding="utf-8"))

    def wrong_schema(document, _observations) -> None:
        document["schema_version"] = "commercevision.retrieval-evaluation.v0"

    def hidden_daily_split(document, _observations) -> None:
        document["split"] = "hidden-release"

    def weakened_rights_gate(document, _observations) -> None:
        document["thresholds"]["maximum_unauthorized_return_count"] = 1

    def incomplete_rights_snapshot(document, _observations) -> None:
        document["queries"][0]["rights_snapshot"].pop()

    def invalid_relevance_grade(document, _observations) -> None:
        first_asset_id = next(iter(document["queries"][0]["relevance_grades"]))
        document["queries"][0]["relevance_grades"][first_asset_id] = 4

    def missing_license(document, _observations) -> None:
        document["assets"][0]["license"] = ""

    def result_outside_candidate_universe(_document, observation_document) -> None:
        observation_document["queries"][0]["retrieved_asset_version_ids"].append(
            "0198f4d8-1f7c-7b2d-8da9-214a92a88fff"
        )

    def excessive_bootstrap(document, _observations) -> None:
        document["bootstrap"]["samples"] = 10_001

    def category_without_assets(document, _observations) -> None:
        document["queries"][0]["category"] = "food"

    def excessive_ranking(_document, observation_document) -> None:
        observation_document["queries"][0]["retrieved_asset_version_ids"] = [ASSET_IDS[0]] * 1_001

    cases = (
        ("wrong-schema", wrong_schema, "schema"),
        ("hidden-daily-split", hidden_daily_split, "split"),
        ("weakened-rights-gate", weakened_rights_gate, "unauthorized"),
        ("incomplete-rights-snapshot", incomplete_rights_snapshot, "rights snapshot"),
        ("invalid-relevance-grade", invalid_relevance_grade, "relevance"),
        ("missing-license", missing_license, "license"),
        ("outside-universe", result_outside_candidate_universe, "candidate universe"),
        ("excessive-bootstrap", excessive_bootstrap, "bootstrap samples"),
        ("category-without-assets", category_without_assets, "query category"),
        ("excessive-ranking", excessive_ranking, "ranking limit"),
    )
    for case_name, mutate, message in cases:
        changed_manifest = deepcopy(manifest)
        changed_observations = deepcopy(observations)
        mutate(changed_manifest, changed_observations)
        manifest_path = tmp_path / f"{case_name}-manifest.json"
        observations_path = tmp_path / f"{case_name}-observations.json"
        manifest_path.write_text(json.dumps(changed_manifest), encoding="utf-8")
        observations_path.write_text(json.dumps(changed_observations), encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            load_retrieval_evaluation(manifest_path, observations_path, profile="daily")


def test_release_profile_uses_conservative_bootstrap_bounds_for_quality_gates(tmp_path) -> None:
    dataset_root = Path("evaluation/retrieval/daily-v1")
    manifest = json.loads((dataset_root / "manifest.json").read_text(encoding="utf-8"))
    observations = json.loads((dataset_root / "observations.json").read_text(encoding="utf-8"))
    manifest["profile"] = "release"
    manifest["split"] = "hidden-release"
    manifest["thresholds"]["basis"] = "confidence-bound"
    manifest["thresholds"]["minimum_recall_at"] = {str(k): 0.4 for k in (5, 10, 20)}
    manifest["thresholds"]["minimum_precision_at"] = {str(k): 0.0 for k in (5, 10, 20)}
    manifest["thresholds"]["minimum_mrr"] = 0.0
    manifest["thresholds"]["minimum_ndcg_at"] = {str(k): 0.0 for k in (5, 10, 20)}
    manifest["thresholds"]["minimum_ann_recall_at"] = {str(k): 0.0 for k in (5, 10, 20)}
    observations["queries"][1]["retrieved_asset_version_ids"] = [
        "0198f4d8-1f7c-7b2d-8da9-214a92a88804",
        "0198f4d8-1f7c-7b2d-8da9-214a92a88805",
        "0198f4d8-1f7c-7b2d-8da9-214a92a88806",
    ]
    manifest_path = tmp_path / "hidden-manifest.json"
    observations_path = tmp_path / "hidden-observations.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    observations_path.write_text(json.dumps(observations), encoding="utf-8")

    report = evaluate_retrieval(
        load_retrieval_evaluation(manifest_path, observations_path, profile="release")
    )

    assert report.overall.recall_at[5] == 0.5
    assert report.overall.recall_at[5] >= manifest["thresholds"]["minimum_recall_at"]["5"]
    assert report.confidence_intervals.recall_at[5].lower == 0.0
    assert "recall@5" in report.gate.failures


def test_release_profile_rejects_point_estimate_thresholds(tmp_path) -> None:
    dataset_root = Path("evaluation/retrieval/daily-v1")
    manifest = json.loads((dataset_root / "manifest.json").read_text(encoding="utf-8"))
    observations = json.loads((dataset_root / "observations.json").read_text(encoding="utf-8"))
    manifest["profile"] = "release"
    manifest["split"] = "hidden-release"
    manifest_path = tmp_path / "manifest.json"
    observations_path = tmp_path / "observations.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    observations_path.write_text(json.dumps(observations), encoding="utf-8")

    with pytest.raises(ValueError, match="confidence-bound"):
        load_retrieval_evaluation(manifest_path, observations_path, profile="release")


def test_cli_retains_machine_and_human_reports_without_candidate_payloads(tmp_path) -> None:
    dataset_root = Path("evaluation/retrieval/daily-v1")
    json_output = tmp_path / "reports" / "retrieval-evaluation.json"
    markdown_output = tmp_path / "reports" / "retrieval-evaluation.md"

    exit_code = evaluation_cli(
        [
            "--manifest",
            str(dataset_root / "manifest.json"),
            "--observations",
            str(dataset_root / "observations.json"),
            "--profile",
            "daily",
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )

    assert exit_code == 0
    machine_report = json.loads(json_output.read_text(encoding="utf-8"))
    human_report = markdown_output.read_text(encoding="utf-8")
    assert machine_report["identity"]["suite_version"] == "retrieval-daily-v1"
    assert machine_report["gate"]["passed"] is True
    assert "# Retrieval evaluation report" in human_report
    assert "automotive-accessory" in human_report
    assert "PRODUCT_FUSED" in human_report
    assert "PASS" in human_report
    assert all(asset_id not in human_report for asset_id in ASSET_IDS)


def test_report_writer_rejects_overlapping_output_paths(tmp_path) -> None:
    dataset_root = Path("evaluation/retrieval/daily-v1")
    report = evaluate_retrieval(
        load_retrieval_evaluation(
            dataset_root / "manifest.json",
            dataset_root / "observations.json",
            profile="daily",
        )
    )
    output = tmp_path / "report"

    with pytest.raises(ValueError, match="distinct"):
        write_retrieval_report(report, json_path=output, markdown_path=output)


def test_machine_report_freezes_canonical_input_hashes_thresholds_and_bootstrap() -> None:
    dataset_root = Path("evaluation/retrieval/daily-v1")
    report = evaluate_retrieval(
        load_retrieval_evaluation(
            dataset_root / "manifest.json",
            dataset_root / "observations.json",
            profile="daily",
        )
    )
    machine_report = json.loads(retrieval_report_json(report))

    assert len(machine_report["identity"]["manifest_sha256"]) == 64
    assert len(machine_report["identity"]["observations_sha256"]) == 64
    assert machine_report["thresholds"]["basis"] == "point-estimate"
    assert machine_report["bootstrap"] == {
        "confidence_level": 0.95,
        "samples": 500,
        "seed": 20260804,
    }
