"""Tool gateway combining registry resolution, policy, and execution."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from .errors import ToolExecutionError, ToolPolicyError
from .models import ToolExecutionContext, ToolInvocation, ToolResult
from .policy import ToolPolicy
from .registry import ToolRegistry


def stable_tool_key(
    *,
    workflow_id: str,
    step_key: str,
    tool_name: str,
    tool_version: str,
    policy_version: str,
    arguments: Mapping[str, Any],
) -> str:
    canonical = json.dumps(arguments, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    raw = (
        f"{workflow_id}|{step_key}|{tool_name}|{tool_version}|{policy_version}|{canonical}"
    ).encode()
    return hashlib.sha256(raw).hexdigest()


class ToolExecutionGateway:
    def __init__(self, *, registry: ToolRegistry, policy: ToolPolicy) -> None:
        self.registry = registry
        self.policy = policy

    def execute(
        self,
        *,
        context: ToolExecutionContext,
        invocation: ToolInvocation,
    ) -> ToolResult:
        self.policy.validate(invocation)
        definition = self.registry.resolve(invocation.tool_name, invocation.tool_version)
        if definition.implementation is None:
            raise ToolExecutionError("tool execution adapter is unavailable")
        if not definition.required_scopes.issubset(context.scopes):
            raise ToolPolicyError("tool scope is not authorized")
        if definition.input_model is not None:
            try:
                validated = definition.input_model.model_validate(invocation.arguments)
            except ValidationError as exc:
                raise ToolExecutionError("tool input schema validation failed") from exc
            invocation = ToolInvocation(
                tool_name=invocation.tool_name,
                tool_version=invocation.tool_version,
                arguments=validated.model_dump(mode="python"),
                idempotency_key=invocation.idempotency_key,
                policy_version=invocation.policy_version,
                reason=invocation.reason,
            )
        result = definition.implementation(context, invocation)
        if (
            result.tool_name != definition.name
            or result.tool_version != definition.version
            or result.idempotency_key != invocation.idempotency_key
        ):
            raise ToolExecutionError("tool result identity is inconsistent")
        if definition.output_model is not None:
            try:
                output = definition.output_model.model_validate(result.output).model_dump(
                    mode="json"
                )
            except ValidationError as exc:
                raise ToolExecutionError("tool output schema validation failed") from exc
            result = ToolResult(
                tool_name=result.tool_name,
                tool_version=result.tool_version,
                idempotency_key=result.idempotency_key,
                output=output,
                provider_request_id=result.provider_request_id,
                cost_amount_minor=result.cost_amount_minor,
                currency=result.currency,
                completed_at=result.completed_at,
            )
        output_bytes = len(
            json.dumps(
                result.output, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        )
        if output_bytes > min(definition.maximum_output_bytes, context.maximum_output_bytes):
            raise ToolExecutionError("tool output exceeds its byte limit")
        return result
