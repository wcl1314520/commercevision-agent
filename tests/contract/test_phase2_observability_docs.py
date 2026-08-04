from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_phase2_incident_runbook_covers_every_required_failure_mode() -> None:
    runbook = (ROOT / "docs/runbooks/phase2-observability.md").read_text(encoding="utf-8")

    required_sections = (
        "## Stuck quarantine",
        "## ClamAV outage",
        "## Content safety outage",
        "## Provider throttling",
        "## Index lag",
        "## Stale vectors",
        "## Milvus loss",
        "## Deletion backlog",
        "## DLQ replay",
        "## Rebuild failure",
        "## Readiness contract",
        "## Data safety",
    )
    assert all(section in runbook for section in required_sections)
    for marker in ("Signal", "Containment", "Recovery proof", "Escalate"):
        assert runbook.count(marker) >= 10


def test_observability_guide_publishes_local_metrics_and_redaction_contract() -> None:
    guide = (ROOT / "docs/05-deployment/observability-and-operations.md").read_text(
        encoding="utf-8"
    )

    assert "http://127.0.0.1:19464/metrics" in guide
    assert "commercevision.phase2.operation.dlq" in guide
    assert "provider_request_id" in guide
    assert "签名 URL" in guide
    assert "phase2-observability.md" in guide
