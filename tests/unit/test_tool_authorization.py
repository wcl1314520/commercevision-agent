from dataclasses import FrozenInstanceError

import pytest
from commercevision_tool_runtime import (
    ToolAuditLevel,
    ToolAuthorizationFacts,
    ToolAuthorizationReason,
    ToolCostClass,
    ToolDefinition,
    ToolIntentAuthorizer,
    ToolIntentCandidate,
    ToolRegistry,
)
from pydantic import BaseModel, ConfigDict, Field


class _GenerateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    count: int = Field(ge=1, le=4)


class _GenerateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    command_id: str


class _AssetInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    asset_version_id: str


def _not_executed(context, invocation):
    del context, invocation
    raise AssertionError("authorization must not execute a tool")


def _no_resources(arguments: BaseModel) -> tuple[str, ...]:
    del arguments
    return ()


def _asset_resource(arguments: BaseModel) -> tuple[str, ...]:
    typed = _AssetInput.model_validate(arguments)
    return (typed.asset_version_id,)


def _authorizer() -> ToolIntentAuthorizer:
    definition = ToolDefinition(
        name="fixture.image.generate",
        version="1.0",
        description="future fixture image command",
        input_schema=_GenerateInput.model_json_schema(),
        output_schema=_GenerateOutput.model_json_schema(),
        implementation=_not_executed,
        input_model=_GenerateInput,
        output_model=_GenerateOutput,
        required_scopes=frozenset({"image.generate"}),
        allowed_nodes=frozenset({"execute_tool"}),
        resource_resolver=_no_resources,
        provider="fixture",
        cost_class=ToolCostClass.LOW,
        audit_level=ToolAuditLevel.METADATA,
    )
    return ToolIntentAuthorizer(
        registry=ToolRegistry([definition]),
        policy_version="tool-intent-policy-v1",
    )


def _facts(**changes: object) -> ToolAuthorizationFacts:
    values: dict[str, object] = {
        "workspace_id": "workspace",
        "actor_id": "actor",
        "workflow_id": "workflow",
        "workflow_version": 9,
        "creative_plan_id": "plan",
        "creative_plan_version_id": "plan-version",
        "creative_plan_version": 2,
        "approval_id": "approval",
        "node": "execute_tool",
        "granted_scopes": frozenset({"image.generate"}),
        "authorized_resource_ids": frozenset(),
        "allowed_providers": frozenset({"fixture"}),
        "allowed_cost_classes": frozenset({ToolCostClass.LOW}),
        "remaining_quota_units": 5,
        "remaining_budget_units": 5,
    }
    values.update(changes)
    return ToolAuthorizationFacts(**values)  # type: ignore[arg-type]


def _candidate(**changes: object) -> ToolIntentCandidate:
    values: dict[str, object] = {
        "intent_key": "hero-fixture-image",
        "tool_name": "fixture.image.generate",
        "schema_version": "1.0",
        "purpose": "Generate one fixture candidate",
        "arguments_json": '{"count":1}',
        "estimated_cost_units": 1,
    }
    values.update(changes)
    return ToolIntentCandidate(**values)  # type: ignore[arg-type]


def test_authorization_is_pure_immutable_and_deterministic() -> None:
    authorizer = _authorizer()

    first = authorizer.authorize(candidate=_candidate(), facts=_facts())
    second = authorizer.authorize(candidate=_candidate(), facts=_facts())

    assert first == second
    assert first.allowed is True
    assert first.reason is ToolAuthorizationReason.ALLOWED
    assert first.arguments_json == '{"count":1}'
    assert first.resource_ids == ()
    assert first.idempotency_key.startswith("tool-intent:v1:")
    assert first.audit.argument_sha256
    assert not hasattr(first.audit, "arguments_json")
    assert not hasattr(first.audit, "purpose")
    with pytest.raises(FrozenInstanceError):
        first.allowed = False  # type: ignore[misc]


@pytest.mark.parametrize(
    ("candidate", "facts", "reason"),
    [
        (
            _candidate(tool_name="malicious.registered.by.prompt"),
            _facts(),
            ToolAuthorizationReason.REGISTRY_DENIED,
        ),
        (
            _candidate(schema_version="999.0"),
            _facts(),
            ToolAuthorizationReason.REGISTRY_DENIED,
        ),
        (
            _candidate(arguments_json='{"count":1,"workspace_id":"other"}'),
            _facts(),
            ToolAuthorizationReason.INVALID_ARGUMENTS,
        ),
        (
            _candidate(arguments_json='{"count":1,"url":"https://attacker.invalid"}'),
            _facts(),
            ToolAuthorizationReason.INVALID_ARGUMENTS,
        ),
        (
            _candidate(arguments_json='{"count":1,"path":"/etc/passwd"}'),
            _facts(),
            ToolAuthorizationReason.INVALID_ARGUMENTS,
        ),
        (
            _candidate(arguments_json='{"count":1,"sql":"DROP TABLE workflows"}'),
            _facts(),
            ToolAuthorizationReason.INVALID_ARGUMENTS,
        ),
        (
            _candidate(arguments_json='{"count":1,"object_key":"foreign/object"}'),
            _facts(),
            ToolAuthorizationReason.INVALID_ARGUMENTS,
        ),
        (
            _candidate(arguments_json='{"count":5}'),
            _facts(),
            ToolAuthorizationReason.INVALID_ARGUMENTS,
        ),
        (
            _candidate(arguments_json='{"count":1,"count":2}'),
            _facts(),
            ToolAuthorizationReason.INVALID_ARGUMENTS,
        ),
        (
            _candidate(estimated_cost_units=6),
            _facts(),
            ToolAuthorizationReason.QUOTA_EXCEEDED,
        ),
        (
            _candidate(estimated_cost_units=6),
            _facts(remaining_quota_units=10),
            ToolAuthorizationReason.BUDGET_EXCEEDED,
        ),
        (
            _candidate(),
            _facts(allowed_providers=frozenset({"other-provider"})),
            ToolAuthorizationReason.PROVIDER_DENIED,
        ),
    ],
)
def test_authorization_fails_closed_without_future_command(
    candidate: ToolIntentCandidate,
    facts: ToolAuthorizationFacts,
    reason: ToolAuthorizationReason,
) -> None:
    decision = _authorizer().authorize(candidate=candidate, facts=facts)

    assert decision.allowed is False
    assert decision.reason is reason
    assert decision.arguments_json is None
    assert decision.resource_ids == ()
    assert decision.idempotency_key is None


def test_resource_authority_is_narrowed_and_audited_only_as_hashes() -> None:
    definition = ToolDefinition(
        name="assets.use",
        version="1.0",
        description="use one exact Asset version",
        input_schema=_AssetInput.model_json_schema(),
        output_schema={},
        implementation=None,
        input_model=_AssetInput,
        allowed_nodes=frozenset({"execute_tool"}),
        resource_resolver=_asset_resource,
        cost_class=ToolCostClass.LOW,
        audit_level=ToolAuditLevel.RESOURCE_IDENTITIES,
    )
    authorizer = ToolIntentAuthorizer(
        registry=ToolRegistry([definition]),
        policy_version="tool-intent-policy-v1",
    )
    foreign_candidate = _candidate(
        tool_name="assets.use",
        arguments_json='{"asset_version_id":"other-workspace:asset-v1"}',
    )
    allowed_candidate = _candidate(
        tool_name="assets.use",
        arguments_json='{"asset_version_id":"workspace:asset-v1"}',
    )

    denied = authorizer.authorize(
        candidate=foreign_candidate,
        facts=_facts(authorized_resource_ids=frozenset({"workspace:asset-v1"})),
    )
    allowed = authorizer.authorize(
        candidate=allowed_candidate,
        facts=_facts(authorized_resource_ids=frozenset({"workspace:asset-v1"})),
    )

    assert denied.reason is ToolAuthorizationReason.RESOURCE_DENIED
    assert denied.idempotency_key is None
    assert allowed.allowed is True
    assert allowed.resource_ids == ("workspace:asset-v1",)
    assert allowed.audit.audit_level is ToolAuditLevel.RESOURCE_IDENTITIES
    assert len(allowed.audit.resource_identity_sha256s) == 1
    assert "workspace:asset-v1" not in repr(allowed.audit)


def test_future_command_key_is_fenced_by_exact_authority_facts() -> None:
    authorizer = _authorizer()
    baseline = authorizer.authorize(candidate=_candidate(), facts=_facts())

    assert (
        baseline.idempotency_key
        != authorizer.authorize(
            candidate=_candidate(),
            facts=_facts(creative_plan_version_id="plan-version-3", creative_plan_version=3),
        ).idempotency_key
    )
    assert (
        baseline.idempotency_key
        != authorizer.authorize(
            candidate=_candidate(),
            facts=_facts(approval_id="approval-2"),
        ).idempotency_key
    )


def test_resource_resolver_failure_returns_auditable_denial() -> None:
    def broken_resolver(arguments: BaseModel) -> tuple[str, ...]:
        del arguments
        raise RuntimeError("authority adapter unavailable")

    definition = ToolDefinition(
        name="assets.broken",
        version="1.0",
        description="broken resolver fixture",
        input_schema=_AssetInput.model_json_schema(),
        output_schema={},
        implementation=None,
        input_model=_AssetInput,
        allowed_nodes=frozenset({"execute_tool"}),
        resource_resolver=broken_resolver,
        cost_class=ToolCostClass.LOW,
        audit_level=ToolAuditLevel.METADATA,
    )
    decision = ToolIntentAuthorizer(
        registry=ToolRegistry([definition]),
        policy_version="tool-intent-policy-v1",
    ).authorize(
        candidate=_candidate(
            tool_name="assets.broken",
            arguments_json='{"asset_version_id":"workspace:asset-v1"}',
        ),
        facts=_facts(),
    )

    assert decision.allowed is False
    assert decision.reason is ToolAuthorizationReason.INVALID_ARGUMENTS
    assert decision.idempotency_key is None
