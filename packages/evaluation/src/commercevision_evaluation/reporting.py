"""Safe machine and human report rendering for retrieval evaluation."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from .models import EvaluationMetrics, RetrievalEvaluationReport
from .release_acceptance import Phase2ReleaseReport, Phase3ReleaseReport


def retrieval_report_json(report: RetrievalEvaluationReport) -> str:
    """Serialize aggregate-only evidence without candidate or asset payloads."""

    return json.dumps(asdict(report), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def phase2_release_report_json(report: Phase2ReleaseReport) -> str:
    """Serialize evidence hashes and gate identities without deployment secrets."""

    return json.dumps(asdict(report), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def phase2_release_report_markdown(report: Phase2ReleaseReport) -> str:
    """Render a concise human-readable Phase 2 release audit."""

    status = "PASS" if report.passed else "FAIL"
    lines = [
        "# Phase 2 release acceptance report",
        "",
        f"- Gate: **{status}**",
        f"- Release: `{report.release_id}`",
        f"- Phase: `{report.phase}`",
        f"- Manifest SHA-256: `{report.manifest_sha256}`",
        f"- Evidence records: `{len(report.evidence)}`",
        "",
    ]
    for heading, values in (
        ("Requirements", report.requirement_ids),
        ("Fault injection", report.fault_components),
        ("Recovery invariants", report.recovery_invariant_ids),
        ("CI gates", report.ci_gate_ids),
    ):
        lines.extend([f"## {heading}", "", *(f"- `{value}`" for value in values), ""])
    lines.extend(
        [
            "## Public demo isolation",
            "",
            f"- Workspaces: `{len(report.public_demo_workspace_ids)}`",
            f"- Dedicated buckets: `{len(report.public_demo_bucket_names)}`",
            f"- Authorized datasets: `{len(report.public_demo_dataset_ids)}`",
            "",
            "## Evidence integrity",
            "",
            "| Path | SHA-256 |",
            "|---|---|",
        ]
    )
    unique_evidence = {(item.path, item.sha256) for item in report.evidence}
    lines.extend(f"| `{path}` | `{digest}` |" for path, digest in sorted(unique_evidence))
    return "\n".join(lines).rstrip() + "\n"


def phase3_release_report_json(report: Phase3ReleaseReport) -> str:
    """Serialize aggregate Phase 3 proof without deployment or Prompt identities."""

    payload = {
        "schema_version": report.schema_version,
        "release_id": report.release_id,
        "phase": report.phase,
        "manifest_sha256": report.manifest_sha256,
        "passed": report.passed,
        "requirement_ids": report.requirement_ids,
        "fault_components": report.fault_components,
        "recovery_invariant_ids": report.recovery_invariant_ids,
        "ci_gate_ids": report.ci_gate_ids,
        "public_demo": {
            "workspace_count": len(report.public_demo_workspace_ids),
            "bucket_count": len(report.public_demo_bucket_names),
            "dataset_count": len(report.public_demo_dataset_ids),
            "prompt_revision_count": len(report.public_demo_prompt_revision_ids),
            "cursor_signing_scope_count": len(report.public_demo_cursor_signing_scopes),
        },
        "evidence": tuple(
            {"path": path, "sha256": digest}
            for path, digest in sorted({(item.path, item.sha256) for item in report.evidence})
        ),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def phase3_release_report_markdown(report: Phase3ReleaseReport) -> str:
    """Render concise Phase 3 proof with counts instead of deployment identities."""

    status = "PASS" if report.passed else "FAIL"
    lines = [
        "# Phase 3 release acceptance report",
        "",
        f"- Gate: **{status}**",
        f"- Release: `{report.release_id}`",
        f"- Phase: `{report.phase}`",
        f"- Manifest SHA-256: `{report.manifest_sha256}`",
        f"- Evidence records: `{len(report.evidence)}`",
        "",
    ]
    for heading, values in (
        ("Requirements", report.requirement_ids),
        ("Fault injection", report.fault_components),
        ("Recovery invariants", report.recovery_invariant_ids),
        ("CI gates", report.ci_gate_ids),
    ):
        lines.extend([f"## {heading}", "", *(f"- `{value}`" for value in values), ""])
    lines.extend(
        [
            "## Public demo isolation",
            "",
            f"- Workspaces: `{len(report.public_demo_workspace_ids)}`",
            f"- Dedicated buckets: `{len(report.public_demo_bucket_names)}`",
            f"- Authorized datasets: `{len(report.public_demo_dataset_ids)}`",
            f"- Pinned Prompt revisions: `{len(report.public_demo_prompt_revision_ids)}`",
            f"- Cursor signing scopes: `{len(report.public_demo_cursor_signing_scopes)}`",
            "",
            "## Evidence integrity",
            "",
            "| Path | SHA-256 |",
            "|---|---|",
        ]
    )
    unique_evidence = {(item.path, item.sha256) for item in report.evidence}
    lines.extend(f"| `{path}` | `{digest}` |" for path, digest in sorted(unique_evidence))
    return "\n".join(lines).rstrip() + "\n"


def _metric_table(metrics: EvaluationMetrics) -> list[str]:
    rows = [
        "| K | Recall | Precision | nDCG | ANN recall | Unauthorized recall |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    rows.extend(
        f"| {k} | {metrics.recall_at[k]:.6f} | {metrics.precision_at[k]:.6f} | "
        f"{metrics.ndcg_at[k]:.6f} | {metrics.ann_recall_at[k]:.6f} | "
        f"{metrics.unauthorized_recall_at[k]:.6f} |"
        for k in sorted(metrics.recall_at)
    )
    return rows


def retrieval_report_markdown(report: RetrievalEvaluationReport) -> str:
    """Render human-readable aggregate evidence without candidate payloads."""

    identity = report.identity
    status = "PASS" if report.gate.passed else "FAIL"
    lines = [
        "# Retrieval evaluation report",
        "",
        f"- Gate: **{status}**",
        f"- Suite: `{identity.suite_version}`",
        f"- Profile / split: `{identity.profile}` / `{identity.split}`",
        f"- Retrieval policy: `{identity.retrieval_policy_version}`",
        f"- Embedding model: `{identity.embedding_model_version}`",
        f"- Collection: `{identity.collection_version}`",
        f"- Candidate universe: `{identity.candidate_universe_version}`",
        f"- Rights snapshot: `{identity.rights_snapshot_version}`",
        f"- Manifest SHA-256: `{identity.manifest_sha256}`",
        f"- Observations SHA-256: `{identity.observations_sha256}`",
        f"- Threshold basis: `{report.thresholds.basis}`",
        f"- Bootstrap: `{report.bootstrap.samples}` samples, seed `{report.bootstrap.seed}`",
        "",
        "## Overall",
        "",
        *_metric_table(report.overall),
        "",
        f"- MRR: `{report.overall.mrr:.6f}`",
        f"- P50 latency: `{report.overall.p50_latency_ms:.3f} ms`",
        f"- P95 latency: `{report.overall.p95_latency_ms:.3f} ms`",
        f"- Unauthorized return count: `{report.overall.unauthorized_return_count}`",
        "- Queries with unauthorized results: "
        f"`{report.overall.queries_with_unauthorized_results}`",
        "",
        f"## Bootstrap {report.confidence_intervals.confidence_level:.0%} confidence intervals",
        "",
        "| Metric | Estimate | Lower | Upper |",
        "|---|---:|---:|---:|",
    ]
    for k in sorted(report.confidence_intervals.recall_at):
        interval = report.confidence_intervals.recall_at[k]
        lines.append(
            f"| Recall@{k} | {interval.estimate:.6f} | {interval.lower:.6f} | "
            f"{interval.upper:.6f} |"
        )
        interval = report.confidence_intervals.ndcg_at[k]
        lines.append(
            f"| nDCG@{k} | {interval.estimate:.6f} | {interval.lower:.6f} | {interval.upper:.6f} |"
        )
    for name, interval in (
        ("MRR", report.confidence_intervals.mrr),
        ("P50 latency ms", report.confidence_intervals.p50_latency_ms),
        ("P95 latency ms", report.confidence_intervals.p95_latency_ms),
    ):
        lines.append(
            f"| {name} | {interval.estimate:.6f} | {interval.lower:.6f} | {interval.upper:.6f} |"
        )
    for heading, breakdown in (
        ("Category breakdown", report.per_category),
        ("Vector-kind breakdown", report.per_vector_kind),
    ):
        lines.extend(["", f"## {heading}", ""])
        for name, metrics in breakdown.items():
            lines.extend(
                [
                    f"### {name}",
                    "",
                    *_metric_table(metrics),
                    "",
                    f"MRR `{metrics.mrr:.6f}` · P95 `{metrics.p95_latency_ms:.3f} ms` · "
                    f"unauthorized returns `{metrics.unauthorized_return_count}`",
                    "",
                ]
            )
    if report.gate.failures:
        lines.extend(["## Gate failures", ""])
        lines.extend(f"- `{failure}`" for failure in report.gate.failures)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def write_retrieval_report(
    report: RetrievalEvaluationReport,
    *,
    json_path: str | Path,
    markdown_path: str | Path,
) -> None:
    json_output = Path(json_path).resolve()
    markdown_output = Path(markdown_path).resolve()
    if json_output == markdown_output:
        raise ValueError("evaluation report output paths must be distinct")
    _atomic_write(json_output, retrieval_report_json(report))
    _atomic_write(markdown_output, retrieval_report_markdown(report))


def write_phase2_release_report(
    report: Phase2ReleaseReport,
    *,
    json_path: str | Path,
    markdown_path: str | Path,
) -> None:
    json_output = Path(json_path).resolve()
    markdown_output = Path(markdown_path).resolve()
    if json_output == markdown_output:
        raise ValueError("release report output paths must be distinct")
    _atomic_write(json_output, phase2_release_report_json(report))
    _atomic_write(markdown_output, phase2_release_report_markdown(report))


def write_phase3_release_report(
    report: Phase3ReleaseReport,
    *,
    json_path: str | Path,
    markdown_path: str | Path,
) -> None:
    json_output = Path(json_path).resolve()
    markdown_output = Path(markdown_path).resolve()
    if json_output == markdown_output:
        raise ValueError("release report output paths must be distinct")
    _atomic_write(json_output, phase3_release_report_json(report))
    _atomic_write(markdown_output, phase3_release_report_markdown(report))
