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
def indexing_migration_database(integration_settings, monkeypatch):
    source_url = make_url(integration_settings.mysql_dsn)
    database_name = f"commercevision_ticket09_migration_{uuid.uuid4().hex[:8]}"
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


def test_indexing_migration_creates_generation_fenced_microsecond_facts(
    indexing_migration_database,
) -> None:
    config, engine = indexing_migration_database

    command.upgrade(config, "head")

    inspector = inspect(engine)
    assert {
        "collection_registry",
        "embedding_records",
        "product_search_documents",
    }.issubset(inspector.get_table_names())
    collection_columns = {
        column["name"]: column for column in inspector.get_columns("collection_registry")
    }
    embedding_columns = {
        column["name"]: column for column in inspector.get_columns("embedding_records")
    }
    assert "write_generation" not in collection_columns
    assert embedding_columns["write_generation"]["nullable"] is False
    assert embedding_columns["provider"]["nullable"] is False
    assert embedding_columns["product_brief_version_id"]["nullable"] is True
    assert embedding_columns["controlled_text_sha256"]["nullable"] is True
    for name in ("indexed_at", "stale_at", "created_at", "updated_at"):
        assert isinstance(embedding_columns[name]["type"], DATETIME)
        assert embedding_columns[name]["type"].fsp == 6
    search_columns = {
        column["name"]: column for column in inspector.get_columns("product_search_documents")
    }
    assert search_columns["controlled_text_sha256"]["nullable"] is False
    for name in ("retention_deadline", "created_at", "updated_at"):
        assert isinstance(search_columns[name]["type"], DATETIME)
        assert search_columns[name]["type"].fsp == 6
    with engine.connect() as connection:
        create_table = connection.execute(text("SHOW CREATE TABLE product_search_documents")).one()[
            1
        ]
    assert "FULLTEXT KEY `ft_product_search_cjk`" in create_table
    assert "WITH PARSER `ngram`" in create_table
    command.check(config)
