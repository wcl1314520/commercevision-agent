from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from commercevision_application import CreativePlanApplicationService, CreativePlanCursorCodec
from commercevision_contracts import (
    CreativePlanCreateRequestV1,
    CreativePlanRevisionRequestV1,
)
from commercevision_domain import (
    ConcurrencyError,
    CreativePlanDirection,
    CreativePlanPayload,
    CreativePlanProvenance,
    CreativePlanSource,
    CreativePlanVersion,
    ImageRole,
    NotFoundError,
    PlanningContextPolicy,
    PlanningContextSource,
    PlanningContextSourceKind,
    PromptRevision,
    PromptTemplateVariable,
    ToolIntentProposal,
    build_planning_context,
)
from commercevision_persistence import (
    PlanningContextSnapshotRepository,
    SqlAlchemyCreativePlanUnitOfWork,
)
from commercevision_persistence.creative_plan_models import (
    CreativePlanModel,
    CreativePlanVersionModel,
)
from commercevision_persistence.models import AuditEventModel, IdempotencyKeyModel, WorkflowModel
from commercevision_persistence.prompt_registry import PromptRevisionRepository
from commercevision_persistence.retrieval_models import RetrievalRunModel
from sqlalchemy import func, select

pytestmark = pytest.mark.integration


NOW = datetime(2026, 8, 5, 3, 30, tzinfo=UTC)
WORKFLOW_ID = "019b0000-0000-7000-8000-000000000710"
PLAN_ID = "019b0000-0000-7000-8000-000000000713"


def _create_request(version: CreativePlanVersion) -> CreativePlanCreateRequestV1:
    provenance = version.provenance
    return CreativePlanCreateRequestV1.model_validate(
        {
            "workflow_id": version.workflow_id,
            "creative_plan_id": version.creative_plan_id,
            "payload": version.payload.to_canonical_data(),
            "provenance": {
                "product_brief_id": provenance.product_brief_id,
                "product_brief_version": provenance.product_brief_version,
                "product_brief_sha256": provenance.product_brief_sha256,
                "brand_profile_id": provenance.brand_profile_id,
                "brand_profile_version": provenance.brand_profile_version,
                "brand_profile_sha256": provenance.brand_profile_sha256,
                "retrieval_run_id": provenance.retrieval_run_id,
                "retrieval_citation_ids": list(provenance.retrieval_citation_ids),
                "context_policy_version": provenance.context_policy_version,
                "context_sha256": provenance.context_sha256,
                "prompt_id": provenance.prompt_id,
                "prompt_revision": provenance.prompt_revision,
                "prompt_sha256": provenance.prompt_sha256,
            },
            "expected_workflow_version": 7,
            "expected_head_version": 0,
        }
    )


def _version() -> CreativePlanVersion:
    return CreativePlanVersion.create(
        workspace_id="planning-domain",
        workflow_id=WORKFLOW_ID,
        creative_plan_id=PLAN_ID,
        version_number=1,
        supersedes_version_id=None,
        source=CreativePlanSource.AGENT,
        payload=CreativePlanPayload(
            directions=(
                CreativePlanDirection(
                    key="hero",
                    image_role=ImageRole.HERO,
                    scene="Clean studio",
                    composition="Centered product",
                    camera="Eye level",
                    lighting="Soft key light",
                    color_direction="Brand blue",
                    product_constraints=("Preserve packaging",),
                    required_elements=("Product",),
                    prohibited_elements=(),
                    citation_selections=(),
                    candidate_count=1,
                    quality_targets=("Sharp label",),
                    repair_scope=(),
                    tool_intents=(),
                ),
            )
        ),
        provenance=CreativePlanProvenance(
            product_brief_id="019b0000-0000-7000-8000-000000000711",
            product_brief_version=3,
            product_brief_sha256="1" * 64,
            brand_profile_id=None,
            brand_profile_version=None,
            brand_profile_sha256=None,
            retrieval_run_id="019b0000-0000-7000-8000-000000000712",
            retrieval_citation_ids=(),
            context_policy_version="planning-context-v1",
            context_sha256="2" * 64,
            prompt_id="creative-planner",
            prompt_revision="1.0.0",
            prompt_sha256="3" * 64,
        ),
        actor_id="fixture-planner",
        revision_reason=None,
        now=NOW,
    )


def _authorized_bundle():
    product_source = PlanningContextSource.create(
        kind=PlanningContextSourceKind.PRODUCT_BRIEF,
        source_id="019b0000-0000-7000-8000-000000000711",
        version_number=3,
        content_sha256="1" * 64,
        content={"summary": "Approved product facts"},
    )
    snapshot = build_planning_context(
        workspace_id="planning-domain",
        workflow_id=WORKFLOW_ID,
        product_brief=product_source,
        brand_profile=None,
        retrieval_citations=(),
        policy=PlanningContextPolicy(
            version="planning-context-v1",
            maximum_tokens=1_000,
            maximum_images=0,
        ),
    )
    prompt = PromptRevision.create(
        workspace_id="planning-domain",
        prompt_id="creative-planner",
        semantic_revision="1.0.0",
        node="CREATE_CREATIVE_PLAN",
        category_applicability=("beauty",),
        model_family_applicability=("fixture-planner",),
        input_schema_version="planning-context.v1",
        output_schema_version="creative-plan.v1",
        policy_version="prompt-policy.v1",
        content="Transform {{ planning_context }} into {{ output_schema }}.",
        variables=(
            PromptTemplateVariable(name="planning_context", required=True),
            PromptTemplateVariable(name="output_schema", required=True),
        ),
        created_by="prompt-admin",
        change_summary="Initial planner prompt",
        now=NOW - timedelta(seconds=3),
    )
    prompt = prompt.submit_for_review(
        expected_version=1,
        actor_id="prompt-admin",
        now=NOW - timedelta(seconds=2),
    )
    prompt = prompt.stage(
        expected_version=2,
        reviewer_id="prompt-reviewer",
        now=NOW - timedelta(seconds=1),
    )
    prompt = prompt.publish(
        expected_version=3,
        actor_id="prompt-publisher",
        now=NOW,
    )
    version = _version()
    version = CreativePlanVersion(
        id=version.id,
        workspace_id=version.workspace_id,
        workflow_id=version.workflow_id,
        creative_plan_id=version.creative_plan_id,
        version_number=version.version_number,
        supersedes_version_id=version.supersedes_version_id,
        source=version.source,
        payload=version.payload,
        provenance=CreativePlanProvenance(
            product_brief_id=product_source.source_id,
            product_brief_version=product_source.version_number or 0,
            product_brief_sha256=product_source.content_sha256,
            brand_profile_id=None,
            brand_profile_version=None,
            brand_profile_sha256=None,
            retrieval_run_id="019b0000-0000-7000-8000-000000000712",
            retrieval_citation_ids=(),
            context_policy_version=snapshot.policy.version,
            context_sha256=snapshot.context_sha256,
            prompt_id=prompt.prompt_id,
            prompt_revision=prompt.semantic_revision,
            prompt_sha256=prompt.content_sha256,
        ),
        payload_sha256=version.payload_sha256,
        actor_id=version.actor_id,
        revision_reason=version.revision_reason,
        created_at=version.created_at,
    )
    return version, snapshot, prompt


def _seed_authority(session, *, snapshot, prompt, deadline: datetime) -> None:
    session.flush()
    PlanningContextSnapshotRepository(session, clock=lambda: NOW).save(
        snapshot,
        retain_until=deadline,
    )
    PromptRevisionRepository(session).add(prompt)
    session.add(
        RetrievalRunModel(
            id="019b0000-0000-7000-8000-000000000712",
            workspace_id="planning-domain",
            requester_id="fixture-planner",
            query_json={"query": "hero"},
            query_sha256="4" * 64,
            retrieval_policy_version="retrieval-v1",
            complete_hybrid=True,
            degradations_json=[],
            eligible_asset_version_count=0,
            fused_candidate_count=0,
            final_authorized_candidate_count=0,
            latency_ms=1,
            created_at=NOW,
            expires_at=deadline,
        )
    )


def test_real_mysql_first_version_round_trips_through_public_interface(
    integration_database,
) -> None:
    deadline = NOW + timedelta(days=30)
    version, snapshot, prompt = _authorized_bundle()
    with integration_database.session_factory.begin() as session:
        session.add(
            WorkflowModel(
                id=WORKFLOW_ID,
                workspace_id="planning-domain",
                created_by="operator",
                workflow_type="creative-planning",
                status="PLANNING",
                retention_status="ACTIVE",
                current_node="create_plan",
                version=7,
                input_json={},
                result_json=None,
                expires_at=deadline,
                cancellation_requested_at=None,
                created_at=NOW - timedelta(hours=1),
                updated_at=NOW,
            )
        )
        _seed_authority(session, snapshot=snapshot, prompt=prompt, deadline=deadline)
    service = CreativePlanApplicationService(
        lambda: SqlAlchemyCreativePlanUnitOfWork(integration_database.session_factory)
    )
    created = service.append_version(
        version=version,
        expected_workflow_version=7,
        expected_head_version=0,
    )

    reconstructed = service.get_current(
        workspace_id="planning-domain",
        workflow_id=WORKFLOW_ID,
        creative_plan_id=PLAN_ID,
    )

    assert reconstructed == created
    assert reconstructed.head.retain_until == deadline
    assert reconstructed.version.payload.to_canonical_data() == (
        created.version.payload.to_canonical_data()
    )
    assert reconstructed.version.provenance == created.version.provenance


def test_real_mysql_nested_payload_reconstructs_in_canonical_order(
    integration_database,
) -> None:
    deadline = NOW + timedelta(days=30)
    first, snapshot, prompt = _authorized_bundle()
    tool_a = ToolIntentProposal.create(
        intent_key="a-retouch",
        tool_name="retouch-proposal",
        schema_version="1.0.0",
        purpose="Propose label cleanup",
        arguments={"mask": {"areas": ["label", "cap"]}, "strength": 2},
        estimated_cost_units=2,
    )
    tool_z = ToolIntentProposal.create(
        intent_key="z-render",
        tool_name="render-proposal",
        schema_version="1.0.0",
        purpose="Propose the final render",
        arguments={"variants": 2, "style": {"finish": "matte"}},
        estimated_cost_units=5,
    )
    base = first.payload.directions[0]
    payload = CreativePlanPayload(
        directions=(
            replace(base, key="z-detail", image_role=ImageRole.DETAIL),
            replace(base, key="a-hero", tool_intents=(tool_z, tool_a)),
        )
    )
    first = replace(first, payload=payload, payload_sha256=payload.payload_sha256)
    with integration_database.session_factory.begin() as session:
        session.add(
            WorkflowModel(
                id=WORKFLOW_ID,
                workspace_id="planning-domain",
                created_by="operator",
                workflow_type="creative-planning",
                status="PLANNING",
                retention_status="ACTIVE",
                current_node="create_plan",
                version=7,
                input_json={},
                result_json=None,
                expires_at=deadline,
                cancellation_requested_at=None,
                created_at=NOW - timedelta(hours=1),
                updated_at=NOW,
            )
        )
        _seed_authority(session, snapshot=snapshot, prompt=prompt, deadline=deadline)
    service = CreativePlanApplicationService(
        lambda: SqlAlchemyCreativePlanUnitOfWork(integration_database.session_factory)
    )

    service.append_version(
        version=first,
        expected_workflow_version=7,
        expected_head_version=0,
    )
    reconstructed = service.get_version(
        workspace_id=first.workspace_id,
        workflow_id=first.workflow_id,
        creative_plan_id=first.creative_plan_id,
        version_number=1,
    )

    assert reconstructed == first
    assert tuple(item.key for item in reconstructed.payload.directions) == (
        "a-hero",
        "z-detail",
    )
    assert tuple(item.intent_key for item in reconstructed.payload.directions[0].tool_intents) == (
        "a-retouch",
        "z-render",
    )


def test_real_mysql_foreign_workspace_write_is_rejected_without_partial_plan(
    integration_database,
) -> None:
    deadline = NOW + timedelta(days=30)
    first, snapshot, prompt = _authorized_bundle()
    with integration_database.session_factory.begin() as session:
        session.add(
            WorkflowModel(
                id=WORKFLOW_ID,
                workspace_id="planning-domain",
                created_by="operator",
                workflow_type="creative-planning",
                status="PLANNING",
                retention_status="ACTIVE",
                current_node="create_plan",
                version=7,
                input_json={},
                result_json=None,
                expires_at=deadline,
                cancellation_requested_at=None,
                created_at=NOW - timedelta(hours=1),
                updated_at=NOW,
            )
        )
        _seed_authority(session, snapshot=snapshot, prompt=prompt, deadline=deadline)
    foreign = replace(first, workspace_id="planning-domain-foreign")
    service = CreativePlanApplicationService(
        lambda: SqlAlchemyCreativePlanUnitOfWork(integration_database.session_factory)
    )

    with pytest.raises(NotFoundError, match="Workflow"):
        service.append_version(
            version=foreign,
            expected_workflow_version=7,
            expected_head_version=0,
        )

    with integration_database.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(CreativePlanModel)) == 0
        assert session.scalar(select(func.count()).select_from(CreativePlanVersionModel)) == 0


def test_real_mysql_missing_provenance_authority_leaves_no_partial_plan(
    integration_database,
) -> None:
    deadline = NOW + timedelta(days=30)
    with integration_database.session_factory.begin() as session:
        session.add(
            WorkflowModel(
                id=WORKFLOW_ID,
                workspace_id="planning-domain",
                created_by="operator",
                workflow_type="creative-planning",
                status="PLANNING",
                retention_status="ACTIVE",
                current_node="create_plan",
                version=7,
                input_json={},
                result_json=None,
                expires_at=deadline,
                cancellation_requested_at=None,
                created_at=NOW - timedelta(hours=1),
                updated_at=NOW,
            )
        )
    service = CreativePlanApplicationService(
        lambda: SqlAlchemyCreativePlanUnitOfWork(integration_database.session_factory)
    )

    with pytest.raises(NotFoundError, match="provenance authority"):
        service.append_version(
            version=_version(),
            expected_workflow_version=7,
            expected_head_version=0,
        )

    with integration_database.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(CreativePlanModel)) == 0
        assert session.scalar(select(func.count()).select_from(CreativePlanVersionModel)) == 0


def test_real_mysql_duplicate_delivery_and_revision_preserve_ordered_history(
    integration_database,
) -> None:
    deadline = NOW + timedelta(days=30)
    first, snapshot, prompt = _authorized_bundle()
    with integration_database.session_factory.begin() as session:
        session.add(
            WorkflowModel(
                id=WORKFLOW_ID,
                workspace_id="planning-domain",
                created_by="operator",
                workflow_type="creative-planning",
                status="PLANNING",
                retention_status="ACTIVE",
                current_node="create_plan",
                version=7,
                input_json={},
                result_json=None,
                expires_at=deadline,
                cancellation_requested_at=None,
                created_at=NOW - timedelta(hours=1),
                updated_at=NOW,
            )
        )
        _seed_authority(session, snapshot=snapshot, prompt=prompt, deadline=deadline)
    service = CreativePlanApplicationService(
        lambda: SqlAlchemyCreativePlanUnitOfWork(integration_database.session_factory)
    )
    created = service.append_version(
        version=first,
        expected_workflow_version=7,
        expected_head_version=0,
    )
    replayed = service.append_version(
        version=first,
        expected_workflow_version=7,
        expected_head_version=0,
    )
    revised_direction = replace(first.payload.directions[0], scene="Retail shelf")
    second = first.revise_by_user(
        payload=CreativePlanPayload(directions=(revised_direction,)),
        actor_id="creative-reviewer",
        reason="Use the approved retail setting",
        now=NOW + timedelta(seconds=1),
    )
    revised = service.append_version(
        version=second,
        expected_workflow_version=7,
        expected_head_version=1,
    )

    history = service.list_versions(
        workspace_id="planning-domain",
        workflow_id=WORKFLOW_ID,
        creative_plan_id=PLAN_ID,
    )

    assert replayed == created
    assert revised.head.current_version_id == second.id
    assert revised.head.current_version_number == 2
    assert revised.head.version == 2
    assert revised.head.retain_until == created.head.retain_until == deadline
    assert tuple(item.id for item in history) == (first.id, second.id)
    assert history[1].source is CreativePlanSource.USER
    assert history[1].provenance == first.provenance
    with integration_database.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(CreativePlanModel)) == 1
        assert session.scalar(select(func.count()).select_from(CreativePlanVersionModel)) == 2


def test_real_mysql_version_history_uses_signed_keyset_pagination(
    integration_database,
) -> None:
    deadline = NOW + timedelta(days=30)
    first, snapshot, prompt = _authorized_bundle()
    with integration_database.session_factory.begin() as session:
        session.add(
            WorkflowModel(
                id=WORKFLOW_ID,
                workspace_id="planning-domain",
                created_by="operator",
                workflow_type="creative-planning",
                status="PLANNING",
                retention_status="ACTIVE",
                current_node="create_plan",
                version=7,
                input_json={},
                result_json=None,
                expires_at=deadline,
                cancellation_requested_at=None,
                created_at=NOW - timedelta(hours=1),
                updated_at=NOW,
            )
        )
        _seed_authority(session, snapshot=snapshot, prompt=prompt, deadline=deadline)
    cursor_codec = CreativePlanCursorCodec(
        current_key_id="test-current",
        current_secret="c" * 32,
        max_age_seconds=300,
        future_skew_seconds=30,
        clock=lambda: NOW,
    )
    service = CreativePlanApplicationService(
        lambda: SqlAlchemyCreativePlanUnitOfWork(integration_database.session_factory),
        cursor_codec=cursor_codec,
    )
    service.append_version(
        version=first,
        expected_workflow_version=7,
        expected_head_version=0,
    )
    second = first.revise_by_user(
        payload=CreativePlanPayload(
            directions=(replace(first.payload.directions[0], scene="Retail shelf"),)
        ),
        actor_id="reviewer-a",
        reason="Use the approved retail setting",
        now=NOW + timedelta(seconds=1),
    )
    service.append_version(
        version=second,
        expected_workflow_version=7,
        expected_head_version=1,
    )
    third = second.revise_by_user(
        payload=CreativePlanPayload(
            directions=(replace(second.payload.directions[0], scene="Bathroom vanity"),)
        ),
        actor_id="reviewer-b",
        reason="Use the approved bathroom setting",
        now=NOW + timedelta(seconds=2),
    )
    service.append_version(
        version=third,
        expected_workflow_version=7,
        expected_head_version=2,
    )

    first_page = service.list_version_page(
        workspace_id=first.workspace_id,
        workflow_id=first.workflow_id,
        creative_plan_id=first.creative_plan_id,
        limit=2,
        cursor=None,
    )
    assert tuple(item.version_number for item in first_page.items) == (1, 2)
    assert first_page.next_cursor is not None

    second_page = service.list_version_page(
        workspace_id=first.workspace_id,
        workflow_id=first.workflow_id,
        creative_plan_id=first.creative_plan_id,
        limit=2,
        cursor=first_page.next_cursor,
    )
    assert second_page.items == (third,)
    assert second_page.next_cursor is None
    with pytest.raises(ValueError, match="Creative Plan cursor is invalid"):
        service.list_version_page(
            workspace_id="foreign-workspace",
            workflow_id=first.workflow_id,
            creative_plan_id=first.creative_plan_id,
            limit=2,
            cursor=first_page.next_cursor,
        )


def test_real_mysql_concurrent_revisions_allow_one_head_winner_without_orphan(
    integration_database,
) -> None:
    deadline = NOW + timedelta(days=30)
    first, snapshot, prompt = _authorized_bundle()
    with integration_database.session_factory.begin() as session:
        session.add(
            WorkflowModel(
                id=WORKFLOW_ID,
                workspace_id="planning-domain",
                created_by="operator",
                workflow_type="creative-planning",
                status="PLANNING",
                retention_status="ACTIVE",
                current_node="create_plan",
                version=7,
                input_json={},
                result_json=None,
                expires_at=deadline,
                cancellation_requested_at=None,
                created_at=NOW - timedelta(hours=1),
                updated_at=NOW,
            )
        )
        _seed_authority(session, snapshot=snapshot, prompt=prompt, deadline=deadline)
    service = CreativePlanApplicationService(
        lambda: SqlAlchemyCreativePlanUnitOfWork(integration_database.session_factory)
    )
    service.append_version(
        version=first,
        expected_workflow_version=7,
        expected_head_version=0,
    )
    versions = tuple(
        first.revise_by_user(
            payload=CreativePlanPayload(
                directions=(replace(first.payload.directions[0], scene=scene),)
            ),
            actor_id=actor,
            reason=f"Use {scene}",
            now=NOW + timedelta(seconds=1),
        )
        for scene, actor in (
            ("Retail shelf", "reviewer-a"),
            ("Bathroom vanity", "reviewer-b"),
        )
    )
    barrier = Barrier(2)

    def append(version: CreativePlanVersion):
        barrier.wait(timeout=10)
        try:
            return service.append_version(
                version=version,
                expected_workflow_version=7,
                expected_head_version=1,
            )
        except ConcurrencyError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(append, versions))

    winners = tuple(outcome for outcome in outcomes if not isinstance(outcome, Exception))
    losers = tuple(outcome for outcome in outcomes if isinstance(outcome, ConcurrencyError))
    assert len(winners) == 1
    assert len(losers) == 1
    current = service.get_current(
        workspace_id="planning-domain",
        workflow_id=WORKFLOW_ID,
        creative_plan_id=PLAN_ID,
    )
    assert current.head.current_version_id == winners[0].version.id
    assert current.head.version == 2
    with integration_database.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(CreativePlanModel)) == 1
        assert session.scalar(select(func.count()).select_from(CreativePlanVersionModel)) == 2


def test_real_mysql_exact_duplicate_replays_after_retrieval_authority_expires(
    integration_database,
) -> None:
    deadline = NOW + timedelta(days=30)
    first, snapshot, prompt = _authorized_bundle()
    with integration_database.session_factory.begin() as session:
        session.add(
            WorkflowModel(
                id=WORKFLOW_ID,
                workspace_id="planning-domain",
                created_by="operator",
                workflow_type="creative-planning",
                status="PLANNING",
                retention_status="ACTIVE",
                current_node="create_plan",
                version=7,
                input_json={},
                result_json=None,
                expires_at=deadline,
                cancellation_requested_at=None,
                created_at=NOW - timedelta(hours=1),
                updated_at=NOW,
            )
        )
        _seed_authority(session, snapshot=snapshot, prompt=prompt, deadline=deadline)
    service = CreativePlanApplicationService(
        lambda: SqlAlchemyCreativePlanUnitOfWork(integration_database.session_factory)
    )
    created = service.append_version(
        version=first,
        expected_workflow_version=7,
        expected_head_version=0,
    )
    with integration_database.session_factory.begin() as session:
        run = session.get(RetrievalRunModel, first.provenance.retrieval_run_id)
        assert run is not None
        run.expires_at = NOW - timedelta(seconds=1)

    replayed = service.append_version(
        version=first,
        expected_workflow_version=7,
        expected_head_version=0,
    )

    assert replayed == created
    with integration_database.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(CreativePlanModel)) == 1
        assert session.scalar(select(func.count()).select_from(CreativePlanVersionModel)) == 1


def test_real_mysql_revision_never_extends_frozen_plan_retention(
    integration_database,
) -> None:
    deadline = NOW + timedelta(days=30)
    first, snapshot, prompt = _authorized_bundle()
    with integration_database.session_factory.begin() as session:
        session.add(
            WorkflowModel(
                id=WORKFLOW_ID,
                workspace_id="planning-domain",
                created_by="operator",
                workflow_type="creative-planning",
                status="PLANNING",
                retention_status="ACTIVE",
                current_node="create_plan",
                version=7,
                input_json={},
                result_json=None,
                expires_at=deadline,
                cancellation_requested_at=None,
                created_at=NOW - timedelta(hours=1),
                updated_at=NOW,
            )
        )
        _seed_authority(session, snapshot=snapshot, prompt=prompt, deadline=deadline)
    service = CreativePlanApplicationService(
        lambda: SqlAlchemyCreativePlanUnitOfWork(integration_database.session_factory)
    )
    created = service.append_version(
        version=first,
        expected_workflow_version=7,
        expected_head_version=0,
    )
    with integration_database.session_factory.begin() as session:
        workflow = session.get(WorkflowModel, WORKFLOW_ID)
        assert workflow is not None
        workflow.expires_at = deadline + timedelta(days=10)
    second = first.revise_by_user(
        payload=CreativePlanPayload(
            directions=(replace(first.payload.directions[0], scene="Retail shelf"),)
        ),
        actor_id="creative-reviewer",
        reason="Use the approved retail setting",
        now=NOW + timedelta(seconds=1),
    )

    revised = service.append_version(
        version=second,
        expected_workflow_version=7,
        expected_head_version=1,
    )

    assert revised.head.retain_until == created.head.retain_until == deadline
    with integration_database.session_factory() as session:
        retained_until = tuple(
            session.scalars(
                select(CreativePlanVersionModel.retain_until).order_by(
                    CreativePlanVersionModel.version_number
                )
            )
        )
    assert retained_until == (deadline, deadline)


def test_real_mysql_user_revision_is_allowed_while_exact_plan_is_awaiting_review(
    integration_database,
) -> None:
    deadline = NOW + timedelta(days=30)
    first, snapshot, prompt = _authorized_bundle()
    with integration_database.session_factory.begin() as session:
        session.add(
            WorkflowModel(
                id=WORKFLOW_ID,
                workspace_id="planning-domain",
                created_by="operator",
                workflow_type="creative-planning",
                status="PLANNING",
                retention_status="ACTIVE",
                current_node="create_plan",
                version=7,
                input_json={},
                result_json=None,
                expires_at=deadline,
                cancellation_requested_at=None,
                created_at=NOW - timedelta(hours=1),
                updated_at=NOW,
            )
        )
        _seed_authority(session, snapshot=snapshot, prompt=prompt, deadline=deadline)
    service = CreativePlanApplicationService(
        lambda: SqlAlchemyCreativePlanUnitOfWork(integration_database.session_factory)
    )
    service.append_version(
        version=first,
        expected_workflow_version=7,
        expected_head_version=0,
    )
    with integration_database.session_factory.begin() as session:
        workflow = session.get(WorkflowModel, WORKFLOW_ID)
        assert workflow is not None
        workflow.status = "AWAITING_PLAN_APPROVAL"
        workflow.current_node = "approve_plan"
        workflow.version = 8
    second = first.revise_by_user(
        payload=CreativePlanPayload(
            directions=(replace(first.payload.directions[0], scene="Retail shelf"),)
        ),
        actor_id="creative-reviewer",
        reason="Use the approved retail setting",
        now=NOW + timedelta(seconds=1),
    )

    revised = service.append_version(
        version=second,
        expected_workflow_version=8,
        expected_head_version=1,
    )

    assert revised.version.source is CreativePlanSource.USER
    assert revised.head.current_version_number == 2


def test_real_mysql_http_commands_are_idempotent_and_audit_only_aggregates(
    integration_database,
) -> None:
    deadline = NOW + timedelta(days=30)
    fixture, snapshot, prompt = _authorized_bundle()
    with integration_database.session_factory.begin() as session:
        session.add(
            WorkflowModel(
                id=WORKFLOW_ID,
                workspace_id="planning-domain",
                created_by="operator",
                workflow_type="creative-planning",
                status="PLANNING",
                retention_status="ACTIVE",
                current_node="create_plan",
                version=7,
                input_json={},
                result_json=None,
                expires_at=deadline,
                cancellation_requested_at=None,
                created_at=NOW - timedelta(hours=1),
                updated_at=NOW,
            )
        )
        _seed_authority(session, snapshot=snapshot, prompt=prompt, deadline=deadline)
    service = CreativePlanApplicationService(
        lambda: SqlAlchemyCreativePlanUnitOfWork(integration_database.session_factory)
    )
    create_request = _create_request(fixture)
    create_arguments = {
        "workspace_id": fixture.workspace_id,
        "actor_id": "creative-planner",
        "request": create_request,
        "trace_id": "trace-create-http",
        "idempotency_key": "mysql-create-request-001",
    }

    created = service.create_plan(**create_arguments)
    assert service.create_plan(**create_arguments) == created

    with integration_database.session_factory.begin() as session:
        workflow = session.get(WorkflowModel, WORKFLOW_ID)
        assert workflow is not None
        workflow.status = "AWAITING_PLAN_APPROVAL"
        workflow.current_node = "approve_plan"
        workflow.version = 8
    changed_payload = CreativePlanPayload(
        directions=(replace(fixture.payload.directions[0], scene="Approved retail shelf"),)
    )
    revise_request = CreativePlanRevisionRequestV1.model_validate(
        {
            "workflow_id": WORKFLOW_ID,
            "payload": changed_payload.to_canonical_data(),
            "revision_reason": "Use the approved retail setting",
            "expected_workflow_version": 8,
            "expected_head_version": 1,
        }
    )
    revise_arguments = {
        "workspace_id": fixture.workspace_id,
        "creative_plan_id": PLAN_ID,
        "actor_id": "creative-reviewer",
        "request": revise_request,
        "trace_id": "trace-revise-http",
        "idempotency_key": "mysql-revise-request-001",
    }

    revised = service.revise_plan(**revise_arguments)
    with integration_database.session_factory.begin() as session:
        workflow = session.get(WorkflowModel, WORKFLOW_ID)
        assert workflow is not None
        workflow.status = "COMPLETED"
        workflow.current_node = "completed"
        workflow.version = 9
    assert service.revise_plan(**revise_arguments) == revised

    with integration_database.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(CreativePlanModel)) == 1
        assert session.scalar(select(func.count()).select_from(CreativePlanVersionModel)) == 2
        assert session.scalar(select(func.count()).select_from(IdempotencyKeyModel)) == 2
        audit_events = tuple(
            session.scalars(select(AuditEventModel).order_by(AuditEventModel.created_at))
        )
    assert [event.action for event in audit_events] == [
        "creative_plan.created",
        "creative_plan.revised",
    ]
    for event in audit_events:
        assert set(event.metadata_json) == {
            "version_number",
            "source",
            "direction_count",
            "payload_sha256",
            "expected_workflow_version",
            "expected_head_version",
        }
        serialized = str(event.metadata_json)
        assert "Approved retail shelf" not in serialized
        assert "approved retail setting" not in serialized
        assert "product_brief_id" not in serialized
