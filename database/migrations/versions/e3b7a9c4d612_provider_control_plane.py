"""Add Provider control-plane authority.

Revision ID: e3b7a9c4d612
Revises: d9a6e4b2c517
Create Date: 2026-08-06 13:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "e3b7a9c4d612"
down_revision = "d9a6e4b2c517"
branch_labels = None
depends_on = None

_WORKSPACE = sa.String(128, collation="utf8mb4_0900_bin")
_EXACT_3 = sa.String(3, collation="utf8mb4_0900_bin")
_EXACT_36 = sa.String(36, collation="utf8mb4_0900_bin")
_EXACT_64 = sa.String(64, collation="utf8mb4_0900_bin")
_EXACT_128 = sa.String(128, collation="utf8mb4_0900_bin")
_DATETIME = mysql.DATETIME(fsp=6)
_MONEY = sa.Numeric(20, 6)
_SCORE = sa.Numeric(7, 6)

_IMMUTABLE_TRIGGERS = (
    "trg_provider_capability_versions_immutable",
    "trg_model_route_policy_versions_immutable",
    "trg_provider_endpoint_observations_immutable",
)


def upgrade() -> None:
    op.create_table(
        "provider_identities",
        sa.Column("id", _EXACT_128, nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", _EXACT_128, nullable=False),
        sa.Column("created_at", _DATETIME, nullable=False),
        sa.Column("updated_by", _EXACT_128, nullable=False),
        sa.Column("updated_at", _DATETIME, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_provider_identity"),
        sa.CheckConstraint("version > 0", name="ck_provider_identity_version"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )

    op.create_table(
        "provider_endpoint_capability_versions",
        sa.Column("id", _EXACT_36, nullable=False),
        sa.Column("provider_id", _EXACT_128, nullable=False),
        sa.Column("endpoint_id", _EXACT_128, nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("capability_sha256", _EXACT_64, nullable=False),
        sa.Column("configuration_sha256", _EXACT_64, nullable=False),
        sa.Column("secret_reference", _EXACT_128, nullable=False),
        sa.Column("capability_json", sa.JSON(), nullable=False),
        sa.Column("unit_price", _MONEY, nullable=False),
        sa.Column("currency", _EXACT_3, nullable=False),
        sa.Column("created_by", _EXACT_128, nullable=False),
        sa.Column("created_at", _DATETIME, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_provider_capability_version"),
        sa.UniqueConstraint(
            "provider_id",
            "endpoint_id",
            "version_number",
            name="uq_provider_capability_version_number",
        ),
        sa.UniqueConstraint(
            "provider_id",
            "endpoint_id",
            "id",
            name="uq_provider_capability_endpoint_id",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["provider_identities.id"],
            name="fk_provider_capability_provider",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "version_number > 0 AND unit_price >= 0",
            name="ck_provider_capability_positive_values",
        ),
        sa.CheckConstraint(
            "capability_sha256 REGEXP '^[0-9a-f]{64}$' AND "
            "configuration_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_provider_capability_hashes",
        ),
        sa.CheckConstraint(
            "secret_reference REGEXP '^secret-ref:[A-Za-z0-9][A-Za-z0-9._:-]{0,116}$'",
            name="ck_provider_capability_secret_reference",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_provider_capability_created",
        "provider_endpoint_capability_versions",
        ["provider_id", "endpoint_id", "created_at"],
    )

    op.create_table(
        "provider_endpoint_capability_heads",
        sa.Column("provider_id", _EXACT_128, nullable=False),
        sa.Column("endpoint_id", _EXACT_128, nullable=False),
        sa.Column("current_version_id", _EXACT_36, nullable=True),
        sa.Column("current_version_number", sa.Integer(), nullable=False),
        sa.Column("latest_version_number", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by", _EXACT_128, nullable=True),
        sa.Column("updated_at", _DATETIME, nullable=False),
        sa.PrimaryKeyConstraint(
            "provider_id",
            "endpoint_id",
            name="pk_provider_endpoint_capability_head",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["provider_identities.id"],
            name="fk_provider_capability_head_provider",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id", "endpoint_id", "current_version_id"],
            [
                "provider_endpoint_capability_versions.provider_id",
                "provider_endpoint_capability_versions.endpoint_id",
                "provider_endpoint_capability_versions.id",
            ],
            name="fk_provider_capability_head_current",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "current_version_number >= 0 AND latest_version_number >= current_version_number "
            "AND version >= 0",
            name="ck_provider_capability_head_counters",
        ),
        sa.CheckConstraint(
            "(current_version_id IS NULL AND current_version_number = 0) OR "
            "(current_version_id IS NOT NULL AND current_version_number > 0)",
            name="ck_provider_capability_head_current",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )

    op.create_table(
        "provider_discovery_candidates",
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("id", _EXACT_36, nullable=False),
        sa.Column("provider_id", _EXACT_128, nullable=False),
        sa.Column("endpoint_id", _EXACT_128, nullable=False),
        sa.Column("discovered_model_id", _EXACT_128, nullable=False),
        sa.Column("discovery_sha256", _EXACT_64, nullable=False),
        sa.Column("discovery_json", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("discovered_by", _EXACT_128, nullable=False),
        sa.Column("discovered_at", _DATETIME, nullable=False),
        sa.Column("reviewed_by", _EXACT_128, nullable=True),
        sa.Column("reviewed_at", _DATETIME, nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "id", name="pk_provider_discovery_candidate"),
        sa.UniqueConstraint(
            "workspace_id",
            "provider_id",
            "endpoint_id",
            "discovery_sha256",
            name="uq_provider_discovery_candidate_evidence",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["provider_identities.id"],
            name="fk_provider_discovery_provider",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "state IN ('PENDING_REVIEW', 'APPROVED', 'REJECTED')",
            name="ck_provider_discovery_state",
        ),
        sa.CheckConstraint(
            "discovery_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_provider_discovery_hash",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_provider_discovery_review",
        "provider_discovery_candidates",
        ["workspace_id", "state", "discovered_at"],
    )

    op.create_table(
        "model_route_policy_versions",
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("id", _EXACT_36, nullable=False),
        sa.Column("policy_key", _EXACT_128, nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("policy_version", _EXACT_128, nullable=False),
        sa.Column("policy_sha256", _EXACT_64, nullable=False),
        sa.Column("policy_json", sa.JSON(), nullable=False),
        sa.Column("quality_weight", _SCORE, nullable=False),
        sa.Column("availability_weight", _SCORE, nullable=False),
        sa.Column("latency_weight", _SCORE, nullable=False),
        sa.Column("quota_weight", _SCORE, nullable=False),
        sa.Column("price_weight", _SCORE, nullable=False),
        sa.Column("created_by", _EXACT_128, nullable=False),
        sa.Column("created_at", _DATETIME, nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "id", name="pk_model_route_policy_version"),
        sa.UniqueConstraint(
            "workspace_id",
            "policy_key",
            "version_number",
            name="uq_model_route_policy_version_number",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "policy_key",
            "id",
            name="uq_model_route_policy_key_id",
        ),
        sa.CheckConstraint("version_number > 0", name="ck_model_route_policy_version_number"),
        sa.CheckConstraint(
            "policy_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_model_route_policy_hash",
        ),
        sa.CheckConstraint(
            "quality_weight + availability_weight + latency_weight + quota_weight + "
            "price_weight = 1.000000",
            name="ck_model_route_policy_weight_sum",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_model_route_policy_created",
        "model_route_policy_versions",
        ["workspace_id", "policy_key", "created_at"],
    )

    op.create_table(
        "model_route_policy_heads",
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("policy_key", _EXACT_128, nullable=False),
        sa.Column("current_version_id", _EXACT_36, nullable=True),
        sa.Column("current_version_number", sa.Integer(), nullable=False),
        sa.Column("latest_version_number", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by", _EXACT_128, nullable=True),
        sa.Column("updated_at", _DATETIME, nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "policy_key", name="pk_model_route_policy_head"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "policy_key", "current_version_id"],
            [
                "model_route_policy_versions.workspace_id",
                "model_route_policy_versions.policy_key",
                "model_route_policy_versions.id",
            ],
            name="fk_model_route_policy_head_current",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "current_version_number >= 0 AND latest_version_number >= current_version_number "
            "AND version >= 0",
            name="ck_model_route_policy_head_counters",
        ),
        sa.CheckConstraint(
            "(current_version_id IS NULL AND current_version_number = 0) OR "
            "(current_version_id IS NOT NULL AND current_version_number > 0)",
            name="ck_model_route_policy_head_current",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )

    op.create_table(
        "provider_endpoint_observations",
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("id", _EXACT_36, nullable=False),
        sa.Column("endpoint_capability_version_id", _EXACT_36, nullable=False),
        sa.Column("quality_score", _SCORE, nullable=False),
        sa.Column("availability_score", _SCORE, nullable=False),
        sa.Column("latency_score", _SCORE, nullable=False),
        sa.Column("quota_score", _SCORE, nullable=False),
        sa.Column("circuit_state", sa.String(24), nullable=False),
        sa.Column("remaining_quota_units", sa.BigInteger(), nullable=False),
        sa.Column("observation_source", sa.String(32), nullable=False),
        sa.Column("idempotency_key_sha256", _EXACT_64, nullable=False),
        sa.Column("observed_at", _DATETIME, nullable=False),
        sa.Column("created_by", _EXACT_128, nullable=False),
        sa.Column("created_at", _DATETIME, nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "id", name="pk_provider_endpoint_observation"),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key_sha256",
            name="uq_provider_endpoint_observation_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["endpoint_capability_version_id"],
            ["provider_endpoint_capability_versions.id"],
            name="fk_provider_endpoint_observation_capability",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "quality_score BETWEEN 0 AND 1 AND availability_score BETWEEN 0 AND 1 AND "
            "latency_score BETWEEN 0 AND 1 AND quota_score BETWEEN 0 AND 1",
            name="ck_provider_endpoint_observation_scores",
        ),
        sa.CheckConstraint(
            "circuit_state IN ('CLOSED', 'OPEN', 'HALF_OPEN') AND remaining_quota_units >= 0",
            name="ck_provider_endpoint_observation_authority",
        ),
        sa.CheckConstraint(
            "idempotency_key_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_provider_endpoint_observation_idempotency_hash",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_provider_endpoint_observation_latest",
        "provider_endpoint_observations",
        ["workspace_id", "endpoint_capability_version_id", "observed_at", "id"],
    )

    _create_immutable_trigger(
        "trg_provider_capability_versions_immutable",
        "provider_endpoint_capability_versions",
        "Provider capability version is immutable",
    )
    _create_immutable_trigger(
        "trg_model_route_policy_versions_immutable",
        "model_route_policy_versions",
        "Model route policy version is immutable",
    )
    _create_immutable_trigger(
        "trg_provider_endpoint_observations_immutable",
        "provider_endpoint_observations",
        "Provider endpoint observation is immutable",
    )


def _create_immutable_trigger(name: str, table: str, message: str) -> None:
    op.execute(
        f"""
        CREATE TRIGGER {name}
        BEFORE UPDATE ON {table} FOR EACH ROW
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{message}'
        """
    )


def downgrade() -> None:
    for trigger in _IMMUTABLE_TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    op.drop_index(
        "ix_provider_endpoint_observation_latest",
        table_name="provider_endpoint_observations",
    )
    op.drop_table("provider_endpoint_observations")
    op.drop_table("model_route_policy_heads")
    op.drop_index("ix_model_route_policy_created", table_name="model_route_policy_versions")
    op.drop_table("model_route_policy_versions")
    op.drop_index("ix_provider_discovery_review", table_name="provider_discovery_candidates")
    op.drop_table("provider_discovery_candidates")
    op.drop_table("provider_endpoint_capability_heads")
    op.drop_index(
        "ix_provider_capability_created",
        table_name="provider_endpoint_capability_versions",
    )
    op.drop_table("provider_endpoint_capability_versions")
    op.drop_table("provider_identities")
