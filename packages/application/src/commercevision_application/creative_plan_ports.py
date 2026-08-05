"""Narrow persistence seams for immutable Creative Plan versions and their head."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from commercevision_domain import CreativePlanHead, CreativePlanVersion

from .ports import WorkflowRepositoryPort


class CreativePlanRepositoryPort(Protocol):
    def append_version(
        self,
        version: CreativePlanVersion,
        *,
        expected_head_version: int,
        retain_until: datetime,
        authorized_at: datetime,
    ) -> CreativePlanHead: ...

    def get_current(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
    ) -> tuple[CreativePlanHead, CreativePlanVersion] | None: ...

    def get_version_by_number(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
        version_number: int,
    ) -> CreativePlanVersion | None: ...

    def list_versions(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
    ) -> tuple[CreativePlanVersion, ...] | None: ...


class CreativePlanUnitOfWorkPort(Protocol):
    workflows: WorkflowRepositoryPort
    creative_plans: CreativePlanRepositoryPort

    def __enter__(self) -> CreativePlanUnitOfWorkPort: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None: ...

    def database_now(self) -> datetime: ...

    def commit(self) -> None: ...


CreativePlanUnitOfWorkFactory = Callable[[], CreativePlanUnitOfWorkPort]
