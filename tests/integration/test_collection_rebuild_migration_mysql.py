from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.integration


@pytest.fixture
def rebuild_migration_database(integration_settings, monkeypatch):
    source_url = make_url(integration_settings.mysql_dsn)
    database_name = f"commercevision_ticket14_migration_{uuid.uuid4().hex[:8]}"
    admin_engine = create_engine(source_url.set(database="mysql"))
    test_url = source_url.set(database=database_name)
    engine = create_engine(test_url)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    monkeypatch.setenv(
        "CV_MIGRATION_MYSQL_DSN",
        test_url.render_as_string(hide_password=False),
    )
    try:
        with admin_engine.begin() as connection:
            connection.execute(
                text(
                    f"CREATE DATABASE `{database_name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
                )
            )
        yield config, engine
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{database_name}`"))
        admin_engine.dispose()


def test_collection_rebuild_migration_matches_durable_checkpoint_schema(
    rebuild_migration_database,
) -> None:
    config, engine = rebuild_migration_database
    command.upgrade(config, "head")

    inspector = inspect(engine)
    assert {
        "retrieval_policy_pointers",
        "collection_rebuilds",
        "collection_rebuild_placements",
        "collection_rebuild_progress",
    } <= set(inspector.get_table_names())
    columns = {item["name"]: item for item in inspector.get_columns("collection_rebuilds")}
    for name in (
        "snapshot_watermark",
        "replay_watermark",
        "replay_cursor_occurred_at",
        "validation_watermark",
        "retire_after",
    ):
        assert isinstance(columns[name]["type"], DATETIME)
        assert columns[name]["type"].fsp == 6
    assert {
        "uq_collection_registry_logical_instance",
        "uq_collection_registry_spec_instance",
        "uq_collection_registry_rebuild",
    } <= {item["name"] for item in inspector.get_unique_constraints("collection_registry")}
    assert "ix_outbox_rebuild_replay" in {
        item["name"] for item in inspector.get_indexes("outbox_events")
    }
    assert not any(
        item["name"] == "fk_collection_rebuild_operation"
        for item in inspector.get_foreign_keys("collection_rebuilds")
    )
    command.check(config)


def test_empty_collection_rebuild_migration_round_trips(rebuild_migration_database) -> None:
    config, engine = rebuild_migration_database
    command.upgrade(config, "head")
    command.downgrade(config, "e1b7c4d9a263")

    inspector = inspect(engine)
    assert {
        "retrieval_policy_pointers",
        "collection_rebuilds",
        "collection_rebuild_placements",
        "collection_rebuild_progress",
    }.isdisjoint(inspector.get_table_names())
    assert "instance_generation" not in {
        item["name"] for item in inspector.get_columns("collection_registry")
    }
    assert "ix_outbox_rebuild_replay" not in {
        item["name"] for item in inspector.get_indexes("outbox_events")
    }
