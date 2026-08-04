from __future__ import annotations

import json
from pathlib import Path

from commercevision_evaluation import audit_phase2_release
from commercevision_evaluation.release_cli import main as release_main

ROOT = Path(__file__).parents[2]
MANIFEST = ROOT / "evaluation/phase2/release-v1/manifest.json"


def test_checked_in_phase2_release_manifest_covers_every_exit_gate() -> None:
    report = audit_phase2_release(MANIFEST, repository_root=ROOT)

    assert report.passed is True
    assert report.phase == "phase-2"
    assert len(report.requirement_ids) == 10
    assert len(report.fault_components) == 10
    assert len(report.recovery_invariant_ids) == 8
    assert len(report.ci_gate_ids) == 13
    assert len(report.evidence) >= 41


def test_phase2_release_cli_writes_machine_and_human_evidence(tmp_path: Path) -> None:
    json_output = tmp_path / "phase2-release.json"
    markdown_output = tmp_path / "phase2-release.md"

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
    assert report["phase"] == "phase-2"
    assert "Gate: **PASS**" in markdown_output.read_text(encoding="utf-8")


def test_ci_runs_phase2_acceptance_type_and_license_gates() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    for command in (
        "uv run python scripts/check_mypy_baseline.py",
        "uv run python scripts/audit_licenses.py",
        "uv run commercevision-phase2-acceptance",
        "pnpm licenses list --prod --json | node scripts/audit-node-licenses.mjs",
        "--env-file infra/public-demo/phase2.env.example",
    ):
        assert command in workflow
    assert "phase2-release-acceptance" in workflow


def test_public_demo_profile_is_dedicated_and_fail_closed() -> None:
    values = {}
    for line in (
        (ROOT / "infra/public-demo/phase2.env.example").read_text(encoding="utf-8").splitlines()
    ):
        if line and not line.startswith("#"):
            name, value = line.split("=", 1)
            values[name] = value

    assert values["CV_WEB_ALLOWED_WORKSPACE_IDS"] == "catalog-demo"
    assert values["CV_WEB_ADMIN_WORKSPACE_IDS"] == ""
    assert {
        values["CV_OBJECT_STORE_QUARANTINE_BUCKET"],
        values["CV_OBJECT_STORE_TASK_BUCKET"],
        values["CV_OBJECT_STORE_FOUNDATION_BUCKET"],
        values["CV_OBJECT_STORE_PROVIDER_RESULT_BUCKET"],
    } == {
        "public-demo-quarantine-assets",
        "public-demo-task-assets",
        "public-demo-foundation-assets",
        "public-demo-provider-results",
    }
    assert values["CV_OBJECT_STORE_CREDENTIAL_MODE"] == "oidc_role_arn"
    assert values["CV_VISION_ADAPTER"] == "deterministic"
    assert values["CV_EMBEDDING_PROVIDER"] == "fixture"
    assert values["CV_VALIDATION_DATA_TRANSFER_ENABLED"] == "false"
    assert values["CV_VISION_DATA_TRANSFER_ENABLED"] == "false"
    assert values["CV_EMBEDDING_DATA_TRANSFER_ENABLED"] == "false"
