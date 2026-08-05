from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from commercevision_application import PlanningContextExactReference
from commercevision_domain import PlanningContextPolicy, PlanningContextSourceKind, new_uuid7
from commercevision_persistence import MySqlPlanningContextAuthority
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
