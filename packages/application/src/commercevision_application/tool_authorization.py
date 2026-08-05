"""Application seam for authorizing every intent in one exact approved Creative Plan."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from commercevision_domain.workflow.errors import ApprovalConflictError
from commercevision_tool_runtime import (
    ToolAuthorizationDecision,
    ToolAuthorizationFacts,
    ToolAuthorizationReason,
    ToolCostClass,
    ToolIntentAuthorizer,
    ToolIntentCandidate,
)

from .planning_observability import NullPlanningObserver, PlanningObserver
from .workflows import CreativePlanExecutionClaim


class CreativePlanExecutionAuthorityPort(Protocol):
    def validate_creative_plan_execution_claim(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
        creative_plan_version: int,
        approval_id: str,
    ) -> CreativePlanExecutionClaim: ...


@dataclass(frozen=True, slots=True)
class ToolAuthorizationEntitlements:
    """Current server-derived Rights, provider, quota, and budget facts."""

    granted_scopes: frozenset[str]
    authorized_resource_ids: frozenset[str]
    allowed_providers: frozenset[str]
    allowed_cost_classes: frozenset[ToolCostClass]
    remaining_quota_units: int
    remaining_budget_units: int

    def __post_init__(self) -> None:
        for value, name in (
            (self.granted_scopes, "scopes"),
            (self.authorized_resource_ids, "resource identities"),
            (self.allowed_providers, "providers"),
            (self.allowed_cost_classes, "cost classes"),
        ):
            if not isinstance(value, frozenset):
                raise ValueError(f"Tool authorization {name} must be immutable")
        if self.remaining_quota_units < 0 or self.remaining_budget_units < 0:
            raise ValueError("Tool authorization quota and budget cannot be negative")


class ToolAuthorizationEntitlementsPort(Protocol):
    def for_approved_plan(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        creative_plan_id: str,
    ) -> ToolAuthorizationEntitlements: ...


class ConfiguredToolAuthorizationEntitlements:
    """Serve immutable deployment policy facts through the Rights authority seam."""

    def __init__(self, entitlements: ToolAuthorizationEntitlements) -> None:
        self._entitlements = entitlements

    def for_approved_plan(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        creative_plan_id: str,
    ) -> ToolAuthorizationEntitlements:
        if not workspace_id or not actor_id or not creative_plan_id:
            raise ValueError("Tool authorization authority identity is incomplete")
        return self._entitlements


@dataclass(frozen=True, slots=True)
class ToolAuthorizationPolicy:
    version: str
    node: str
    maximum_intents: int = 16

    def __post_init__(self) -> None:
        if not self.version or len(self.version) > 128:
            raise ValueError("Tool authorization policy version is invalid")
        if not self.node or len(self.node) > 128:
            raise ValueError("Tool authorization node is invalid")
        if not 1 <= self.maximum_intents <= 192:
            raise ValueError("Tool authorization intent limit is invalid")


@dataclass(frozen=True, slots=True)
class PlanToolAuthorizationResult:
    workflow_id: str
    workflow_version: int
    creative_plan_id: str
    creative_plan_version_id: str
    creative_plan_version: int
    approval_id: str
    decisions: tuple[ToolAuthorizationDecision, ...]

    @property
    def allowed(self) -> bool:
        return all(item.allowed for item in self.decisions)


class PlanToolAuthorizationService:
    """Revalidate MySQL authority, then apply pure policy without executing a tool."""

    def __init__(
        self,
        *,
        execution_authority: CreativePlanExecutionAuthorityPort,
        entitlements: ToolAuthorizationEntitlementsPort,
        authorizer: ToolIntentAuthorizer,
        policy: ToolAuthorizationPolicy,
        observer: PlanningObserver | None = None,
    ) -> None:
        if authorizer.policy_version != policy.version:
            raise ValueError("Tool authorization policy versions do not match")
        self._execution_authority = execution_authority
        self._entitlements = entitlements
        self._authorizer = authorizer
        self._policy = policy
        self._observer = observer or NullPlanningObserver()

    def authorize_plan(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
        creative_plan_version: int,
        approval_id: str,
    ) -> PlanToolAuthorizationResult:
        with self._observer.observe(
            step="tool.policy",
            workflow_id=workflow_id,
            plan_id=creative_plan_id,
            plan_version=creative_plan_version,
            approval_id=approval_id,
            policy_id=self._policy.version,
        ):
            try:
                result = self._authorize_plan(
                    workspace_id=workspace_id,
                    workflow_id=workflow_id,
                    creative_plan_id=creative_plan_id,
                    creative_plan_version=creative_plan_version,
                    approval_id=approval_id,
                )
            except ApprovalConflictError:
                self._observer.record_approval(outcome="stale")
                raise
            for decision in result.decisions:
                self._observer.record_policy(
                    outcome=("allowed" if decision.allowed else "denied"),
                    reason=decision.reason.value,
                )
            return result

    def _authorize_plan(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
        creative_plan_version: int,
        approval_id: str,
    ) -> PlanToolAuthorizationResult:
        claim = self._execution_authority.validate_creative_plan_execution_claim(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            creative_plan_id=creative_plan_id,
            creative_plan_version=creative_plan_version,
            approval_id=approval_id,
        )
        plan = claim.plan
        if (
            plan.workspace_id != workspace_id
            or plan.workflow_id != workflow_id
            or plan.creative_plan_id != creative_plan_id
            or plan.version_number != creative_plan_version
            or claim.approval.id != approval_id
        ):
            raise ApprovalConflictError(
                "execution authority returned a mismatched Creative Plan claim"
            )
        entitlements = self._entitlements.for_approved_plan(
            workspace_id=plan.workspace_id,
            actor_id=claim.approval.approved_by,
            creative_plan_id=plan.creative_plan_id,
        )
        candidates = tuple(
            ToolIntentCandidate(
                intent_key=intent.intent_key,
                tool_name=intent.tool_name,
                schema_version=intent.schema_version,
                purpose=intent.purpose,
                arguments_json=intent.arguments_json,
                estimated_cost_units=intent.estimated_cost_units,
            )
            for direction in plan.payload.directions
            for intent in direction.tool_intents
        )
        remaining_quota = entitlements.remaining_quota_units
        remaining_budget = entitlements.remaining_budget_units
        decisions: list[ToolAuthorizationDecision] = []
        base_facts = ToolAuthorizationFacts(
            workspace_id=plan.workspace_id,
            actor_id=claim.approval.approved_by,
            workflow_id=plan.workflow_id,
            workflow_version=claim.workflow_version,
            creative_plan_id=plan.creative_plan_id,
            creative_plan_version_id=plan.id,
            creative_plan_version=plan.version_number,
            approval_id=claim.approval.id,
            node=self._policy.node,
            granted_scopes=entitlements.granted_scopes,
            authorized_resource_ids=entitlements.authorized_resource_ids,
            allowed_providers=entitlements.allowed_providers,
            allowed_cost_classes=entitlements.allowed_cost_classes,
            remaining_quota_units=remaining_quota,
            remaining_budget_units=remaining_budget,
        )
        if len(candidates) > self._policy.maximum_intents:
            limit_decisions = tuple(
                self._authorizer.deny(
                    candidate=candidate,
                    facts=base_facts,
                    reason=ToolAuthorizationReason.INTENT_LIMIT_EXCEEDED,
                )
                for candidate in candidates
            )
            return PlanToolAuthorizationResult(
                workflow_id=plan.workflow_id,
                workflow_version=claim.workflow_version,
                creative_plan_id=plan.creative_plan_id,
                creative_plan_version_id=plan.id,
                creative_plan_version=plan.version_number,
                approval_id=claim.approval.id,
                decisions=limit_decisions,
            )
        for candidate in candidates:
            facts = replace(
                base_facts,
                remaining_quota_units=remaining_quota,
                remaining_budget_units=remaining_budget,
            )
            decision = self._authorizer.authorize(candidate=candidate, facts=facts)
            decisions.append(decision)
            if decision.allowed:
                remaining_quota -= candidate.estimated_cost_units
                remaining_budget -= candidate.estimated_cost_units

        return PlanToolAuthorizationResult(
            workflow_id=plan.workflow_id,
            workflow_version=claim.workflow_version,
            creative_plan_id=plan.creative_plan_id,
            creative_plan_version_id=plan.id,
            creative_plan_version=plan.version_number,
            approval_id=claim.approval.id,
            decisions=tuple(decisions),
        )
