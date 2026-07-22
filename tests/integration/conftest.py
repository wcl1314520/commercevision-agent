from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from commercevision_contracts import Settings
from commercevision_persistence import create_database
from commercevision_persistence.models import Base
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
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    try:
        yield database
    finally:
        database.dispose()
