"""Sanitized, bounded-cardinality telemetry for the Phase 3 planning lifecycle."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, fields
from enum import StrEnum
from typing import cast

from opentelemetry import metrics, trace
from opentelemetry.metrics import Meter
from opentelemetry.trace import Status, StatusCode, Tracer

from ._safe_telemetry import StructuredLogger, bounded_dimension, safe_token
from .logging import get_logger

_CONTEXT_OUTCOMES = frozenset({"complete", "clipped"})
_PLANNER_OUTCOMES = frozenset({"valid", "invalid"})
_REVISION_OUTCOMES = frozenset({"created", "revised"})
_APPROVAL_OUTCOMES = frozenset({"approve", "reject", "regenerate", "stale"})
_POLICY_OUTCOMES = frozenset({"allowed", "denied"})
_POLICY_REASONS = frozenset(
    {
        "ALLOWED",
        "REGISTRY_DENIED",
        "INVALID_ARGUMENTS",
        "RIGHTS_DENIED",
        "RESOURCE_DENIED",
        "PROVIDER_DENIED",
        "COST_CLASS_DENIED",
        "INTENT_LIMIT_EXCEEDED",
        "QUOTA_EXCEEDED",
        "BUDGET_EXCEEDED",
    }
)
_HUMAN_OUTCOMES = frozenset({"approve", "reject", "regenerate", "confirmed"})
_SSE_OUTCOMES = frozenset({"connected", "emitted", "disconnected"})
_RESUME_OUTCOMES = frozenset(
    {"succeeded", "checkpoint_mismatch", "contract_mismatch", "execution_failed"}
)


def _enum_dimension(name: str, value: str, allowed: frozenset[str]) -> str:
    normalized = bounded_dimension(name, value)
    if normalized not in allowed:
        raise ValueError(f"telemetry {name} is not an allowed bounded value")
    return cast(str, normalized)


class PlanningSpan(StrEnum):
    CONTEXT_BUILD = "context.build"
    PROMPT_RESOLUTION = "prompt.resolution"
    PLANNER = "planner"
    VERSIONING = "versioning"
    APPROVAL = "approval"
    TOOL_POLICY = "tool.policy"
    LANGGRAPH_RESUME = "langgraph.resume"
    SSE = "sse"


@dataclass(frozen=True, slots=True)
class PlanningTelemetryIdentity:
    """Planning correlation fields allowed in spans and logs, never metric labels."""

    trace_id: str | None = None
    workflow_id: str | None = None
    plan_id: str | None = None
    plan_version: int | None = None
    context_hash: str | None = None
    prompt_revision: str | None = None
    prompt_revision_id: str | None = None
    approval_id: str | None = None
    event_id: str | None = None
    operation_id: str | None = None
    policy_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "workflow_id",
            "plan_id",
            "prompt_revision",
            "prompt_revision_id",
            "approval_id",
            "event_id",
            "policy_id",
        ):
            object.__setattr__(self, name, safe_token(getattr(self, name)))
        object.__setattr__(self, "trace_id", safe_token(self.trace_id, always_hash=True))
        object.__setattr__(
            self,
            "operation_id",
            safe_token(self.operation_id, always_hash=True),
        )
        if self.context_hash is not None and (
            not isinstance(self.context_hash, str)
            or not (
                len(self.context_hash) == 64
                and all(character in "0123456789abcdef" for character in self.context_hash)
            )
        ):
            raise ValueError("telemetry context hash must be lowercase SHA-256")
        if self.plan_version is not None and (
            not isinstance(self.plan_version, int)
            or isinstance(self.plan_version, bool)
            or not 1 <= self.plan_version <= 1_000_000
        ):
            raise ValueError("telemetry plan version must be a bounded positive integer")

    def attributes(self) -> dict[str, str | int]:
        names = {
            "trace_id": "commercevision.request.trace_id",
            "workflow_id": "commercevision.workflow.id",
            "plan_id": "commercevision.plan.id",
            "plan_version": "commercevision.plan.version",
            "context_hash": "commercevision.context.sha256",
            "prompt_revision": "commercevision.prompt.revision",
            "prompt_revision_id": "commercevision.prompt.revision_id",
            "approval_id": "commercevision.approval.id",
            "event_id": "commercevision.event.id",
            "operation_id": "commercevision.operation.id",
            "policy_id": "commercevision.policy.id",
        }
        return {
            names[field.name]: value
            for field in fields(self)
            if (value := getattr(self, field.name)) is not None
        }

    def log_fields(self) -> dict[str, str | int]:
        return {
            ("request_trace_id" if field.name == "trace_id" else field.name): value
            for field in fields(self)
            if (value := getattr(self, field.name)) is not None
        }


class PlanningTelemetry:
    """Public safe emission surface for planning and human-control processes."""

    def __init__(
        self,
        *,
        logger: StructuredLogger | None = None,
        tracer: Tracer | None = None,
        meter: Meter | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._logger = logger or get_logger("commercevision.phase3.planning")
        self._tracer = tracer or trace.get_tracer("commercevision.phase3.planning")
        resolved_meter = meter or metrics.get_meter("commercevision.phase3.planning")
        self._monotonic = monotonic or time.monotonic
        prefix = "commercevision.phase3.planning"
        self._context_clipped = resolved_meter.create_histogram(
            f"{prefix}.context.clipped_sources", unit="{source}"
        )
        self._planner_validity = resolved_meter.create_counter(
            f"{prefix}.planner.validity", unit="{result}"
        )
        self._planner_duration = resolved_meter.create_histogram(
            f"{prefix}.planner.duration", unit="ms"
        )
        self._revisions = resolved_meter.create_counter(f"{prefix}.revisions", unit="{revision}")
        self._stale_approvals = resolved_meter.create_counter(
            f"{prefix}.approvals.stale", unit="{approval}"
        )
        self._policy_denials = resolved_meter.create_counter(
            f"{prefix}.policy.denials", unit="{denial}"
        )
        self._human_wait = resolved_meter.create_histogram(f"{prefix}.human.wait", unit="s")
        self._human_confirmations = resolved_meter.create_counter(
            f"{prefix}.human.confirmations", unit="{confirmation}"
        )
        self._sse_clients = resolved_meter.create_histogram(
            f"{prefix}.sse.clients", unit="{client}"
        )
        self._sse_reconnects = resolved_meter.create_counter(
            f"{prefix}.sse.reconnects", unit="{reconnect}"
        )
        self._sse_lag = resolved_meter.create_histogram(f"{prefix}.sse.lag", unit="s")
        self._resume_failures = resolved_meter.create_counter(
            f"{prefix}.resume.failures", unit="{failure}"
        )

    @contextmanager
    def span(
        self,
        name: PlanningSpan,
        *,
        identity: PlanningTelemetryIdentity | None = None,
    ) -> Iterator[None]:
        identity = identity or PlanningTelemetryIdentity()
        started = self._monotonic()
        event = name.value.replace(".", "_")
        with self._tracer.start_as_current_span(
            f"commercevision.phase3.planning.{name.value}",
            attributes=identity.attributes(),
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            self._logger.info(
                f"planning_{event}_started",
                **identity.log_fields(),
                **self._trace_fields(),
            )
            try:
                yield
            except Exception as exc:
                error_type = bounded_dimension("error_class", type(exc).__name__)
                assert error_type is not None
                span.set_attributes(
                    {
                        "error.type": error_type,
                        "commercevision.error.code": "UNCLASSIFIED",
                        "commercevision.error.category": "internal",
                        "commercevision.error.retryable": False,
                    }
                )
                span.set_status(Status(StatusCode.ERROR, "UNCLASSIFIED"))
                self._logger.error(
                    f"planning_{event}_failed",
                    **identity.log_fields(),
                    error_code="UNCLASSIFIED",
                    error_category="internal",
                    error_type=error_type,
                    retryable=False,
                    **self._trace_fields(),
                )
                raise
            else:
                span.set_status(Status(StatusCode.OK))
                self._logger.info(
                    f"planning_{event}_completed",
                    **identity.log_fields(),
                    duration_ms=round(max(0.0, self._monotonic() - started) * 1000, 3),
                    **self._trace_fields(),
                )

    @staticmethod
    def _trace_fields() -> dict[str, str]:
        context = trace.get_current_span().get_span_context()
        if not context.is_valid:
            return {}
        return {
            "trace_id": f"{context.trace_id:032x}",
            "span_id": f"{context.span_id:016x}",
        }

    def observe(
        self,
        *,
        step: str,
        trace_id: str | None = None,
        workflow_id: str | None = None,
        plan_id: str | None = None,
        plan_version: int | None = None,
        context_hash: str | None = None,
        prompt_revision: str | None = None,
        prompt_revision_id: str | None = None,
        approval_id: str | None = None,
        event_id: str | None = None,
        operation_id: str | None = None,
        policy_id: str | None = None,
    ) -> AbstractContextManager[None]:
        return self.span(
            PlanningSpan(step),
            identity=PlanningTelemetryIdentity(
                trace_id=trace_id,
                workflow_id=workflow_id,
                plan_id=plan_id,
                plan_version=plan_version,
                context_hash=context_hash,
                prompt_revision=prompt_revision,
                prompt_revision_id=prompt_revision_id,
                approval_id=approval_id,
                event_id=event_id,
                operation_id=operation_id,
                policy_id=policy_id,
            ),
        )

    @staticmethod
    def annotate(
        *,
        workflow_id: str | None = None,
        plan_id: str | None = None,
        plan_version: int | None = None,
        context_hash: str | None = None,
        prompt_revision: str | None = None,
        prompt_revision_id: str | None = None,
        approval_id: str | None = None,
        event_id: str | None = None,
        operation_id: str | None = None,
        policy_id: str | None = None,
    ) -> None:
        identity = PlanningTelemetryIdentity(
            workflow_id=workflow_id,
            plan_id=plan_id,
            plan_version=plan_version,
            context_hash=context_hash,
            prompt_revision=prompt_revision,
            prompt_revision_id=prompt_revision_id,
            approval_id=approval_id,
            event_id=event_id,
            operation_id=operation_id,
            policy_id=policy_id,
        )
        trace.get_current_span().set_attributes(identity.attributes())

    def record_context(self, *, outcome: str, clipped_sources: int) -> None:
        self._context_clipped.record(
            max(0, clipped_sources),
            {"outcome": _enum_dimension("outcome", outcome, _CONTEXT_OUTCOMES)},
        )

    def record_planner(self, *, outcome: str, latency_ms: float, valid: bool) -> None:
        attributes = {"outcome": _enum_dimension("outcome", outcome, _PLANNER_OUTCOMES)}
        self._planner_validity.add(1, {**attributes, "valid": valid})
        self._planner_duration.record(max(0.0, latency_ms), attributes)

    def record_revision(self, *, outcome: str) -> None:
        self._revisions.add(
            1,
            {"outcome": _enum_dimension("outcome", outcome, _REVISION_OUTCOMES)},
        )

    def record_approval(self, *, outcome: str) -> None:
        normalized = _enum_dimension("outcome", outcome, _APPROVAL_OUTCOMES)
        if normalized == "stale":
            self._stale_approvals.add(1, {"outcome": normalized})

    def record_policy(self, *, outcome: str, reason: str) -> None:
        normalized = _enum_dimension("outcome", outcome, _POLICY_OUTCOMES)
        if normalized == "denied":
            self._policy_denials.add(
                1,
                {
                    "outcome": normalized,
                    "reason": _enum_dimension("reason", reason, _POLICY_REASONS),
                },
            )

    def record_human(self, *, outcome: str, wait_seconds: float) -> None:
        attributes = {"outcome": _enum_dimension("outcome", outcome, _HUMAN_OUTCOMES)}
        self._human_wait.record(max(0.0, wait_seconds), attributes)
        self._human_confirmations.add(1, attributes)

    def record_sse(
        self,
        *,
        outcome: str,
        reconnect: bool,
        active_clients: int,
        lag_seconds: float,
    ) -> None:
        attributes = {"outcome": _enum_dimension("outcome", outcome, _SSE_OUTCOMES)}
        self._sse_clients.record(max(0, active_clients), attributes)
        self._sse_lag.record(max(0.0, lag_seconds), attributes)
        if reconnect:
            self._sse_reconnects.add(1, attributes)

    def record_resume(self, *, outcome: str) -> None:
        normalized = _enum_dimension("outcome", outcome, _RESUME_OUTCOMES)
        if normalized != "succeeded":
            self._resume_failures.add(1, {"outcome": normalized})
