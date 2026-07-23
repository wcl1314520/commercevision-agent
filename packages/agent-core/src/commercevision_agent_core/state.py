"""Versioned, reference-oriented Agent state."""

from typing import Any, Literal

from commercevision_contracts import WorkspaceId
from pydantic import BaseModel, ConfigDict, Field


class FixtureAgentState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    workflow_id: str
    workflow_version: int = Field(ge=1)
    workspace_id: WorkspaceId
    actor_id: str
    trace_id: str
    input_ref: str | None = None
    fixture_config: dict[str, Any] = Field(default_factory=dict)
    product_brief_ref: str | None = None
    retrieved_asset_refs: list[str] = Field(default_factory=list)
    creative_plan_ref: str | None = None
    plan_iteration: int = Field(default=0, ge=0, le=10)
    plan_decision: str | None = None
    generation_iteration: int = Field(default=0, ge=0, le=10)
    generation_attempt_refs: list[str] = Field(default_factory=list)
    candidate_refs: list[str] = Field(default_factory=list)
    evaluation_report_ref: str | None = None
    result_decision: str | None = None
    export_ref: str | None = None
    current_node: str = "validate_input"
