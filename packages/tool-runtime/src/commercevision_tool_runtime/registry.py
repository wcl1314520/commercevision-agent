"""Static versioned tool registry."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel

from .errors import ToolRegistryError
from .models import ToolExecutionContext, ToolInvocation, ToolResult

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)


class ToolImplementation(Protocol):
    def __call__(
        self,
        context: ToolExecutionContext,
        invocation: ToolInvocation,
    ) -> ToolResult: ...


class ToolCostClass(StrEnum):
    """Server-owned relative cost classification used during authorization."""

    LOW = "low"
    STANDARD = "standard"
    HIGH = "high"


class ToolAuditLevel(StrEnum):
    """Maximum non-sensitive detail recorded for an authorization decision."""

    METADATA = "metadata"
    RESOURCE_IDENTITIES = "resource_identities"


class ToolResourceResolver(Protocol):
    """Resolve typed planner arguments to trusted resource identities."""

    def __call__(self, arguments: BaseModel) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    version: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    implementation: ToolImplementation | None
    input_model: type[BaseModel] | None = None
    output_model: type[BaseModel] | None = None
    required_scopes: frozenset[str] = frozenset()
    allowed_nodes: frozenset[str] = frozenset()
    resource_resolver: ToolResourceResolver | None = None
    provider: str | None = None
    cost_class: ToolCostClass = ToolCostClass.HIGH
    audit_level: ToolAuditLevel = ToolAuditLevel.METADATA
    maximum_output_bytes: int = 256 * 1024
    enabled: bool = True

    def __post_init__(self) -> None:
        if _IDENTIFIER_PATTERN.fullmatch(self.name) is None:
            raise ValueError("tool name identifier is invalid")
        if _IDENTIFIER_PATTERN.fullmatch(self.version) is None:
            raise ValueError("tool version identifier is invalid")
        if not isinstance(self.allowed_nodes, frozenset) or any(
            _IDENTIFIER_PATTERN.fullmatch(node) is None for node in self.allowed_nodes
        ):
            raise ValueError("tool node identifier is invalid")
        if self.maximum_output_bytes < 1:
            raise ValueError("tool output byte limit must be positive")
        if self.provider is not None and (not self.provider or len(self.provider) > 128):
            raise ValueError("tool provider identity is invalid")


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

    def resolve_for_node(self, name: str, version: str, *, node: str) -> ToolDefinition:
        """Resolve a tool only when its server definition is safe for planning."""

        definition = self.resolve(name, version)
        if definition.input_model is None or definition.resource_resolver is None:
            raise ToolRegistryError(f"tool is not planner-authorizable: {name}@{version}")
        if definition.input_schema != definition.input_model.model_json_schema():
            raise ToolRegistryError(
                f"tool input schema does not match its typed model: {name}@{version}"
            )
        if node not in definition.allowed_nodes:
            raise ToolRegistryError(f"tool is not allowed from node: {name}@{version}")
        return definition

    def list(self) -> list[ToolDefinition]:
        return sorted(self._definitions.values(), key=lambda item: (item.name, item.version))
