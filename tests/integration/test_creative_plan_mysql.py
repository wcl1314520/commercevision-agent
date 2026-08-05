from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from commercevision_api.main import create_app
from commercevision_application import (
    CreativePlanApplicationService,
    CreativePlanCursorCodec,
    CreativePlanWriteResult,
    WorkflowApplicationService,
)
from commercevision_contracts import (
    ApprovalRequest,
    CreativePlanCreateRequestV1,
    CreativePlanRevisionRequestV1,
)
from commercevision_contracts.events import WorkflowResumeRequestedPayload
from commercevision_domain import (
    ApprovalDecision,
    ApprovalType,
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
from commercevision_domain.workflow.errors import ApprovalConflictError, IdempotencyConflictError
from commercevision_persistence import (
    PlanningContextSnapshotRepository,
    SqlAlchemyCreativePlanUnitOfWork,
    SqlAlchemyUnitOfWork,
)
from commercevision_persistence.creative_plan_models import (
    CreativePlanModel,
    CreativePlanVersionModel,
)
from commercevision_persistence.models import (
    ApprovalModel,
    AuditEventModel,
    IdempotencyKeyModel,
    OutboxEventModel,
    WorkflowModel,
)
from commercevision_persistence.product_brief_models import (
    ProductBriefModel,
    ProductBriefVersionModel,
)
from commercevision_persistence.prompt_registry import PromptRevisionRepository
from commercevision_persistence.retrieval_models import RetrievalRunModel
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update

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
    product_source = next(
        item.source
        for item in snapshot.included_sources
        if item.source.kind is PlanningContextSourceKind.PRODUCT_BRIEF
    )
    version_id = "019b0000-0000-7000-8000-000000000714"
    session.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
    try:
        session.add(
            ProductBriefModel(
                id=product_source.source_id,
                workspace_id=snapshot.workspace_id,
                workflow_id=snapshot.workflow_id,
                product_id="019b0000-0000-7000-8000-000000000715",
                operation_id="019b0000-0000-7000-8000-000000000716",
                created_by="fixture-planner",
                state="CONFIRMED",
                current_version_id=version_id,
                confirmed_version_id=version_id,
                version=2,
                retention_class="TASK",
                retention_deadline=deadline,
                created_at=NOW - timedelta(minutes=1),
                updated_at=NOW,
            )
        )
        session.add(
            ProductBriefVersionModel(
                id=version_id,
                workspace_id=snapshot.workspace_id,
                product_brief_id=product_source.source_id,
                version_number=product_source.version_number,
                supersedes_version_id=None,
                category="BEAUTY",
                common_schema_version="common-v1",
                category_schema_version="beauty-v1",
                payload_sha256=product_source.content_sha256,
                changed_paths_json=["common.title"],
                confirmation_required=False,
                unresolved_field_count=0,
                review_policy_version="review-v1",
                source="HUMAN",
                prompt_version=None,
                provider_call_id=None,
                actor_id="fixture-planner",
                revision_reason="confirmed fixture",
                retention_class="TASK",
                retention_deadline=deadline,
                created_at=NOW - timedelta(minutes=1),
            )
        )
        session.flush()
    finally:
        session.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
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


def test_real_mysql_rechecks_current_confirmed_product_brief_at_plan_commit(
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
    with integration_database.session_factory.begin() as session:
        session.execute(
            update(ProductBriefModel)
            .where(
                ProductBriefModel.workspace_id == "planning-domain",
                ProductBriefModel.id == first.provenance.product_brief_id,
            )
            .values(state="ARCHIVED", version=3, updated_at=NOW + timedelta(seconds=1))
        )
    service = CreativePlanApplicationService(
        lambda: SqlAlchemyCreativePlanUnitOfWork(integration_database.session_factory)
    )

    with pytest.raises(NotFoundError, match="provenance authority"):
        service.append_version(
            version=first,
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


def _seed_review_plan(
    integration_database,
    *,
    with_revision: bool,
) -> tuple[CreativePlanVersion, CreativePlanApplicationService, CreativePlanWriteResult]:
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
    plans = CreativePlanApplicationService(
        lambda: SqlAlchemyCreativePlanUnitOfWork(integration_database.session_factory)
    )
    plans.create_plan(
        workspace_id=fixture.workspace_id,
        actor_id="creative-planner",
        request=_create_request(fixture),
        trace_id="trace-create-before-approval",
        idempotency_key="stale-approval-create-001",
    )
    with integration_database.session_factory.begin() as session:
        workflow = session.get(WorkflowModel, WORKFLOW_ID)
        assert workflow is not None
        workflow.status = "AWAITING_PLAN_APPROVAL"
        workflow.current_node = "approve_plan"
        workflow.version = 8
    if with_revision:
        changed_payload = CreativePlanPayload(
            directions=(replace(fixture.payload.directions[0], scene="Approved retail shelf"),)
        )
        plans.revise_plan(
            workspace_id=fixture.workspace_id,
            creative_plan_id=PLAN_ID,
            actor_id="creative-reviewer",
            request=CreativePlanRevisionRequestV1.model_validate(
                {
                    "workflow_id": WORKFLOW_ID,
                    "payload": changed_payload.to_canonical_data(),
                    "revision_reason": "Use the approved retail setting",
                    "expected_workflow_version": 8,
                    "expected_head_version": 1,
                }
            ),
            trace_id="trace-revise-before-approval",
            idempotency_key="stale-approval-revise-001",
        )
    return (
        fixture,
        plans,
        plans.get_current(
            workspace_id=fixture.workspace_id,
            workflow_id=WORKFLOW_ID,
            creative_plan_id=PLAN_ID,
        ),
    )


def test_real_mysql_stale_plan_approval_has_no_side_effects(integration_database) -> None:
    fixture, plans, current = _seed_review_plan(
        integration_database,
        with_revision=True,
    )
    with integration_database.session_factory() as session:
        before = {
            "approvals": session.scalar(select(func.count()).select_from(ApprovalModel)),
            "audits": session.scalar(select(func.count()).select_from(AuditEventModel)),
            "idempotency": session.scalar(select(func.count()).select_from(IdempotencyKeyModel)),
            "outbox": session.scalar(select(func.count()).select_from(OutboxEventModel)),
        }
    workflows = WorkflowApplicationService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory)
    )

    with pytest.raises(ApprovalConflictError, match="authoritative current Creative Plan"):
        workflows.approve(
            workflow_id=WORKFLOW_ID,
            workspace_id=fixture.workspace_id,
            actor_id="creative-reviewer",
            approval_type=ApprovalType.CREATIVE_PLAN,
            request=ApprovalRequest(
                expected_workflow_version=8,
                subject_id=PLAN_ID,
                subject_version=1,
                decision=ApprovalDecision.APPROVE,
            ),
            idempotency_key="stale-plan-approval-001",
            trace_id="trace-stale-plan-approval",
        )

    with integration_database.session_factory() as session:
        workflow = session.get(WorkflowModel, WORKFLOW_ID)
        assert workflow is not None
        assert workflow.status == "AWAITING_PLAN_APPROVAL"
        assert workflow.version == 8
        assert before == {
            "approvals": session.scalar(select(func.count()).select_from(ApprovalModel)),
            "audits": session.scalar(select(func.count()).select_from(AuditEventModel)),
            "idempotency": session.scalar(select(func.count()).select_from(IdempotencyKeyModel)),
            "outbox": session.scalar(select(func.count()).select_from(OutboxEventModel)),
        }

    approval_request = ApprovalRequest(
        expected_workflow_version=8,
        subject_id=PLAN_ID,
        subject_version=current.version.version_number,
        decision=ApprovalDecision.APPROVE,
    )
    approval_arguments = {
        "workflow_id": WORKFLOW_ID,
        "workspace_id": fixture.workspace_id,
        "actor_id": "creative-reviewer",
        "approval_type": ApprovalType.CREATIVE_PLAN,
        "request": approval_request,
        "idempotency_key": "current-plan-approval-001",
        "trace_id": "trace-current-plan-approval",
    }
    approved = workflows.approve(**approval_arguments)
    replayed = workflows.approve(**approval_arguments)

    assert replayed == approved
    with pytest.raises(IdempotencyConflictError, match="different request"):
        workflows.approve(
            **{
                **approval_arguments,
                "actor_id": "different-reviewer",
            }
        )
    assert approved.status.value == "GENERATING"
    assert len(approved.approvals) == 1
    assert approved.approvals[0].subject_version == current.version.version_number
    execution = workflows.validate_creative_plan_execution_claim(
        workspace_id=fixture.workspace_id,
        workflow_id=WORKFLOW_ID,
        creative_plan_id=PLAN_ID,
        creative_plan_version=current.version.version_number,
        approval_id=approved.approvals[0].id,
    )
    assert execution.workflow_version == approved.version
    assert execution.plan == current.version
    assert execution.approval.id == approved.approvals[0].id
    assert execution.approval.subject_id == approved.approvals[0].subject_id
    assert execution.approval.subject_version == approved.approvals[0].subject_version

    with pytest.raises(ApprovalConflictError, match="exact approved Creative Plan"):
        workflows.validate_creative_plan_execution_claim(
            workspace_id=fixture.workspace_id,
            workflow_id=WORKFLOW_ID,
            creative_plan_id=PLAN_ID,
            creative_plan_version=current.version.version_number,
            approval_id="019b0000-0000-7000-8000-000000000799",
        )
    with integration_database.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ApprovalModel)) == 1
        assert (
            session.scalar(select(func.count()).select_from(AuditEventModel))
            == before["audits"] + 1
        )
        assert (
            session.scalar(select(func.count()).select_from(IdempotencyKeyModel))
            == before["idempotency"] + 1
        )
        assert session.scalar(select(func.count()).select_from(OutboxEventModel)) == 1


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("approval_id", "019b0000-0000-7000-8000-000000000799"),
        ("decision", ApprovalDecision.REJECT),
        ("expected_workflow_version", 7),
        ("resulting_workflow_version", 10),
        ("subject_id", "019b0000-0000-7000-8000-000000000799"),
        ("subject_version", 2),
    ],
)
def test_real_mysql_plan_resume_revalidates_every_approval_fact(
    integration_database,
    field: str,
    replacement: object,
) -> None:
    fixture, _, current = _seed_review_plan(
        integration_database,
        with_revision=False,
    )
    workflows = WorkflowApplicationService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory)
    )
    approved = workflows.approve(
        workflow_id=WORKFLOW_ID,
        workspace_id=fixture.workspace_id,
        actor_id="creative-reviewer",
        approval_type=ApprovalType.CREATIVE_PLAN,
        request=ApprovalRequest(
            expected_workflow_version=8,
            subject_id=PLAN_ID,
            subject_version=current.version.version_number,
            decision=ApprovalDecision.APPROVE,
        ),
        idempotency_key="exact-plan-resume-approval-001",
        trace_id="trace-exact-plan-resume",
    )
    event = workflows.events(
        workflow_id=WORKFLOW_ID,
        workspace_id=fixture.workspace_id,
    )[-1]
    payload = WorkflowResumeRequestedPayload.model_validate(event.payload)

    claim = workflows.validate_creative_plan_resume_claim(
        workspace_id=fixture.workspace_id,
        payload=payload,
    )

    assert claim.workflow_version == approved.version
    assert claim.plan == current.version
    assert claim.approval.id == approved.approvals[0].id
    with pytest.raises(ApprovalConflictError, match="exact Creative Plan approval"):
        workflows.validate_creative_plan_resume_claim(
            workspace_id=fixture.workspace_id,
            payload=payload.model_copy(update={field: replacement}),
        )


def test_real_mysql_retention_expiry_prevents_plan_resume(integration_database) -> None:
    fixture, _, current = _seed_review_plan(
        integration_database,
        with_revision=False,
    )
    workflows = WorkflowApplicationService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory)
    )
    workflows.approve(
        workflow_id=WORKFLOW_ID,
        workspace_id=fixture.workspace_id,
        actor_id="creative-reviewer",
        approval_type=ApprovalType.CREATIVE_PLAN,
        request=ApprovalRequest(
            expected_workflow_version=8,
            subject_id=PLAN_ID,
            subject_version=current.version.version_number,
            decision=ApprovalDecision.APPROVE,
        ),
        idempotency_key="retention-plan-resume-approval-001",
        trace_id="trace-retention-plan-resume",
    )
    payload = WorkflowResumeRequestedPayload.model_validate(
        workflows.events(
            workflow_id=WORKFLOW_ID,
            workspace_id=fixture.workspace_id,
        )[-1].payload
    )
    with integration_database.session_factory.begin() as session:
        workflow = session.get(WorkflowModel, WORKFLOW_ID)
        assert workflow is not None
        workflow.retention_status = "EXPIRING"

    with pytest.raises(ApprovalConflictError, match="exact Creative Plan approval"):
        workflows.validate_creative_plan_resume_claim(
            workspace_id=fixture.workspace_id,
            payload=payload,
        )


def test_real_mysql_rejected_plan_requires_a_later_version_before_approval(
    integration_database,
) -> None:
    fixture, _, current = _seed_review_plan(
        integration_database,
        with_revision=False,
    )
    workflows = WorkflowApplicationService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory)
    )
    reject_arguments = {
        "workflow_id": WORKFLOW_ID,
        "workspace_id": fixture.workspace_id,
        "actor_id": "creative-reviewer",
        "approval_type": ApprovalType.CREATIVE_PLAN,
        "request": ApprovalRequest(
            expected_workflow_version=8,
            subject_id=PLAN_ID,
            subject_version=current.version.version_number,
            decision=ApprovalDecision.REJECT,
            reason_code="PLAN_NEEDS_REVISION",
        ),
        "idempotency_key": "current-plan-rejection-001",
        "trace_id": "trace-current-plan-rejection",
    }

    rejected = workflows.approve(**reject_arguments)
    assert workflows.approve(**reject_arguments) == rejected
    assert rejected.status.value == "PLANNING"
    assert len(rejected.approvals) == 1
    assert rejected.approvals[0].decision is ApprovalDecision.REJECT

    with integration_database.session_factory.begin() as session:
        workflow = session.get(WorkflowModel, WORKFLOW_ID)
        assert workflow is not None
        workflow.status = "AWAITING_PLAN_APPROVAL"
        workflow.current_node = "approve_plan"
        workflow.version = 10
    with integration_database.session_factory() as session:
        before = {
            "approvals": session.scalar(select(func.count()).select_from(ApprovalModel)),
            "audits": session.scalar(select(func.count()).select_from(AuditEventModel)),
            "idempotency": session.scalar(select(func.count()).select_from(IdempotencyKeyModel)),
            "outbox": session.scalar(select(func.count()).select_from(OutboxEventModel)),
        }

    with pytest.raises(ApprovalConflictError, match="requires a later Creative Plan version"):
        workflows.approve(
            workflow_id=WORKFLOW_ID,
            workspace_id=fixture.workspace_id,
            actor_id="creative-reviewer",
            approval_type=ApprovalType.CREATIVE_PLAN,
            request=ApprovalRequest(
                expected_workflow_version=10,
                subject_id=PLAN_ID,
                subject_version=current.version.version_number,
                decision=ApprovalDecision.APPROVE,
            ),
            idempotency_key="rejected-plan-approval-001",
            trace_id="trace-rejected-plan-approval",
        )

    with integration_database.session_factory() as session:
        assert before == {
            "approvals": session.scalar(select(func.count()).select_from(ApprovalModel)),
            "audits": session.scalar(select(func.count()).select_from(AuditEventModel)),
            "idempotency": session.scalar(select(func.count()).select_from(IdempotencyKeyModel)),
            "outbox": session.scalar(select(func.count()).select_from(OutboxEventModel)),
        }


def test_real_mysql_plan_rejection_limit_is_bounded_before_side_effects(
    integration_database,
) -> None:
    fixture, _, current = _seed_review_plan(
        integration_database,
        with_revision=False,
    )
    with integration_database.session_factory.begin() as session:
        for index in range(10):
            session.add(
                ApprovalModel(
                    id=f"019b0000-0000-7000-8000-{index + 900:012d}",
                    workflow_id=WORKFLOW_ID,
                    approval_type=ApprovalType.CREATIVE_PLAN.value,
                    subject_id=PLAN_ID,
                    subject_version=index + 100,
                    decision=ApprovalDecision.REJECT.value,
                    reason_code="PLAN_NEEDS_REVISION",
                    comment_ref=None,
                    approved_by="creative-reviewer",
                    expected_workflow_version=index + 100,
                    created_at=NOW + timedelta(seconds=index),
                )
            )
    workflows = WorkflowApplicationService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory)
    )
    with integration_database.session_factory() as session:
        before = {
            "approvals": session.scalar(select(func.count()).select_from(ApprovalModel)),
            "audits": session.scalar(select(func.count()).select_from(AuditEventModel)),
            "idempotency": session.scalar(select(func.count()).select_from(IdempotencyKeyModel)),
            "outbox": session.scalar(select(func.count()).select_from(OutboxEventModel)),
        }

    with pytest.raises(ApprovalConflictError, match="rejection limit"):
        workflows.approve(
            workflow_id=WORKFLOW_ID,
            workspace_id=fixture.workspace_id,
            actor_id="creative-reviewer",
            approval_type=ApprovalType.CREATIVE_PLAN,
            request=ApprovalRequest(
                expected_workflow_version=8,
                subject_id=PLAN_ID,
                subject_version=current.version.version_number,
                decision=ApprovalDecision.REJECT,
                reason_code="PLAN_NEEDS_REVISION",
            ),
            idempotency_key="bounded-plan-rejection-001",
            trace_id="trace-bounded-plan-rejection",
        )

    with integration_database.session_factory() as session:
        assert before == {
            "approvals": session.scalar(select(func.count()).select_from(ApprovalModel)),
            "audits": session.scalar(select(func.count()).select_from(AuditEventModel)),
            "idempotency": session.scalar(select(func.count()).select_from(IdempotencyKeyModel)),
            "outbox": session.scalar(select(func.count()).select_from(OutboxEventModel)),
        }


def test_real_mysql_foreign_fabricated_and_expired_plan_approval_has_no_side_effects(
    integration_database,
) -> None:
    fixture, _, current = _seed_review_plan(
        integration_database,
        with_revision=False,
    )
    workflows = WorkflowApplicationService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory)
    )

    def counts() -> dict[str, int]:
        with integration_database.session_factory() as session:
            return {
                "approvals": session.scalar(select(func.count()).select_from(ApprovalModel)) or 0,
                "audits": session.scalar(select(func.count()).select_from(AuditEventModel)) or 0,
                "idempotency": (
                    session.scalar(select(func.count()).select_from(IdempotencyKeyModel)) or 0
                ),
                "outbox": session.scalar(select(func.count()).select_from(OutboxEventModel)) or 0,
            }

    before = counts()
    with pytest.raises(NotFoundError):
        workflows.approve(
            workflow_id=WORKFLOW_ID,
            workspace_id="foreign-planning-workspace",
            actor_id="creative-reviewer",
            approval_type=ApprovalType.CREATIVE_PLAN,
            request=ApprovalRequest(
                expected_workflow_version=8,
                subject_id=PLAN_ID,
                subject_version=current.version.version_number,
                decision=ApprovalDecision.APPROVE,
            ),
            idempotency_key="foreign-plan-approval-001",
            trace_id="trace-foreign-plan-approval",
        )
    with pytest.raises(ApprovalConflictError, match="authoritative current Creative Plan"):
        workflows.approve(
            workflow_id=WORKFLOW_ID,
            workspace_id=fixture.workspace_id,
            actor_id="creative-reviewer",
            approval_type=ApprovalType.CREATIVE_PLAN,
            request=ApprovalRequest(
                expected_workflow_version=8,
                subject_id="019b0000-0000-7000-8000-000000000799",
                subject_version=current.version.version_number,
                decision=ApprovalDecision.APPROVE,
            ),
            idempotency_key="fabricated-plan-approval-001",
            trace_id="trace-fabricated-plan-approval",
        )
    assert counts() == before

    with integration_database.session_factory.begin() as session:
        workflow = session.get(WorkflowModel, WORKFLOW_ID)
        assert workflow is not None
        workflow.retention_status = "EXPIRED"
    with pytest.raises(ApprovalConflictError, match="retention has expired"):
        workflows.approve(
            workflow_id=WORKFLOW_ID,
            workspace_id=fixture.workspace_id,
            actor_id="creative-reviewer",
            approval_type=ApprovalType.CREATIVE_PLAN,
            request=ApprovalRequest(
                expected_workflow_version=8,
                subject_id=PLAN_ID,
                subject_version=current.version.version_number,
                decision=ApprovalDecision.APPROVE,
            ),
            idempotency_key="expired-plan-approval-001",
            trace_id="trace-expired-plan-approval",
        )
    assert counts() == before


def test_http_plan_approval_rejects_a_stale_page_and_replays_the_exact_current_decision(
    integration_database,
    integration_settings,
) -> None:
    fixture, _, current = _seed_review_plan(
        integration_database,
        with_revision=True,
    )
    app = create_app(integration_settings)
    headers = {
        "X-Workspace-Id": fixture.workspace_id,
        "X-Actor-Id": "creative-reviewer",
        "Idempotency-Key": "test-test-test",
        "X-Trace-Id": "trace-http-stale-plan-approval",
    }
    with TestClient(app) as client:
        fabricated = client.post(
            f"/api/v1/workflows/{WORKFLOW_ID}/creative-plan:approve",
            headers={
                **headers,
                "Idempotency-Key": "test-test-test-test",
            },
            json={
                "expected_workflow_version": 8,
                "subject_id": "019b0000-0000-7000-8000-000000000799",
                "subject_version": current.version.version_number,
                "decision": "APPROVE",
            },
        )
        stale = client.post(
            f"/api/v1/workflows/{WORKFLOW_ID}/creative-plan:approve",
            headers=headers,
            json={
                "expected_workflow_version": 8,
                "subject_id": PLAN_ID,
                "subject_version": 1,
                "decision": "APPROVE",
            },
        )
        current_headers = {
            **headers,
            "Idempotency-Key": "test-test-test-test-test",
            "X-Trace-Id": "trace-http-current-plan-approval",
        }
        payload = {
            "expected_workflow_version": 8,
            "subject_id": PLAN_ID,
            "subject_version": current.version.version_number,
            "decision": "APPROVE",
        }
        approved = client.post(
            f"/api/v1/workflows/{WORKFLOW_ID}/creative-plan:approve",
            headers=current_headers,
            json=payload,
        )
        replayed = client.post(
            f"/api/v1/workflows/{WORKFLOW_ID}/creative-plan:approve",
            headers=current_headers,
            json=payload,
        )

    assert fabricated.status_code == 409
    assert fabricated.json()["code"] == "CREATIVE_PLAN_SUBJECT_CONFLICT"
    assert stale.status_code == 409
    assert stale.json()["code"] == "CREATIVE_PLAN_VERSION_CONFLICT"
    assert approved.status_code == 200, approved.text
    assert replayed.status_code == 200, replayed.text
    assert replayed.json() == approved.json()
    assert approved.json()["status"] == "GENERATING"
    assert approved.json()["approvals"][0]["subject_version"] == 2


def test_real_mysql_plan_revision_and_approval_race_has_one_authoritative_winner(
    integration_database,
) -> None:
    fixture, plans, current = _seed_review_plan(
        integration_database,
        with_revision=False,
    )
    workflows = WorkflowApplicationService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory)
    )
    barrier = Barrier(2)

    def approve_current():
        barrier.wait(timeout=10)
        try:
            return workflows.approve(
                workflow_id=WORKFLOW_ID,
                workspace_id=fixture.workspace_id,
                actor_id="creative-reviewer",
                approval_type=ApprovalType.CREATIVE_PLAN,
                request=ApprovalRequest(
                    expected_workflow_version=8,
                    subject_id=PLAN_ID,
                    subject_version=current.version.version_number,
                    decision=ApprovalDecision.APPROVE,
                ),
                idempotency_key="racing-plan-approval-001",
                trace_id="trace-racing-plan-approval",
            )
        except (ApprovalConflictError, ConcurrencyError) as exc:
            return exc

    def revise_current():
        barrier.wait(timeout=10)
        try:
            return plans.revise_plan(
                workspace_id=fixture.workspace_id,
                creative_plan_id=PLAN_ID,
                actor_id="creative-reviewer",
                request=CreativePlanRevisionRequestV1.model_validate(
                    {
                        "workflow_id": WORKFLOW_ID,
                        "payload": CreativePlanPayload(
                            directions=(
                                replace(
                                    fixture.payload.directions[0],
                                    scene="Concurrent retail revision",
                                ),
                            )
                        ).to_canonical_data(),
                        "revision_reason": "Apply the concurrent reviewer revision",
                        "expected_workflow_version": 8,
                        "expected_head_version": 1,
                    }
                ),
                trace_id="trace-racing-plan-revision",
                idempotency_key="racing-plan-revision-001",
            )
        except (ApprovalConflictError, ConcurrencyError) as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        approval_future = executor.submit(approve_current)
        revision_future = executor.submit(revise_current)
        approval_outcome = approval_future.result(timeout=30)
        revision_outcome = revision_future.result(timeout=30)

    assert isinstance(approval_outcome, Exception) != isinstance(revision_outcome, Exception)
    with integration_database.session_factory() as session:
        workflow = session.get(WorkflowModel, WORKFLOW_ID)
        assert workflow is not None
        approval_count = session.scalar(select(func.count()).select_from(ApprovalModel))
        version_count = session.scalar(select(func.count()).select_from(CreativePlanVersionModel))
        if isinstance(approval_outcome, Exception):
            assert isinstance(approval_outcome, ApprovalConflictError)
            assert workflow.status == "AWAITING_PLAN_APPROVAL"
            assert workflow.version == 8
            assert approval_count == 0
            assert version_count == 2
        else:
            assert isinstance(revision_outcome, ConcurrencyError)
            assert workflow.status == "GENERATING"
            assert workflow.version == 9
            assert approval_count == 1
            assert version_count == 1
