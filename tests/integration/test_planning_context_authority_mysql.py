from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from commercevision_application import (
    CreativePlanApplicationService,
    DurableFixturePlanner,
    DurableFixturePlannerCommand,
    PlanningContextApplicationService,
    PlanningContextExactReference,
    PromptRegistryApplicationService,
    WorkflowApplicationService,
)
from commercevision_contracts import (
    ApprovalRequest,
    PromptRevisionCreateRequestV1,
    PromptRevisionTransitionRequestV1,
    PromptTemplateVariableV1,
)
from commercevision_domain import (
    ApprovalDecision,
    ApprovalType,
    NotFoundError,
    PlanningContextPolicy,
    PlanningContextSourceKind,
    new_uuid7,
)
from commercevision_persistence import (
    MySqlFixturePlanningAuthority,
    MySqlPlanningContextAuthority,
    SqlAlchemyCreativePlanUnitOfWork,
    SqlAlchemyPlanningContextSnapshotStore,
    SqlAlchemyPromptRegistryUnitOfWork,
    SqlAlchemyUnitOfWork,
)
from sqlalchemy import text

pytestmark = pytest.mark.integration

WORKSPACE_ID = "planning-context-authority"
PURPOSE = "creative-planning"


def _seed_confirmed_product_brief(database) -> tuple[str, str, str]:
    workflow_id = new_uuid7()
    product_id = new_uuid7()
    operation_id = new_uuid7()
    brief_id = new_uuid7()
    version_id = new_uuid7()
    now = datetime.now(UTC).replace(tzinfo=None)
    deadline = now + timedelta(days=30)
    with database.engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        connection.execute(
            text(
                "INSERT INTO workflows "
                "(id, workspace_id, created_by, workflow_type, status, retention_status, "
                "current_node, version, input_json, result_json, expires_at, "
                "cancellation_requested_at, created_at, updated_at) VALUES "
                "(:workflow, :workspace, 'fixture', 'creative-planning', 'RUNNING', 'ACTIVE', "
                "'planner', 1, '{}', NULL, :deadline, NULL, :now, :now)"
            ),
            {
                "workflow": workflow_id,
                "workspace": WORKSPACE_ID,
                "deadline": deadline,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO product_briefs "
                "(id, workspace_id, workflow_id, product_id, operation_id, created_by, state, "
                "current_version_id, confirmed_version_id, version, retention_class, "
                "retention_deadline, created_at, updated_at) VALUES "
                "(:brief, :workspace, :workflow, :product, :operation, 'reviewer', 'CONFIRMED', "
                ":version, :version, 2, 'TASK', :deadline, :now, :now)"
            ),
            {
                "brief": brief_id,
                "workspace": WORKSPACE_ID,
                "workflow": workflow_id,
                "product": product_id,
                "operation": operation_id,
                "version": version_id,
                "deadline": deadline,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO product_brief_versions "
                "(id, workspace_id, product_brief_id, version_number, supersedes_version_id, "
                "category, common_schema_version, category_schema_version, payload_sha256, "
                "changed_paths_json, confirmation_required, unresolved_field_count, "
                "review_policy_version, source, prompt_version, provider_call_id, actor_id, "
                "revision_reason, retention_class, retention_deadline, created_at) VALUES "
                "(:version, :workspace, :brief, 3, NULL, 'BEAUTY', 'common-v1', 'beauty-v1', "
                ":hash, :paths, 0, 0, 'review-v1', 'HUMAN', NULL, NULL, 'reviewer', "
                "'confirmed fixture', 'TASK', :deadline, :now)"
            ),
            {
                "version": version_id,
                "workspace": WORKSPACE_ID,
                "brief": brief_id,
                "hash": "1" * 64,
                "paths": json.dumps(["common.title", "common.internal_note"]),
                "deadline": deadline,
                "now": now,
            },
        )
        for path, value, sensitive in (
            ("common.title", "Travel mug", 0),
            ("common.internal_note", "must-not-leak", 1),
        ):
            connection.execute(
                text(
                    "INSERT INTO product_brief_fields "
                    "(id, workspace_id, product_brief_id, product_brief_version_id, path, "
                    "value_json, confidence, source, conflict, review_required, `sensitive`, "
                    "review_reasons_json, created_at) VALUES "
                    "(:id, :workspace, :brief, :version, :path, :value, 1, 'HUMAN', 'NONE', "
                    "0, :sensitive, '[]', :now)"
                ),
                {
                    "id": new_uuid7(),
                    "workspace": WORKSPACE_ID,
                    "brief": brief_id,
                    "version": version_id,
                    "path": path,
                    "value": json.dumps(value),
                    "sensitive": sensitive,
                    "now": now,
                },
            )
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    return workflow_id, brief_id, version_id


def test_mysql_authority_requires_current_confirmation_and_redacts_sensitive_fields(
    integration_database,
) -> None:
    workflow_id, brief_id, _ = _seed_confirmed_product_brief(integration_database)
    policy = PlanningContextPolicy(
        version="planning-context-v1",
        maximum_tokens=2_000,
        maximum_images=4,
    )
    authority = MySqlPlanningContextAuthority(
        integration_database.session_factory,
        policies={policy.version: policy},
    )
    reference = PlanningContextExactReference(
        kind=PlanningContextSourceKind.PRODUCT_BRIEF,
        source_id=brief_id,
        version_number=3,
        content_sha256="1" * 64,
    )

    authorized = authority.load_authorized_source(
        workspace_id=WORKSPACE_ID,
        workflow_id=workflow_id,
        purpose=PURPOSE,
        reference=reference,
        at=authority.database_now(),
    )

    assert authorized is not None
    fields = authorized.source.content()["fields"]
    assert fields == [
        {
            "path": "common.internal_note",
            "redacted": True,
            "value": "[REDACTED]",
        },
        {"path": "common.title", "redacted": False, "value": "Travel mug"},
    ]
    assert "must-not-leak" not in authorized.source.content_json
    assert (
        authority.load_authorized_source(
            workspace_id="foreign-workspace",
            workflow_id=workflow_id,
            purpose=PURPOSE,
            reference=reference,
            at=authority.database_now(),
        )
        is None
    )
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workflows SET retention_status = 'EXPIRING', version = version + 1, "
                "updated_at = :now WHERE workspace_id = :workspace AND id = :workflow"
            ),
            {
                "now": datetime.now(UTC).replace(tzinfo=None),
                "workspace": WORKSPACE_ID,
                "workflow": workflow_id,
            },
        )
    assert (
        authority.workflow_retention_deadline(
            workspace_id=WORKSPACE_ID,
            workflow_id=workflow_id,
        )
        is None
    )
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workflows SET retention_status = 'ACTIVE', version = version + 1, "
                "updated_at = :now WHERE workspace_id = :workspace AND id = :workflow"
            ),
            {
                "now": datetime.now(UTC).replace(tzinfo=None),
                "workspace": WORKSPACE_ID,
                "workflow": workflow_id,
            },
        )

    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE product_briefs SET state = 'ARCHIVED', version = version + 1, "
                "updated_at = :now WHERE workspace_id = :workspace AND id = :brief"
            ),
            {
                "now": datetime.now(UTC).replace(tzinfo=None),
                "workspace": WORKSPACE_ID,
                "brief": brief_id,
            },
        )
    assert (
        authority.load_authorized_source(
            workspace_id=WORKSPACE_ID,
            workflow_id=workflow_id,
            purpose=PURPOSE,
            reference=reference,
            at=authority.database_now(),
        )
        is None
    )


def test_fixture_planner_authority_reuses_one_exact_retained_retrieval_run(
    integration_database,
) -> None:
    workflow_id, brief_id, version_id = _seed_confirmed_product_brief(integration_database)
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workflows SET status = 'PLANNING', current_node = 'create_plan', "
                "version = 7 WHERE workspace_id = :workspace AND id = :workflow"
            ),
            {"workspace": WORKSPACE_ID, "workflow": workflow_id},
        )
    authority = MySqlFixturePlanningAuthority(integration_database.session_factory)
    arguments = {
        "workspace_id": WORKSPACE_ID,
        "workflow_id": workflow_id,
        "product_brief_version_id": version_id,
        "product_brief_version_number": 3,
        "expected_workflow_version": 7,
    }

    first = authority.load(**arguments)
    second = authority.load(**arguments)

    assert second == first
    assert first.product_brief.source_id == brief_id
    assert first.product_brief.content_sha256 == "1" * 64
    assert first.category == "beauty"
    with integration_database.engine.connect() as connection:
        runs = (
            connection.execute(
                text("SELECT id, query_json FROM retrieval_runs WHERE workspace_id = :workspace"),
                {"workspace": WORKSPACE_ID},
            )
            .mappings()
            .all()
        )
    assert len(runs) == 1
    assert runs[0]["id"] == first.retrieval_run_id
    assert "must-not-leak" not in str(runs[0]["query_json"])

    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE product_briefs SET state = 'ARCHIVED' "
                "WHERE workspace_id = :workspace AND id = :brief"
            ),
            {"workspace": WORKSPACE_ID, "brief": brief_id},
        )
    with pytest.raises(NotFoundError, match="ProductBrief was not found"):
        authority.load(**arguments)


def _publish_fixture_planner_prompt(database) -> PromptRegistryApplicationService:
    service = PromptRegistryApplicationService(
        lambda: SqlAlchemyPromptRegistryUnitOfWork(database.session_factory)
    )
    created = service.create_revision(
        workspace_id=WORKSPACE_ID,
        actor_id="prompt-admin",
        request=PromptRevisionCreateRequestV1(
            prompt_id="creative-planner",
            semantic_revision="1.0.0",
            node="CREATE_CREATIVE_PLAN",
            category_applicability=["beauty", "automotive-parts"],
            model_family_applicability=["fixture-planner"],
            input_schema_version="planning-context.v1",
            output_schema_version="creative-plan.v1",
            policy_version="prompt-policy.v1",
            content="Plan {{ planning_context }} into {{ output_schema }}.",
            variables=[
                PromptTemplateVariableV1(name="planning_context", required=True),
                PromptTemplateVariableV1(name="output_schema", required=True),
            ],
            change_summary="Built-in deterministic Fixture Planner",
        ),
        trace_id="trace-create-fixture-prompt",
        idempotency_key="create-fixture-prompt-001",
    )
    reviewed = service.submit_for_review(
        workspace_id=WORKSPACE_ID,
        revision_id=created.id,
        actor_id="prompt-admin",
        request=PromptRevisionTransitionRequestV1(expected_version=1),
        trace_id="trace-review-fixture-prompt",
        idempotency_key="review-fixture-prompt-001",
    )
    staged = service.stage(
        workspace_id=WORKSPACE_ID,
        revision_id=created.id,
        actor_id="prompt-reviewer",
        request=PromptRevisionTransitionRequestV1(expected_version=reviewed.version),
        trace_id="trace-stage-fixture-prompt",
        idempotency_key="stage-fixture-prompt-001",
    )
    service.publish(
        workspace_id=WORKSPACE_ID,
        revision_id=created.id,
        actor_id="release-manager",
        request=PromptRevisionTransitionRequestV1(expected_version=staged.version),
        trace_id="trace-publish-fixture-prompt",
        idempotency_key="publish-fixture-prompt-001",
    )
    return service


def test_durable_fixture_planner_writes_one_replay_safe_authoritative_version(
    integration_database,
) -> None:
    workflow_id, _brief_id, version_id = _seed_confirmed_product_brief(integration_database)
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workflows SET status = 'PLANNING', current_node = 'create_plan', "
                "version = 7 WHERE workspace_id = :workspace AND id = :workflow"
            ),
            {"workspace": WORKSPACE_ID, "workflow": workflow_id},
        )
    prompt_registry = _publish_fixture_planner_prompt(integration_database)
    policy = PlanningContextPolicy(
        version="planning-context-v1",
        maximum_tokens=2_000,
        maximum_images=4,
    )
    planner = DurableFixturePlanner(
        authority=MySqlFixturePlanningAuthority(integration_database.session_factory),
        contexts=PlanningContextApplicationService(
            authority=MySqlPlanningContextAuthority(
                integration_database.session_factory,
                policies={policy.version: policy},
            ),
            snapshots=SqlAlchemyPlanningContextSnapshotStore(integration_database.session_factory),
        ),
        prompts=prompt_registry,
        plans=CreativePlanApplicationService(
            lambda: SqlAlchemyCreativePlanUnitOfWork(integration_database.session_factory)
        ),
    )
    command = DurableFixturePlannerCommand(
        workspace_id=WORKSPACE_ID,
        workflow_id=workflow_id,
        product_brief_version_id=version_id,
        product_brief_version_number=3,
        actor_id="fixture-planner",
        expected_workflow_version=7,
        trace_id="trace-durable-fixture-plan",
        idempotency_key="durable-fixture-plan-step-001",
    )

    first = planner.create_plan(command)
    replayed = planner.create_plan(command)

    assert replayed == first
    assert first.version_number == 1
    assert first.prompt_revision == "1.0.0"
    assert first.to_step_output()["creative_plan_ref"] == first.creative_plan_id
    with integration_database.engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM creative_plan_versions")) == 1
        assert connection.scalar(text("SELECT COUNT(*) FROM planning_context_snapshots")) == 1
        assert connection.scalar(text("SELECT COUNT(*) FROM retrieval_runs")) == 1
        public_event_data = json.dumps(
            {
                "outbox": connection.execute(
                    text("SELECT payload_json FROM outbox_events WHERE workspace_id = :workspace"),
                    {"workspace": WORKSPACE_ID},
                )
                .scalars()
                .all(),
                "audit": connection.execute(
                    text("SELECT metadata_json FROM audit_events WHERE workspace_id = :workspace"),
                    {"workspace": WORKSPACE_ID},
                )
                .scalars()
                .all(),
            },
            sort_keys=True,
        )
    assert "must-not-leak" not in public_event_data
    assert "Premium vanity studio" not in public_event_data
    assert '"directions"' not in public_event_data

    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workflows SET status = 'AWAITING_PLAN_APPROVAL', "
                "current_node = 'approve_plan', version = 8 "
                "WHERE workspace_id = :workspace AND id = :workflow"
            ),
            {"workspace": WORKSPACE_ID, "workflow": workflow_id},
        )
    workflows = WorkflowApplicationService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(integration_database.session_factory)
    )
    rejected = workflows.approve(
        workflow_id=workflow_id,
        workspace_id=WORKSPACE_ID,
        actor_id="creative-reviewer",
        approval_type=ApprovalType.CREATIVE_PLAN,
        request=ApprovalRequest(
            expected_workflow_version=8,
            subject_id=first.creative_plan_id,
            subject_version=first.version_number,
            decision=ApprovalDecision.REJECT,
            reason_code="PLAN_NEEDS_REVISION",
        ),
        idempotency_key="reject-durable-fixture-plan-001",
        trace_id="trace-reject-durable-fixture-plan",
    )
    with integration_database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workflows SET current_node = 'create_plan', version = :version "
                "WHERE workspace_id = :workspace AND id = :workflow"
            ),
            {
                "version": rejected.version + 1,
                "workspace": WORKSPACE_ID,
                "workflow": workflow_id,
            },
        )
    revision_command = replace(
        command,
        plan_iteration=1,
        prior_plan_version_id=first.version_id,
        prior_plan_version=first.version_number,
        expected_workflow_version=rejected.version + 1,
        idempotency_key="durable-fixture-plan-step-002",
    )

    second = planner.create_plan(revision_command)
    replayed_second = planner.create_plan(revision_command)

    assert replayed_second == second
    assert second.creative_plan_id == first.creative_plan_id
    assert second.version_number == 2
    with integration_database.engine.connect() as connection:
        versions = (
            connection.execute(
                text(
                    "SELECT version_number, supersedes_version_id, source "
                    "FROM creative_plan_versions ORDER BY version_number"
                )
            )
            .mappings()
            .all()
        )
    assert [version["version_number"] for version in versions] == [1, 2]
    assert versions[1]["supersedes_version_id"] == first.version_id
    assert versions[1]["source"] == "AGENT"
