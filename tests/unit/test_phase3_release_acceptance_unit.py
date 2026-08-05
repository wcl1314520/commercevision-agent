from __future__ import annotations

import json
from pathlib import Path

import commercevision_evaluation.reporting as release_reporting
import pytest
from commercevision_evaluation import (
    audit_phase3_release,
    phase3_release_report_json,
    phase3_release_report_markdown,
    write_phase3_release_report,
)
from commercevision_evaluation.phase3_release_cli import main as phase3_release_main

REQUIREMENTS = (
    "browser-e2e",
    "fault-injection",
    "recovery-convergence",
    "migration-paths",
    "authorization-safety",
    "planner-security",
    "quality-security-supply-chain",
    "public-demo-isolation",
    "documentation-alignment",
    "metadata-phase",
)
FAULTS = (
    "worker-commit",
    "rabbitmq",
    "mysql",
    "checkpointer",
    "sse",
    "evaluation",
)
INVARIANTS = (
    "unique-plan-version",
    "unique-approval",
    "no-stale-authorization",
    "zero-unauthorized-intent",
    "no-retention-extension",
    "eventual-convergence",
)
CI_GATES = (
    "python",
    "web",
    "openapi",
    "real-mysql",
    "langgraph",
    "sse",
    "e2e",
    "evaluation",
    "security",
    "secrets",
    "dependencies",
    "containers",
    "licenses",
    "sbom",
)


def _evidence(anchor: str) -> dict[str, str]:
    return {"path": "evidence.txt", "anchor": anchor}


def _manifest() -> dict[str, object]:
    return {
        "schema_version": "commercevision.phase3-release-acceptance.v1",
        "release_id": "phase3-v1",
        "phase": "phase-3",
        "private_boundaries": {
            "workspace_ids": ["private-production"],
            "bucket_names": ["foundation-assets", "task-assets"],
            "credential_scopes": ["secret://commercevision/private/providers"],
            "dataset_paths": ["private/evaluation/planner-hidden-release"],
            "prompt_revision_ids": ["private-planner-r7"],
            "cursor_signing_scopes": ["secret://commercevision/private/cursors"],
        },
        "public_demo": {
            "workspace_ids": ["catalog-demo"],
            "admin_workspace_ids": [],
            "bucket_names": [
                "public-demo-quarantine-assets",
                "public-demo-task-assets",
                "public-demo-foundation-assets",
                "public-demo-provider-results",
            ],
            "object_prefix": "phase3/planner-ci-v1/",
            "credential_scopes": ["secret://commercevision/public-demo/providers"],
            "prompt_revision_ids": ["public-demo-planner-r1"],
            "cursor_signing_scopes": ["secret://commercevision/public-demo/cursors"],
            "quotas": {
                "requests_per_minute": 30,
                "concurrent_operations": 2,
                "provider_calls_per_day": 100,
                "storage_bytes": 536_870_912,
            },
            "datasets": [
                {
                    "dataset_id": "planner-ci-v1",
                    "version": "v1",
                    "license": "CC0-1.0",
                    "path": "evaluation/planner/ci-v1/manifest.json",
                    "public_demo_allowed": True,
                }
            ],
        },
        "requirements": [
            {"id": item, "evidence": [_evidence(f"requirement:{item}")]} for item in REQUIREMENTS
        ],
        "fault_injection": [
            {"component": item, "evidence": _evidence(f"fault:{item}")} for item in FAULTS
        ],
        "recovery_invariants": [
            {"id": item, "evidence": _evidence(f"invariant:{item}")} for item in INVARIANTS
        ],
        "ci_gates": [{"id": item, "evidence": _evidence(f"gate:{item}")} for item in CI_GATES],
    }


def _write_fixture(root: Path, manifest: dict[str, object]) -> Path:
    anchors = [
        *(f"requirement:{item}" for item in REQUIREMENTS),
        *(f"fault:{item}" for item in FAULTS),
        *(f"invariant:{item}" for item in INVARIANTS),
        *(f"gate:{item}" for item in CI_GATES),
    ]
    (root / "evidence.txt").write_text("\n".join(anchors), encoding="utf-8")
    dataset = root / "evaluation/planner/ci-v1/manifest.json"
    dataset.parent.mkdir(parents=True)
    dataset.write_text("{}\n", encoding="utf-8")
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_phase3_release_audit_accepts_complete_isolated_evidence(tmp_path: Path) -> None:
    report = audit_phase3_release(_write_fixture(tmp_path, _manifest()), repository_root=tmp_path)

    assert report.phase == "phase-3"
    assert report.passed is True
    assert report.requirement_ids == REQUIREMENTS
    assert report.fault_components == FAULTS
    assert report.recovery_invariant_ids == INVARIANTS
    assert report.ci_gate_ids == CI_GATES
    assert report.public_demo_prompt_revision_ids == ("public-demo-planner-r1",)
    assert report.public_demo_cursor_signing_scopes == (
        "secret://commercevision/public-demo/cursors",
    )
    assert len(report.evidence) == 36


@pytest.mark.parametrize(
    ("boundary", "value"),
    [
        ("workspace_ids", "catalog-demo"),
        ("bucket_names", "public-demo-task-assets"),
        ("credential_scopes", "secret://commercevision/public-demo/providers"),
        ("dataset_paths", "evaluation/planner/ci-v1/manifest.json"),
        ("prompt_revision_ids", "public-demo-planner-r1"),
        ("cursor_signing_scopes", "secret://commercevision/public-demo/cursors"),
    ],
)
def test_phase3_release_audit_rejects_public_private_overlap(
    tmp_path: Path,
    boundary: str,
    value: str,
) -> None:
    manifest = _manifest()
    private = manifest["private_boundaries"]
    assert isinstance(private, dict)
    private[boundary] = [value]

    with pytest.raises(ValueError, match="public-demo .* overlaps private"):
        audit_phase3_release(_write_fixture(tmp_path, manifest), repository_root=tmp_path)


def test_phase3_release_reports_are_aggregate_only_and_atomic(tmp_path: Path) -> None:
    report = audit_phase3_release(_write_fixture(tmp_path, _manifest()), repository_root=tmp_path)

    machine = phase3_release_report_json(report)
    human = phase3_release_report_markdown(report)
    assert json.loads(machine)["manifest_sha256"] == report.manifest_sha256
    assert "Gate: **PASS**" in human
    assert "secret://" not in machine + human
    assert "private-production" not in machine + human
    assert "public-demo-planner-r1" not in machine + human
    assert "requirement:" not in machine + human

    json_output = tmp_path / "artifacts/release.json"
    markdown_output = tmp_path / "artifacts/release.md"
    write_phase3_release_report(report, json_path=json_output, markdown_path=markdown_output)
    assert json_output.read_text(encoding="utf-8") == machine
    assert markdown_output.read_text(encoding="utf-8") == human

    with pytest.raises(ValueError, match="distinct"):
        write_phase3_release_report(report, json_path=json_output, markdown_path=json_output)


def test_phase3_release_cli_writes_machine_and_human_evidence(tmp_path: Path) -> None:
    json_output = tmp_path / "phase3-release.json"
    markdown_output = tmp_path / "phase3-release.md"

    assert (
        phase3_release_main(
            [
                "--manifest",
                str(_write_fixture(tmp_path, _manifest())),
                "--repository-root",
                str(tmp_path),
                "--json-output",
                str(json_output),
                "--markdown-output",
                str(markdown_output),
            ]
        )
        == 0
    )
    assert json.loads(json_output.read_text(encoding="utf-8"))["passed"] is True
    assert "Gate: **PASS**" in markdown_output.read_text(encoding="utf-8")


def test_evaluation_report_interruption_never_publishes_a_partial_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = audit_phase3_release(_write_fixture(tmp_path, _manifest()), repository_root=tmp_path)
    json_output = tmp_path / "artifacts/release.json"
    markdown_output = tmp_path / "artifacts/release.md"

    def interrupted_replace(source: Path, target: Path) -> None:
        del source, target
        raise OSError("simulated publish interruption")

    monkeypatch.setattr(release_reporting.os, "replace", interrupted_replace)

    with pytest.raises(OSError, match="simulated publish interruption"):
        write_phase3_release_report(
            report,
            json_path=json_output,
            markdown_path=markdown_output,
        )

    assert not json_output.exists()
    assert not markdown_output.exists()
    assert list(json_output.parent.glob(".*.tmp")) == []
