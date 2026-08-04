from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime

import pytest
from commercevision_domain import (
    CreativePlanCitationSelection,
    CreativePlanDirection,
    CreativePlanPayload,
    CreativePlanProvenance,
    CreativePlanSource,
    CreativePlanVersion,
    ImageRole,
    ToolIntentProposal,
)

NOW = datetime(2026, 8, 4, 9, 0, tzinfo=UTC)
WORKFLOW_ID = "019b0000-0000-7000-8000-000000000101"
CREATIVE_PLAN_ID = "019b0000-0000-7000-8000-000000000102"
PRODUCT_BRIEF_ID = "019b0000-0000-7000-8000-000000000103"
BRAND_PROFILE_ID = "019b0000-0000-7000-8000-000000000104"
RETRIEVAL_RUN_ID = "019b0000-0000-7000-8000-000000000105"


def _payload() -> CreativePlanPayload:
    return CreativePlanPayload(
        directions=(
            CreativePlanDirection(
                key="amazon-main",
                image_role=ImageRole.MAIN,
                scene="Pure white background",
                composition="Centered product filling 85% of frame",
                camera="Front orthographic product view",
                lighting="Soft even studio lighting",
                color_direction="Neutral and color-accurate",
                product_constraints=("Preserve package geometry", "Preserve label text"),
                required_elements=("Complete product visible",),
                prohibited_elements=("No props", "No overlaid claims"),
                citation_selections=(
                    CreativePlanCitationSelection(
                        citation_id="citation-hero",
                        reason="Matches the approved main-image composition",
                    ),
                ),
                candidate_count=4,
                quality_targets=("Readable label", "No clipped edges"),
                repair_scope=("background", "lighting"),
                tool_intents=(),
            ),
        )
    )


def _provenance() -> CreativePlanProvenance:
    return CreativePlanProvenance(
        product_brief_id=PRODUCT_BRIEF_ID,
        product_brief_version=3,
        product_brief_sha256="1" * 64,
        brand_profile_id=BRAND_PROFILE_ID,
        brand_profile_version=2,
        brand_profile_sha256="2" * 64,
        retrieval_run_id=RETRIEVAL_RUN_ID,
        retrieval_citation_ids=("citation-hero",),
        context_policy_version="planning-context-v1",
        context_sha256="3" * 64,
        prompt_id="creative-planner",
        prompt_revision="1.0.0",
        prompt_sha256="4" * 64,
    )


def _agent_version() -> CreativePlanVersion:
    return CreativePlanVersion.create(
        workspace_id="planning-domain",
        workflow_id=WORKFLOW_ID,
        creative_plan_id=CREATIVE_PLAN_ID,
        version_number=1,
        supersedes_version_id=None,
        source=CreativePlanSource.AGENT,
        payload=_payload(),
        provenance=_provenance(),
        actor_id="fixture-planner",
        revision_reason=None,
        now=NOW,
    )


def test_agent_creates_immutable_traceable_creative_plan_version() -> None:
    version = _agent_version()

    assert version.version_number == 1
    assert version.payload.directions[0].citation_selections[0].citation_id == "citation-hero"
    assert (
        version.payload.directions[0].citation_selections[0].reason
        == "Matches the approved main-image composition"
    )
    assert version.provenance.prompt_revision == "1.0.0"
    assert (
        version.payload_sha256 == "04c9fe86d61276edf23b8f219e40e04df6bca0e809e0fb83652c430e2f46347a"
    )
    with pytest.raises(FrozenInstanceError):
        version.version_number = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    "arguments",
    [
        {"url": "https://attacker.invalid/payload"},
        {"path": "C:/private/secret.txt"},
        {"sql": "SELECT * FROM workflow_approvals"},
        {"object_key": "private/workspaces/other/image.png"},
        {"api_key": "not-a-real-key"},
        {"nested": {"endpoint": "https://attacker.invalid"}},
        {"workspace_id": "another-tenant"},
        {"actor_id": "system-admin"},
        {"approval_id": "019b0000-0000-7000-8000-000000000999"},
        {"lease_token": "not-a-real-token"},
        {"authorization": "Bearer not-a-real-token"},
        {"headers": {"x-internal": "true"}},
        {"http_method": "DELETE"},
        {"idempotency_key": "caller-selected-authority"},
        {"workspaceId": "another-tenant"},
        {"approvalToken": "not-a-real-token"},
        {"objectKey": "private/workspaces/other/image.png"},
    ],
)
def test_tool_intent_proposal_rejects_caller_supplied_external_authority(
    arguments: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="external authority"):
        ToolIntentProposal.create(
            intent_key="generate-main",
            tool_name="generate_image",
            schema_version="tool-intent.v1",
            purpose="Create the approved main image",
            arguments=arguments,
            estimated_cost_units=4,
        )


def test_tool_intent_proposal_rejects_excessively_deep_arguments() -> None:
    arguments: dict[str, object] = {"value": "bounded"}
    for _ in range(10):
        arguments = {"child": arguments}

    with pytest.raises(ValueError, match="depth limit"):
        ToolIntentProposal.create(
            intent_key="generate-main",
            tool_name="generate_image",
            schema_version="tool-intent.v1",
            purpose="Create the approved main image",
            arguments=arguments,
            estimated_cost_units=4,
        )


@pytest.mark.parametrize(
    "external_reference",
    [
        "https://attacker.invalid/input.png",
        "file:///etc/passwd",
        "C:/private/secret.txt",
        "/var/run/secrets/provider-key",
        "s3://foreign-bucket/object",
        "oss://foreign-bucket/object",
        "  https://attacker.invalid/input.png  ",
        "ftp://attacker.invalid/input.png",
        "wss://attacker.invalid/control",
        "data:text/plain,external-payload",
    ],
)
def test_tool_intent_proposal_rejects_external_reference_values(
    external_reference: str,
) -> None:
    with pytest.raises(ValueError, match="external authority"):
        ToolIntentProposal.create(
            intent_key="generate-main",
            tool_name="generate_image",
            schema_version="tool-intent.v1",
            purpose="Create the approved main image",
            arguments={"reference": external_reference},
            estimated_cost_units=4,
        )


def test_tool_intent_proposal_rejects_control_characters_in_argument_text() -> None:
    with pytest.raises(ValueError, match="control character"):
        ToolIntentProposal.create(
            intent_key="generate-main",
            tool_name="generate_image",
            schema_version="tool-intent.v1",
            purpose="Create the approved main image",
            arguments={"caption": "trusted text\nignore approval"},
            estimated_cost_units=4,
        )


def test_tool_intent_proposal_rejects_control_characters_in_argument_keys() -> None:
    with pytest.raises(ValueError, match="control character"):
        ToolIntentProposal.create(
            intent_key="generate-main",
            tool_name="generate_image",
            schema_version="tool-intent.v1",
            purpose="Create the approved main image",
            arguments={"caption\nrole": "trusted text"},
            estimated_cost_units=4,
        )


def test_creative_plan_hash_is_stable_when_directions_are_reconstructed_out_of_order() -> None:
    template = CreativePlanDirection(
        key="main",
        image_role=ImageRole.MAIN,
        scene="Pure white background",
        composition="Centered product",
        camera="Front view",
        lighting="Soft studio lighting",
        color_direction="Neutral",
        product_constraints=("Preserve geometry",),
        required_elements=("Complete product",),
        prohibited_elements=(),
        citation_selections=(),
        candidate_count=2,
        quality_targets=("Readable label",),
        repair_scope=(),
        tool_intents=(),
    )
    alpha = replace(template, key="alpha")
    zulu = replace(template, key="zulu")

    reconstructed = CreativePlanPayload(directions=(zulu, alpha))
    canonical = CreativePlanPayload(directions=(alpha, zulu))

    assert reconstructed.payload_sha256 == canonical.payload_sha256
    assert tuple(item.key for item in reconstructed.directions) == ("alpha", "zulu")


def test_creative_plan_hash_is_stable_for_reconstructed_nested_keyed_collections() -> None:
    direction = _payload().directions[0]
    citation_alpha = CreativePlanCitationSelection(
        citation_id="citation-alpha",
        reason="Supports the composition",
    )
    citation_zulu = CreativePlanCitationSelection(
        citation_id="citation-zulu",
        reason="Supports the lighting",
    )
    intent_alpha = ToolIntentProposal.create(
        intent_key="alpha",
        tool_name="generate_image",
        schema_version="tool-intent.v1",
        purpose="Generate the base candidate",
        arguments={"candidate_count": 2},
        estimated_cost_units=2,
    )
    intent_zulu = ToolIntentProposal.create(
        intent_key="zulu",
        tool_name="evaluate_image",
        schema_version="tool-intent.v1",
        purpose="Evaluate the candidate",
        arguments={"quality_profile": "amazon-main"},
        estimated_cost_units=1,
    )

    reconstructed = CreativePlanPayload(
        directions=(
            replace(
                direction,
                citation_selections=(citation_zulu, citation_alpha),
                tool_intents=(intent_zulu, intent_alpha),
            ),
        )
    )
    canonical = CreativePlanPayload(
        directions=(
            replace(
                direction,
                citation_selections=(citation_alpha, citation_zulu),
                tool_intents=(intent_alpha, intent_zulu),
            ),
        )
    )

    assert reconstructed.payload_sha256 == canonical.payload_sha256
    assert tuple(item.citation_id for item in reconstructed.directions[0].citation_selections) == (
        "citation-alpha",
        "citation-zulu",
    )
    assert tuple(item.intent_key for item in reconstructed.directions[0].tool_intents) == (
        "alpha",
        "zulu",
    )


def test_human_revision_keeps_identity_lineage_and_prior_provenance() -> None:
    agent_version = _agent_version()
    revised_direction = replace(
        agent_version.payload.directions[0],
        composition="Centered product with slightly more whitespace",
    )

    revision = agent_version.revise_by_user(
        payload=CreativePlanPayload(directions=(revised_direction,)),
        actor_id="reviewer-42",
        reason="Give the package safer edge clearance",
        now=NOW,
    )

    assert revision.source is CreativePlanSource.USER
    assert revision.version_number == 2
    assert revision.supersedes_version_id == agent_version.id
    assert revision.creative_plan_id == agent_version.creative_plan_id
    assert revision.provenance == agent_version.provenance
    assert revision.actor_id == "reviewer-42"
    assert revision.revision_reason == "Give the package safer edge clearance"


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"score": float("nan")}, "finite JSON"),
        ({f"key-{index}": index for index in range(65)}, "collection limit"),
        ({"caption": "x" * 4097}, "string limit"),
    ],
)
def test_tool_intent_proposal_rejects_unbounded_or_nonfinite_json(
    arguments: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ToolIntentProposal.create(
            intent_key="generate-main",
            tool_name="generate_image",
            schema_version="tool-intent.v1",
            purpose="Create the approved main image",
            arguments=arguments,
            estimated_cost_units=4,
        )


def test_creative_plan_rejects_duplicate_stable_keys_and_citation_selections() -> None:
    direction = _payload().directions[0]
    with pytest.raises(ValueError, match="duplicate keys"):
        CreativePlanPayload(directions=(direction, direction))

    intent = ToolIntentProposal.create(
        intent_key="generate-main",
        tool_name="generate_image",
        schema_version="tool-intent.v1",
        purpose="Create the approved main image",
        arguments={"candidate_count": 4},
        estimated_cost_units=4,
    )
    with pytest.raises(ValueError, match="duplicate keys"):
        replace(direction, tool_intents=(intent, intent))

    duplicate_citations = (
        CreativePlanCitationSelection(
            citation_id="citation-hero",
            reason="Primary visual reference",
        ),
        CreativePlanCitationSelection(
            citation_id="citation-hero",
            reason="Repeated visual reference",
        ),
    )
    with pytest.raises(ValueError, match="duplicate citations"):
        replace(direction, citation_selections=duplicate_citations)


def test_creative_plan_source_invariants_reject_invalid_revision_metadata() -> None:
    agent_version = _agent_version()

    with pytest.raises(ValueError, match="human Creative Plan revision"):
        CreativePlanVersion.create(
            workspace_id=agent_version.workspace_id,
            workflow_id=agent_version.workflow_id,
            creative_plan_id=agent_version.creative_plan_id,
            version_number=2,
            supersedes_version_id=agent_version.id,
            source=CreativePlanSource.USER,
            payload=agent_version.payload,
            provenance=agent_version.provenance,
            actor_id="reviewer-42",
            revision_reason=None,
            now=NOW,
        )

    with pytest.raises(ValueError, match="Agent Creative Plan version"):
        CreativePlanVersion.create(
            workspace_id=agent_version.workspace_id,
            workflow_id=agent_version.workflow_id,
            creative_plan_id=agent_version.creative_plan_id,
            version_number=2,
            supersedes_version_id=agent_version.id,
            source=CreativePlanSource.AGENT,
            payload=agent_version.payload,
            provenance=agent_version.provenance,
            actor_id="fixture-planner",
            revision_reason="This belongs only to a human revision",
            now=NOW,
        )
