"""Static versioned tool registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel

from .errors import ToolRegistryError
from .models import ToolExecutionContext, ToolInvocation, ToolResult


class ToolImplementation(Protocol):
    def __call__(
        self,
        context: ToolExecutionContext,
        invocation: ToolInvocation,
    ) -> ToolResult: ...


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    version: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    implementation: ToolImplementation
    input_model: type[BaseModel] | None = None
    output_model: type[BaseModel] | None = None
    required_scopes: frozenset[str] = frozenset()
    maximum_output_bytes: int = 256 * 1024
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.maximum_output_bytes < 1:
            raise ValueError("tool output byte limit must be positive")


class ToolRegistry:
    """Resolve only explicitly registered and enabled tools."""

    def __init__(self, definitions: list[ToolDefinition] | None = None) -> None:
        self._definitions: dict[tuple[str, str], ToolDefinition] = {}
        for definition in definitions or []:
            self.register(definition)

    def register(self, definition: ToolDefinition) -> None:
        key = (definition.name, definition.version)
        if key in self._definitions:
            raise ToolRegistryError(
                f"duplicate tool definition: {definition.name}@{definition.version}"
            )
        self._definitions[key] = definition

    def resolve(self, name: str, version: str) -> ToolDefinition:
        definition = self._definitions.get((name, version))
        if definition is None:
            raise ToolRegistryError(f"unknown tool: {name}@{version}")
        if not definition.enabled:
            raise ToolRegistryError(f"disabled tool: {name}@{version}")
        return definition

    def list(self) -> list[ToolDefinition]:
        return sorted(self._definitions.values(), key=lambda item: (item.name, item.version))
