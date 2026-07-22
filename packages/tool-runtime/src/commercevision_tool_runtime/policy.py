"""Server-side tool policy and transaction boundary checks."""

from __future__ import annotations

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
        if self._transaction_active():
            raise ToolPolicyError(
                "external tool execution cannot run inside a database transaction"
            )
