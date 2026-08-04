from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from alembic import command
from alembic.config import Config
from commercevision_application import PromptRegistryApplicationService
from commercevision_contracts import (
    PromptProductionSelectionRequestV1,
    PromptRevisionCreateRequestV1,
    PromptRevisionTransitionRequestV1,
    PromptTemplateVariableV1,
    Settings,
)
from commercevision_domain import ConcurrencyError, InvalidTransitionError
from commercevision_persistence import SqlAlchemyPromptRegistryUnitOfWork
from commercevision_persistence.prompt_registry import PromptRevisionRepository
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration


@pytest.fixture
def prompt_registry_migration_database(
    integration_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Engine]:
    source_url = make_url(integration_settings.mysql_dsn)
    database_name = f"commercevision_phase3_prompt_{uuid.uuid4().hex[:8]}"
    admin_engine = create_engine(source_url.set(database="mysql"))
    test_url = source_url.set(database=database_name)
    engine = create_engine(test_url)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    monkeypatch.setenv("CV_MIGRATION_MYSQL_DSN", test_url.render_as_string(hide_password=False))
    try:
        with admin_engine.begin() as connection:
            connection.execute(
                text(
                    f"CREATE DATABASE `{database_name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
                )
            )
        command.upgrade(config, "head")
        yield engine
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{database_name}`"))
        admin_engine.dispose()


def test_prompt_registry_schema_preserves_immutable_revisions_and_exact_pointer(
    prompt_registry_migration_database: Engine,
) -> None:
    engine = prompt_registry_migration_database
    inspector = inspect(engine)
    assert {"prompt_revisions", "prompt_production_pointers"}.issubset(inspector.get_table_names())

    revision_columns = {
        column["name"]: column for column in inspector.get_columns("prompt_revisions")
    }
    pointer_columns = {
        column["name"]: column for column in inspector.get_columns("prompt_production_pointers")
    }
    for columns, names in (
        (
            revision_columns,
            (
                "created_at",
                "updated_at",
                "submitted_at",
                "reviewed_at",
                "published_at",
                "deprecated_at",
            ),
        ),
        (pointer_columns, ("updated_at",)),
    ):
        for name in names:
            column_type = columns[name]["type"]
            assert isinstance(column_type, DATETIME)
            assert column_type.fsp == 6

    revision_unique = {
        item["name"]: tuple(item["column_names"])
        for item in inspector.get_unique_constraints("prompt_revisions")
    }
    assert revision_unique["uq_prompt_revisions_workspace_semantic"] == (
        "workspace_id",
        "prompt_id",
        "semantic_revision",
    )
    assert revision_unique["uq_prompt_revisions_workspace_id"] == ("workspace_id", "id")

    pointer_foreign_keys = {
        item["name"]: (
            tuple(item["constrained_columns"]),
            tuple(item["referred_columns"]),
            item["options"].get("ondelete"),
        )
        for item in inspector.get_foreign_keys("prompt_production_pointers")
    }
    assert pointer_foreign_keys["fk_prompt_pointer_exact_revision"] == (
        ("workspace_id", "revision_id"),
        ("workspace_id", "id"),
        "RESTRICT",
    )

    with engine.connect() as connection:
        triggers = {row["Trigger"] for row in connection.execute(text("SHOW TRIGGERS")).mappings()}
    assert {
        "trg_prompt_revisions_immutable_content",
        "trg_prompt_revisions_no_delete",
        "trg_prompt_production_pointers_no_delete",
        "trg_prompt_pointer_validate_insert",
        "trg_prompt_pointer_validate_update",
        "trg_prompt_revision_active_no_deprecate",
    }.issubset(triggers)


def _request(semantic_revision: str, content_prefix: str) -> PromptRevisionCreateRequestV1:
    return PromptRevisionCreateRequestV1(
        prompt_id="creative-planner",
        semantic_revision=semantic_revision,
        node="CREATE_CREATIVE_PLAN",
        category_applicability=["beauty"],
        model_family_applicability=["fixture-planner"],
        input_schema_version="planning-context.v1",
        output_schema_version="creative-plan.v1",
        policy_version="prompt-policy.v1",
        content=(f"{content_prefix} {{{{ planning_context }}}} into {{{{ output_schema }}}}."),
        variables=[
            PromptTemplateVariableV1(name="planning_context", required=True),
            PromptTemplateVariableV1(name="output_schema", required=True),
        ],
        change_summary=f"Publish {semantic_revision}",
    )


def _service(engine: Engine) -> PromptRegistryApplicationService:
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return PromptRegistryApplicationService(lambda: SqlAlchemyPromptRegistryUnitOfWork(sessions))


def _stage_revision(
    service: PromptRegistryApplicationService,
    *,
    semantic_revision: str,
    content_prefix: str,
) -> str:
    created = service.create_revision(
        workspace_id="planning-domain",
        actor_id="prompt-admin",
        request=_request(semantic_revision, content_prefix),
        trace_id=f"trace-create-{semantic_revision}",
        idempotency_key=f"create-{semantic_revision}",
    )
    service.submit_for_review(
        workspace_id="planning-domain",
        revision_id=created.id,
        actor_id="prompt-admin",
        request=PromptRevisionTransitionRequestV1(expected_version=1),
        trace_id=f"trace-review-{semantic_revision}",
        idempotency_key=f"review-{semantic_revision}",
    )
    service.stage(
        workspace_id="planning-domain",
        revision_id=created.id,
        actor_id="prompt-reviewer",
        request=PromptRevisionTransitionRequestV1(expected_version=2),
        trace_id=f"trace-stage-{semantic_revision}",
        idempotency_key=f"stage-{semantic_revision}",
    )
    return cast(str, created.id)


def _publish_revision(
    service: PromptRegistryApplicationService,
    *,
    semantic_revision: str,
    content_prefix: str,
) -> str:
    revision_id = _stage_revision(
        service,
        semantic_revision=semantic_revision,
        content_prefix=content_prefix,
    )
    service.publish(
        workspace_id="planning-domain",
        revision_id=revision_id,
        actor_id="release-manager",
        request=PromptRevisionTransitionRequestV1(expected_version=3),
        trace_id=f"trace-publish-{semantic_revision}",
        idempotency_key=f"publish-{semantic_revision}",
    )
    return revision_id


def test_real_mysql_rolls_back_exact_pointer_and_protects_active_revision(
    prompt_registry_migration_database: Engine,
) -> None:
    service = _service(prompt_registry_migration_database)
    first_id = _publish_revision(
        service,
        semantic_revision="1.0.0",
        content_prefix="Plan",
    )
    second_id = _publish_revision(
        service,
        semantic_revision="1.1.0",
        content_prefix="Improve",
    )

    selected = service.select_production(
        workspace_id="planning-domain",
        prompt_id="creative-planner",
        actor_id="release-manager",
        request=PromptProductionSelectionRequestV1(
            revision_id=first_id,
            expected_pointer_version=2,
        ),
        trace_id="trace-rollback",
        idempotency_key="rollback-1.0.0",
    )
    assert selected.revision_id == first_id
    assert selected.version == 3

    with pytest.raises(InvalidTransitionError, match="active production"):
        service.deprecate(
            workspace_id="planning-domain",
            revision_id=first_id,
            actor_id="release-manager",
            request=PromptRevisionTransitionRequestV1(expected_version=4),
            trace_id="trace-deprecate-active",
            idempotency_key="deprecate-active-1.0.0",
        )

    deprecated = service.deprecate(
        workspace_id="planning-domain",
        revision_id=second_id,
        actor_id="release-manager",
        request=PromptRevisionTransitionRequestV1(expected_version=4),
        trace_id="trace-deprecate-old",
        idempotency_key="deprecate-old-1.1.0",
    )
    assert deprecated.status == "DEPRECATED"

    resolved = service.resolve_production(
        workspace_id="planning-domain",
        prompt_id="creative-planner",
        node="CREATE_CREATIVE_PLAN",
        category="beauty",
        model_family="fixture-planner",
    )
    assert resolved.id == first_id
    assert resolved.content_sha256 == selected.content_sha256


def test_real_mysql_stale_publication_writer_loses_version_cas(
    prompt_registry_migration_database: Engine,
) -> None:
    engine = prompt_registry_migration_database
    service = _service(engine)
    revision_id = _stage_revision(
        service,
        semantic_revision="2.0.0",
        content_prefix="Concurrent",
    )
    first_session = Session(engine, expire_on_commit=False)
    second_session = Session(engine, expire_on_commit=False)
    try:
        first_repository = PromptRevisionRepository(first_session)
        second_repository = PromptRevisionRepository(second_session)
        first = first_repository.get(workspace_id="planning-domain", revision_id=revision_id)
        second = second_repository.get(workspace_id="planning-domain", revision_id=revision_id)
        assert first is not None and second is not None
        first_published = first.publish(
            expected_version=3,
            actor_id="publisher-a",
        )
        second_published = second.publish(
            expected_version=3,
            actor_id="publisher-b",
        )
        first_repository.save_lifecycle(first_published, expected_version=3)
        first_session.commit()

        with pytest.raises(ConcurrencyError, match="changed concurrently"):
            second_repository.save_lifecycle(second_published, expected_version=3)
    finally:
        first_session.rollback()
        second_session.rollback()
        first_session.close()
        second_session.close()


def test_prompt_registry_downgrade_fails_closed_while_immutable_facts_exist(
    prompt_registry_migration_database: Engine,
) -> None:
    service = _service(prompt_registry_migration_database)
    service.create_revision(
        workspace_id="planning-domain",
        actor_id="prompt-admin",
        request=_request("3.0.0", "Durable"),
        trace_id="trace-create-durable",
        idempotency_key="create-durable-3.0.0",
    )
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))

    with pytest.raises(RuntimeError, match="immutable facts exist"):
        command.downgrade(config, "f4c8a1e7b205")
