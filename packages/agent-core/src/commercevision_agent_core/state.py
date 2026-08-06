"""Versioned, reference-oriented Agent state."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from commercevision_contracts import WorkspaceId
from commercevision_contracts.workflow import generation_batch_checkpoint_generation
from pydantic import BaseModel, ConfigDict, Field, model_validator


def _is_canonical_uuid(value: str) -> bool:
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


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
    creative_plan_version_id: str | None = None
    creative_plan_version: int | None = Field(default=None, ge=1)
    plan_iteration: int = Field(default=0, ge=0, le=10)
    plan_decision: str | None = None
    generation_iteration: int = Field(default=0, ge=0, le=10)
    generation_attempt_refs: list[str] = Field(default_factory=list)
    generation_batch_id: str | None = None
    generation_checkpoint_generation: str | None = None
    candidate_refs: list[str] = Field(default_factory=list)
    evaluation_report_ref: str | None = None
    result_decision: str | None = None
    export_ref: str | None = None
    current_node: str = "validate_input"
    initial_entry_reason: Literal[
        "WORKFLOW_CREATED",
        "PRODUCT_BRIEF_CONFIRMED",
        "GENERATION_CANDIDATES_READY",
    ] = "WORKFLOW_CREATED"

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
        elif self.initial_entry_reason == "WORKFLOW_CREATED" and any(
            value is not None
            for value in (
                *version_identity,
                self.product_brief_approval_id,
                self.product_brief_checkpoint_generation,
                self.initial_step_id,
            )
        ):
            raise ValueError("workflow-created entry cannot carry ProductBrief authority")
        plan_identity = (
            self.creative_plan_ref,
            self.creative_plan_version_id,
            self.creative_plan_version,
        )
        if any(value is not None for value in plan_identity) and not all(
            value is not None for value in plan_identity
        ):
            raise ValueError("Creative Plan continuation identity must be complete")
        if self.initial_entry_reason == "GENERATION_CANDIDATES_READY":
            if not all(
                value is not None
                for value in (
                    *plan_identity,
                    self.generation_batch_id,
                    self.generation_checkpoint_generation,
                )
            ):
                raise ValueError("generation candidate authority must be complete")
            assert self.generation_batch_id is not None
            if not _is_canonical_uuid(self.generation_batch_id):
                raise ValueError("generation candidate authority is invalid")
            expected_generation = generation_batch_checkpoint_generation(
                workspace_id=self.workspace_id,
                generation_batch_id=self.generation_batch_id,
            )
            if self.generation_checkpoint_generation != expected_generation:
                raise ValueError("generation checkpoint generation is invalid")
            if not self.candidate_refs or len(set(self.candidate_refs)) != len(self.candidate_refs):
                raise ValueError("generation candidate authority is invalid")
            candidate_ids = tuple(
                reference.removeprefix("mysql://candidate-images/")
                for reference in self.candidate_refs
            )
            if any(
                not reference.startswith("mysql://candidate-images/")
                or not _is_canonical_uuid(candidate_id)
                for reference, candidate_id in zip(self.candidate_refs, candidate_ids, strict=True)
            ):
                raise ValueError("generation candidate authority is invalid")
            if any(
                value is not None
                for value in (
                    self.product_brief_approval_id,
                    self.product_brief_checkpoint_generation,
                    self.initial_step_id,
                )
            ):
                raise ValueError(
                    "generation candidate entry cannot carry prior continuation authority"
                )
        elif any(
            value is not None
            for value in (
                self.generation_batch_id,
                self.generation_checkpoint_generation,
            )
        ):
            raise ValueError("non-generation entry cannot carry Generation Batch authority")
        return self
