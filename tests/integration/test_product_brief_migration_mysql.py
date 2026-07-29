from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, exc, inspect, text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.integration

_PRODUCT_BRIEF_TABLES = {
    "product_briefs",
    "product_brief_analysis_requests",
    "product_brief_source_assets",
    "product_brief_provider_attempts",
    "product_brief_provider_calls",
    "product_brief_versions",
    "product_brief_fields",
    "product_brief_evidence",
    "product_brief_confirmations",
}


@pytest.fixture
def product_brief_migration_database(integration_settings, monkeypatch):
    source_url = make_url(integration_settings.mysql_dsn)
    database_name = f"commercevision_ticket07_migration_{uuid.uuid4().hex[:8]}"
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
        yield config, engine
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{database_name}`"))
        admin_engine.dispose()


def _seed_append_only_history(engine) -> dict[str, tuple[str, str]]:
    now = datetime(2026, 7, 28, 12, 0, 0, 123456)
    deadline = datetime(2026, 7, 31, 12, 0, 0, 654321)
    ids = {
        "brief": "019f9aab-0000-7000-8000-000000000001",
        "workflow": "019f9aab-0000-7000-8000-000000000002",
        "product": "019f9aab-0000-7000-8000-000000000003",
        "operation": "019f9aab-0000-7000-8000-000000000004",
        "analysis": "019f9aab-0000-7000-8000-000000000005",
        "attempt": "019f9aab-0000-7000-8000-000000000006",
        "call": "019f9aab-0000-7000-8000-000000000007",
        "version": "019f9aab-0000-7000-8000-000000000008",
        "field": "019f9aab-0000-7000-8000-000000000009",
        "evidence": "019f9aab-0000-7000-8000-000000000010",
        "asset": "019f9aab-0000-7000-8000-000000000011",
        "asset_version": "019f9aab-0000-7000-8000-000000000012",
        "asset_object": "019f9aab-0000-7000-8000-000000000013",
    }
    workspace = "append-only-migration-workspace"
    with engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        connection.execute(
            text(
                "INSERT INTO product_briefs "
                "(id, workspace_id, workflow_id, product_id, operation_id, "
                "created_by, state, current_version_id, confirmed_version_id, "
                "version, retention_class, retention_deadline, created_at, updated_at) "
                "VALUES (:brief, :workspace, :workflow, :product, :operation, "
                "'migration-test', 'DRAFT', NULL, NULL, 1, 'TASK', :deadline, "
                ":now, :now)"
            ),
            {**ids, "workspace": workspace, "deadline": deadline, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO product_brief_analysis_requests "
                "(id, workspace_id, product_brief_id, operation_id, category, "
                "expected_workflow_version, product_catalog_version, provider, "
                "endpoint_region, endpoint_host, requested_model, "
                "submitted_model_snapshot, provider_configuration_snapshot_sha256, "
                "prompt_version, review_policy_version, review_confidence_threshold, "
                "review_mandatory_paths_json, review_sensitive_claim_paths_json, "
                "review_policy_snapshot_sha256, transfer_policy_version, "
                "transfer_policy_snapshot_sha256, created_by, retention_class, "
                "retention_deadline, created_at) VALUES "
                "(:analysis, :workspace, :brief, :operation, 'BEAUTY', 1, 1, "
                "'migration-provider', 'local', 'provider.local', 'model', "
                "'model-snapshot', :hash1, 'prompt-v1', 'review-v1', 0.9000, "
                "JSON_ARRAY(), JSON_ARRAY(), :hash2, 'transfer-v1', :hash3, "
                "'migration-test', 'TASK', :deadline, :now)"
            ),
            {
                **ids,
                "workspace": workspace,
                "deadline": deadline,
                "now": now,
                "hash1": "1" * 64,
                "hash2": "2" * 64,
                "hash3": "3" * 64,
            },
        )
        connection.execute(
            text(
                "INSERT INTO product_brief_source_assets "
                "(workspace_id, analysis_request_id, asset_id, asset_version_id, "
                "asset_object_id, ordinal, created_at) VALUES "
                "(:workspace, :analysis, :asset, :asset_version, :asset_object, "
                "0, :now)"
            ),
            {**ids, "workspace": workspace, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO product_brief_provider_attempts "
                "(id, workspace_id, product_brief_id, operation_id, "
                "operation_attempt, call_index, submission_key_sha256, input_sha256, "
                "provider, endpoint_region, endpoint_host, requested_model, "
                "submitted_model_snapshot, prompt_version, config_snapshot_sha256, "
                "retention_class, retention_deadline, created_at) VALUES "
                "(:attempt, :workspace, :brief, :operation, 1, 0, :hash1, :hash2, "
                "'migration-provider', 'local', 'provider.local', 'model', "
                "'model-snapshot', 'prompt-v1', :hash3, 'TASK', :deadline, :now)"
            ),
            {
                **ids,
                "workspace": workspace,
                "deadline": deadline,
                "now": now,
                "hash1": "4" * 64,
                "hash2": "5" * 64,
                "hash3": "6" * 64,
            },
        )
        connection.execute(
            text(
                "INSERT INTO product_brief_provider_calls "
                "(id, workspace_id, product_brief_id, operation_id, operation_attempt, "
                "call_index, status, provider, endpoint_region, endpoint_host, "
                "requested_model, submitted_model_snapshot, resolved_model, "
                "prompt_version, config_snapshot_sha256, request_id, input_tokens, "
                "output_tokens, total_tokens, latency_ms, "
                "request_artifact_storage_backend, request_artifact_location, "
                "request_artifact_bucket, request_artifact_key, "
                "request_artifact_provider_version_id, request_artifact_etag, "
                "request_artifact_sha256, request_artifact_byte_size, "
                "response_artifact_storage_backend, response_artifact_location, "
                "response_artifact_bucket, response_artifact_key, "
                "response_artifact_provider_version_id, response_artifact_etag, "
                "response_artifact_sha256, response_artifact_byte_size, error_code, "
                "error_category, error_retryable, retention_class, retention_deadline, "
                "created_at) VALUES "
                "(:call, :workspace, :brief, :operation, 1, 0, 'SUCCEEDED', "
                "'migration-provider', 'local', 'provider.local', 'model', "
                "'model-snapshot', 'model-snapshot', 'prompt-v1', :hash1, "
                "'request-1', 1, 1, 2, 10, 'MINIO', 'PROVIDER_RESULT', "
                "'provider-results', 'request.json', 'request-version', "
                "'request-etag', :hash2, 64, NULL, NULL, NULL, NULL, NULL, NULL, "
                "NULL, NULL, NULL, NULL, NULL, 'TASK', :deadline, :now)"
            ),
            {
                **ids,
                "workspace": workspace,
                "deadline": deadline,
                "now": now,
                "hash1": "7" * 64,
                "hash2": "8" * 64,
            },
        )
        connection.execute(
            text(
                "INSERT INTO product_brief_versions "
                "(id, workspace_id, product_brief_id, version_number, "
                "supersedes_version_id, category, common_schema_version, "
                "category_schema_version, payload_sha256, changed_paths_json, "
                "confirmation_required, unresolved_field_count, review_policy_version, "
                "source, prompt_version, provider_call_id, actor_id, revision_reason, "
                "retention_class, retention_deadline, created_at) VALUES "
                "(:version, :workspace, :brief, 1, NULL, 'BEAUTY', 'common-v1', "
                "'beauty-v1', :hash1, JSON_ARRAY('/title'), 1, 1, 'review-v1', "
                "'HUMAN', NULL, NULL, 'migration-test', 'initial import', "
                "'TASK', :deadline, :now)"
            ),
            {
                **ids,
                "workspace": workspace,
                "deadline": deadline,
                "now": now,
                "hash1": "9" * 64,
            },
        )
        connection.execute(
            text(
                "INSERT INTO product_brief_fields "
                "(id, workspace_id, product_brief_id, product_brief_version_id, "
                "path, value_json, confidence, source, conflict, review_required, "
                "`sensitive`, review_reasons_json, created_at) VALUES "
                "(:field, :workspace, :brief, :version, '/title', "
                "JSON_QUOTE('Product'), 0.9000, 'HUMAN', 'NONE', 0, 0, "
                "JSON_ARRAY(), :now)"
            ),
            {**ids, "workspace": workspace, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO product_brief_evidence "
                "(id, workspace_id, product_brief_id, product_brief_version_id, "
                "field_id, source_asset_version_id, kind, reference, region_json, "
                "excerpt_sha256, created_at) VALUES "
                "(:evidence, :workspace, :brief, :version, :field, :asset_version, "
                "'IMAGE_REGION', 'asset://region', JSON_OBJECT(), NULL, :now)"
            ),
            {**ids, "workspace": workspace, "now": now},
        )
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    return {
        "product_briefs": ("id", ids["brief"]),
        "product_brief_analysis_requests": ("id", ids["analysis"]),
        "product_brief_source_assets": ("analysis_request_id", ids["analysis"]),
        "product_brief_provider_attempts": ("id", ids["attempt"]),
        "product_brief_provider_calls": ("id", ids["call"]),
        "product_brief_versions": ("id", ids["version"]),
        "product_brief_fields": ("id", ids["field"]),
        "product_brief_evidence": ("id", ids["evidence"]),
    }


def test_product_brief_schema_is_versioned_immutable_and_microsecond_precise(
    product_brief_migration_database,
) -> None:
    config, engine = product_brief_migration_database
    inspector = inspect(engine)
    assert _PRODUCT_BRIEF_TABLES.issubset(inspector.get_table_names())

    for table_name, timestamp_names in (
        (
            "product_briefs",
            ("retention_deadline", "created_at", "updated_at"),
        ),
        (
            "product_brief_provider_attempts",
            ("retention_deadline", "created_at"),
        ),
        (
            "product_brief_provider_calls",
            ("retention_deadline", "created_at"),
        ),
        (
            "product_brief_confirmations",
            ("created_at",),
        ),
    ):
        columns = {column["name"]: column for column in inspector.get_columns(table_name)}
        for timestamp_name in timestamp_names:
            assert isinstance(columns[timestamp_name]["type"], DATETIME)
            assert columns[timestamp_name]["type"].fsp == 6

    version_columns = {
        column["name"]: column for column in inspector.get_columns("product_brief_versions")
    }
    assert version_columns["changed_paths_json"]["nullable"] is False
    version_uniques = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("product_brief_versions")
    }
    assert (
        "workspace_id",
        "id",
        "product_brief_id",
        "version_number",
    ) in version_uniques

    confirmation_uniques = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("product_brief_confirmations")
    }
    assert ("approval_id",) in confirmation_uniques
    approval_uniques = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("workflow_approvals")
    }
    assert (
        "id",
        "workflow_id",
        "subject_id",
        "subject_version",
        "approval_type",
        "decision",
    ) in approval_uniques
    confirmation_columns = {
        column["name"]: column for column in inspector.get_columns("product_brief_confirmations")
    }
    assert confirmation_columns["product_brief_version_number"]["nullable"] is False
    assert confirmation_columns["approval_type"]["nullable"] is False
    assert confirmation_columns["approval_decision"]["nullable"] is False
    confirmation_checks = {
        constraint["name"]: constraint["sqltext"]
        for constraint in inspector.get_check_constraints("product_brief_confirmations")
    }
    approval_type_check = confirmation_checks["ck_product_brief_confirmations_approval_type"]
    approval_decision_check = confirmation_checks[
        "ck_product_brief_confirmations_approval_decision"
    ]
    assert "approval_type" in approval_type_check
    assert "PRODUCT_BRIEF" in approval_type_check
    assert "approval_decision" in approval_decision_check
    assert "APPROVE" in approval_decision_check
    confirmation_foreign_keys = {
        foreign_key["name"]: (
            tuple(foreign_key["constrained_columns"]),
            tuple(foreign_key["referred_columns"]),
        )
        for foreign_key in inspector.get_foreign_keys("product_brief_confirmations")
    }
    assert confirmation_foreign_keys["fk_product_brief_confirmations_approval_subject"] == (
        (
            "approval_id",
            "workflow_id",
            "product_brief_version_id",
            "product_brief_version_number",
            "approval_type",
            "approval_decision",
        ),
        (
            "id",
            "workflow_id",
            "subject_id",
            "subject_version",
            "approval_type",
            "decision",
        ),
    )
    assert confirmation_foreign_keys["fk_product_brief_confirmations_version"] == (
        (
            "workspace_id",
            "product_brief_version_id",
            "product_brief_id",
            "product_brief_version_number",
        ),
        ("workspace_id", "id", "product_brief_id", "version_number"),
    )
    provider_call_uniques = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("product_brief_provider_calls")
    }
    assert ("workspace_id", "id", "product_brief_id") in provider_call_uniques
    provider_attempt_columns = {
        column["name"]: column
        for column in inspector.get_columns("product_brief_provider_attempts")
    }
    assert provider_attempt_columns["call_index"]["nullable"] is False
    provider_attempt_uniques = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("product_brief_provider_attempts")
    }
    assert ("operation_id", "operation_attempt", "call_index") in provider_attempt_uniques
    provider_attempt_checks = {
        constraint["name"]: constraint["sqltext"]
        for constraint in inspector.get_check_constraints("product_brief_provider_attempts")
    }
    provider_attempt_number_check = (
        provider_attempt_checks["ck_product_brief_provider_attempts_number"]
        .lower()
        .replace("`", "")
    )
    assert "call_index >= 0" in provider_attempt_number_check
    provider_call_columns = {
        column["name"]: column for column in inspector.get_columns("product_brief_provider_calls")
    }
    for prefix in ("request_artifact", "response_artifact"):
        for suffix in (
            "storage_backend",
            "location",
            "bucket",
            "key",
            "provider_version_id",
            "etag",
            "sha256",
            "byte_size",
        ):
            assert f"{prefix}_{suffix}" in provider_call_columns
    assert "submitted_model_snapshot" in provider_call_columns
    assert provider_call_columns["request_artifact_storage_backend"]["nullable"] is False
    assert provider_call_columns["response_artifact_storage_backend"]["nullable"] is True
    provider_call_checks = {
        constraint["name"]: constraint["sqltext"]
        for constraint in inspector.get_check_constraints("product_brief_provider_calls")
    }
    assert "'UNKNOWN'" in provider_call_checks["ck_product_brief_provider_calls_status"]
    assert (
        "BETWEEN 0 AND 2097152"
        in provider_call_checks["ck_pb_provider_calls_request_artifact"].upper()
    )
    assert (
        "BETWEEN 0 AND 2097152"
        in provider_call_checks["ck_pb_provider_calls_response_artifact"].upper()
    )
    for table_name in (
        "product_brief_analysis_requests",
        "product_brief_provider_attempts",
    ):
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        assert "submitted_model_snapshot" in columns
    version_foreign_keys = {
        foreign_key["name"]: (
            tuple(foreign_key["constrained_columns"]),
            tuple(foreign_key["referred_columns"]),
        )
        for foreign_key in inspector.get_foreign_keys("product_brief_versions")
    }
    assert version_foreign_keys["fk_product_brief_versions_provider_call"] == (
        ("workspace_id", "provider_call_id", "product_brief_id"),
        ("workspace_id", "id", "product_brief_id"),
    )
    analysis_uniques = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("product_brief_analysis_requests")
    }
    assert ("workspace_id", "product_brief_id") not in analysis_uniques
    analysis_indexes = {
        index["name"]: tuple(index["column_names"])
        for index in inspector.get_indexes("product_brief_analysis_requests")
    }
    assert analysis_indexes["ix_product_brief_analysis_requests_brief_created"] == (
        "workspace_id",
        "product_brief_id",
        "created_at",
        "id",
    )

    brief_indexes = {
        index["name"]: tuple(index["column_names"])
        for index in inspector.get_indexes("product_briefs")
    }
    assert brief_indexes["ix_product_briefs_workspace_updated"] == (
        "workspace_id",
        "updated_at",
        "id",
    )
    assert brief_indexes["ix_product_briefs_retention"] == (
        "retention_class",
        "retention_deadline",
        "state",
    )

    with engine.connect() as connection:
        triggers = {row["Trigger"] for row in connection.execute(text("SHOW TRIGGERS")).mappings()}
    assert {
        "trg_product_brief_analysis_requests_no_update",
        "trg_product_brief_source_assets_no_update",
        "trg_product_brief_provider_attempts_no_update",
        "trg_product_brief_provider_calls_no_update",
        "trg_product_brief_versions_no_update",
        "trg_product_brief_fields_no_update",
        "trg_product_brief_evidence_no_update",
        "trg_product_brief_confirmations_no_update",
        "trg_product_briefs_no_delete",
        "trg_product_brief_analysis_requests_no_delete",
        "trg_product_brief_source_assets_no_delete",
        "trg_product_brief_provider_attempts_no_delete",
        "trg_product_brief_provider_calls_no_delete",
        "trg_product_brief_versions_no_delete",
        "trg_product_brief_fields_no_delete",
        "trg_product_brief_evidence_no_delete",
        "trg_product_brief_confirmations_no_delete",
    }.issubset(triggers)

    now = datetime(2026, 7, 28, 12, 0, 0, 123456)
    with engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        connection.execute(
            text(
                "INSERT INTO product_briefs "
                "(id, workspace_id, workflow_id, product_id, operation_id, "
                "created_by, state, current_version_id, confirmed_version_id, "
                "version, retention_class, retention_deadline, created_at, updated_at) "
                "VALUES (:id, 'migration-workspace', :workflow_id, :product_id, "
                ":operation_id, 'migration-actor', 'DRAFT', NULL, NULL, 1, "
                "'TASK', :deadline, :created_at, :updated_at)"
            ),
            {
                "created_at": now,
                "deadline": datetime(2026, 7, 31, 12, 0, 0, 654321),
                "id": "019f9aaa-0000-7000-8000-000000000001",
                "operation_id": "019f9aaa-0000-7000-8000-000000000004",
                "product_id": "019f9aaa-0000-7000-8000-000000000003",
                "updated_at": now,
                "workflow_id": "019f9aaa-0000-7000-8000-000000000002",
            },
        )
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))

    with pytest.raises(RuntimeError, match="ProductBrief history"):
        command.downgrade(config, "c8d3e7f1a602")
    assert _PRODUCT_BRIEF_TABLES.issubset(inspect(engine).get_table_names())


def test_each_product_brief_history_table_rejects_direct_delete(
    product_brief_migration_database,
) -> None:
    config, engine = product_brief_migration_database
    command.downgrade(config, "d9e4f7a2b610")
    protected_rows = _seed_append_only_history(engine)

    for table_name, (key_name, key_value) in protected_rows.items():
        with (
            pytest.raises(exc.DatabaseError, match="cannot be deleted"),
            engine.begin() as connection,
        ):
            connection.execute(
                text(f"DELETE FROM {table_name} WHERE {key_name} = :key_value"),
                {"key_value": key_value},
            )
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(f"SELECT COUNT(*) FROM {table_name} WHERE {key_name} = :key_value"),
                    {"key_value": key_value},
                ).scalar_one()
                == 1
            )


def test_product_brief_schema_downgrades_when_history_is_empty(
    product_brief_migration_database,
) -> None:
    config, engine = product_brief_migration_database

    command.downgrade(config, "c8d3e7f1a602")

    assert _PRODUCT_BRIEF_TABLES.isdisjoint(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "c8d3e7f1a602"
        )
