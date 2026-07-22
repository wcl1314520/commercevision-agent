import pytest
from commercevision_tool_runtime import (
    FixtureImageTool,
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionGateway,
    ToolInvocation,
    ToolPolicyError,
    ToolRegistry,
)
from commercevision_tool_runtime.gateway import stable_tool_key
from commercevision_tool_runtime.policy import ToolPolicy


def _gateway(*, transaction_active=lambda: False) -> ToolExecutionGateway:
    fixture = FixtureImageTool()
    registry = ToolRegistry(
        [
            ToolDefinition(
                name=fixture.name,
                version=fixture.version,
                description="fixture",
                input_schema={},
                output_schema={},
                implementation=fixture,
            )
        ]
    )
    return ToolExecutionGateway(
        registry=registry,
        policy=ToolPolicy(
            version="tool-policy-v1",
            allowed_tools=frozenset({fixture.name}),
            transaction_active=transaction_active,
        ),
    )


def test_stable_tool_key_is_canonical() -> None:
    first = stable_tool_key(
        workflow_id="workflow",
        step_key="execute:0",
        tool_name="fixture.generate_image",
        arguments={"count": 2, "quality": "high"},
    )
    second = stable_tool_key(
        workflow_id="workflow",
        step_key="execute:0",
        tool_name="fixture.generate_image",
        arguments={"quality": "high", "count": 2},
    )
    assert first == second


def test_tool_policy_rejects_execution_inside_transaction() -> None:
    gateway = _gateway(transaction_active=lambda: True)
    invocation = ToolInvocation(
        tool_name="fixture.generate_image",
        tool_version="1.0.0",
        arguments={"count": 1},
        idempotency_key="key",
        policy_version="tool-policy-v1",
        reason="test",
    )
    context = ToolExecutionContext(
        workflow_id="workflow",
        workspace_id="workspace",
        actor_id="user",
        trace_id="trace",
        idempotency_key="key",
        policy_version="tool-policy-v1",
    )
    with pytest.raises(ToolPolicyError):
        gateway.execute(context=context, invocation=invocation)


def test_fixture_tool_is_deterministic_for_same_idempotency_key() -> None:
    gateway = _gateway()
    invocation = ToolInvocation(
        tool_name="fixture.generate_image",
        tool_version="1.0.0",
        arguments={"count": 2},
        idempotency_key="stable-key",
        policy_version="tool-policy-v1",
        reason="test",
    )
    context = ToolExecutionContext(
        workflow_id="workflow",
        workspace_id="workspace",
        actor_id="user",
        trace_id="trace",
        idempotency_key="stable-key",
        policy_version="tool-policy-v1",
    )
    first = gateway.execute(context=context, invocation=invocation)
    second = gateway.execute(context=context, invocation=invocation)
    assert first.output == second.output
