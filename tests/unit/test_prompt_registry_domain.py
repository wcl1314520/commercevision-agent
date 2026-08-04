from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest
from commercevision_domain import (
    ConcurrencyError,
    InvalidTransitionError,
    PromptProductionPointer,
    PromptRevision,
    PromptRevisionStatus,
    PromptTemplateVariable,
)

NOW = datetime(2026, 8, 4, 10, 0, tzinfo=UTC)


def test_admin_creates_immutable_traceable_prompt_revision() -> None:
    content = (
        "You are the CommerceVision creative planner.\n"
        "Use {{ planning_context }}.\n"
        "Return {{ output_schema }} only."
    )

    revision = PromptRevision.create(
        workspace_id="planning-domain",
        prompt_id="creative-planner",
        semantic_revision="1.0.0",
        node="CREATE_CREATIVE_PLAN",
        category_applicability=("beauty", "automotive-parts"),
        model_family_applicability=("fixture-planner",),
        input_schema_version="planning-context.v1",
        output_schema_version="creative-plan.v1",
        policy_version="prompt-policy.v1",
        content=content,
        variables=(
            PromptTemplateVariable(name="planning_context", required=True),
            PromptTemplateVariable(name="output_schema", required=True),
        ),
        created_by="admin-42",
        change_summary="Initial deterministic Planner prompt",
        now=NOW,
    )

    assert revision.status is PromptRevisionStatus.DRAFT
    assert revision.version == 1
    assert revision.content_sha256 == (
        "6721d8700c7d7acbd005d8ffafb9f5a8d601dde3f16aa0152d6ba976b2a4d3d3"
    )
    assert revision.created_at == NOW
    assert revision.variables[0].name == "output_schema"
    with pytest.raises(FrozenInstanceError):
        revision.version = 2  # type: ignore[misc]


def test_prompt_revision_lifecycle_is_version_checked_and_auditable() -> None:
    draft = PromptRevision.create(
        workspace_id="planning-domain",
        prompt_id="creative-planner",
        semantic_revision="1.0.0",
        node="CREATE_CREATIVE_PLAN",
        category_applicability=("beauty",),
        model_family_applicability=("fixture-planner",),
        input_schema_version="planning-context.v1",
        output_schema_version="creative-plan.v1",
        policy_version="prompt-policy.v1",
        content="Plan from {{ planning_context }} into {{ output_schema }}.",
        variables=(
            PromptTemplateVariable(name="planning_context", required=True),
            PromptTemplateVariable(name="output_schema", required=True),
        ),
        created_by="admin-42",
        change_summary="Initial deterministic Planner prompt",
        now=NOW,
    )

    review = draft.submit_for_review(
        expected_version=1,
        actor_id="admin-42",
        now=NOW + timedelta(minutes=1),
    )
    staging = review.stage(
        expected_version=2,
        reviewer_id="reviewer-7",
        now=NOW + timedelta(minutes=2),
    )
    production = staging.publish(
        expected_version=3,
        actor_id="release-manager",
        now=NOW + timedelta(minutes=3),
    )
    deprecated = production.deprecate(
        expected_version=4,
        actor_id="release-manager",
        now=NOW + timedelta(minutes=4),
    )

    assert review.status is PromptRevisionStatus.REVIEW
    assert staging.status is PromptRevisionStatus.STAGING
    assert production.status is PromptRevisionStatus.PRODUCTION
    assert deprecated.status is PromptRevisionStatus.DEPRECATED
    assert deprecated.version == 5
    assert deprecated.submitted_by == "admin-42"
    assert deprecated.reviewed_by == "reviewer-7"
    assert deprecated.published_by == "release-manager"
    assert deprecated.deprecated_by == "release-manager"
    assert draft.status is PromptRevisionStatus.DRAFT
    assert draft.version == 1

    with pytest.raises(ConcurrencyError):
        draft.submit_for_review(
            expected_version=2,
            actor_id="admin-42",
            now=NOW + timedelta(minutes=1),
        )
    with pytest.raises(InvalidTransitionError):
        draft.publish(
            expected_version=1,
            actor_id="release-manager",
            now=NOW + timedelta(minutes=1),
        )


@pytest.mark.parametrize("variable_name", ["api_key", "provider_secret", "access_token"])
def test_prompt_revision_rejects_secret_template_variables(variable_name: str) -> None:
    with pytest.raises(ValueError, match="secret"):
        PromptRevision.create(
            workspace_id="planning-domain",
            prompt_id="creative-planner",
            semantic_revision="1.0.0",
            node="CREATE_CREATIVE_PLAN",
            category_applicability=("beauty",),
            model_family_applicability=("fixture-planner",),
            input_schema_version="planning-context.v1",
            output_schema_version="creative-plan.v1",
            policy_version="prompt-policy.v1",
            content=f"Never expose {{{{ {variable_name} }}}}.",
            variables=(PromptTemplateVariable(name=variable_name, required=True),),
            created_by="admin-42",
            change_summary="Unsafe secret variable must fail closed",
            now=NOW,
        )


@pytest.mark.parametrize(
    "content",
    [
        "Do not set provider_secret = here.",
        "Never include -----BEGIN PRIVATE KEY----- in a Prompt.",
    ],
)
def test_prompt_revision_rejects_secret_material_in_content(content: str) -> None:
    with pytest.raises(ValueError, match="secret"):
        PromptRevision.create(
            workspace_id="planning-domain",
            prompt_id="creative-planner",
            semantic_revision="1.0.0",
            node="CREATE_CREATIVE_PLAN",
            category_applicability=("beauty",),
            model_family_applicability=("fixture-planner",),
            input_schema_version="planning-context.v1",
            output_schema_version="creative-plan.v1",
            policy_version="prompt-policy.v1",
            content=content,
            variables=(),
            created_by="admin-42",
            change_summary="Unsafe secret material must fail closed",
            now=NOW,
        )


@pytest.mark.parametrize(
    ("content", "variables", "input_schema", "output_schema", "message"),
    [
        ("Plain prompt.", (), "unknown-input.v1", "creative-plan.v1", "unsupported"),
        ("Plain prompt.", (), "planning-context.v1", "unknown-output.v1", "unsupported"),
        ("x" * 32_769, (), "planning-context.v1", "creative-plan.v1", "content is invalid"),
        (
            "Unsafe\tcontrol.",
            (),
            "planning-context.v1",
            "creative-plan.v1",
            "content is invalid",
        ),
        (
            "Unknown {{ missing }}.",
            (),
            "planning-context.v1",
            "creative-plan.v1",
            "placeholders",
        ),
        (
            "{% include 'remote' %}",
            (),
            "planning-context.v1",
            "creative-plan.v1",
            "substitutions only",
        ),
    ],
    ids=[
        "unknown-input-schema",
        "unknown-output-schema",
        "oversized-content",
        "unsafe-control-character",
        "undeclared-placeholder",
        "template-control-block",
    ],
)
def test_prompt_revision_rejects_unsafe_template_contracts(
    content: str,
    variables: tuple[PromptTemplateVariable, ...],
    input_schema: str,
    output_schema: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        PromptRevision.create(
            workspace_id="planning-domain",
            prompt_id="creative-planner",
            semantic_revision="1.0.0",
            node="CREATE_CREATIVE_PLAN",
            category_applicability=("beauty",),
            model_family_applicability=("fixture-planner",),
            input_schema_version=input_schema,
            output_schema_version=output_schema,
            policy_version="prompt-policy.v1",
            content=content,
            variables=variables,
            created_by="admin-42",
            change_summary="Invalid Prompt contract must fail closed",
            now=NOW,
        )


def test_production_pointer_selects_one_exact_published_revision() -> None:
    draft = PromptRevision.create(
        workspace_id="planning-domain",
        prompt_id="creative-planner",
        semantic_revision="1.0.0",
        node="CREATE_CREATIVE_PLAN",
        category_applicability=("beauty",),
        model_family_applicability=("fixture-planner",),
        input_schema_version="planning-context.v1",
        output_schema_version="creative-plan.v1",
        policy_version="prompt-policy.v1",
        content="Plan from {{ planning_context }} into {{ output_schema }}.",
        variables=(
            PromptTemplateVariable(name="planning_context", required=True),
            PromptTemplateVariable(name="output_schema", required=True),
        ),
        created_by="admin-42",
        change_summary="Initial deterministic Planner prompt",
        now=NOW,
    )
    review = draft.submit_for_review(
        expected_version=1,
        actor_id="admin-42",
        now=NOW + timedelta(minutes=1),
    )
    staging = review.stage(
        expected_version=2,
        reviewer_id="reviewer-7",
        now=NOW + timedelta(minutes=2),
    )
    production = staging.publish(
        expected_version=3,
        actor_id="release-manager",
        now=NOW + timedelta(minutes=3),
    )

    pointer = PromptProductionPointer.create(
        revision=production,
        actor_id="release-manager",
        now=NOW + timedelta(minutes=3),
    )

    assert pointer.workspace_id == production.workspace_id
    assert pointer.prompt_id == "creative-planner"
    assert pointer.revision_id == production.id
    assert pointer.semantic_revision == "1.0.0"
    assert pointer.content_sha256 == production.content_sha256
    assert pointer.version == 1
    with pytest.raises(InvalidTransitionError):
        PromptProductionPointer.create(
            revision=draft,
            actor_id="release-manager",
            now=NOW + timedelta(minutes=1),
        )
