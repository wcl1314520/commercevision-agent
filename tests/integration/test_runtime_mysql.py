from __future__ import annotations

import pytest
from commercevision_application import (
    WorkflowApplicationService,
)
from commercevision_contracts.workflow import ApprovalRequest, WorkflowCreateRequest
from commercevision_domain import ApprovalDecision, ApprovalType
from commercevision_persistence import (
    SqlAlchemyUnitOfWork,
    is_unit_of_work_active,
)
from commercevision_tool_runtime import (
    FixtureImageTool,
    ToolDefinition,
    ToolExecutionGateway,
    ToolRegistry,
)
from commercevision_tool_runtime.policy import ToolPolicy
from commercevision_worker.runtime import WorkerRuntime

pytestmark = pytest.mark.integration


def test_worker_restart_duplicate_delivery_and_human_resume(
    integration_database, integration_settings
) -> None:
    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(integration_database.session_factory)

    service = WorkflowApplicationService(uow_factory=uow_factory)
    created = service.create(
        request=WorkflowCreateRequest(
            input_data={"fixture_config": {"count": 2}},
        ),
        workspace_id="integration-runtime",
        actor_id="integration-user",
        idempotency_key="runtime-create-0001",
        trace_id="runtime-trace",
    )
    initial_event = service.events(
        workflow_id=created.id,
        workspace_id="integration-runtime",
    )[0]

    first_worker = WorkerRuntime.build(integration_settings)
    assert first_worker.process_event(initial_event.event_id) == "processed"
    assert first_worker.process_event(initial_event.event_id) == "duplicate"
    first_worker.close()

    waiting_plan = service.get(
        workflow_id=created.id,
        workspace_id="integration-runtime",
    )
    assert waiting_plan.status.value == "AWAITING_PLAN_APPROVAL"
    plan_step = [step for step in waiting_plan.steps if step.step_type.value == "CREATE_PLAN"][-1]
    plan_response = service.approve(
        workflow_id=created.id,
        workspace_id="integration-runtime",
        actor_id="integration-user",
        approval_type=ApprovalType.CREATIVE_PLAN,
        request=ApprovalRequest(
            expected_workflow_version=waiting_plan.version,
            subject_id=plan_step.output_data["creative_plan_ref"],
            subject_version=1,
            decision=ApprovalDecision.APPROVE,
        ),
        idempotency_key="runtime-plan-approve-0001",
        trace_id="runtime-trace",
    )
    resume_plan = [
        event
        for event in service.events(
            workflow_id=created.id,
            workspace_id="integration-runtime",
        )
        if event.event_type == "workflow.resume.requested"
    ][-1]

    second_worker = WorkerRuntime.build(integration_settings)
    assert second_worker.process_event(resume_plan.event_id) == "processed"
    second_worker.close()
    awaiting_results = service.get(
        workflow_id=created.id,
        workspace_id="integration-runtime",
    )
    assert awaiting_results.status.value == "AWAITING_RESULT_APPROVAL"
    assert len(awaiting_results.attempts) == 1

    evaluation_step = [
        step for step in awaiting_results.steps if step.step_type.value == "EVALUATE_RESULTS"
    ][-1]
    result_response = service.approve(
        workflow_id=created.id,
        workspace_id="integration-runtime",
        actor_id="integration-user",
        approval_type=ApprovalType.RESULTS,
        request=ApprovalRequest(
            expected_workflow_version=awaiting_results.version,
            subject_id=evaluation_step.output_data["evaluation_report_ref"],
            subject_version=1,
            decision=ApprovalDecision.APPROVE,
        ),
        idempotency_key="runtime-result-approve-0001",
        trace_id="runtime-trace",
    )
    resume_result = [
        event
        for event in service.events(
            workflow_id=created.id,
            workspace_id="integration-runtime",
        )
        if event.event_type == "workflow.resume.requested"
    ][-1]
    third_worker = WorkerRuntime.build(integration_settings)
    assert third_worker.process_event(resume_result.event_id) == "processed"
    assert third_worker.process_event(resume_result.event_id) == "duplicate"
    third_worker.close()

    completed = service.get(
        workflow_id=created.id,
        workspace_id="integration-runtime",
    )
    assert completed.status.value == "COMPLETED"
    assert len(completed.attempts) == 1
    assert completed.result_data["export_ref"].startswith("fixture://exports/")
    assert plan_response.id == created.id
    assert result_response.id == created.id


def test_tool_execution_never_runs_inside_uow(integration_database) -> None:
    observed: list[bool] = []

    fixture = FixtureImageTool()

    def implementation(context, invocation):
        observed.append(is_unit_of_work_active())
        return fixture(context, invocation)

    registry = ToolRegistry(
        [
            ToolDefinition(
                name=fixture.name,
                version=fixture.version,
                description="transaction boundary test",
                input_schema={},
                output_schema={},
                implementation=implementation,
            )
        ]
    )
    gateway = ToolExecutionGateway(
        registry=registry,
        policy=ToolPolicy(
            version="tool-policy-v1",
            allowed_tools=frozenset({fixture.name}),
            transaction_active=is_unit_of_work_active,
        ),
    )
    from commercevision_tool_runtime import ToolExecutionContext, ToolInvocation

    context = ToolExecutionContext(
        workflow_id="workflow",
        workspace_id="workspace",
        actor_id="user",
        trace_id="trace",
        idempotency_key="key",
        policy_version="tool-policy-v1",
    )
    gateway.execute(
        context=context,
        invocation=ToolInvocation(
            tool_name=fixture.name,
            tool_version=fixture.version,
            arguments={"count": 1},
            idempotency_key="key",
            policy_version="tool-policy-v1",
            reason="boundary test",
        ),
    )
    assert observed == [False]
