from datetime import UTC, datetime

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
from pydantic import BaseModel, ConfigDict


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: int


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    doubled: int


class _TimestampOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    completed_at: datetime


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
        tool_version="1.0.0",
        policy_version="tool-policy-v1",
        arguments={"count": 2, "quality": "high"},
    )
    second = stable_tool_key(
        workflow_id="workflow",
        step_key="execute:0",
        tool_name="fixture.generate_image",
        tool_version="1.0.0",
        policy_version="tool-policy-v1",
        arguments={"quality": "high", "count": 2},
    )
    assert first == second


def test_stable_tool_key_fences_tool_and_policy_versions() -> None:
    common = {
        "workflow_id": "workflow",
        "step_key": "execute:0",
        "tool_name": "assets.search",
        "arguments": {"query": "shoe"},
    }
    first = stable_tool_key(**common, tool_version="v1", policy_version="policy-v1")
    assert first != stable_tool_key(**common, tool_version="v2", policy_version="policy-v1")
    assert first != stable_tool_key(**common, tool_version="v1", policy_version="policy-v2")


def test_gateway_validates_input_and_output_models() -> None:
    def invalid_output(context, invocation):
        del context, invocation
        from commercevision_tool_runtime import ToolResult

        return ToolResult(
            tool_name="test.double",
            tool_version="v1",
            idempotency_key="key",
            output={"doubled": "2"},
        )

    gateway = ToolExecutionGateway(
        registry=ToolRegistry(
            [
                ToolDefinition(
                    name="test.double",
                    version="v1",
                    description="double",
                    input_schema=_Input.model_json_schema(),
                    output_schema=_Output.model_json_schema(),
                    implementation=invalid_output,
                    input_model=_Input,
                    output_model=_Output,
                )
            ]
        ),
        policy=ToolPolicy(version="tool-policy-v1", allowed_tools=frozenset({"test.double"})),
    )
    context = ToolExecutionContext(
        workflow_id="workflow",
        workspace_id="workspace",
        actor_id="actor",
        trace_id="trace",
        idempotency_key="key",
        policy_version="tool-policy-v1",
    )
    invocation = ToolInvocation(
        tool_name="test.double",
        tool_version="v1",
        arguments={"value": 1},
        idempotency_key="key",
        policy_version="tool-policy-v1",
        reason="test",
    )
    from commercevision_tool_runtime import ToolExecutionError

    with pytest.raises(ToolExecutionError, match="output schema"):
        gateway.execute(context=context, invocation=invocation)


def test_gateway_preserves_python_types_until_strict_output_validation() -> None:
    completed_at = datetime(2026, 8, 3, tzinfo=UTC)

    def timestamp_output(context, invocation):
        del context
        from commercevision_tool_runtime import ToolResult

        return ToolResult(
            tool_name=invocation.tool_name,
            tool_version=invocation.tool_version,
            idempotency_key=invocation.idempotency_key,
            output={"completed_at": completed_at},
        )

    gateway = ToolExecutionGateway(
        registry=ToolRegistry(
            [
                ToolDefinition(
                    name="test.timestamp",
                    version="v1",
                    description="timestamp",
                    input_schema=_Input.model_json_schema(),
                    output_schema=_TimestampOutput.model_json_schema(),
                    implementation=timestamp_output,
                    input_model=_Input,
                    output_model=_TimestampOutput,
                )
            ]
        ),
        policy=ToolPolicy(
            version="tool-policy-v1",
            allowed_tools=frozenset({"test.timestamp"}),
        ),
    )
    invocation = ToolInvocation(
        tool_name="test.timestamp",
        tool_version="v1",
        arguments={"value": 1},
        idempotency_key="key",
        policy_version="tool-policy-v1",
        reason="test",
    )
    context = ToolExecutionContext(
        workflow_id="workflow",
        workspace_id="workspace",
        actor_id="actor",
        trace_id="trace",
        idempotency_key="key",
        policy_version="tool-policy-v1",
    )

    result = gateway.execute(context=context, invocation=invocation)

    assert result.output == {"completed_at": "2026-08-03T00:00:00Z"}


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
