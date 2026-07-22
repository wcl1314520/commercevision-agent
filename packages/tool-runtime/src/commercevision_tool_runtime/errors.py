"""Tool runtime errors classified for retry and governance."""


class ToolRuntimeError(Exception):
    """Base tool runtime failure."""


class ToolRegistryError(ToolRuntimeError):
    """Tool is missing or has an incompatible version."""


class ToolPolicyError(ToolRuntimeError):
    """Tool invocation violates a server-side policy."""


class ToolExecutionError(ToolRuntimeError):
    """Tool execution failed."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable
