"""Tool gateway combining registry resolution, policy, and execution."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from .models import ToolExecutionContext, ToolInvocation, ToolResult
from .policy import ToolPolicy
from .registry import ToolRegistry


def stable_tool_key(
    *, workflow_id: str, step_key: str, tool_name: str, arguments: Mapping[str, Any]
) -> str:
    canonical = json.dumps(arguments, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    raw = f"{workflow_id}|{step_key}|{tool_name}|{canonical}".encode()
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
        return definition.implementation(context, invocation)
