from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from commercevision_contracts import Settings
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration

_TABLES = {
    "provider_identities",
    "provider_endpoint_capability_versions",
    "provider_endpoint_capability_heads",
    "provider_discovery_candidates",
    "model_route_policy_versions",
    "model_route_policy_heads",
    "provider_endpoint_observations",
}


@pytest.fixture
def provider_control_plane_migration_database(
    integration_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Engine, Config]]:
    source_url = make_url(integration_settings.mysql_dsn)
    database_name = f"commercevision_phase4_control_{uuid.uuid4().hex[:8]}"
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
        yield engine, config
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{database_name}`"))
        admin_engine.dispose()


def test_provider_control_plane_schema_is_exact_immutable_and_reversible(
    provider_control_plane_migration_database: tuple[Engine, Config],
) -> None:
    engine, config = provider_control_plane_migration_database
    inspector = inspect(engine)
    assert _TABLES.issubset(inspector.get_table_names())
    assert tuple(
        inspector.get_pk_constraint("model_route_policy_heads")["constrained_columns"]
    ) == ("workspace_id", "policy_key")
    assert tuple(
        inspector.get_pk_constraint("provider_endpoint_observations")["constrained_columns"]
    ) == ("workspace_id", "id")

    with engine.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT TABLE_NAME, COLUMN_NAME, COLLATION_NAME, DATETIME_PRECISION, "
                "NUMERIC_PRECISION, NUMERIC_SCALE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND ((TABLE_NAME = "
                "'provider_endpoint_capability_versions' AND COLUMN_NAME IN "
                "('id', 'provider_id', 'endpoint_id', 'unit_price', 'created_at')) OR "
                "(TABLE_NAME = 'model_route_policy_heads' AND COLUMN_NAME = 'workspace_id'))"
            )
        ).mappings()
        columns = {(row["TABLE_NAME"], row["COLUMN_NAME"]): row for row in rows}
        for column_name in ("id", "provider_id", "endpoint_id"):
            assert (
                columns[("provider_endpoint_capability_versions", column_name)]["COLLATION_NAME"]
                == "utf8mb4_0900_bin"
            )
        assert columns[("model_route_policy_heads", "workspace_id")]["COLLATION_NAME"] == (
            "utf8mb4_0900_bin"
        )
        assert (
            columns[("provider_endpoint_capability_versions", "created_at")]["DATETIME_PRECISION"]
            == 6
        )
        price = columns[("provider_endpoint_capability_versions", "unit_price")]
        assert (price["NUMERIC_PRECISION"], price["NUMERIC_SCALE"]) == (20, 6)

        triggers = set(
            connection.execute(
                text(
                    "SELECT TRIGGER_NAME FROM information_schema.TRIGGERS "
                    "WHERE TRIGGER_SCHEMA = DATABASE()"
                )
            ).scalars()
        )
        assert {
            "trg_provider_capability_versions_immutable",
            "trg_model_route_policy_versions_immutable",
            "trg_provider_endpoint_observations_immutable",
        }.issubset(triggers)

        connection.execute(
            text(
                "INSERT INTO provider_identities "
                "(id, display_name, enabled, version, created_by, created_at, updated_by, "
                "updated_at) VALUES ('kuaipao', 'Kuaipao', 1, 1, 'admin', UTC_TIMESTAMP(6), "
                "'admin', UTC_TIMESTAMP(6))"
            )
        )
        connection.execute(
            text(
                "INSERT INTO provider_endpoint_capability_versions "
                "(id, provider_id, endpoint_id, version_number, capability_sha256, "
                "configuration_sha256, secret_reference, capability_json, unit_price, currency, "
                "created_by, created_at) VALUES (:id, 'kuaipao', 'images', 1, :hash, :hash, "
                "'secret-ref:test', :payload, 0.200000, 'CNY', 'admin', UTC_TIMESTAMP(6))"
            ),
            {
                "id": "019b0000-0000-7000-8000-000000000611",
                "hash": "a" * 64,
                "payload": json.dumps({"schema_version": "provider-capability.v1"}),
            },
        )

    with pytest.raises(DBAPIError, match="immutable"), engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE provider_endpoint_capability_versions SET unit_price = 0.100000 "
                "WHERE id = '019b0000-0000-7000-8000-000000000611'"
            )
        )

    command.downgrade(config, "d9a6e4b2c517")
    assert _TABLES.isdisjoint(inspect(engine).get_table_names())
    command.upgrade(config, "head")
    assert _TABLES.issubset(inspect(engine).get_table_names())
