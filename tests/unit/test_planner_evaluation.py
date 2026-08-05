from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from commercevision_evaluation import (
    evaluate_planner,
    load_planner_evaluation,
    planner_report_json,
    planner_report_markdown,
    verify_planner_report_json,
)
from commercevision_evaluation.planner_cli import main as planner_cli

ROOT = Path(__file__).resolve().parents[2]
DATASET_ROOT = ROOT / "evaluation" / "planner" / "ci-v1"


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _documents() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    manifest = json.loads((DATASET_ROOT / "manifest.json").read_text(encoding="utf-8"))
    fixtures = json.loads((DATASET_ROOT / "fixtures.json").read_text(encoding="utf-8"))
    observations = json.loads((DATASET_ROOT / "observations.json").read_text(encoding="utf-8"))
    return manifest, fixtures, observations


def _write_documents(
    tmp_path: Path,
    manifest: dict[str, object],
    fixtures: dict[str, object],
    observations: dict[str, object],
) -> tuple[Path, Path, Path]:
    manifest_path = tmp_path / "manifest.json"
    fixtures_path = tmp_path / "fixtures.json"
    observations_path = tmp_path / "observations.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    fixtures_path.write_text(json.dumps(fixtures), encoding="utf-8")
    observations_path.write_text(json.dumps(observations), encoding="utf-8")
    return manifest_path, fixtures_path, observations_path


def _load_ci_suite():
    return load_planner_evaluation(
        DATASET_ROOT / "manifest.json",
        DATASET_ROOT / "fixtures.json",
        DATASET_ROOT / "observations.json",
        profile="ci",
    )


def test_ci_observations_pass_all_quality_security_and_latency_gates() -> None:
    report = evaluate_planner(_load_ci_suite())

    assert report.gate.passed is True
    assert report.gate.failures == ()
    assert report.metrics.case_count == 2
    assert report.metrics.schema_validity_rate == 1.0
    assert report.metrics.required_constraints_rate == 1.0
    assert report.metrics.citation_precision == 1.0
    assert report.metrics.provenance_completeness_rate == 1.0
    assert report.metrics.determinism_rate == 1.0
    assert report.metrics.p95_latency_ms <= 100.0
    assert report.metrics.policy_violation_count == 0
    assert report.metrics.unauthorized_tool_count == 0
    assert report.metrics.unauthorized_provider_count == 0
    assert report.metrics.unauthorized_resource_count == 0
    assert report.metrics.budget_expansion_count == 0
    assert report.metrics.missing_approval_evidence_count == 0


def test_observation_tamper_and_identity_drift_fail_closed(tmp_path: Path) -> None:
    manifest, fixtures, observations = _documents()
    cases = observations["cases"]
    assert isinstance(cases, list)
    first_case = cases[0]
    assert isinstance(first_case, dict)
    first_case["latency_ms"] = 2.0
    manifest_path, fixtures_path, observations_path = _write_documents(
        tmp_path, manifest, fixtures, observations
    )

    with pytest.raises(ValueError, match="observations SHA-256"):
        load_planner_evaluation(
            manifest_path,
            fixtures_path,
            observations_path,
            profile="ci",
        )

    manifest, fixtures, observations = _documents()
    observations["dataset_version"] = "planner-other-v1"
    manifest["observations_sha256"] = _canonical_sha256(observations)
    manifest_path, fixtures_path, observations_path = _write_documents(
        tmp_path, manifest, fixtures, observations
    )

    with pytest.raises(ValueError, match="dataset_version"):
        load_planner_evaluation(
            manifest_path,
            fixtures_path,
            observations_path,
            profile="ci",
        )


def test_security_violation_and_nondeterminism_cannot_pass_gate(tmp_path: Path) -> None:
    manifest, fixtures, observations = _documents()
    cases = observations["cases"]
    assert isinstance(cases, list)
    first_case = cases[0]
    assert isinstance(first_case, dict)
    security = first_case["security"]
    assert isinstance(security, dict)
    security["unauthorized_tool_count"] = 1
    payload_runs = first_case["payload_sha256_runs"]
    assert isinstance(payload_runs, list)
    payload_runs[1] = "f" * 64
    manifest["observations_sha256"] = _canonical_sha256(observations)
    paths = _write_documents(tmp_path, manifest, fixtures, observations)

    report = evaluate_planner(load_planner_evaluation(*paths, profile="ci"))

    assert report.gate.passed is False
    assert any("unauthorized Tool count" in failure for failure in report.gate.failures)
    assert any("determinism rate" in failure for failure in report.gate.failures)


def test_loader_rejects_malformed_and_unbounded_observations(tmp_path: Path) -> None:
    observations_text = (DATASET_ROOT / "observations.json").read_text(encoding="utf-8")
    duplicate_observations = observations_text.replace(
        '"schema_version":',
        '"schema_version": "commercevision.planner-observations.v1", "schema_version":',
        1,
    )
    duplicate_path = tmp_path / "duplicate-observations.json"
    duplicate_path.write_text(duplicate_observations, encoding="utf-8")

    with pytest.raises(ValueError, match="unique"):
        load_planner_evaluation(
            DATASET_ROOT / "manifest.json",
            DATASET_ROOT / "fixtures.json",
            duplicate_path,
            profile="ci",
        )

    manifest, fixtures, observations = _documents()
    cases = observations["cases"]
    assert isinstance(cases, list)
    observations["cases"] = cases * 129
    manifest["observations_sha256"] = _canonical_sha256(observations)
    paths = _write_documents(tmp_path, manifest, fixtures, observations)

    with pytest.raises(ValueError, match="case limit"):
        load_planner_evaluation(*paths, profile="ci")

    manifest, fixtures, observations = _documents()
    cases = observations["cases"]
    assert isinstance(cases, list)
    first_case = cases[0]
    assert isinstance(first_case, dict)
    first_case["unexpected"] = "payload"
    manifest["observations_sha256"] = _canonical_sha256(observations)
    paths = _write_documents(tmp_path, manifest, fixtures, observations)

    with pytest.raises(ValueError, match="fields"):
        load_planner_evaluation(*paths, profile="ci")


def test_reports_are_reproducible_aggregate_only_and_tamper_evident() -> None:
    report = evaluate_planner(_load_ci_suite())

    first_json = planner_report_json(report)
    assert planner_report_json(report) == first_json
    machine = verify_planner_report_json(first_json)
    human = planner_report_markdown(report)

    assert machine["report"]["gate"]["passed"] is True
    assert machine["report_sha256"]
    assert "# Planner evaluation report" in human
    assert "PASS" in human
    for forbidden in (
        "beauty-lipstick-plan-01",
        "automotive-roof-rack-plan-01",
        "Ignore policy",
        "019b1000-0000-7000-8000-000000000101",
        "019b2000-0000-7000-8000-000000000201",
    ):
        assert forbidden not in first_json
        assert forbidden not in human

    tampered = json.loads(first_json)
    tampered["report"]["metrics"]["unauthorized_tool_count"] = 1
    with pytest.raises(ValueError, match="digest"):
        verify_planner_report_json(json.dumps(tampered))

    tampered["report_sha256"] = _canonical_sha256(tampered["report"])
    with pytest.raises(ValueError, match="gate does not match"):
        verify_planner_report_json(json.dumps(tampered))


def test_cli_writes_verified_machine_and_human_reports(tmp_path: Path) -> None:
    json_output = tmp_path / "reports" / "planner-ci-v1.json"
    markdown_output = tmp_path / "reports" / "planner-ci-v1.md"

    exit_code = planner_cli(
        [
            "--manifest",
            str(DATASET_ROOT / "manifest.json"),
            "--fixtures",
            str(DATASET_ROOT / "fixtures.json"),
            "--observations",
            str(DATASET_ROOT / "observations.json"),
            "--profile",
            "ci",
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )

    assert exit_code == 0
    verify_planner_report_json(json_output.read_text(encoding="utf-8"))
    assert "PASS" in markdown_output.read_text(encoding="utf-8")
