"""Add durable collection rebuilds and atomic retrieval pointers.

Revision ID: f4c8a1e7b205
Revises: e1b7c4d9a263
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "f4c8a1e7b205"
down_revision: str | Sequence[str] | None = "e1b7c4d9a263"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKSPACE = sa.String(128, collation="utf8mb4_0900_bin")
_EXACT_64 = sa.String(64, collation="utf8mb4_0900_bin")


def upgrade() -> None:
    op.create_index(
        "ix_outbox_rebuild_replay",
        "outbox_events",
        ["event_type", "occurred_at", "id"],
    )
    op.add_column(
        "collection_registry",
        sa.Column("instance_generation", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "collection_registry",
        sa.Column("rebuild_id", sa.String(36), nullable=True),
    )
    op.drop_constraint("uq_collection_registry_logical_key", "collection_registry", type_="unique")
    op.drop_constraint("uq_collection_registry_spec_hash", "collection_registry", type_="unique")
    op.create_unique_constraint(
        "uq_collection_registry_logical_instance",
        "collection_registry",
        ["logical_key", "instance_generation"],
    )
    op.create_unique_constraint(
        "uq_collection_registry_spec_instance",
        "collection_registry",
        ["spec_hash", "instance_generation"],
    )
    op.create_unique_constraint(
        "uq_collection_registry_rebuild", "collection_registry", ["rebuild_id"]
    )
    op.create_check_constraint(
        "ck_collection_registry_instance_identity",
        "collection_registry",
        "(instance_generation = 0 AND rebuild_id IS NULL) OR "
        "(instance_generation > 0 AND rebuild_id IS NOT NULL)",
    )

    op.create_table(
        "retrieval_policy_pointers",
        sa.Column("vector_kind", sa.String(32), nullable=False),
        sa.Column("collection_id", sa.String(36), nullable=False),
        sa.Column("retrieval_policy_version", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("vector_kind", name="pk_retrieval_policy_pointers"),
        sa.UniqueConstraint("collection_id", name="uq_retrieval_policy_pointer_collection"),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["collection_registry.id"],
            name="fk_retrieval_policy_pointer_collection",
            ondelete="RESTRICT",
        ),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.execute(
        sa.text(
            "INSERT INTO retrieval_policy_pointers "
            "(vector_kind, collection_id, retrieval_policy_version, version, updated_at) "
            "SELECT c.vector_kind, c.id, 'retrieval-policy-v1', 1, UTC_TIMESTAMP(6) "
            "FROM collection_registry c "
            "WHERE c.state = 'ACTIVE' AND c.is_read_enabled = 1 "
            "AND c.id = (SELECT MIN(c2.id) FROM collection_registry c2 "
            "WHERE c2.vector_kind = c.vector_kind AND c2.state = 'ACTIVE' "
            "AND c2.is_read_enabled = 1)"
        )
    )

    op.create_table(
        "collection_rebuilds",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("operation_id", sa.String(36), nullable=False),
        sa.Column("source_collection_id", sa.String(36), nullable=False),
        sa.Column("candidate_collection_id", sa.String(36), nullable=False),
        sa.Column("vector_kind", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("source_collection_version", sa.Integer(), nullable=False),
        sa.Column("policy_pointer_version", sa.Integer(), nullable=False),
        sa.Column("snapshot_watermark", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("backfill_cursor", sa.String(36), nullable=True),
        sa.Column("replay_watermark", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("replay_cursor_occurred_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("replay_cursor_event_id", sa.String(36), nullable=True),
        sa.Column("rights_cursor", sa.String(36), nullable=True),
        sa.Column("processed_count", sa.Integer(), nullable=False),
        sa.Column("validation_summary_json", sa.JSON(), nullable=False),
        sa.Column("validation_watermark", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("retire_after", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_collection_rebuilds"),
        sa.UniqueConstraint("operation_id", name="uq_collection_rebuild_operation"),
        sa.UniqueConstraint("candidate_collection_id", name="uq_collection_rebuild_candidate"),
        sa.ForeignKeyConstraint(
            ["source_collection_id"],
            ["collection_registry.id"],
            name="fk_collection_rebuild_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_collection_id"],
            ["collection_registry.id"],
            name="fk_collection_rebuild_candidate",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("generation > 0 AND version > 0", name="ck_collection_rebuild_versions"),
        sa.CheckConstraint(
            "state IN ('REQUESTED', 'PROVISIONING', 'BACKFILLING', 'REPLAYING', "
            "'RIGHTS_RESCAN', 'AWAITING_VALIDATION', 'VALIDATING', 'READY', "
            "'ACTIVATING', 'ACTIVE', 'FAILED', 'RETIRING', 'RETIRED')",
            name="ck_collection_rebuild_state",
        ),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_collection_rebuild_state", "collection_rebuilds", ["state", "updated_at", "id"]
    )
    op.create_index(
        "ix_collection_rebuild_retirement",
        "collection_rebuilds",
        ["state", "retire_after", "id"],
    )

    op.create_table(
        "collection_rebuild_placements",
        sa.Column("rebuild_id", sa.String(36), nullable=False),
        sa.Column("embedding_record_id", sa.String(36), nullable=False),
        sa.Column("workspace_id", _WORKSPACE, nullable=False),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("asset_version_id", sa.String(36), nullable=False),
        sa.Column("milvus_primary_key", _EXACT_64, nullable=False),
        sa.Column("input_hash", _EXACT_64, nullable=False),
        sa.Column("embedding_spec_hash", _EXACT_64, nullable=False),
        sa.Column("write_generation", sa.Integer(), nullable=False),
        sa.Column("placed_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint(
            "rebuild_id", "embedding_record_id", name="pk_collection_rebuild_placements"
        ),
        sa.UniqueConstraint(
            "rebuild_id", "milvus_primary_key", name="uq_collection_rebuild_placement_pk"
        ),
        sa.ForeignKeyConstraint(
            ["rebuild_id"],
            ["collection_rebuilds.id"],
            name="fk_collection_rebuild_placement_rebuild",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["embedding_record_id"],
            ["embedding_records.id"],
            name="fk_collection_rebuild_placement_embedding",
            ondelete="RESTRICT",
        ),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_collection_rebuild_placement_asset",
        "collection_rebuild_placements",
        ["rebuild_id", "asset_id"],
    )

    op.create_table(
        "collection_rebuild_progress",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("rebuild_id", sa.String(36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("processed_count", sa.Integer(), nullable=False),
        sa.Column("message_code", sa.String(64), nullable=False),
        sa.Column("observed_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_collection_rebuild_progress"),
        sa.UniqueConstraint("rebuild_id", "sequence", name="uq_collection_rebuild_progress_seq"),
        sa.ForeignKeyConstraint(
            ["rebuild_id"],
            ["collection_rebuilds.id"],
            name="fk_collection_rebuild_progress_rebuild",
            ondelete="RESTRICT",
        ),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_collection_rebuild_progress_latest",
        "collection_rebuild_progress",
        ["rebuild_id", "sequence"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    rebuild_count = int(
        connection.execute(sa.text("SELECT COUNT(*) FROM collection_rebuilds")).scalar_one()
    )
    if rebuild_count:
        raise RuntimeError("cannot downgrade while collection rebuild facts exist")

    op.drop_table("collection_rebuild_progress")
    op.drop_table("collection_rebuild_placements")
    op.drop_table("collection_rebuilds")
    op.drop_table("retrieval_policy_pointers")
    op.drop_index("ix_outbox_rebuild_replay", table_name="outbox_events")
    op.drop_constraint(
        "ck_collection_registry_instance_identity", "collection_registry", type_="check"
    )
    op.drop_constraint("uq_collection_registry_rebuild", "collection_registry", type_="unique")
    op.drop_constraint(
        "uq_collection_registry_spec_instance", "collection_registry", type_="unique"
    )
    op.drop_constraint(
        "uq_collection_registry_logical_instance", "collection_registry", type_="unique"
    )
    op.create_unique_constraint(
        "uq_collection_registry_spec_hash", "collection_registry", ["spec_hash"]
    )
    op.create_unique_constraint(
        "uq_collection_registry_logical_key", "collection_registry", ["logical_key"]
    )
    op.drop_column("collection_registry", "rebuild_id")
    op.drop_column("collection_registry", "instance_generation")
