"""Pure fail-closed authorization for untrusted Planner Tool Intents."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Never

from .errors import ToolRegistryError
from .registry import ToolAuditLevel, ToolCostClass, ToolRegistry

_MAX_ARGUMENT_BYTES = 16 * 1024
_MAX_RESOURCE_IDENTITIES = 32


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Tool Intent arguments contain a duplicate key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Never:
    raise ValueError(f"Tool Intent arguments contain non-finite JSON: {value}")


class ToolAuthorizationReason(StrEnum):
    ALLOWED = "ALLOWED"
    REGISTRY_DENIED = "REGISTRY_DENIED"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    RIGHTS_DENIED = "RIGHTS_DENIED"
    RESOURCE_DENIED = "RESOURCE_DENIED"
    PROVIDER_DENIED = "PROVIDER_DENIED"
    COST_CLASS_DENIED = "COST_CLASS_DENIED"
    INTENT_LIMIT_EXCEEDED = "INTENT_LIMIT_EXCEEDED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


@dataclass(frozen=True, slots=True)
class ToolIntentCandidate:
    """Bounded Planner output; every field remains untrusted."""

    intent_key: str
    tool_name: str
    schema_version: str
    purpose: str
    arguments_json: str
    estimated_cost_units: int

    def __post_init__(self) -> None:
        for value, name in (
            (self.intent_key, "intent key"),
            (self.tool_name, "tool name"),
            (self.schema_version, "schema version"),
        ):
            if not isinstance(value, str) or not value or len(value) > 128:
                raise ValueError(f"Tool Intent {name} is invalid")
        if not isinstance(self.purpose, str) or not self.purpose.strip() or len(self.purpose) > 512:
            raise ValueError("Tool Intent purpose is invalid")
        if (
            not isinstance(self.arguments_json, str)
            or len(self.arguments_json.encode("utf-8")) > _MAX_ARGUMENT_BYTES
        ):
            raise ValueError("Tool Intent arguments are invalid")
        if (
            not isinstance(self.estimated_cost_units, int)
            or isinstance(self.estimated_cost_units, bool)
            or not 1 <= self.estimated_cost_units <= 1_000_000
        ):
            raise ValueError("Tool Intent cost estimate is invalid")


@dataclass(frozen=True, slots=True)
class ToolAuthorizationFacts:
    """Server-derived authority facts; never populate this from Planner output."""

    workspace_id: str
    actor_id: str
    workflow_id: str
    workflow_version: int
    creative_plan_id: str
    creative_plan_version_id: str
    creative_plan_version: int
    approval_id: str
    node: str
    granted_scopes: frozenset[str]
    authorized_resource_ids: frozenset[str]
    allowed_providers: frozenset[str]
    allowed_cost_classes: frozenset[ToolCostClass]
    remaining_quota_units: int
    remaining_budget_units: int

    def __post_init__(self) -> None:
        for value, name in (
            (self.workspace_id, "workspace"),
            (self.actor_id, "actor"),
            (self.workflow_id, "Workflow"),
            (self.creative_plan_id, "Creative Plan"),
            (self.creative_plan_version_id, "Creative Plan version"),
            (self.approval_id, "Approval"),
            (self.node, "node"),
        ):
            if not isinstance(value, str) or not value or len(value) > 128:
                raise ValueError(f"trusted {name} identity is invalid")
        if self.workflow_version < 1 or self.creative_plan_version < 1:
            raise ValueError("trusted authority versions must be positive")
        if self.remaining_quota_units < 0 or self.remaining_budget_units < 0:
            raise ValueError("trusted Tool Intent budgets cannot be negative")
        for collection, name in (
            (self.granted_scopes, "scopes"),
            (self.authorized_resource_ids, "resources"),
            (self.allowed_providers, "providers"),
            (self.allowed_cost_classes, "cost classes"),
        ):
            if not isinstance(collection, frozenset):
                raise ValueError(f"trusted {name} must be immutable")


@dataclass(frozen=True, slots=True)
class ToolAuthorizationAudit:
    """Bounded audit projection with hashes and identities, never raw Planner text."""

    policy_version: str
    workspace_id: str
    actor_id: str
    workflow_id: str
    workflow_version: int
    creative_plan_id: str
    creative_plan_version_id: str
    creative_plan_version: int
    approval_id: str
    intent_key: str
    tool_name: str
    schema_version: str
    allowed: bool
    reason: ToolAuthorizationReason
    audit_level: ToolAuditLevel
    argument_sha256: str
    resource_count: int
    resource_identity_sha256s: tuple[str, ...]
    estimated_cost_units: int


@dataclass(frozen=True, slots=True)
class ToolAuthorizationDecision:
    """Immutable future-command authorization; contains no executable side effect."""

    allowed: bool
    reason: ToolAuthorizationReason
    policy_version: str
    intent_key: str
    tool_name: str
    schema_version: str
    arguments_json: str | None
    resource_ids: tuple[str, ...]
    idempotency_key: str | None
    audit: ToolAuthorizationAudit


class ToolIntentAuthorizer:
    """Authorize Planner proposals exclusively against server-owned definitions and facts."""

    def __init__(self, *, registry: ToolRegistry, policy_version: str) -> None:
        if not policy_version or len(policy_version) > 128:
            raise ValueError("Tool Intent policy version is invalid")
        self._registry = registry
        self._policy_version = policy_version

    @property
    def policy_version(self) -> str:
        return self._policy_version

    def authorize(
        self,
        *,
        candidate: ToolIntentCandidate,
        facts: ToolAuthorizationFacts,
    ) -> ToolAuthorizationDecision:
        argument_sha256 = hashlib.sha256(candidate.arguments_json.encode("utf-8")).hexdigest()
        try:
            definition = self._registry.resolve_for_node(
                candidate.tool_name,
                candidate.schema_version,
                node=facts.node,
            )
        except ToolRegistryError:
            return self._deny(
                candidate, facts, ToolAuthorizationReason.REGISTRY_DENIED, argument_sha256
            )
        try:
            arguments = json.loads(
                candidate.arguments_json,
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            )
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be an object")
            assert definition.input_model is not None
            typed_arguments = definition.input_model.model_validate(
                arguments,
                strict=True,
                extra="forbid",
            )
            canonical_arguments = json.dumps(
                typed_arguments.model_dump(mode="json"),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            if len(canonical_arguments.encode("utf-8")) > _MAX_ARGUMENT_BYTES:
                raise ValueError("typed arguments exceed the byte limit")
            assert definition.resource_resolver is not None
            resources = definition.resource_resolver(typed_arguments)
            if (
                not isinstance(resources, tuple)
                or len(resources) > _MAX_RESOURCE_IDENTITIES
                or len(set(resources)) != len(resources)
                or any(
                    not isinstance(resource_id, str) or not resource_id or len(resource_id) > 128
                    for resource_id in resources
                )
            ):
                raise ValueError("resource resolution is invalid")
            resource_ids = tuple(sorted(resources))
        except Exception:
            # This boundary deliberately contains parser, schema, and resolver failures.
            return self._deny(
                candidate,
                facts,
                ToolAuthorizationReason.INVALID_ARGUMENTS,
                argument_sha256,
                audit_level=definition.audit_level,
            )

        if not definition.required_scopes.issubset(facts.granted_scopes):
            return self._deny(
                candidate,
                facts,
                ToolAuthorizationReason.RIGHTS_DENIED,
                argument_sha256,
                audit_level=definition.audit_level,
                resource_ids=resource_ids,
            )
        if not set(resource_ids).issubset(facts.authorized_resource_ids):
            return self._deny(
                candidate,
                facts,
                ToolAuthorizationReason.RESOURCE_DENIED,
                argument_sha256,
                audit_level=definition.audit_level,
                resource_ids=resource_ids,
            )
        if definition.provider is not None and definition.provider not in facts.allowed_providers:
            return self._deny(
                candidate,
                facts,
                ToolAuthorizationReason.PROVIDER_DENIED,
                argument_sha256,
                audit_level=definition.audit_level,
                resource_ids=resource_ids,
            )
        if definition.cost_class not in facts.allowed_cost_classes:
            return self._deny(
                candidate,
                facts,
                ToolAuthorizationReason.COST_CLASS_DENIED,
                argument_sha256,
                audit_level=definition.audit_level,
                resource_ids=resource_ids,
            )
        if candidate.estimated_cost_units > facts.remaining_quota_units:
            return self._deny(
                candidate,
                facts,
                ToolAuthorizationReason.QUOTA_EXCEEDED,
                argument_sha256,
                audit_level=definition.audit_level,
                resource_ids=resource_ids,
            )
        if candidate.estimated_cost_units > facts.remaining_budget_units:
            return self._deny(
                candidate,
                facts,
                ToolAuthorizationReason.BUDGET_EXCEEDED,
                argument_sha256,
                audit_level=definition.audit_level,
                resource_ids=resource_ids,
            )

        idempotency_key = self._idempotency_key(
            candidate=candidate,
            facts=facts,
            canonical_arguments=canonical_arguments,
            resource_ids=resource_ids,
            provider=definition.provider,
        )
        authorized_argument_sha256 = hashlib.sha256(canonical_arguments.encode("utf-8")).hexdigest()
        audit = self._audit(
            candidate=candidate,
            facts=facts,
            allowed=True,
            reason=ToolAuthorizationReason.ALLOWED,
            audit_level=definition.audit_level,
            argument_sha256=authorized_argument_sha256,
            resource_ids=resource_ids,
        )
        return ToolAuthorizationDecision(
            allowed=True,
            reason=ToolAuthorizationReason.ALLOWED,
            policy_version=self._policy_version,
            intent_key=candidate.intent_key,
            tool_name=candidate.tool_name,
            schema_version=candidate.schema_version,
            arguments_json=canonical_arguments,
            resource_ids=resource_ids,
            idempotency_key=idempotency_key,
            audit=audit,
        )

    def deny(
        self,
        *,
        candidate: ToolIntentCandidate,
        facts: ToolAuthorizationFacts,
        reason: ToolAuthorizationReason,
    ) -> ToolAuthorizationDecision:
        """Create a safe plan-level denial without resolving or executing a tool."""

        if reason is ToolAuthorizationReason.ALLOWED:
            raise ValueError("an explicit Tool Intent denial requires a denial reason")
        argument_sha256 = hashlib.sha256(candidate.arguments_json.encode("utf-8")).hexdigest()
        return self._deny(candidate, facts, reason, argument_sha256)

    def _deny(
        self,
        candidate: ToolIntentCandidate,
        facts: ToolAuthorizationFacts,
        reason: ToolAuthorizationReason,
        argument_sha256: str,
        *,
        audit_level: ToolAuditLevel = ToolAuditLevel.METADATA,
        resource_ids: tuple[str, ...] = (),
    ) -> ToolAuthorizationDecision:
        return ToolAuthorizationDecision(
            allowed=False,
            reason=reason,
            policy_version=self._policy_version,
            intent_key=candidate.intent_key,
            tool_name=candidate.tool_name,
            schema_version=candidate.schema_version,
            arguments_json=None,
            resource_ids=(),
            idempotency_key=None,
            audit=self._audit(
                candidate=candidate,
                facts=facts,
                allowed=False,
                reason=reason,
                audit_level=audit_level,
                argument_sha256=argument_sha256,
                resource_ids=resource_ids,
            ),
        )

    def _audit(
        self,
        *,
        candidate: ToolIntentCandidate,
        facts: ToolAuthorizationFacts,
        allowed: bool,
        reason: ToolAuthorizationReason,
        audit_level: ToolAuditLevel,
        argument_sha256: str,
        resource_ids: tuple[str, ...],
    ) -> ToolAuthorizationAudit:
        return ToolAuthorizationAudit(
            policy_version=self._policy_version,
            workspace_id=facts.workspace_id,
            actor_id=facts.actor_id,
            workflow_id=facts.workflow_id,
            workflow_version=facts.workflow_version,
            creative_plan_id=facts.creative_plan_id,
            creative_plan_version_id=facts.creative_plan_version_id,
            creative_plan_version=facts.creative_plan_version,
            approval_id=facts.approval_id,
            intent_key=candidate.intent_key,
            tool_name=candidate.tool_name,
            schema_version=candidate.schema_version,
            allowed=allowed,
            reason=reason,
            audit_level=audit_level,
            argument_sha256=argument_sha256,
            resource_count=len(resource_ids),
            resource_identity_sha256s=(
                tuple(
                    hashlib.sha256(resource_id.encode("utf-8")).hexdigest()
                    for resource_id in resource_ids
                )
                if audit_level is ToolAuditLevel.RESOURCE_IDENTITIES
                else ()
            ),
            estimated_cost_units=candidate.estimated_cost_units,
        )

    def _idempotency_key(
        self,
        *,
        candidate: ToolIntentCandidate,
        facts: ToolAuthorizationFacts,
        canonical_arguments: str,
        resource_ids: tuple[str, ...],
        provider: str | None,
    ) -> str:
        payload = json.dumps(
            {
                "policy_version": self._policy_version,
                "workspace_id": facts.workspace_id,
                "actor_id": facts.actor_id,
                "workflow_id": facts.workflow_id,
                "workflow_version": facts.workflow_version,
                "creative_plan_id": facts.creative_plan_id,
                "creative_plan_version_id": facts.creative_plan_version_id,
                "creative_plan_version": facts.creative_plan_version,
                "approval_id": facts.approval_id,
                "node": facts.node,
                "intent_key": candidate.intent_key,
                "tool_name": candidate.tool_name,
                "schema_version": candidate.schema_version,
                "arguments_json": canonical_arguments,
                "resource_ids": resource_ids,
                "provider": provider,
                "cost_units": candidate.estimated_cost_units,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"tool-intent:v1:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"
