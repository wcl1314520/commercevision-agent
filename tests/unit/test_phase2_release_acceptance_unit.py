from __future__ import annotations

import json
from pathlib import Path

import pytest
from commercevision_evaluation import (
    audit_phase2_release,
    phase2_release_report_json,
    phase2_release_report_markdown,
    write_phase2_release_report,
)

REQUIREMENTS = (
    "browser-e2e",
    "fault-injection",
    "recovery-convergence",
    "migration-paths",
    "compose-health",
    "quality-security-supply-chain",
    "public-demo-isolation",
    "metadata-phase",
    "documentation-alignment",
    "retrieval-safety",
)
FAULTS = (
    "minio",
    "milvus",
    "rabbitmq",
    "clamav",
    "content-safety",
    "vision",
    "embedding",
    "reranker",
    "worker",
    "rebuild",
)
INVARIANTS = (
    "unique-logical-operation",
    "unique-vector",
    "zero-unauthorized-return",
    "no-retention-extension",
    "eventual-convergence",
    "incremental-indexing",
    "rebuild-recovery",
    "product-brief-restart",
)
CI_GATES = (
    "python",
    "web",
    "openapi",
    "mcp",
    "providers",
    "real-infrastructure",
    "e2e",
    "evaluation",
    "security",
    "dependencies",
    "containers",
    "licenses",
    "sbom",
)


def _evidence(anchor: str) -> dict[str, str]:
    return {"path": "evidence.txt", "anchor": anchor}


def _manifest() -> dict[str, object]:
    return {
        "schema_version": "commercevision.phase2-release-acceptance.v1",
        "release_id": "phase2-v1",
        "phase": "phase-2",
        "private_boundaries": {
            "workspace_ids": ["private-production"],
            "bucket_names": ["foundation-assets", "task-assets"],
            "credential_scopes": ["secret://commercevision/private/providers"],
            "dataset_paths": ["private/evaluation/hidden-release"],
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
            "object_prefix": "phase2/retrieval-daily-v1/",
            "credential_scopes": ["secret://commercevision/public-demo/providers"],
            "quotas": {
                "requests_per_minute": 60,
                "concurrent_operations": 4,
                "provider_calls_per_day": 500,
                "storage_bytes": 1_073_741_824,
            },
            "datasets": [
                {
                    "dataset_id": "retrieval-daily-v1",
                    "version": "v1",
                    "license": "CC0-1.0",
                    "path": "evaluation/retrieval/daily-v1/manifest.json",
                    "public_demo_allowed": True,
                }
            ],
        },
        "requirements": [
            {"id": requirement, "evidence": [_evidence(f"requirement:{requirement}")]}
            for requirement in REQUIREMENTS
        ],
        "fault_injection": [
            {"component": component, "evidence": _evidence(f"fault:{component}")}
            for component in FAULTS
        ],
        "recovery_invariants": [
            {"id": invariant, "evidence": _evidence(f"invariant:{invariant}")}
            for invariant in INVARIANTS
        ],
        "ci_gates": [{"id": gate, "evidence": _evidence(f"gate:{gate}")} for gate in CI_GATES],
    }


def _write_fixture(root: Path, manifest: dict[str, object]) -> Path:
    anchors = [
        *(f"requirement:{value}" for value in REQUIREMENTS),
        *(f"fault:{value}" for value in FAULTS),
        *(f"invariant:{value}" for value in INVARIANTS),
        *(f"gate:{value}" for value in CI_GATES),
    ]
    (root / "evidence.txt").write_text("\n".join(anchors), encoding="utf-8")
    dataset = root / "evaluation/retrieval/daily-v1/manifest.json"
    dataset.parent.mkdir(parents=True)
    dataset.write_text("{}\n", encoding="utf-8")
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_phase2_release_audit_accepts_complete_isolated_evidence(tmp_path: Path) -> None:
    report = audit_phase2_release(_write_fixture(tmp_path, _manifest()), repository_root=tmp_path)

    assert report.phase == "phase-2"
    assert report.passed is True
    assert report.requirement_ids == REQUIREMENTS
    assert report.fault_components == FAULTS
    assert report.recovery_invariant_ids == INVARIANTS
    assert report.ci_gate_ids == CI_GATES
    assert len(report.evidence) == 41
    assert all(item.sha256 and item.path == "evidence.txt" for item in report.evidence)


@pytest.mark.parametrize(
    ("boundary", "value"),
    [
        ("workspace_ids", "catalog-demo"),
        ("bucket_names", "public-demo-task-assets"),
        ("credential_scopes", "secret://commercevision/public-demo/providers"),
        ("dataset_paths", "evaluation/retrieval/daily-v1/manifest.json"),
    ],
)
def test_phase2_release_audit_rejects_public_private_overlap(
    tmp_path: Path,
    boundary: str,
    value: str,
) -> None:
    manifest = _manifest()
    private = manifest["private_boundaries"]
    assert isinstance(private, dict)
    private[boundary] = [value]

    with pytest.raises(ValueError, match="public-demo .* overlaps private"):
        audit_phase2_release(_write_fixture(tmp_path, manifest), repository_root=tmp_path)


def test_phase2_release_audit_rejects_path_escape_before_reading_evidence(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    requirements = manifest["requirements"]
    assert isinstance(requirements, list)
    first = requirements[0]
    assert isinstance(first, dict)
    first["evidence"] = [{"path": "../outside.txt", "anchor": "ignored"}]

    with pytest.raises(ValueError, match="must remain inside the repository"):
        audit_phase2_release(_write_fixture(tmp_path, manifest), repository_root=tmp_path)


def test_phase2_release_reports_are_aggregate_only_and_atomic(tmp_path: Path) -> None:
    report = audit_phase2_release(_write_fixture(tmp_path, _manifest()), repository_root=tmp_path)

    machine = phase2_release_report_json(report)
    human = phase2_release_report_markdown(report)
    assert json.loads(machine)["manifest_sha256"] == report.manifest_sha256
    assert "Gate: **PASS**" in human
    assert "secret://" not in machine + human
    assert "private-production" not in machine + human

    json_output = tmp_path / "artifacts/release.json"
    markdown_output = tmp_path / "artifacts/release.md"
    write_phase2_release_report(
        report,
        json_path=json_output,
        markdown_path=markdown_output,
    )
    assert json_output.read_text(encoding="utf-8") == machine
    assert markdown_output.read_text(encoding="utf-8") == human

    with pytest.raises(ValueError, match="distinct"):
        write_phase2_release_report(
            report,
            json_path=json_output,
            markdown_path=json_output,
        )
