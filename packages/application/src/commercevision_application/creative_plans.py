"""Application authority for appending immutable Creative Plan versions."""

from __future__ import annotations

from dataclasses import dataclass

from commercevision_domain import (
    CreativePlanHead,
    CreativePlanSource,
    CreativePlanVersion,
    InvalidTransitionError,
    NotFoundError,
    RetentionStatus,
    WorkflowStatus,
)

from .creative_plan_ports import CreativePlanUnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class CreativePlanWriteResult:
    head: CreativePlanHead
    version: CreativePlanVersion


class CreativePlanApplicationService:
    """Fence a version against current Workflow authority before persistence."""

    def __init__(self, unit_of_work_factory: CreativePlanUnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def append_version(
        self,
        *,
        version: CreativePlanVersion,
        expected_workflow_version: int,
        expected_head_version: int,
    ) -> CreativePlanWriteResult:
        with self._unit_of_work_factory() as unit_of_work:
            workflow = unit_of_work.workflows.get(
                version.workflow_id,
                workspace_id=version.workspace_id,
                for_update=True,
            )
            if workflow is None:
                raise NotFoundError("Workflow does not exist")
            workflow.assert_version(expected_workflow_version)
            now = unit_of_work.database_now()
            if (
                workflow.retention_status is not RetentionStatus.ACTIVE
                or now >= workflow.expires_at
            ):
                raise InvalidTransitionError("Workflow retention is not active")
            planning_write = (
                workflow.status is WorkflowStatus.PLANNING
                and workflow.current_node == "create_plan"
            )
            review_edit = (
                version.source is CreativePlanSource.USER
                and workflow.status is WorkflowStatus.AWAITING_PLAN_APPROVAL
                and workflow.current_node == "approve_plan"
            )
            if not (planning_write or review_edit):
                raise InvalidTransitionError("Workflow is not accepting Creative Plan versions")
            head = unit_of_work.creative_plans.append_version(
                version,
                expected_head_version=expected_head_version,
                retain_until=workflow.expires_at,
                authorized_at=now,
            )
            unit_of_work.commit()
        return CreativePlanWriteResult(head=head, version=version)

    def get_current(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
    ) -> CreativePlanWriteResult:
        with self._unit_of_work_factory() as unit_of_work:
            current = unit_of_work.creative_plans.get_current(
                workspace_id=workspace_id,
                workflow_id=workflow_id,
                creative_plan_id=creative_plan_id,
            )
            if current is None:
                raise NotFoundError("Creative Plan does not exist")
            head, version = current
        return CreativePlanWriteResult(head=head, version=version)

    def get_version(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
        version_number: int,
    ) -> CreativePlanVersion:
        with self._unit_of_work_factory() as unit_of_work:
            version = unit_of_work.creative_plans.get_version_by_number(
                workspace_id=workspace_id,
                workflow_id=workflow_id,
                creative_plan_id=creative_plan_id,
                version_number=version_number,
            )
            if version is None:
                raise NotFoundError("Creative Plan version does not exist")
        return version

    def list_versions(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
    ) -> tuple[CreativePlanVersion, ...]:
        with self._unit_of_work_factory() as unit_of_work:
            versions = unit_of_work.creative_plans.list_versions(
                workspace_id=workspace_id,
                workflow_id=workflow_id,
                creative_plan_id=creative_plan_id,
            )
            if versions is None:
                raise NotFoundError("Creative Plan does not exist")
        return versions
