from datetime import UTC, datetime

import pytest
from commercevision_tool_runtime import (
    FixtureImageTool,
    ToolAuditLevel,
    ToolCostClass,
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionGateway,
    ToolInvocation,
    ToolPolicyError,
    ToolRegistry,
    ToolRegistryError,
    fixture_image_intent_definition,
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


def _no_resources(arguments: BaseModel) -> tuple[str, ...]:
    del arguments
    return ()


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


def test_tool_definition_owns_planner_authorization_metadata() -> None:
    fixture = FixtureImageTool()

    definition = ToolDefinition(
        name="fixture.image.generate",
        version="1.0",
        description="fixture",
        input_schema=_Input.model_json_schema(),
        output_schema=_Output.model_json_schema(),
        implementation=fixture,
        input_model=_Input,
        output_model=_Output,
        allowed_nodes=frozenset({"execute_tool"}),
        resource_resolver=_no_resources,
        cost_class=ToolCostClass.LOW,
        audit_level=ToolAuditLevel.RESOURCE_IDENTITIES,
    )

    assert definition.allowed_nodes == frozenset({"execute_tool"})
    assert definition.resource_resolver is _no_resources
    assert definition.cost_class is ToolCostClass.LOW
    assert definition.audit_level is ToolAuditLevel.RESOURCE_IDENTITIES


def test_fixture_planner_tool_is_authorization_only_and_server_owned() -> None:
    definition = fixture_image_intent_definition()

    assert definition.name == "fixture.image.generate"
    assert definition.version == "1.0"
    assert definition.allowed_nodes == frozenset({"execute_tool"})
    assert definition.required_scopes == frozenset({"image.generate"})
    assert definition.provider == "fixture"
    assert definition.implementation is None


def test_registry_resolves_only_fully_typed_tools_for_allowed_planner_node() -> None:
    fixture = FixtureImageTool()
    planner_definition = ToolDefinition(
        name="fixture.image.generate",
        version="1.0",
        description="fixture",
        input_schema=_Input.model_json_schema(),
        output_schema=_Output.model_json_schema(),
        implementation=fixture,
        input_model=_Input,
        output_model=_Output,
        allowed_nodes=frozenset({"execute_tool"}),
        resource_resolver=_no_resources,
        cost_class=ToolCostClass.LOW,
        audit_level=ToolAuditLevel.METADATA,
    )
    execution_only = ToolDefinition(
        name=fixture.name,
        version=fixture.version,
        description="legacy fixture",
        input_schema={},
        output_schema={},
        implementation=fixture,
    )
    registry = ToolRegistry([planner_definition, execution_only])

    assert (
        registry.resolve_for_node("fixture.image.generate", "1.0", node="execute_tool")
        is planner_definition
    )
    with pytest.raises(ToolRegistryError, match="not allowed from node"):
        registry.resolve_for_node("fixture.image.generate", "1.0", node="create_plan")
    with pytest.raises(ToolRegistryError, match="not planner-authorizable"):
        registry.resolve_for_node(fixture.name, fixture.version, node="execute_tool")


def test_registry_rejects_declared_schema_drift_for_planner_tool() -> None:
    fixture = FixtureImageTool()
    registry = ToolRegistry(
        [
            ToolDefinition(
                name="fixture.image.generate",
                version="1.0",
                description="fixture",
                input_schema={"type": "object"},
                output_schema={},
                implementation=fixture,
                input_model=_Input,
                allowed_nodes=frozenset({"execute_tool"}),
                resource_resolver=_no_resources,
            )
        ]
    )

    with pytest.raises(ToolRegistryError, match="schema does not match"):
        registry.resolve_for_node("fixture.image.generate", "1.0", node="execute_tool")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "fixture image generate"),
        ("version", "version 1"),
        ("allowed_nodes", frozenset({"execute tool"})),
    ],
)
def test_tool_definition_rejects_unstable_registry_identifiers(
    field: str,
    value: object,
) -> None:
    fixture = FixtureImageTool()
    values = {
        "name": "fixture.image.generate",
        "version": "1.0",
        "description": "fixture",
        "input_schema": _Input.model_json_schema(),
        "output_schema": _Output.model_json_schema(),
        "implementation": fixture,
        "allowed_nodes": frozenset({"execute_tool"}),
    }
    values[field] = value

    with pytest.raises(ValueError, match="identifier"):
        ToolDefinition(**values)  # type: ignore[arg-type]


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


def test_gateway_rejects_authorization_only_tool_without_execution_adapter() -> None:
    registry = ToolRegistry(
        [
            ToolDefinition(
                name="fixture.image.generate",
                version="1.0",
                description="future command",
                input_schema=_Input.model_json_schema(),
                output_schema={},
                implementation=None,
                input_model=_Input,
                allowed_nodes=frozenset({"execute_tool"}),
                resource_resolver=_no_resources,
                provider="fixture",
                cost_class=ToolCostClass.LOW,
                audit_level=ToolAuditLevel.METADATA,
            )
        ]
    )
    gateway = ToolExecutionGateway(
        registry=registry,
        policy=ToolPolicy(
            version="tool-policy-v1",
            allowed_tools=frozenset({"fixture.image.generate"}),
        ),
    )
    invocation = ToolInvocation(
        tool_name="fixture.image.generate",
        tool_version="1.0",
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
    from commercevision_tool_runtime import ToolExecutionError

    with pytest.raises(ToolExecutionError, match="execution adapter"):
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
