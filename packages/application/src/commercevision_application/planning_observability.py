"""Technology-neutral observability seam for planning and human control."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext, suppress
from typing import Literal, Protocol

PlanningObservationStep = Literal[
    "context.build",
    "prompt.resolution",
    "planner",
    "versioning",
    "approval",
    "tool.policy",
    "langgraph.resume",
    "sse",
]


class PlanningObserver(Protocol):
    def observe(
        self,
        *,
        step: PlanningObservationStep,
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
    ) -> AbstractContextManager[None]: ...

    def annotate(
        self,
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
    ) -> None: ...

    def record_context(self, *, outcome: str, clipped_sources: int) -> None: ...

    def record_planner(self, *, outcome: str, latency_ms: float, valid: bool) -> None: ...

    def record_revision(self, *, outcome: str) -> None: ...

    def record_approval(self, *, outcome: str) -> None: ...

    def record_policy(self, *, outcome: str, reason: str) -> None: ...

    def record_human(self, *, outcome: str, wait_seconds: float) -> None: ...

    def record_sse(
        self,
        *,
        outcome: str,
        reconnect: bool,
        active_clients: int,
        lag_seconds: float,
    ) -> None: ...

    def record_resume(self, *, outcome: str) -> None: ...


class NullPlanningObserver:
    def observe(
        self,
        *,
        step: PlanningObservationStep,
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
        del (
            step,
            trace_id,
            workflow_id,
            plan_id,
            plan_version,
            context_hash,
            prompt_revision,
            prompt_revision_id,
            approval_id,
            event_id,
            operation_id,
            policy_id,
        )
        return nullcontext()

    def annotate(self, **values: object) -> None:
        del values

    def record_context(self, *, outcome: str, clipped_sources: int) -> None:
        del outcome, clipped_sources

    def record_planner(self, *, outcome: str, latency_ms: float, valid: bool) -> None:
        del outcome, latency_ms, valid

    def record_revision(self, *, outcome: str) -> None:
        del outcome

    def record_approval(self, *, outcome: str) -> None:
        del outcome

    def record_policy(self, *, outcome: str, reason: str) -> None:
        del outcome, reason

    def record_human(self, *, outcome: str, wait_seconds: float) -> None:
        del outcome, wait_seconds

    def record_sse(
        self,
        *,
        outcome: str,
        reconnect: bool,
        active_clients: int,
        lag_seconds: float,
    ) -> None:
        del outcome, reconnect, active_clients, lag_seconds

    def record_resume(self, *, outcome: str) -> None:
        del outcome


class SafePlanningObserver:
    """Keep optional telemetry failures outside business transaction semantics."""

    def __init__(self, delegate: PlanningObserver) -> None:
        self._delegate = delegate

    @contextmanager
    def observe(
        self,
        *,
        step: PlanningObservationStep,
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
    ) -> Iterator[None]:
        try:
            manager = self._delegate.observe(
                step=step,
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
            )
        except Exception:
            yield
            return
        try:
            manager.__enter__()
        except Exception as error:
            with suppress(Exception):
                manager.__exit__(type(error), error, error.__traceback__)
            yield
            return
        try:
            yield
        except BaseException as error:
            with suppress(Exception):
                manager.__exit__(type(error), error, error.__traceback__)
            raise
        else:
            with suppress(Exception):
                manager.__exit__(None, None, None)

    def annotate(self, **values: object) -> None:
        self._emit(self._delegate.annotate, **values)

    def record_context(self, *, outcome: str, clipped_sources: int) -> None:
        self._emit(
            self._delegate.record_context,
            outcome=outcome,
            clipped_sources=clipped_sources,
        )

    def record_planner(self, *, outcome: str, latency_ms: float, valid: bool) -> None:
        self._emit(
            self._delegate.record_planner,
            outcome=outcome,
            latency_ms=latency_ms,
            valid=valid,
        )

    def record_revision(self, *, outcome: str) -> None:
        self._emit(self._delegate.record_revision, outcome=outcome)

    def record_approval(self, *, outcome: str) -> None:
        self._emit(self._delegate.record_approval, outcome=outcome)

    def record_policy(self, *, outcome: str, reason: str) -> None:
        self._emit(self._delegate.record_policy, outcome=outcome, reason=reason)

    def record_human(self, *, outcome: str, wait_seconds: float) -> None:
        self._emit(
            self._delegate.record_human,
            outcome=outcome,
            wait_seconds=wait_seconds,
        )

    def record_sse(
        self,
        *,
        outcome: str,
        reconnect: bool,
        active_clients: int,
        lag_seconds: float,
    ) -> None:
        self._emit(
            self._delegate.record_sse,
            outcome=outcome,
            reconnect=reconnect,
            active_clients=active_clients,
            lag_seconds=lag_seconds,
        )

    def record_resume(self, *, outcome: str) -> None:
        self._emit(self._delegate.record_resume, outcome=outcome)

    @staticmethod
    def _emit(method: Callable[..., object], **values: object) -> None:
        with suppress(Exception):
            method(**values)
