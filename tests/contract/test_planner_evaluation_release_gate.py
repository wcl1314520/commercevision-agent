from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from commercevision_application.fixture_planner import (
    DeterministicFixturePlanner,
    FixturePlannerRequest,
)
from commercevision_domain import (
    PlanningContextPolicy,
    PlanningContextSource,
    PlanningContextSourceKind,
    PromptRevision,
    PromptTemplateVariable,
    build_planning_context,
)
from commercevision_evaluation import load_planner_evaluation
from commercevision_tool_runtime import (
    ToolAuthorizationFacts,
    ToolAuthorizationReason,
    ToolCostClass,
    ToolIntentAuthorizer,
    ToolIntentCandidate,
    ToolRegistry,
    fixture_image_intent_definition,
)

ROOT = Path(__file__).resolve().parents[2]
DATASET_ROOT = ROOT / "evaluation" / "planner" / "ci-v1"
PROMPT_CONTENT = "Plan from {{ planning_context }} into {{ output_schema }}."


def _sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _prompt() -> PromptRevision:
    now = datetime(2026, 8, 5, 8, tzinfo=UTC)
    return (
        PromptRevision.create(
            workspace_id="planner-evaluation",
            prompt_id="creative-planner",
            semantic_revision="1.0.0",
            node="CREATE_CREATIVE_PLAN",
            category_applicability=("beauty", "automotive-parts"),
            model_family_applicability=("fixture-planner",),
            input_schema_version="planning-context.v1",
            output_schema_version="creative-plan.v1",
            policy_version="prompt-policy.v1",
            content=PROMPT_CONTENT,
            variables=(
                PromptTemplateVariable(name="planning_context", required=True),
                PromptTemplateVariable(name="output_schema", required=True),
            ),
            created_by="planner-eval",
            change_summary="Planner evaluation fixture",
            now=now,
        )
        .submit_for_review(
            expected_version=1,
            actor_id="planner-eval",
            now=now + timedelta(seconds=1),
        )
        .stage(
            expected_version=2,
            reviewer_id="planner-reviewer",
            now=now + timedelta(seconds=2),
        )
        .publish(
            expected_version=3,
            actor_id="planner-release",
            now=now + timedelta(seconds=3),
        )
    )


def _request(case, *, surface: str | None = None, malicious_text: str = ""):
    facts = [{"path": fact.path, "value": fact.value} for fact in case.product_brief.facts]
    if surface in {"source-text", "ocr-evidence", "user-edit"}:
        facts.append({"path": f"evaluation.{surface}", "value": malicious_text})
    product_content = {"category": case.category, "fields": facts}
    product = PlanningContextSource.create(
        kind=PlanningContextSourceKind.PRODUCT_BRIEF,
        source_id=case.product_brief.reference.id,
        version_number=case.product_brief.reference.version,
        content_sha256=(
            _sha256(product_content)
            if surface in {"source-text", "ocr-evidence", "user-edit"}
            else case.product_brief.reference.sha256
        ),
        content=product_content,
    )
    brand = None
    if case.brand_profile is not None:
        rules = list(case.brand_profile.rules)
        if surface == "brand-rule":
            rules.append(malicious_text)
        brand_content = {"rules": rules}
        brand = PlanningContextSource.create(
            kind=PlanningContextSourceKind.BRAND_PROFILE,
            source_id=case.brand_profile.reference.id,
            version_number=case.brand_profile.reference.version,
            content_sha256=(
                _sha256(brand_content)
                if surface == "brand-rule"
                else case.brand_profile.reference.sha256
            ),
            content=brand_content,
        )
    citations = []
    for rank, citation in enumerate(case.retrieval_citations, start=1):
        reason = malicious_text if surface == "retrieval-reason" and rank == 1 else citation.reason
        citation_content = {"reason": reason}
        citations.append(
            PlanningContextSource.create(
                kind=PlanningContextSourceKind.RETRIEVAL_CITATION,
                source_id=citation.asset_version_id,
                version_number=None,
                content_sha256=_sha256(citation_content),
                content=citation_content,
                authority_id=citation.rights_record_id,
                authority_version=citation.rights_record_version,
                retrieval_run_id=case.retrieval_run_id,
                retrieval_policy_version="retrieval-policy-v1",
                retrieval_rank=rank,
                citation_id=citation.citation_id,
                image_count=1,
            )
        )
    context = build_planning_context(
        workspace_id=case.workspace_id,
        workflow_id=case.workflow_id,
        product_brief=product,
        brand_profile=brand,
        retrieval_citations=citations,
        policy=PlanningContextPolicy(
            version=case.planning_context.policy_version,
            maximum_tokens=case.planning_context.maximum_tokens,
            maximum_images=case.planning_context.maximum_images,
        ),
    )
    return FixturePlannerRequest(
        context=context,
        prompt=_prompt(),
        retrieval_run_id=case.retrieval_run_id,
    )


def test_committed_observations_match_current_provider_free_fixture_planner() -> None:
    run = load_planner_evaluation(
        DATASET_ROOT / "manifest.json",
        DATASET_ROOT / "fixtures.json",
        DATASET_ROOT / "observations.json",
        profile="ci",
    )
    observations = {item.case_id: item for item in run.observations}
    planner = DeterministicFixturePlanner()

    for case in run.suite.cases:
        observation = observations[case.case_id]
        first = planner.plan(_request(case))
        second = planner.plan(_request(case))
        assert first.payload.payload_sha256 == second.payload.payload_sha256
        assert observation.payload_sha256_runs == (
            first.payload.payload_sha256,
            second.payload.payload_sha256,
        )
        assert asdict(first.provenance) == asdict(observation.provenance)
        assert {
            intent.tool_name
            for direction in first.payload.directions
            for intent in direction.tool_intents
        } == set(observation.tool_intents)
        assert all(
            intent.schema_version == "1.0"
            and intent.arguments_json == '{"count":1}'
            and intent.estimated_cost_units == 1
            for direction in first.payload.directions
            for intent in direction.tool_intents
        )
        assert all(
            getattr(observation.security, field) == 0 for field in observation.security.__slots__
        )
        assert {direction.image_role.value for direction in first.payload.directions} == set(
            observation.direction_roles
        )

        malicious_observations = {item.surface: item for item in observation.malicious_variants}
        for variant in case.malicious_variants:
            injected_first = planner.plan(
                _request(case, surface=variant.surface, malicious_text=variant.text)
            )
            injected_second = planner.plan(
                _request(case, surface=variant.surface, malicious_text=variant.text)
            )
            observed = malicious_observations[variant.surface]
            assert observed.payload_sha256_runs == (
                injected_first.payload.payload_sha256,
                injected_second.payload.payload_sha256,
            )
            encoded_payload = json.dumps(injected_first.payload.to_canonical_data())
            assert variant.text not in encoded_payload
            assert {
                intent.tool_name
                for direction in injected_first.payload.directions
                for intent in direction.tool_intents
            } == {"fixture.image.generate"}
            assert all(
                getattr(observed.security, field) == 0 for field in observed.security.__slots__
            )


def test_user_edit_injection_cannot_register_or_execute_a_tool() -> None:
    run = load_planner_evaluation(
        DATASET_ROOT / "manifest.json",
        DATASET_ROOT / "fixtures.json",
        DATASET_ROOT / "observations.json",
        profile="ci",
    )
    user_edit = next(
        variant
        for case in run.suite.cases
        for variant in case.malicious_variants
        if variant.surface == "user-edit"
    )
    decision = ToolIntentAuthorizer(
        registry=ToolRegistry([fixture_image_intent_definition()]),
        policy_version="tool-intent-policy-v1",
    ).authorize(
        candidate=ToolIntentCandidate(
            intent_key="user-edit-injection",
            tool_name="shell.exec",
            schema_version="1.0",
            purpose=user_edit.text,
            arguments_json='{"count":1}',
            estimated_cost_units=100,
        ),
        facts=ToolAuthorizationFacts(
            workspace_id="planner-evaluation",
            actor_id="planner-evaluator",
            workflow_id="evaluation-workflow",
            workflow_version=1,
            creative_plan_id="evaluation-plan",
            creative_plan_version_id="evaluation-plan-version",
            creative_plan_version=1,
            approval_id="evaluation-approval",
            node="execute_tool",
            granted_scopes=frozenset({"image.generate"}),
            authorized_resource_ids=frozenset(),
            allowed_providers=frozenset({"fixture"}),
            allowed_cost_classes=frozenset({ToolCostClass.LOW}),
            remaining_quota_units=1,
            remaining_budget_units=1,
        ),
    )

    assert decision.allowed is False
    assert decision.reason is ToolAuthorizationReason.REGISTRY_DENIED
    assert decision.arguments_json is None
    assert decision.resource_ids == ()
    assert decision.idempotency_key is None


def test_python_ci_runs_planner_gate_and_retains_verified_reports() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "uv run commercevision-planner-eval" in workflow
    assert "evaluation/planner/ci-v1/manifest.json" in workflow
    assert "evaluation/planner/ci-v1/fixtures.json" in workflow
    assert "evaluation/planner/ci-v1/observations.json" in workflow
    assert ".artifacts/planner-evaluation/planner-ci-v1.json" in workflow
    assert ".artifacts/planner-evaluation/planner-ci-v1.md" in workflow
    assert "name: planner-evaluation-ci" in workflow
    assert "test_planner_evaluation_release_gate.py -q" in workflow


def test_hidden_release_profile_is_external_and_documented() -> None:
    ignore_rules = (ROOT / ".gitignore").read_text(encoding="utf-8")
    evaluation_docs = (ROOT / "docs" / "03-ai" / "evaluation-and-replay.md").read_text(
        encoding="utf-8"
    )
    release_docs = (ROOT / "docs" / "05-deployment" / "ci-cd-and-release.md").read_text(
        encoding="utf-8"
    )

    assert "evaluation/planner/hidden-release/" in ignore_rules
    assert "commercevision-planner-eval" in evaluation_docs
    assert "evaluation/planner/hidden-release/manifest.json" in evaluation_docs
    assert "--profile release" in evaluation_docs
    assert "planner-evaluation-release" in release_docs
