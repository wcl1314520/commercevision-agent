from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.integration


def test_complete_migration_chain_round_trips_on_mysql(
    integration_settings,
    monkeypatch,
) -> None:
    source_url = make_url(integration_settings.mysql_dsn)
    database_name = f"commercevision_roundtrip_{uuid.uuid4().hex[:8]}"
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

        command.upgrade(config, "head")
        assert "product_brief_provider_artifacts" in inspect(engine).get_table_names()

        command.downgrade(config, "base")
        assert set(inspect(engine).get_table_names()) <= {"alembic_version"}

        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert "product_brief_provider_artifacts" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{database_name}`"))
        admin_engine.dispose()
