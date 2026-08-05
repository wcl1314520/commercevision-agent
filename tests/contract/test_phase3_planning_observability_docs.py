from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]


def test_planning_runbook_covers_every_locked_failure_mode() -> None:
    runbook = (ROOT / "docs/runbooks/planning-observability.md").read_text(encoding="utf-8")
    required_sections = (
        "## Stuck planning",
        "## Invalid Planner output",
        "## Stale approval",
        "## Repeated rejection",
        "## Resume mismatch",
        "## Tool Policy denial surge",
        "## SSE lag or reconnect storm",
        "## Retention expiry",
    )
    assert all(section in runbook for section in required_sections)
    for marker in ("Signal", "Containment", "Recovery", "Recovery proof", "Escalate"):
        assert runbook.count(marker) >= len(required_sections)


def test_planning_observability_guide_locks_metrics_alerts_labels_and_redaction() -> None:
    guide = (ROOT / "docs/05-deployment/observability-and-operations.md").read_text(
        encoding="utf-8"
    )
    for metric in (
        "commercevision.phase3.planning.context.clipped_sources",
        "commercevision.phase3.planning.planner.validity",
        "commercevision.phase3.planning.planner.duration",
        "commercevision.phase3.planning.revisions",
        "commercevision.phase3.planning.approvals.stale",
        "commercevision.phase3.planning.policy.denials",
        "commercevision.phase3.planning.human.wait",
        "commercevision.phase3.planning.human.confirmations",
        "commercevision.phase3.planning.sse.clients",
        "commercevision.phase3.planning.sse.reconnects",
        "commercevision.phase3.planning.sse.lag",
        "commercevision.phase3.planning.resume.failures",
    ):
        assert metric in guide
    for phrase in (
        "bounded-cardinality",
        "Raw Creative Plan",
        "Prompt",
        "Planning Context",
        "Provider payload",
        "arbitrary user text",
        "sensitive Citation",
        "planning-observability.md",
        "SSE 不是 readiness",
    ):
        assert phrase in guide
    assert "5 分钟" in guide
    assert "P95" in guide


def test_local_compose_exports_otlp_and_prometheus_without_collector_secrets() -> None:
    compose = yaml.safe_load(
        (ROOT / "infra/compose/docker-compose.yml").read_text(encoding="utf-8")
    )
    collector = compose["services"]["otel-collector"]
    assert collector.get("environment") in (None, {})
    assert any(":9464" in port for port in collector["ports"])
    for service_name in ("api", "worker"):
        environment = compose["services"][service_name]["environment"]
        assert environment["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://otel-collector:4318"

    config = (ROOT / "infra/otel/otel-collector-config.yaml").read_text(encoding="utf-8")
    assert "prometheus:" in config
    assert "debug:" in config
    assert "authorization" not in config.lower()
    assert "password" not in config.lower()
