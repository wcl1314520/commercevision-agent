from __future__ import annotations

from datetime import UTC, datetime, timedelta

from commercevision_application import CreativePlanApplicationService
from commercevision_domain import (
    CreativePlanDirection,
    CreativePlanHead,
    CreativePlanPayload,
    CreativePlanProvenance,
    CreativePlanSource,
    CreativePlanVersion,
    ImageRole,
    RetentionStatus,
    Workflow,
    WorkflowStatus,
)

NOW = datetime(2026, 8, 5, 4, 0, tzinfo=UTC)
WORKFLOW_DEADLINE = NOW + timedelta(days=30)


def _version() -> CreativePlanVersion:
    payload = CreativePlanPayload(
        directions=(
            CreativePlanDirection(
                key="hero",
                image_role=ImageRole.HERO,
                scene="Clean studio",
                composition="Centered product",
                camera="Eye level",
                lighting="Soft key light",
                color_direction="Brand blue",
                product_constraints=("Preserve packaging",),
                required_elements=("Product",),
                prohibited_elements=(),
                citation_selections=(),
                candidate_count=1,
                quality_targets=("Sharp label",),
                repair_scope=(),
                tool_intents=(),
            ),
        )
    )
    provenance = CreativePlanProvenance(
        product_brief_id="019b0000-0000-7000-8000-000000000701",
        product_brief_version=3,
        product_brief_sha256="1" * 64,
        brand_profile_id=None,
        brand_profile_version=None,
        brand_profile_sha256=None,
        retrieval_run_id="019b0000-0000-7000-8000-000000000702",
        retrieval_citation_ids=(),
        context_policy_version="planning-context-v1",
        context_sha256="2" * 64,
        prompt_id="creative-planner",
        prompt_revision="1.0.0",
        prompt_sha256="3" * 64,
    )
    return CreativePlanVersion.create(
        workspace_id="planning-domain",
        workflow_id="019b0000-0000-7000-8000-000000000700",
        creative_plan_id="019b0000-0000-7000-8000-000000000703",
        version_number=1,
        supersedes_version_id=None,
        source=CreativePlanSource.AGENT,
        payload=payload,
        provenance=provenance,
        actor_id="fixture-planner",
        revision_reason=None,
        now=NOW,
    )


class _WorkflowRepository:
    def __init__(self, workflow: Workflow) -> None:
        self.workflow = workflow
        self.get_call: tuple[str, str | None, bool] | None = None

    def get(
        self,
        workflow_id: str,
        *,
        workspace_id: str | None = None,
        for_update: bool = False,
    ) -> Workflow | None:
        self.get_call = (workflow_id, workspace_id, for_update)
        return self.workflow


class _CreativePlanRepository:
    def __init__(self) -> None:
        self.append_call: tuple[CreativePlanVersion, int, datetime, datetime] | None = None
        self.stored_version: CreativePlanVersion | None = None

    def append_version(
        self,
        version: CreativePlanVersion,
        *,
        expected_head_version: int,
        retain_until: datetime,
        authorized_at: datetime,
    ) -> CreativePlanHead:
        self.append_call = (version, expected_head_version, retain_until, authorized_at)
        return CreativePlanHead.from_first_version(version, retain_until=retain_until)

    def get_version_by_number(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
        version_number: int,
    ) -> CreativePlanVersion | None:
        del workspace_id, workflow_id, creative_plan_id, version_number
        return self.stored_version


class _UnitOfWork:
    def __init__(self, workflow: Workflow) -> None:
        self.workflows = _WorkflowRepository(workflow)
        self.creative_plans = _CreativePlanRepository()
        self.committed = False

    def __enter__(self) -> _UnitOfWork:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def database_now(self) -> datetime:
        return NOW

    def commit(self) -> None:
        self.committed = True


def test_first_version_atomically_creates_head_at_workflow_retention_deadline() -> None:
    version = _version()
    workflow = Workflow(
        id=version.workflow_id,
        workspace_id=version.workspace_id,
        created_by="operator",
        workflow_type="creative-planning",
        status=WorkflowStatus.PLANNING,
        retention_status=RetentionStatus.ACTIVE,
        current_node="create_plan",
        version=7,
        input_data={},
        result_data=None,
        expires_at=WORKFLOW_DEADLINE,
        cancellation_requested_at=None,
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW,
    )
    unit_of_work = _UnitOfWork(workflow)
    service = CreativePlanApplicationService(lambda: unit_of_work)

    result = service.append_version(
        version=version,
        expected_workflow_version=7,
        expected_head_version=0,
    )

    assert unit_of_work.workflows.get_call == (
        version.workflow_id,
        version.workspace_id,
        True,
    )
    assert unit_of_work.creative_plans.append_call == (
        version,
        0,
        WORKFLOW_DEADLINE,
        NOW,
    )
    assert unit_of_work.committed is True
    assert result.version == version
    assert result.head.creative_plan_id == version.creative_plan_id
    assert result.head.current_version_id == version.id
    assert result.head.current_version_number == 1
    assert result.head.version == 1
    assert result.head.retain_until == WORKFLOW_DEADLINE


def test_exact_version_can_be_read_through_application_interface() -> None:
    version = _version()
    workflow = Workflow(
        id=version.workflow_id,
        workspace_id=version.workspace_id,
        created_by="operator",
        workflow_type="creative-planning",
        status=WorkflowStatus.PLANNING,
        retention_status=RetentionStatus.ACTIVE,
        current_node="create_plan",
        version=7,
        input_data={},
        result_data=None,
        expires_at=WORKFLOW_DEADLINE,
        cancellation_requested_at=None,
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW,
    )
    unit_of_work = _UnitOfWork(workflow)
    unit_of_work.creative_plans.stored_version = version
    service = CreativePlanApplicationService(lambda: unit_of_work)

    assert (
        service.get_version(
            workspace_id=version.workspace_id,
            workflow_id=version.workflow_id,
            creative_plan_id=version.creative_plan_id,
            version_number=version.version_number,
        )
        == version
    )
