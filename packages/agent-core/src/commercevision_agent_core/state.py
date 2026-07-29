"""Versioned, reference-oriented Agent state."""

from __future__ import annotations

from typing import Any, Literal

from commercevision_contracts import WorkspaceId
from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    product_brief_version_id: str | None = None
    product_brief_version_number: int | None = Field(default=None, ge=1)
    product_brief_approval_id: str | None = None
    product_brief_checkpoint_generation: str | None = None
    initial_step_id: str | None = None
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
    initial_entry_reason: Literal["WORKFLOW_CREATED", "PRODUCT_BRIEF_CONFIRMED"] = (
        "WORKFLOW_CREATED"
    )

    @model_validator(mode="after")
    def validate_product_brief_continuation(self) -> FixtureAgentState:
        version_identity = (
            self.product_brief_version_id,
            self.product_brief_version_number,
        )
        if any(value is not None for value in version_identity) and not all(
            value is not None for value in version_identity
        ):
            raise ValueError("ProductBrief continuation identity must be complete")
        if self.initial_entry_reason == "PRODUCT_BRIEF_CONFIRMED":
            if not all(
                value is not None
                for value in (
                    self.product_brief_ref,
                    *version_identity,
                )
            ):
                raise ValueError("confirmed ProductBrief entry requires an exact identity")
            if self.product_brief_checkpoint_generation is None or not (
                self.product_brief_checkpoint_generation.startswith("product-brief:v1:")
                and len(self.product_brief_checkpoint_generation) == 81
            ):
                raise ValueError("confirmed ProductBrief checkpoint generation is invalid")
            if self.current_node == "retrieve_references" and self.initial_step_id is None:
                raise ValueError("confirmed ProductBrief entry requires an initial node claim")
        elif any(
            value is not None
            for value in (
                *version_identity,
                self.product_brief_approval_id,
                self.product_brief_checkpoint_generation,
                self.initial_step_id,
            )
        ):
            raise ValueError("workflow-created entry cannot carry ProductBrief authority")
        return self
