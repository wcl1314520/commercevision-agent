"""Server-side tool policy and transaction boundary checks."""

from __future__ import annotations

import json
from collections.abc import Callable

from .errors import ToolPolicyError
from .models import ToolInvocation


class ToolPolicy:
    def __init__(
        self,
        *,
        version: str,
        allowed_tools: frozenset[str],
        max_argument_bytes: int = 64 * 1024,
        transaction_active: Callable[[], bool] | None = None,
    ) -> None:
        if max_argument_bytes < 1:
            raise ValueError("tool argument byte limit must be positive")
        self.version = version
        self.allowed_tools = allowed_tools
        self.max_argument_bytes = max_argument_bytes
        self._transaction_active = transaction_active or (lambda: False)

    def validate(self, invocation: ToolInvocation) -> None:
        if invocation.policy_version != self.version:
            raise ToolPolicyError(
                f"policy version mismatch: {invocation.policy_version} != {self.version}"
            )
        if invocation.tool_name not in self.allowed_tools:
            raise ToolPolicyError(f"tool is not allowed: {invocation.tool_name}")
        if not invocation.reason.strip():
            raise ToolPolicyError("tool invocation reason is required")
        argument_bytes = len(
            json.dumps(
                invocation.arguments,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if argument_bytes > self.max_argument_bytes:
            raise ToolPolicyError("tool arguments exceed the configured byte limit")
        if self._transaction_active():
            raise ToolPolicyError(
                "external tool execution cannot run inside a database transaction"
            )
