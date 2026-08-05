from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid5

import pytest
from alembic import command
from alembic.config import Config
from commercevision_application import PromptRegistryApplicationService
from commercevision_contracts import (
    PromptRevisionCreateRequestV1,
    PromptRevisionTransitionRequestV1,
    PromptTemplateVariableV1,
    Settings,
)
from commercevision_domain import (
    CreativePlanDirection,
    CreativePlanPayload,
    ImageRole,
)
from commercevision_persistence import SqlAlchemyPromptRegistryUnitOfWork, create_database
from commercevision_persistence.creative_plan_models import (
    CreativePlanModel,
    CreativePlanVersionModel,
)
from commercevision_persistence.models import Base, WorkflowModel
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def _test_dsn() -> str:
    return os.getenv(
        "CV_TEST_MYSQL_DSN",
        "mysql+pymysql://root:root-change-me@127.0.0.1:13316/commercevision_test",
    )


@pytest.fixture(scope="session")
def integration_settings() -> Iterator[Settings]:
    dsn = _test_dsn()
    url = make_url(dsn)
    admin_url = url.set(database="mysql")
    try:
        admin_engine = create_engine(admin_url, pool_pre_ping=True)
        with admin_engine.begin() as connection:
            database_name = url.database or "commercevision_test"
            connection.execute(
                text(
                    "CREATE DATABASE IF NOT EXISTS "
                    f"`{database_name.replace('`', '')}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
                )
            )
        admin_engine.dispose()
    except Exception as exc:
        pytest.skip(f"MySQL integration database unavailable: {exc}")

    previous = os.environ.get("CV_MYSQL_DSN")
    os.environ["CV_MYSQL_DSN"] = dsn
    try:
        config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("CV_MYSQL_DSN", None)
        else:
            os.environ["CV_MYSQL_DSN"] = previous
    yield Settings(
        environment="ci",
        service_name="integration",
        mysql_dsn=dsn,
        workflow_step_lease_seconds=30,
        workflow_message_max_attempts=3,
    )


@pytest.fixture
def integration_database(integration_settings: Settings):
    database = create_database(integration_settings)
    with database.engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for table in reversed(tuple(Base.metadata.tables.values())):
            connection.exec_driver_sql(f"TRUNCATE TABLE `{table.name}`")
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    try:
        yield database
    finally:
        database.dispose()


@pytest.fixture
def seed_fixture_planner_prompt():
    """Publish the production Prompt required by the deterministic Planner."""

    def seed(database: Any, *, workspace_id: str) -> str:
        service = PromptRegistryApplicationService(
            lambda: SqlAlchemyPromptRegistryUnitOfWork(database.session_factory)
        )
        created = service.create_revision(
            workspace_id=workspace_id,
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
                change_summary="Deterministic integration Planner",
            ),
            trace_id="trace-create-fixture-planner-prompt",
            idempotency_key="create-fixture-planner-prompt-001",
        )
        reviewed = service.submit_for_review(
            workspace_id=workspace_id,
            revision_id=created.id,
            actor_id="prompt-admin",
            request=PromptRevisionTransitionRequestV1(expected_version=created.version),
            trace_id="trace-review-fixture-planner-prompt",
            idempotency_key="review-fixture-planner-prompt-001",
        )
        staged = service.stage(
            workspace_id=workspace_id,
            revision_id=created.id,
            actor_id="prompt-reviewer",
            request=PromptRevisionTransitionRequestV1(expected_version=reviewed.version),
            trace_id="trace-stage-fixture-planner-prompt",
            idempotency_key="stage-fixture-planner-prompt-001",
        )
        service.publish(
            workspace_id=workspace_id,
            revision_id=created.id,
            actor_id="release-manager",
            request=PromptRevisionTransitionRequestV1(expected_version=staged.version),
            trace_id="trace-publish-fixture-planner-prompt",
            idempotency_key="publish-fixture-planner-prompt-001",
        )
        return created.id

    return seed


@pytest.fixture
def legacy_phase1_planner_node(integration_database):
    """Test-only Plan adapter for pre-ProductBrief Phase 1 workflow fixtures."""

    plan_namespace = UUID("30af06d0-c3c7-4b5b-a7ca-1c28036575bb")
    version_namespace = UUID("231da30e-6a9e-4e0a-b777-d98a2e2e4ce4")
    authority_namespace = UUID("2c86484a-c8fc-498c-b42b-52f75ca16c48")

    class LegacyPhase1PlannerNode:
        def create_plan(self, **kwargs: Any) -> SimpleNamespace:
            workspace_id = str(kwargs["workspace_id"])
            workflow_id = str(kwargs["workflow_id"])
            actor_id = str(kwargs["actor_id"])
            identity = f"{workspace_id}\0{workflow_id}"
            plan_id = str(uuid5(plan_namespace, identity))
            version_id = str(uuid5(version_namespace, identity))
            product_brief_id = str(uuid5(authority_namespace, f"brief\0{identity}"))
            retrieval_run_id = str(uuid5(authority_namespace, f"retrieval\0{identity}"))
            now = datetime.now(UTC)
            digest = "0" * 64
            payload = CreativePlanPayload(
                directions=(
                    CreativePlanDirection(
                        key="fixture-hero",
                        image_role=ImageRole.HERO,
                        scene="Deterministic fixture studio",
                        composition="Centered product composition",
                        camera="Eye-level product camera",
                        lighting="Soft neutral studio lighting",
                        color_direction="Neutral fixture palette",
                        product_constraints=("Preserve product identity",),
                        required_elements=("Product remains visible",),
                        prohibited_elements=(),
                        citation_selections=(),
                        candidate_count=1,
                        quality_targets=("Fixture integration coverage",),
                        repair_scope=(),
                        tool_intents=(),
                    ),
                )
            )
            with integration_database.session_factory.begin() as session:
                workflow = session.get(WorkflowModel, workflow_id)
                assert workflow is not None and workflow.workspace_id == workspace_id
                if session.get(CreativePlanModel, (workspace_id, plan_id)) is None:
                    session.add(
                        CreativePlanVersionModel(
                            id=version_id,
                            workspace_id=workspace_id,
                            workflow_id=workflow_id,
                            creative_plan_id=plan_id,
                            version_number=1,
                            supersedes_version_id=None,
                            source="AGENT",
                            payload_json=payload.to_canonical_data(),
                            payload_sha256=payload.payload_sha256,
                            product_brief_id=product_brief_id,
                            product_brief_version=1,
                            product_brief_sha256=digest,
                            brand_profile_id=None,
                            brand_profile_version=None,
                            brand_profile_sha256=None,
                            retrieval_run_id=retrieval_run_id,
                            retrieval_citation_ids_json=[],
                            context_policy_version="fixture-policy-v1",
                            context_sha256=digest,
                            prompt_id="fixture-planner",
                            prompt_revision="fixture-v1",
                            prompt_sha256=digest,
                            actor_id=actor_id,
                            revision_reason=None,
                            retain_until=workflow.expires_at,
                            created_at=now,
                        )
                    )
                    session.flush()
                    session.add(
                        CreativePlanModel(
                            id=plan_id,
                            workspace_id=workspace_id,
                            workflow_id=workflow_id,
                            current_version_id=version_id,
                            current_version_number=1,
                            version=1,
                            retain_until=workflow.expires_at,
                            created_at=now,
                            updated_at=now,
                        )
                    )
            output = {
                "creative_plan_ref": plan_id,
                "creative_plan_version_id": version_id,
                "creative_plan_version": 1,
                "creative_plan_payload_sha256": payload.payload_sha256,
                "planning_context_sha256": digest,
                "prompt_id": "fixture-planner",
                "prompt_revision": "fixture-v1",
                "plan_decision": None,
            }
            return SimpleNamespace(to_step_output=lambda: dict(output))

    return LegacyPhase1PlannerNode()
