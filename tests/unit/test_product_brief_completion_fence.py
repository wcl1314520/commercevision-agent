from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from commercevision_agent_core.graph import (
    _PRECLAIMED_NODE_AUTHORITY,
    FixtureNodes,
    _PreclaimedNodeAuthority,
)
from commercevision_agent_core.state import FixtureAgentState
from commercevision_application import ProductBriefGenerationAuthority
from commercevision_contracts.workflow import product_brief_checkpoint_generation
from commercevision_domain import NotFoundError
from commercevision_tool_runtime import ToolResult


def _generation_authority_metadata() -> dict[str, object]:
    return {
        "workspace_id": "workspace-product-brief-fence",
        "workflow_id": "workflow-product-brief-fence",
        "product_id": "product-v1",
        "product_brief_id": "brief-v1",
        "product_brief_version_id": "brief-version-v1",
        "product_brief_version_number": 1,
        "approval_id": None,
        "initial_step_id": "retrieval-step-v1",
        "checkpoint_generation": "product-brief:v1:" + ("a" * 64),
    }


@pytest.mark.parametrize(
    ("extra_key", "extra_value"),
    [
        ("initial_step_lease_token", "forbidden-live-token"),
        ("unexpected_authority_field", "forbidden-extension"),
    ],
)
def test_product_brief_generation_authority_rejects_extra_metadata(
    extra_key: str,
    extra_value: str,
) -> None:
    generation = {**_generation_authority_metadata(), extra_key: extra_value}

    with pytest.raises(ValueError, match="generation authority fields are invalid"):
        ProductBriefGenerationAuthority.from_step(
            SimpleNamespace(input_data={"product_brief_generation": generation})
        )


def test_product_brief_generation_authority_requires_nullable_approval_key() -> None:
    generation = _generation_authority_metadata()
    authority = ProductBriefGenerationAuthority.from_step(
        SimpleNamespace(input_data={"product_brief_generation": generation})
    )
    assert authority is not None
    assert authority.approval_id is None

    generation.pop("approval_id")
    with pytest.raises(ValueError, match="generation authority fields are invalid"):
        ProductBriefGenerationAuthority.from_step(
            SimpleNamespace(input_data={"product_brief_generation": generation})
        )


def _confirmed_product_brief_state(
    *,
    current_node: str,
    workflow_version: int = 17,
    initial_step_id: str | None = None,
    initial_step_lease_token: str | None = None,
) -> FixtureAgentState:
    return FixtureAgentState(
        workflow_id="workflow-product-brief-fence",
        workflow_version=workflow_version,
        workspace_id="workspace-product-brief-fence",
        actor_id="worker-product-brief-fence",
        trace_id="trace-product-brief-fence",
        fixture_config={"count": 1},
        product_brief_ref="mysql://product-brief-versions/brief-version-v1",
        product_brief_version_id="brief-version-v1",
        product_brief_version_number=1,
        product_brief_approval_id="approval-v1",
        product_brief_checkpoint_generation=product_brief_checkpoint_generation(
            workspace_id="workspace-product-brief-fence",
            product_brief_version_id="brief-version-v1",
            initial_step_id=initial_step_id or "retrieval-step-v1",
            initial_step_lease_token=(initial_step_lease_token or "retrieval-lease-v1"),
        ),
        current_node=current_node,
        initial_entry_reason="PRODUCT_BRIEF_CONFIRMED",
        initial_step_id=initial_step_id,
    )


def test_preclaimed_retrieval_completion_carries_exact_continuation_generation() -> None:
    lifecycle = MagicMock()
    lifecycle.complete_node.return_value = 18
    nodes = FixtureNodes(
        lifecycle=lifecycle,
        planner=MagicMock(),
        tool_gateway=MagicMock(),
        worker_id="worker-product-brief-fence",
    )
    state = _confirmed_product_brief_state(
        current_node="retrieve_references",
        initial_step_id="retrieval-step-v1",
        initial_step_lease_token="retrieval-lease-v1",
    )

    authority_token = _PRECLAIMED_NODE_AUTHORITY.set(
        _PreclaimedNodeAuthority(
            step_id="retrieval-step-v1",
            lease_token="retrieval-lease-v1",
        )
    )
    try:
        nodes.retrieve_references(state)
    finally:
        _PRECLAIMED_NODE_AUTHORITY.reset(authority_token)

    completion = lifecycle.complete_node.call_args.kwargs
    assert completion["expected_workflow_version"] == 17
    assert completion["lease_token"] == "retrieval-lease-v1"
    continuation = completion["product_brief_continuation"]
    assert continuation.workspace_id == state.workspace_id
    assert continuation.product_brief_version_id == "brief-version-v1"
    assert continuation.product_brief_version_number == 1
    assert continuation.approval_id == "approval-v1"


def test_plan_write_replays_the_same_step_key_after_completion_crash() -> None:
    lifecycle = MagicMock()
    lifecycle.begin_node.side_effect = (
        SimpleNamespace(
            workflow_version=17,
            step_id="create-plan-step-v1",
            lease_token="create-plan-lease-v1",
            already_completed=False,
            output_data=None,
        ),
        SimpleNamespace(
            workflow_version=17,
            step_id="create-plan-step-v1",
            lease_token="create-plan-lease-v2",
            already_completed=False,
            output_data=None,
        ),
    )
    lifecycle.complete_node.side_effect = (
        RuntimeError("simulated crash after durable Plan write"),
        18,
    )
    output = {
        "creative_plan_ref": "plan-v1",
        "creative_plan_version_id": "plan-version-v1",
        "creative_plan_version": 1,
        "creative_plan_payload_sha256": "1" * 64,
        "planning_context_sha256": "2" * 64,
        "prompt_id": "creative-planner",
        "prompt_revision": "1.0.0",
        "plan_decision": None,
    }
    planner = MagicMock()
    planner.create_plan.return_value = SimpleNamespace(to_step_output=lambda: output)
    nodes = FixtureNodes(
        lifecycle=lifecycle,
        planner=planner,
        tool_gateway=MagicMock(),
        worker_id="worker-product-brief-fence",
    )
    state = _confirmed_product_brief_state(current_node="create_plan")

    with pytest.raises(RuntimeError, match="after durable Plan write"):
        nodes.create_plan(state)
    replayed = nodes.create_plan(state)

    assert replayed == {
        **output,
        "workflow_version": 18,
        "current_node": "approve_plan",
        "initial_step_id": None,
    }
    assert planner.create_plan.call_count == 2
    calls = [call.kwargs for call in planner.create_plan.call_args_list]
    assert {call["idempotency_key"] for call in calls} == {"fixture-plan:create-plan-step-v1"}
    assert {call["expected_workflow_version"] for call in calls} == {17}
    completion_calls = [call.kwargs for call in lifecycle.complete_node.call_args_list]
    assert [call["lease_token"] for call in completion_calls] == [
        "create-plan-lease-v1",
        "create-plan-lease-v2",
    ]


def test_plan_policy_rejection_is_audited_as_a_permanent_node_failure() -> None:
    lifecycle = MagicMock()
    lifecycle.begin_node.return_value = SimpleNamespace(
        workflow_version=17,
        step_id="create-plan-step-v1",
        lease_token="create-plan-lease-v1",
        already_completed=False,
        output_data=None,
    )
    planner = MagicMock()
    planner.create_plan.side_effect = NotFoundError(
        "Fixture Planner authority or policy was not found"
    )
    nodes = FixtureNodes(
        lifecycle=lifecycle,
        planner=planner,
        tool_gateway=MagicMock(),
        worker_id="worker-product-brief-fence",
    )
    state = _confirmed_product_brief_state(current_node="create_plan")

    with pytest.raises(NotFoundError, match="authority or policy"):
        nodes.create_plan(state)

    failure = lifecycle.fail_node.call_args.kwargs
    assert failure["workflow_id"] == state.workflow_id
    assert failure["step_id"] == "create-plan-step-v1"
    assert failure["lease_token"] == "create-plan-lease-v1"
    assert failure["expected_workflow_version"] == 17
    assert failure["retryable"] is False
    assert failure["retry_delay"].total_seconds() == 0
    lifecycle.complete_node.assert_not_called()


def test_external_tool_persistence_and_completion_share_the_node_claim_fence() -> None:
    lifecycle = MagicMock()
    lifecycle.begin_node.return_value = SimpleNamespace(
        workflow_version=23,
        step_id="execute-step-v1",
        lease_token="execute-lease-v1",
        already_completed=False,
        output_data=None,
    )
    lifecycle.begin_attempt.return_value = SimpleNamespace(
        attempt_id="attempt-v1",
        already_completed=False,
        result_data=None,
    )
    lifecycle.complete_attempt.return_value = {
        "candidates": [{"asset_ref": "fixture://candidate/v1"}]
    }
    lifecycle.complete_node.return_value = 24
    tool_gateway = MagicMock()
    tool_gateway.execute.return_value = ToolResult(
        tool_name="fixture.generate_image",
        tool_version="1.0.0",
        idempotency_key="tool-result-v1",
        output={"candidates": [{"asset_ref": "fixture://candidate/v1"}]},
    )
    nodes = FixtureNodes(
        lifecycle=lifecycle,
        planner=MagicMock(),
        tool_gateway=tool_gateway,
        worker_id="worker-product-brief-fence",
    )
    state = _confirmed_product_brief_state(
        current_node="execute_tool",
        workflow_version=22,
    )

    nodes.execute_tool(state)

    for call in (
        lifecycle.begin_attempt.call_args,
        lifecycle.complete_attempt.call_args,
        lifecycle.complete_node.call_args,
    ):
        fenced = call.kwargs
        assert fenced["expected_workflow_version"] == 23
        assert fenced["workflow_id"] == state.workflow_id
        assert fenced["step_id"] == "execute-step-v1"
        assert fenced["lease_token"] == "execute-lease-v1"
        continuation = fenced["product_brief_continuation"]
        assert continuation.product_brief_version_id == "brief-version-v1"
        assert continuation.product_brief_version_number == 1
