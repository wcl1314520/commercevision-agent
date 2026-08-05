from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from commercevision_application import CreativePlanApplicationService
from commercevision_contracts import (
    CreativePlanCreateRequestV1,
    CreativePlanRevisionRequestV1,
)
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
from commercevision_domain.workflow.errors import IdempotencyConflictError

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
        self.version_page: tuple[CreativePlanVersion, ...] | None = None
        self.page_call: tuple[str, str, str, int | None, int] | None = None
        self.current: tuple[CreativePlanHead, CreativePlanVersion] | None = None
        self.versions_by_id: dict[str, CreativePlanVersion] = {}
        self.append_calls: list[CreativePlanVersion] = []

    def append_version(
        self,
        version: CreativePlanVersion,
        *,
        expected_head_version: int,
        retain_until: datetime,
        authorized_at: datetime,
    ) -> CreativePlanHead:
        self.append_call = (version, expected_head_version, retain_until, authorized_at)
        self.append_calls.append(version)
        if self.current is None:
            head = CreativePlanHead.from_first_version(version, retain_until=retain_until)
        else:
            current_head, _ = self.current
            head = current_head.advance(version, expected_version=expected_head_version)
        self.current = (head, version)
        self.versions_by_id[version.id] = version
        return head

    def get_current(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
    ) -> tuple[CreativePlanHead, CreativePlanVersion] | None:
        if self.current is None:
            return None
        head, version = self.current
        if (
            head.workspace_id,
            head.workflow_id,
            head.creative_plan_id,
        ) != (workspace_id, workflow_id, creative_plan_id):
            return None
        return head, version

    def get_version_by_id(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
        version_id: str,
    ) -> CreativePlanVersion | None:
        version = self.versions_by_id.get(version_id)
        if version is None:
            return None
        if (
            version.workspace_id,
            version.workflow_id,
            version.creative_plan_id,
        ) != (workspace_id, workflow_id, creative_plan_id):
            return None
        return version

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

    def list_version_page(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
        after_version_number: int | None,
        limit: int,
    ) -> tuple[CreativePlanVersion, ...] | None:
        self.page_call = (
            workspace_id,
            workflow_id,
            creative_plan_id,
            after_version_number,
            limit,
        )
        return self.version_page


class _CursorCodec:
    def __init__(self) -> None:
        self.encoded: tuple[str, str, str, int] | None = None

    def decode(
        self,
        token: str,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
    ) -> int:
        del token, workspace_id, workflow_id, creative_plan_id
        return 1

    def encode(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
        version_number: int,
    ) -> str:
        self.encoded = (
            workspace_id,
            workflow_id,
            creative_plan_id,
            version_number,
        )
        return "v1.test.opaque.signature"


@dataclass(frozen=True, slots=True)
class _IdempotencyRecord:
    request_hash: str
    resource_type: str = "PENDING"
    resource_id: str = ""
    response_data: dict[str, Any] | None = None
    status: str = "PENDING"


class _IdempotencyRepository:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], _IdempotencyRecord] = {}
        self.claim_calls = 0

    def claim(
        self,
        *,
        scope: str,
        key_hash: str,
        request_hash: str,
        expires_at: datetime,
    ) -> _IdempotencyRecord:
        del expires_at
        self.claim_calls += 1
        key = (scope, key_hash)
        self.records.setdefault(key, _IdempotencyRecord(request_hash=request_hash))
        return self.records[key]

    def complete(
        self,
        *,
        scope: str,
        key_hash: str,
        request_hash: str,
        resource_type: str,
        resource_id: str,
        response_data: dict[str, Any],
    ) -> None:
        self.records[(scope, key_hash)] = _IdempotencyRecord(
            request_hash=request_hash,
            resource_type=resource_type,
            resource_id=resource_id,
            response_data=response_data,
            status="COMPLETED",
        )


class _AuditRepository:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def add(self, **kwargs: Any) -> None:
        self.events.append(kwargs)


class _UnitOfWork:
    def __init__(self, workflow: Workflow) -> None:
        self.workflows = _WorkflowRepository(workflow)
        self.creative_plans = _CreativePlanRepository()
        self.idempotency = _IdempotencyRepository()
        self.audit = _AuditRepository()
        self.committed = False

    def __enter__(self) -> _UnitOfWork:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def database_now(self) -> datetime:
        return NOW

    def commit(self) -> None:
        self.committed = True


def _create_request(version: CreativePlanVersion) -> CreativePlanCreateRequestV1:
    return CreativePlanCreateRequestV1.model_validate(
        {
            "workflow_id": version.workflow_id,
            "creative_plan_id": version.creative_plan_id,
            "payload": version.payload.to_canonical_data(),
            "provenance": {
                "product_brief_id": version.provenance.product_brief_id,
                "product_brief_version": version.provenance.product_brief_version,
                "product_brief_sha256": version.provenance.product_brief_sha256,
                "brand_profile_id": version.provenance.brand_profile_id,
                "brand_profile_version": version.provenance.brand_profile_version,
                "brand_profile_sha256": version.provenance.brand_profile_sha256,
                "retrieval_run_id": version.provenance.retrieval_run_id,
                "retrieval_citation_ids": list(version.provenance.retrieval_citation_ids),
                "context_policy_version": version.provenance.context_policy_version,
                "context_sha256": version.provenance.context_sha256,
                "prompt_id": version.provenance.prompt_id,
                "prompt_revision": version.provenance.prompt_revision,
                "prompt_sha256": version.provenance.prompt_sha256,
            },
            "expected_workflow_version": 7,
            "expected_head_version": 0,
        }
    )


def _workflow_for(version: CreativePlanVersion) -> Workflow:
    return Workflow(
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


def test_create_command_constructs_agent_version_and_records_aggregate_audit() -> None:
    fixture = _version()
    unit_of_work = _UnitOfWork(_workflow_for(fixture))
    service = CreativePlanApplicationService(lambda: unit_of_work)

    result = service.create_plan(
        workspace_id=fixture.workspace_id,
        actor_id="creative-planner",
        request=_create_request(fixture),
        trace_id="trace-create-plan",
        idempotency_key="create-plan-request-001",
    )

    assert result.version.source is CreativePlanSource.AGENT
    assert result.version.version_number == 1
    assert result.version.supersedes_version_id is None
    assert result.version.payload == fixture.payload
    assert result.version.provenance == fixture.provenance
    assert len(unit_of_work.creative_plans.append_calls) == 1
    assert len(unit_of_work.audit.events) == 1
    event = unit_of_work.audit.events[0]
    assert event["action"] == "creative_plan.created"
    assert event["resource_id"] == fixture.creative_plan_id
    assert event["trace_id"] == "trace-create-plan"
    assert event["metadata"] == {
        "version_number": 1,
        "source": "AGENT",
        "direction_count": 1,
        "payload_sha256": fixture.payload_sha256,
        "expected_workflow_version": 7,
        "expected_head_version": 0,
    }


def test_create_command_replays_exact_result_without_new_version_or_audit() -> None:
    fixture = _version()
    unit_of_work = _UnitOfWork(_workflow_for(fixture))
    service = CreativePlanApplicationService(lambda: unit_of_work)
    command = _create_request(fixture)
    arguments = {
        "workspace_id": fixture.workspace_id,
        "actor_id": "creative-planner",
        "request": command,
        "trace_id": "trace-create-plan",
        "idempotency_key": "create-plan-request-002",
    }

    first = service.create_plan(**arguments)
    later_version = first.version.revise_by_user(
        payload=CreativePlanPayload(
            directions=(replace(first.version.payload.directions[0], scene="Later revision"),)
        ),
        actor_id="creative-reviewer",
        reason="Advance the live head after the original response",
        now=NOW + timedelta(seconds=1),
    )
    later_head = first.head.advance(later_version, expected_version=1)
    unit_of_work.creative_plans.current = (later_head, later_version)
    unit_of_work.creative_plans.versions_by_id[later_version.id] = later_version
    unit_of_work.workflows.workflow = replace(
        unit_of_work.workflows.workflow,
        status=WorkflowStatus.COMPLETED,
        current_node="completed",
        version=8,
    )
    second = service.create_plan(**arguments)

    assert second == first
    assert len(unit_of_work.creative_plans.append_calls) == 1
    assert len(unit_of_work.audit.events) == 1


def test_create_command_rejects_reused_key_for_different_request() -> None:
    fixture = _version()
    unit_of_work = _UnitOfWork(_workflow_for(fixture))
    service = CreativePlanApplicationService(lambda: unit_of_work)
    service.create_plan(
        workspace_id=fixture.workspace_id,
        actor_id="creative-planner",
        request=_create_request(fixture),
        trace_id="trace-create-plan",
        idempotency_key="create-plan-request-003",
    )
    changed = _create_request(
        replace(
            fixture,
            payload=CreativePlanPayload(
                directions=(replace(fixture.payload.directions[0], scene="Changed"),)
            ),
            payload_sha256=CreativePlanPayload(
                directions=(replace(fixture.payload.directions[0], scene="Changed"),)
            ).payload_sha256,
        )
    )

    try:
        service.create_plan(
            workspace_id=fixture.workspace_id,
            actor_id="creative-planner",
            request=changed,
            trace_id="trace-create-plan",
            idempotency_key="create-plan-request-003",
        )
    except IdempotencyConflictError:
        pass
    else:
        raise AssertionError("reused idempotency key must reject a different request")


def test_create_command_rejects_oversized_tool_arguments_before_unit_of_work() -> None:
    fixture = _version()
    request_data = _create_request(fixture).model_dump(mode="json")
    request_data["payload"]["directions"][0]["tool_intents"] = [
        {
            "intent_key": "compose-hero",
            "tool_name": "image-compose",
            "schema_version": "1.0.0",
            "purpose": "Compose the approved hero image",
            "arguments": {"prompt": "x" * (16 * 1024)},
            "estimated_cost_units": 1,
        }
    ]
    request = CreativePlanCreateRequestV1.model_validate(request_data)
    unit_of_work_started = False

    def unit_of_work_factory() -> _UnitOfWork:
        nonlocal unit_of_work_started
        unit_of_work_started = True
        return _UnitOfWork(_workflow_for(fixture))

    service = CreativePlanApplicationService(unit_of_work_factory)

    with pytest.raises(ValueError, match="string limit|Tool Intent arguments are invalid"):
        service.create_plan(
            workspace_id=fixture.workspace_id,
            actor_id="creative-planner",
            request=request,
            trace_id="trace-create-plan",
            idempotency_key="create-plan-request-004",
        )

    assert unit_of_work_started is False


def test_revision_command_appends_user_version_and_preserves_provenance() -> None:
    fixture = _version()
    workflow = replace(
        _workflow_for(fixture),
        status=WorkflowStatus.AWAITING_PLAN_APPROVAL,
        current_node="approve_plan",
        version=8,
    )
    unit_of_work = _UnitOfWork(workflow)
    head = CreativePlanHead.from_first_version(fixture, retain_until=WORKFLOW_DEADLINE)
    unit_of_work.creative_plans.current = (head, fixture)
    unit_of_work.creative_plans.versions_by_id[fixture.id] = fixture
    changed_payload = CreativePlanPayload(
        directions=(replace(fixture.payload.directions[0], scene="Approved retail shelf"),)
    )
    request = CreativePlanRevisionRequestV1.model_validate(
        {
            "workflow_id": fixture.workflow_id,
            "payload": changed_payload.to_canonical_data(),
            "revision_reason": "Use the approved retail setting",
            "expected_workflow_version": 8,
            "expected_head_version": 1,
        }
    )
    service = CreativePlanApplicationService(lambda: unit_of_work)

    result = service.revise_plan(
        workspace_id=fixture.workspace_id,
        creative_plan_id=fixture.creative_plan_id,
        actor_id="creative-reviewer",
        request=request,
        trace_id="trace-revise-plan",
        idempotency_key="revise-plan-request-001",
    )

    assert result.version.source is CreativePlanSource.USER
    assert result.version.version_number == 2
    assert result.version.supersedes_version_id == fixture.id
    assert result.version.provenance == fixture.provenance
    assert result.version.revision_reason == "Use the approved retail setting"
    assert result.head.version == 2
    assert unit_of_work.audit.events[0]["action"] == "creative_plan.revised"


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


def test_version_history_fetches_only_limit_plus_one_and_issues_next_cursor() -> None:
    first = _version()
    second = first.revise_by_user(
        payload=CreativePlanPayload(
            directions=(replace(first.payload.directions[0], scene="Retail shelf"),)
        ),
        actor_id="creative-reviewer",
        reason="Use the approved retail setting",
        now=NOW + timedelta(seconds=1),
    )
    workflow = Workflow(
        id=first.workflow_id,
        workspace_id=first.workspace_id,
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
    unit_of_work.creative_plans.version_page = (first, second)
    cursor_codec = _CursorCodec()
    service = CreativePlanApplicationService(
        lambda: unit_of_work,
        cursor_codec=cursor_codec,
    )

    page = service.list_version_page(
        workspace_id=first.workspace_id,
        workflow_id=first.workflow_id,
        creative_plan_id=first.creative_plan_id,
        limit=1,
        cursor=None,
    )

    assert page.items == (first,)
    assert page.next_cursor == "v1.test.opaque.signature"
    assert unit_of_work.creative_plans.page_call == (
        first.workspace_id,
        first.workflow_id,
        first.creative_plan_id,
        None,
        2,
    )
    assert cursor_codec.encoded == (
        first.workspace_id,
        first.workflow_id,
        first.creative_plan_id,
        1,
    )
