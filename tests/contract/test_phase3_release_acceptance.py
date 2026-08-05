from __future__ import annotations

import json
from pathlib import Path

import yaml
from commercevision_evaluation import audit_phase3_release
from commercevision_evaluation.phase3_release_cli import main as release_main

ROOT = Path(__file__).parents[2]
MANIFEST = ROOT / "evaluation/phase3/release-v1/manifest.json"


def test_checked_in_phase3_release_manifest_covers_every_exit_gate() -> None:
    report = audit_phase3_release(MANIFEST, repository_root=ROOT)

    assert report.passed is True
    assert report.phase == "phase-3"
    assert len(report.requirement_ids) == 10
    assert len(report.fault_components) == 6
    assert len(report.recovery_invariant_ids) == 6
    assert len(report.ci_gate_ids) == 14
    assert len(report.evidence) >= 50


def test_phase3_release_cli_writes_machine_and_human_evidence(tmp_path: Path) -> None:
    json_output = tmp_path / "phase3-release.json"
    markdown_output = tmp_path / "phase3-release.md"

    assert (
        release_main(
            [
                "--manifest",
                str(MANIFEST),
                "--repository-root",
                str(ROOT),
                "--json-output",
                str(json_output),
                "--markdown-output",
                str(markdown_output),
            ]
        )
        == 0
    )
    report = json.loads(json_output.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["phase"] == "phase-3"
    assert "Gate: **PASS**" in markdown_output.read_text(encoding="utf-8")


def test_ci_runs_phase3_acceptance_and_retains_aggregate_evidence() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    for command in (
        "uv run commercevision-phase3-acceptance",
        "--env-file infra/public-demo/phase3.env.example",
    ):
        assert command in workflow
    assert "phase3-release-acceptance" in workflow


def test_phase3_public_demo_profile_is_planning_specific_and_fail_closed() -> None:
    values = {}
    for line in (
        (ROOT / "infra/public-demo/phase3.env.example").read_text(encoding="utf-8").splitlines()
    ):
        if line and not line.startswith("#"):
            name, value = line.split("=", 1)
            values[name] = value

    assert values["CV_WEB_ALLOWED_WORKSPACE_IDS"] == "catalog-demo"
    assert values["CV_WEB_ADMIN_WORKSPACE_IDS"] == ""
    assert values["CV_TRUSTED_PRINCIPAL_CURRENT_KEY_ID"] == "public-demo-phase3-current"
    assert values["CV_CREATIVE_PLAN_CURSOR_MAX_AGE_SECONDS"] == "900"
    assert values["CV_WORKFLOW_EVENT_CURSOR_MAX_AGE_SECONDS"] == "900"
    assert values["CV_TOOL_INTENT_ALLOWED_COST_CLASSES"] == '["low"]'
    assert values["CV_TOOL_INTENT_QUOTA_UNITS"] == "8"
    assert values["CV_VISION_ADAPTER"] == "deterministic"
    assert values["CV_EMBEDDING_PROVIDER"] == "fixture"
    assert values["CV_VALIDATION_DATA_TRANSFER_ENABLED"] == "false"
    assert values["CV_VISION_DATA_TRANSFER_ENABLED"] == "false"
    assert values["CV_EMBEDDING_DATA_TRANSFER_ENABLED"] == "false"


def test_compose_routes_phase3_public_demo_limits_to_the_owning_processes() -> None:
    compose = yaml.safe_load(
        (ROOT / "infra/compose/docker-compose.yml").read_text(encoding="utf-8")
    )
    api = compose["services"]["api"]["environment"]
    worker = compose["services"]["worker"]["environment"]

    for service in ("object-storage-init", "migrate", "api", "worker", "scheduler", "mcp-server"):
        assert compose["services"][service]["environment"]["CV_ENVIRONMENT"] == (
            "${CV_ENVIRONMENT:-local}"
        )
    assert "CV_CREATIVE_PLAN_CURSOR_MAX_AGE_SECONDS" in api
    assert "CV_WORKFLOW_EVENT_CURSOR_MAX_AGE_SECONDS" in api
    assert "CV_TOOL_INTENT_ALLOWED_COST_CLASSES" in worker
    assert "CV_TOOL_INTENT_QUOTA_UNITS" in worker
