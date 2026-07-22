"""Deterministic fixture tool used to prove durable execution semantics."""

from __future__ import annotations

import hashlib
import time

from .errors import ToolExecutionError
from .models import ToolExecutionContext, ToolInvocation, ToolResult


class FixtureImageTool:
    name = "fixture.generate_image"
    version = "1.0.0"

    def __call__(
        self,
        context: ToolExecutionContext,
        invocation: ToolInvocation,
    ) -> ToolResult:
        arguments = invocation.arguments
        delay = float(arguments.get("delay_seconds", 0))
        if delay < 0 or delay > 120:
            raise ToolExecutionError("delay_seconds must be between 0 and 120")
        if delay:
            time.sleep(delay)
        if arguments.get("fail") == "retryable":
            raise ToolExecutionError("fixture retryable failure", retryable=True)
        if arguments.get("fail") == "permanent":
            raise ToolExecutionError("fixture permanent failure")

        count = int(arguments.get("count", 3))
        if count < 1 or count > 10:
            raise ToolExecutionError("count must be between 1 and 10")
        seed = hashlib.sha256(
            f"{context.workflow_id}:{invocation.idempotency_key}".encode()
        ).hexdigest()
        candidates = [
            {
                "asset_ref": f"fixture://{context.workflow_id}/{seed[:16]}/{index}",
                "width": 1280,
                "height": 1280,
                "mime_type": "image/png",
                "sha256": hashlib.sha256(f"{seed}:{index}".encode()).hexdigest(),
            }
            for index in range(count)
        ]
        return ToolResult(
            tool_name=self.name,
            tool_version=self.version,
            idempotency_key=invocation.idempotency_key,
            output={
                "provider": "fixture",
                "candidates": candidates,
                "request": {
                    key: value
                    for key, value in arguments.items()
                    if key not in {"delay_seconds", "fail"}
                },
            },
            provider_request_id=f"fixture-{seed[:20]}",
        )
