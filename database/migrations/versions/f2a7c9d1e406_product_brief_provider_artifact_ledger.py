"""Add the durable ProductBrief provider artifact ledger.

Revision ID: f2a7c9d1e406
Revises: d9e4f7a2b610
Create Date: 2026-07-29 08:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "f2a7c9d1e406"
down_revision: str | Sequence[str] | None = "d9e4f7a2b610"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKSPACE = sa.String(128, collation="utf8mb4_0900_bin")
_ARTIFACT_IMMUTABLE_COLUMNS = (
    "id",
    "workspace_id",
    "product_brief_id",
    "operation_id",
    "operation_attempt",
    "call_index",
    "kind",
    "key_schema_version",
    "storage_backend",
    "location",
    "bucket",
    "object_key",
    "target_sha256",
    "content_type",
    "expected_sha256",
    "expected_byte_size",
    "retention_class",
    "retention_deadline",
    "write_fence",
    "created_at",
)
_CALL_IMMUTABILITY_TRIGGER = "trg_product_brief_provider_calls_no_update"


def _exact(length: int) -> sa.String:
    return sa.String(length, collation="utf8mb4_0900_bin")


def _restore_call_immutability_trigger() -> None:
    op.execute(sa.text(f"DROP TRIGGER IF EXISTS {_CALL_IMMUTABILITY_TRIGGER}"))
    op.execute(
        sa.text(
            f"CREATE TRIGGER {_CALL_IMMUTABILITY_TRIGGER} "
            "BEFORE UPDATE ON product_brief_provider_calls FOR EACH ROW "
            "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
            "'product_brief_provider_calls are immutable'"
        )
    )


def _assert_legacy_provider_calls_are_backfillable(
    connection: sa.Connection,
) -> None:
    successful_calls_without_response = int(
        connection.execute(
            sa.text(
                "SELECT COUNT(*) FROM product_brief_provider_calls "
                "WHERE status = 'SUCCEEDED' "
                "AND response_artifact_storage_backend IS NULL"
            )
        ).scalar_one()
    )
    if successful_calls_without_response:
        raise RuntimeError(
            "successful legacy provider calls require response artifacts"
        )
    incompatibility_count = int(
        connection.execute(
            sa.text(
                """
                SELECT
                    (
                        SELECT COUNT(*)
                        FROM product_brief_provider_calls AS calls
                        WHERE calls.operation_attempt <= 0
                            OR calls.call_index < 0
                            OR calls.request_artifact_storage_backend
                                NOT IN ('MINIO', 'OSS')
                            OR calls.request_artifact_location
                                <> 'PROVIDER_RESULT'
                            OR calls.request_artifact_bucket IS NULL
                            OR calls.request_artifact_key IS NULL
                            OR calls.request_artifact_provider_version_id IS NULL
                            OR calls.request_artifact_etag IS NULL
                            OR calls.request_artifact_sha256
                                NOT REGEXP '^[0-9a-f]{64}$'
                            OR calls.request_artifact_byte_size
                                NOT BETWEEN 0 AND 2097152
                            OR NOT (
                                (
                                    calls.response_artifact_storage_backend IS NULL
                                    AND calls.response_artifact_location IS NULL
                                    AND calls.response_artifact_bucket IS NULL
                                    AND calls.response_artifact_key IS NULL
                                    AND calls.response_artifact_provider_version_id
                                        IS NULL
                                    AND calls.response_artifact_etag IS NULL
                                    AND calls.response_artifact_sha256 IS NULL
                                    AND calls.response_artifact_byte_size IS NULL
                                )
                                OR (
                                    calls.response_artifact_storage_backend
                                        IN ('MINIO', 'OSS')
                                    AND calls.response_artifact_location =
                                        'PROVIDER_RESULT'
                                    AND calls.response_artifact_bucket IS NOT NULL
                                    AND calls.response_artifact_key IS NOT NULL
                                    AND calls.response_artifact_provider_version_id
                                        IS NOT NULL
                                    AND calls.response_artifact_etag IS NOT NULL
                                    AND calls.response_artifact_sha256
                                        REGEXP '^[0-9a-f]{64}$'
                                    AND calls.response_artifact_byte_size
                                        BETWEEN 0 AND 2097152
                                )
                            )
                            OR NOT (
                                (
                                    calls.retention_class = 'TASK'
                                    AND calls.retention_deadline IS NOT NULL
                                )
                                OR (
                                    calls.retention_class = 'FOUNDATION'
                                    AND calls.retention_deadline IS NULL
                                )
                            )
                            OR NOT EXISTS (
                                SELECT 1
                                FROM product_briefs AS brief
                                WHERE brief.workspace_id = calls.workspace_id
                                    AND brief.id = calls.product_brief_id
                            )
                            OR NOT EXISTS (
                                SELECT 1
                                FROM durable_operations AS operation
                                WHERE operation.workspace_id = calls.workspace_id
                                    AND operation.id = calls.operation_id
                            )
                    )
                    +
                    (
                        SELECT COUNT(*)
                        FROM (
                            SELECT
                                legacy_artifact.storage_backend,
                                legacy_artifact.location,
                                LOWER(
                                    SHA2(
                                        CONCAT(
                                            legacy_artifact.storage_backend,
                                            CHAR(0),
                                            legacy_artifact.location,
                                            CHAR(0),
                                            legacy_artifact.bucket,
                                            CHAR(0),
                                            legacy_artifact.object_key
                                        ),
                                        256
                                    )
                                ) AS target_sha256
                            FROM (
                                SELECT
                                    request_artifact_storage_backend
                                        AS storage_backend,
                                    request_artifact_location AS location,
                                    request_artifact_bucket AS bucket,
                                    request_artifact_key AS object_key
                                FROM product_brief_provider_calls
                                UNION ALL
                                SELECT
                                    response_artifact_storage_backend
                                        AS storage_backend,
                                    response_artifact_location AS location,
                                    response_artifact_bucket AS bucket,
                                    response_artifact_key AS object_key
                                FROM product_brief_provider_calls
                                WHERE response_artifact_storage_backend IS NOT NULL
                            ) AS legacy_artifact
                            GROUP BY
                                legacy_artifact.storage_backend,
                                legacy_artifact.location,
                                target_sha256
                            HAVING COUNT(*) > 1
                        ) AS duplicate_physical_targets
                    )
                    +
                    (
                        SELECT COUNT(*)
                        FROM (
                            SELECT
                                logical_artifact.operation_id,
                                logical_artifact.operation_attempt,
                                logical_artifact.call_index,
                                logical_artifact.kind
                            FROM (
                                SELECT
                                    operation_id,
                                    operation_attempt,
                                    call_index,
                                    'REQUEST' AS kind
                                FROM product_brief_provider_calls
                                UNION ALL
                                SELECT
                                    operation_id,
                                    operation_attempt,
                                    call_index,
                                    'RESPONSE' AS kind
                                FROM product_brief_provider_calls
                                WHERE response_artifact_storage_backend IS NOT NULL
                            ) AS logical_artifact
                            GROUP BY
                                logical_artifact.operation_id,
                                logical_artifact.operation_attempt,
                                logical_artifact.call_index,
                                logical_artifact.kind
                            HAVING COUNT(*) > 1
                        ) AS duplicate_logical_artifacts
                    ) AS incompatibility_count
                """
            )
        ).scalar_one()
    )
    if incompatibility_count:
        raise RuntimeError(
            "legacy provider calls cannot be backfilled without losing artifact facts"
        )


def _provider_artifact_ledger_incompatibility_count(
    connection: sa.Connection,
) -> int:
    return int(
        connection.execute(
            sa.text(
                """
                SELECT
                    (
                        SELECT COUNT(*)
                        FROM product_brief_provider_artifacts AS artifact
                        WHERE artifact.state <> 'STORED'
                            OR (
                                artifact.kind = 'REQUEST'
                                AND NOT EXISTS (
                                    SELECT 1
                                    FROM product_brief_provider_calls AS calls
                                    WHERE calls.request_artifact_id = artifact.id
                                        AND calls.workspace_id = artifact.workspace_id
                                        AND calls.product_brief_id =
                                            artifact.product_brief_id
                                        AND calls.operation_id = artifact.operation_id
                                        AND calls.operation_attempt =
                                            artifact.operation_attempt
                                        AND calls.call_index = artifact.call_index
                                        AND calls.request_artifact_storage_backend
                                            <=> artifact.storage_backend
                                        AND calls.request_artifact_location
                                            <=> artifact.location
                                        AND calls.request_artifact_bucket
                                            <=> artifact.bucket
                                        AND calls.request_artifact_key
                                            <=> artifact.object_key
                                        AND calls.request_artifact_provider_version_id
                                            <=> artifact.provider_version_id
                                        AND calls.request_artifact_etag
                                            <=> artifact.etag
                                        AND calls.request_artifact_sha256
                                            <=> artifact.expected_sha256
                                        AND calls.request_artifact_byte_size
                                            <=> artifact.expected_byte_size
                                        AND calls.retention_class
                                            <=> artifact.retention_class
                                        AND calls.retention_deadline
                                            <=> artifact.retention_deadline
                                )
                            )
                            OR (
                                artifact.kind = 'RESPONSE'
                                AND NOT EXISTS (
                                    SELECT 1
                                    FROM product_brief_provider_calls AS calls
                                    WHERE calls.response_artifact_id = artifact.id
                                        AND calls.workspace_id = artifact.workspace_id
                                        AND calls.product_brief_id =
                                            artifact.product_brief_id
                                        AND calls.operation_id = artifact.operation_id
                                        AND calls.operation_attempt =
                                            artifact.operation_attempt
                                        AND calls.call_index = artifact.call_index
                                        AND calls.response_artifact_storage_backend
                                            <=> artifact.storage_backend
                                        AND calls.response_artifact_location
                                            <=> artifact.location
                                        AND calls.response_artifact_bucket
                                            <=> artifact.bucket
                                        AND calls.response_artifact_key
                                            <=> artifact.object_key
                                        AND calls.response_artifact_provider_version_id
                                            <=> artifact.provider_version_id
                                        AND calls.response_artifact_etag
                                            <=> artifact.etag
                                        AND calls.response_artifact_sha256
                                            <=> artifact.expected_sha256
                                        AND calls.response_artifact_byte_size
                                            <=> artifact.expected_byte_size
                                        AND calls.retention_class
                                            <=> artifact.retention_class
                                        AND calls.retention_deadline
                                            <=> artifact.retention_deadline
                                )
                            )
                    )
                    +
                    (
                        SELECT COUNT(*)
                        FROM product_brief_provider_calls AS calls
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM product_brief_provider_artifacts AS artifact
                            WHERE artifact.id = calls.request_artifact_id
                                AND artifact.kind = 'REQUEST'
                                AND artifact.state = 'STORED'
                                AND artifact.workspace_id = calls.workspace_id
                                AND artifact.product_brief_id =
                                    calls.product_brief_id
                                AND artifact.operation_id = calls.operation_id
                                AND artifact.operation_attempt =
                                    calls.operation_attempt
                                AND artifact.call_index = calls.call_index
                                AND artifact.storage_backend
                                    <=> calls.request_artifact_storage_backend
                                AND artifact.location
                                    <=> calls.request_artifact_location
                                AND artifact.bucket
                                    <=> calls.request_artifact_bucket
                                AND artifact.object_key
                                    <=> calls.request_artifact_key
                                AND artifact.provider_version_id
                                    <=> calls.request_artifact_provider_version_id
                                AND artifact.etag
                                    <=> calls.request_artifact_etag
                                AND artifact.expected_sha256
                                    <=> calls.request_artifact_sha256
                                AND artifact.expected_byte_size
                                    <=> calls.request_artifact_byte_size
                                AND artifact.retention_class
                                    <=> calls.retention_class
                                AND artifact.retention_deadline
                                    <=> calls.retention_deadline
                        )
                            OR (
                                calls.response_artifact_id IS NULL
                                AND NOT (
                                    calls.response_artifact_storage_backend IS NULL
                                    AND calls.response_artifact_location IS NULL
                                    AND calls.response_artifact_bucket IS NULL
                                    AND calls.response_artifact_key IS NULL
                                    AND calls.response_artifact_provider_version_id
                                        IS NULL
                                    AND calls.response_artifact_etag IS NULL
                                    AND calls.response_artifact_sha256 IS NULL
                                    AND calls.response_artifact_byte_size IS NULL
                                )
                            )
                            OR (
                                calls.response_artifact_id IS NOT NULL
                                AND NOT EXISTS (
                                    SELECT 1
                                    FROM product_brief_provider_artifacts AS artifact
                                    WHERE artifact.id =
                                        calls.response_artifact_id
                                        AND artifact.kind = 'RESPONSE'
                                        AND artifact.state = 'STORED'
                                        AND artifact.workspace_id =
                                            calls.workspace_id
                                        AND artifact.product_brief_id =
                                            calls.product_brief_id
                                        AND artifact.operation_id =
                                            calls.operation_id
                                        AND artifact.operation_attempt =
                                            calls.operation_attempt
                                        AND artifact.call_index = calls.call_index
                                        AND artifact.storage_backend
                                            <=> calls.response_artifact_storage_backend
                                        AND artifact.location
                                            <=> calls.response_artifact_location
                                        AND artifact.bucket
                                            <=> calls.response_artifact_bucket
                                        AND artifact.object_key
                                            <=> calls.response_artifact_key
                                        AND artifact.provider_version_id
                                            <=> calls.response_artifact_provider_version_id
                                        AND artifact.etag
                                            <=> calls.response_artifact_etag
                                        AND artifact.expected_sha256
                                            <=> calls.response_artifact_sha256
                                        AND artifact.expected_byte_size
                                            <=> calls.response_artifact_byte_size
                                        AND artifact.retention_class
                                            <=> calls.retention_class
                                        AND artifact.retention_deadline
                                            <=> calls.retention_deadline
                                )
                            )
                    ) AS incompatible_count
                """
            )
        ).scalar_one()
    )


def _assert_provider_artifact_ledger_is_downgrade_safe(
    connection: sa.Connection,
) -> None:
    if _provider_artifact_ledger_incompatibility_count(connection):
        raise RuntimeError(
            "provider artifact ledger cannot be represented by the legacy call schema"
        )


def upgrade() -> None:
    """Create the ledger and backfill every completed call reference."""

    _assert_legacy_provider_calls_are_backfillable(op.get_bind())
    op.create_table(
        "product_brief_provider_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("product_brief_id", sa.String(length=36), nullable=False),
        sa.Column("operation_id", sa.String(length=36), nullable=False),
        sa.Column("operation_attempt", sa.Integer(), nullable=False),
        sa.Column("call_index", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("key_schema_version", _exact(32), nullable=False),
        sa.Column("storage_backend", sa.String(length=16), nullable=False),
        sa.Column("location", sa.String(length=32), nullable=False),
        sa.Column("bucket", _exact(255), nullable=False),
        sa.Column("object_key", _exact(1024), nullable=False),
        sa.Column("target_sha256", _exact(64), nullable=False),
        sa.Column("content_type", _exact(128), nullable=False),
        sa.Column("expected_sha256", _exact(64), nullable=False),
        sa.Column("expected_byte_size", sa.Integer(), nullable=False),
        sa.Column("retention_class", sa.String(length=16), nullable=False),
        sa.Column("retention_deadline", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("write_fence", _exact(64), nullable=False),
        sa.Column("provider_version_id", _exact(256), nullable=True),
        sa.Column("etag", _exact(512), nullable=True),
        sa.Column("unknown_reason", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("stored_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.CheckConstraint(
            "operation_attempt > 0 AND call_index >= 0",
            name="ck_pb_provider_artifacts_owner_numbers",
        ),
        sa.CheckConstraint(
            "kind IN ('REQUEST', 'RESPONSE')",
            name="ck_pb_provider_artifacts_kind",
        ),
        sa.CheckConstraint(
            "state IN ('INTENDED', 'STORED', 'UNKNOWN')",
            name="ck_pb_provider_artifacts_state",
        ),
        sa.CheckConstraint(
            "storage_backend IN ('MINIO', 'OSS') AND location = 'PROVIDER_RESULT'",
            name="ck_pb_provider_artifacts_storage",
        ),
        sa.CheckConstraint(
            "target_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND expected_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND write_fence REGEXP '^[0-9a-f]{64}$'",
            name="ck_pb_provider_artifacts_hashes",
        ),
        sa.CheckConstraint(
            "target_sha256 = SHA2(CONCAT("
            "storage_backend, CHAR(0), location, CHAR(0), "
            "bucket, CHAR(0), object_key), 256)",
            name="ck_pb_provider_artifacts_target_identity",
        ),
        sa.CheckConstraint(
            "expected_byte_size BETWEEN 0 AND 2097152",
            name="ck_pb_provider_artifacts_size",
        ),
        sa.CheckConstraint(
            "(retention_class = 'TASK' AND retention_deadline IS NOT NULL) "
            "OR (retention_class = 'FOUNDATION' AND retention_deadline IS NULL)",
            name="ck_pb_provider_artifacts_retention",
        ),
        sa.CheckConstraint(
            "(state = 'STORED' AND provider_version_id IS NOT NULL "
            "AND etag IS NOT NULL AND stored_at IS NOT NULL "
            "AND unknown_reason IS NULL) "
            "OR (state = 'INTENDED' AND provider_version_id IS NULL "
            "AND etag IS NULL AND stored_at IS NULL AND unknown_reason IS NULL) "
            "OR (state = 'UNKNOWN' AND provider_version_id IS NULL "
            "AND etag IS NULL AND stored_at IS NULL AND unknown_reason IS NOT NULL)",
            name="ck_pb_provider_artifacts_lifecycle",
        ),
        sa.CheckConstraint(
            "version > 0",
            name="ck_pb_provider_artifacts_version",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_brief_id"],
            ["product_briefs.workspace_id", "product_briefs.id"],
            name="fk_pb_provider_artifacts_brief",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_pb_provider_artifacts_operation",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_pb_provider_artifacts_workspace_id",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            "product_brief_id",
            name="uq_pb_provider_artifacts_workspace_brief",
        ),
        sa.UniqueConstraint(
            "operation_id",
            "operation_attempt",
            "call_index",
            "kind",
            name="uq_pb_provider_artifacts_logical",
        ),
        sa.UniqueConstraint(
            "storage_backend",
            "location",
            "target_sha256",
            name="uq_pb_provider_artifacts_physical",
        ),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_pb_provider_artifacts_reconciliation",
        "product_brief_provider_artifacts",
        ["state", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_pb_provider_artifacts_brief_created",
        "product_brief_provider_artifacts",
        ["workspace_id", "product_brief_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_pb_provider_artifacts_retention",
        "product_brief_provider_artifacts",
        ["retention_class", "retention_deadline", "state", "id"],
        unique=False,
    )

    op.add_column(
        "product_brief_provider_calls",
        sa.Column("request_artifact_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "product_brief_provider_calls",
        sa.Column("response_artifact_id", sa.String(length=36), nullable=True),
    )

    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            INSERT INTO product_brief_provider_artifacts (
                id, workspace_id, product_brief_id, operation_id,
                operation_attempt, call_index, kind, state,
                key_schema_version, storage_backend, location, bucket,
                object_key, target_sha256, content_type, expected_sha256,
                expected_byte_size, retention_class, retention_deadline,
                write_fence, provider_version_id, etag, unknown_reason,
                version, stored_at, created_at, updated_at
            )
            SELECT
                UUID(), workspace_id, product_brief_id, operation_id,
                operation_attempt, call_index, 'REQUEST', 'STORED',
                'legacy-provider-artifact-v1',
                request_artifact_storage_backend,
                request_artifact_location,
                request_artifact_bucket,
                request_artifact_key,
                LOWER(SHA2(CONCAT(
                    request_artifact_storage_backend, CHAR(0),
                    request_artifact_location, CHAR(0),
                    request_artifact_bucket, CHAR(0),
                    request_artifact_key
                ), 256)),
                'application/json',
                request_artifact_sha256,
                request_artifact_byte_size,
                retention_class,
                retention_deadline,
                LOWER(SHA2(CONCAT(
                    'legacy-provider-artifact', CHAR(58), id, CHAR(58), 'REQUEST'
                ), 256)),
                request_artifact_provider_version_id,
                request_artifact_etag,
                NULL,
                1,
                created_at,
                created_at,
                created_at
            FROM product_brief_provider_calls
            """
        )
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO product_brief_provider_artifacts (
                id, workspace_id, product_brief_id, operation_id,
                operation_attempt, call_index, kind, state,
                key_schema_version, storage_backend, location, bucket,
                object_key, target_sha256, content_type, expected_sha256,
                expected_byte_size, retention_class, retention_deadline,
                write_fence, provider_version_id, etag, unknown_reason,
                version, stored_at, created_at, updated_at
            )
            SELECT
                UUID(), workspace_id, product_brief_id, operation_id,
                operation_attempt, call_index, 'RESPONSE', 'STORED',
                'legacy-provider-artifact-v1',
                response_artifact_storage_backend,
                response_artifact_location,
                response_artifact_bucket,
                response_artifact_key,
                LOWER(SHA2(CONCAT(
                    response_artifact_storage_backend, CHAR(0),
                    response_artifact_location, CHAR(0),
                    response_artifact_bucket, CHAR(0),
                    response_artifact_key
                ), 256)),
                'application/json',
                response_artifact_sha256,
                response_artifact_byte_size,
                retention_class,
                retention_deadline,
                LOWER(SHA2(CONCAT(
                    'legacy-provider-artifact', CHAR(58), id, CHAR(58), 'RESPONSE'
                ), 256)),
                response_artifact_provider_version_id,
                response_artifact_etag,
                NULL,
                1,
                created_at,
                created_at,
                created_at
            FROM product_brief_provider_calls
            WHERE response_artifact_storage_backend IS NOT NULL
            """
        )
    )
    op.execute(sa.text(f"DROP TRIGGER IF EXISTS {_CALL_IMMUTABILITY_TRIGGER}"))
    try:
        connection.execute(
            sa.text(
                """
                UPDATE product_brief_provider_calls AS calls
                INNER JOIN product_brief_provider_artifacts AS artifacts
                    ON artifacts.operation_id = calls.operation_id
                    AND artifacts.operation_attempt = calls.operation_attempt
                    AND artifacts.call_index = calls.call_index
                    AND artifacts.kind = 'REQUEST'
                SET calls.request_artifact_id = artifacts.id
                """
            )
        )
        connection.execute(
            sa.text(
                """
                UPDATE product_brief_provider_calls AS calls
                INNER JOIN product_brief_provider_artifacts AS artifacts
                    ON artifacts.operation_id = calls.operation_id
                    AND artifacts.operation_attempt = calls.operation_attempt
                    AND artifacts.call_index = calls.call_index
                    AND artifacts.kind = 'RESPONSE'
                SET calls.response_artifact_id = artifacts.id
                """
            )
        )
        missing_request = connection.execute(
            sa.text(
                "SELECT COUNT(*) FROM product_brief_provider_calls "
                "WHERE request_artifact_id IS NULL"
            )
        ).scalar_one()
        missing_response = connection.execute(
            sa.text(
                "SELECT COUNT(*) FROM product_brief_provider_calls "
                "WHERE ((response_artifact_storage_backend IS NULL) "
                "<> (response_artifact_id IS NULL)) "
                "OR (status = 'SUCCEEDED' AND response_artifact_id IS NULL)"
            )
        ).scalar_one()
        if missing_request or missing_response:
            raise RuntimeError(
                "provider artifact ledger backfill did not cover every completed call"
            )
    finally:
        _restore_call_immutability_trigger()

    op.alter_column(
        "product_brief_provider_calls",
        "request_artifact_id",
        existing_type=sa.String(length=36),
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_pb_provider_calls_request_artifact",
        "product_brief_provider_calls",
        ["request_artifact_id"],
    )
    op.create_unique_constraint(
        "uq_pb_provider_calls_response_artifact",
        "product_brief_provider_calls",
        ["response_artifact_id"],
    )
    op.create_foreign_key(
        "fk_pb_provider_calls_request_artifact",
        "product_brief_provider_calls",
        "product_brief_provider_artifacts",
        ["workspace_id", "request_artifact_id", "product_brief_id"],
        ["workspace_id", "id", "product_brief_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_pb_provider_calls_response_artifact",
        "product_brief_provider_calls",
        "product_brief_provider_artifacts",
        ["workspace_id", "response_artifact_id", "product_brief_id"],
        ["workspace_id", "id", "product_brief_id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_pb_provider_calls_response_ledger",
        "product_brief_provider_calls",
        "(response_artifact_id IS NULL "
        "AND response_artifact_storage_backend IS NULL) "
        "OR (response_artifact_id IS NOT NULL "
        "AND response_artifact_storage_backend IS NOT NULL)",
    )

    unchanged_artifact = " AND ".join(
        f"(OLD.{column_name} <=> NEW.{column_name})" for column_name in _ARTIFACT_IMMUTABLE_COLUMNS
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_pb_provider_artifacts_lifecycle "
            "BEFORE UPDATE ON product_brief_provider_artifacts FOR EACH ROW "
            "BEGIN "
            "IF NOT ("
            "((OLD.state = 'INTENDED' AND NEW.state IN ('STORED', 'UNKNOWN')) "
            "OR (OLD.state = 'UNKNOWN' AND NEW.state IN ('STORED', 'UNKNOWN'))) "
            "AND NEW.version = OLD.version + 1 "
            f"AND {unchanged_artifact}"
            ") THEN "
            "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
            "'provider artifact lifecycle update is invalid'; "
            "END IF; "
            "END"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_pb_provider_artifacts_no_delete "
            "BEFORE DELETE ON product_brief_provider_artifacts FOR EACH ROW "
            "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
            "'provider artifacts cannot be deleted'"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_pb_provider_calls_validate_artifacts "
            "BEFORE INSERT ON product_brief_provider_calls FOR EACH ROW "
            "BEGIN "
            "IF NOT EXISTS ("
            "SELECT 1 FROM product_brief_provider_artifacts AS artifact "
            "WHERE artifact.id = NEW.request_artifact_id "
            "AND (artifact.workspace_id <=> NEW.workspace_id) "
            "AND (artifact.product_brief_id <=> NEW.product_brief_id) "
            "AND (artifact.operation_id <=> NEW.operation_id) "
            "AND (artifact.operation_attempt <=> NEW.operation_attempt) "
            "AND (artifact.call_index <=> NEW.call_index) "
            "AND artifact.kind = 'REQUEST' "
            "AND artifact.state = 'STORED' "
            "AND (artifact.storage_backend "
            "<=> NEW.request_artifact_storage_backend) "
            "AND (artifact.location <=> NEW.request_artifact_location) "
            "AND (artifact.bucket <=> NEW.request_artifact_bucket) "
            "AND (artifact.object_key <=> NEW.request_artifact_key) "
            "AND (artifact.provider_version_id "
            "<=> NEW.request_artifact_provider_version_id) "
            "AND (artifact.etag <=> NEW.request_artifact_etag) "
            "AND (artifact.expected_sha256 <=> NEW.request_artifact_sha256) "
            "AND (artifact.expected_byte_size "
            "<=> NEW.request_artifact_byte_size) "
            "AND (artifact.retention_class <=> NEW.retention_class) "
            "AND (artifact.retention_deadline <=> NEW.retention_deadline)"
            ") THEN "
            "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
            "'provider call request artifact binding is invalid'; "
            "END IF; "
            "IF (NEW.status = 'SUCCEEDED' "
            "AND NEW.response_artifact_id IS NULL) OR ("
            "NEW.response_artifact_id IS NOT NULL AND NOT EXISTS ("
            "SELECT 1 FROM product_brief_provider_artifacts AS artifact "
            "WHERE artifact.id = NEW.response_artifact_id "
            "AND (artifact.workspace_id <=> NEW.workspace_id) "
            "AND (artifact.product_brief_id <=> NEW.product_brief_id) "
            "AND (artifact.operation_id <=> NEW.operation_id) "
            "AND (artifact.operation_attempt <=> NEW.operation_attempt) "
            "AND (artifact.call_index <=> NEW.call_index) "
            "AND artifact.kind = 'RESPONSE' "
            "AND artifact.state = 'STORED' "
            "AND (artifact.storage_backend "
            "<=> NEW.response_artifact_storage_backend) "
            "AND (artifact.location <=> NEW.response_artifact_location) "
            "AND (artifact.bucket <=> NEW.response_artifact_bucket) "
            "AND (artifact.object_key <=> NEW.response_artifact_key) "
            "AND (artifact.provider_version_id "
            "<=> NEW.response_artifact_provider_version_id) "
            "AND (artifact.etag <=> NEW.response_artifact_etag) "
            "AND (artifact.expected_sha256 <=> NEW.response_artifact_sha256) "
            "AND (artifact.expected_byte_size "
            "<=> NEW.response_artifact_byte_size) "
            "AND (artifact.retention_class <=> NEW.retention_class) "
            "AND (artifact.retention_deadline <=> NEW.retention_deadline)"
            ")) THEN "
            "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
            "'provider call response artifact binding is invalid'; "
            "END IF; "
            "END"
        )
    )


def downgrade() -> None:
    """Remove the ledger while preserving legacy call references."""

    _assert_provider_artifact_ledger_is_downgrade_safe(op.get_bind())
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_pb_provider_calls_validate_artifacts"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_pb_provider_artifacts_no_delete"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_pb_provider_artifacts_lifecycle"))
    op.drop_constraint(
        "ck_pb_provider_calls_response_ledger",
        "product_brief_provider_calls",
        type_="check",
    )
    op.drop_constraint(
        "fk_pb_provider_calls_response_artifact",
        "product_brief_provider_calls",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_pb_provider_calls_request_artifact",
        "product_brief_provider_calls",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_pb_provider_calls_response_artifact",
        "product_brief_provider_calls",
        type_="unique",
    )
    op.drop_constraint(
        "uq_pb_provider_calls_request_artifact",
        "product_brief_provider_calls",
        type_="unique",
    )
    op.drop_column("product_brief_provider_calls", "response_artifact_id")
    op.drop_column("product_brief_provider_calls", "request_artifact_id")
    op.drop_table("product_brief_provider_artifacts")
