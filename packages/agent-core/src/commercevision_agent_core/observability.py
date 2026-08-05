"""Technology-neutral telemetry seam for durable graph resume."""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import Literal, Protocol


class AgentRuntimeObserver(Protocol):
    def observe(
        self,
        *,
        step: Literal["langgraph.resume"],
        trace_id: str | None = None,
        workflow_id: str | None = None,
        plan_id: str | None = None,
        plan_version: int | None = None,
        approval_id: str | None = None,
    ) -> AbstractContextManager[None]: ...

    def record_resume(self, *, outcome: str) -> None: ...


class NullAgentRuntimeObserver:
    def observe(self, **values: object) -> AbstractContextManager[None]:
        del values
        return nullcontext()

    def record_resume(self, *, outcome: str) -> None:
        del outcome
