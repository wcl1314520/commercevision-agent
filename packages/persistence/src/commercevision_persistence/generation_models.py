"""SQLAlchemy models for immutable generation batches and candidate slots."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, UTCDateTime
from .workspace_identity import exact_string_sql_type, workspace_id_sql_type


class GenerationBatchModel(Base):
    __tablename__ = "generation_batches"
    __table_args__ = (
        PrimaryKeyConstraint("workspace_id", "id", name="pk_generation_batch"),
        UniqueConstraint(
            "workspace_id",
            "batch_sha256",
            name="uq_generation_batch_hash",
        ),
        UniqueConstraint(
            "workspace_id",
            "workflow_id",
            "workflow_version",
            "creative_plan_version_id",
            "direction_key",
            "tool_intent_key",
            name="uq_generation_batch_logical",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_generation_batch_workflow",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "creative_plan_version_id"],
            ["creative_plan_versions.workspace_id", "creative_plan_versions.id"],
            name="fk_generation_batch_plan_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["plan_approval_id"],
            ["workflow_approvals.id"],
            name="fk_generation_batch_approval",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "route_decision_sha256"],
            ["model_route_decisions.workspace_id", "model_route_decisions.decision_sha256"],
            name="fk_generation_batch_route_decision",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "candidate_count BETWEEN 1 AND 16 AND workflow_version > 0",
            name="ck_generation_batch_counts",
        ),
        CheckConstraint(
            "operation_kind IN ('IMAGE_GENERATION', 'IMAGE_EDITING')",
            name="ck_generation_batch_kind",
        ),
        CheckConstraint(
            "retention_deadline > created_at "
            "AND retention_deadline <= workflow_deadline "
            "AND (source_rights_deadline IS NULL "
            "OR retention_deadline <= source_rights_deadline)",
            name="ck_generation_batch_retention",
        ),
        CheckConstraint(
            "batch_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND tool_intent_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND prompt_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND context_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND route_decision_sha256 REGEXP '^[0-9a-f]{64}$' "
            "AND route_request_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_generation_batch_hashes",
        ),
        Index(
            "ix_generation_batch_workflow",
            "workspace_id",
            "workflow_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_generation_batch_retention",
            "workspace_id",
            "retention_deadline",
            "id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    id: Mapped[str] = mapped_column(exact_string_sql_type(36), nullable=False)
    batch_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(36), nullable=False)
    workflow_version: Mapped[int] = mapped_column(Integer, nullable=False)
    creative_plan_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    plan_approval_id: Mapped[str] = mapped_column(String(36), nullable=False)
    direction_key: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    tool_intent_key: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    tool_intent_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    prompt_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    context_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    route_decision_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    route_request_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    operation_kind: Mapped[str] = mapped_column(exact_string_sql_type(40), nullable=False)
    authorized_asset_version_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    route_policy_version: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    tool_policy_version: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    rights_policy_version: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    safety_policy_version: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    workflow_deadline: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    source_rights_deadline: Mapped[datetime | None] = mapped_column(UTCDateTime())
    edit_source_asset_version_id: Mapped[str | None] = mapped_column(String(36))
    edit_mask_asset_version_id: Mapped[str | None] = mapped_column(String(36))
    approved_repair_scope_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    retention_deadline: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_by: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class CandidateSlotModel(Base):
    __tablename__ = "candidate_slots"
    __table_args__ = (
        PrimaryKeyConstraint("workspace_id", "id", name="pk_candidate_slot"),
        UniqueConstraint(
            "workspace_id",
            "generation_batch_id",
            "candidate_index",
            name="uq_candidate_slot_index",
        ),
        UniqueConstraint(
            "workspace_id",
            "durable_operation_id",
            name="uq_candidate_slot_operation",
        ),
        UniqueConstraint(
            "workspace_id",
            "logical_identity_sha256",
            name="uq_candidate_slot_logical_identity",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "generation_batch_id"],
            ["generation_batches.workspace_id", "generation_batches.id"],
            name="fk_candidate_slot_batch",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "durable_operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_candidate_slot_operation",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "candidate_index BETWEEN 0 AND 15",
            name="ck_candidate_slot_index",
        ),
        CheckConstraint(
            "operation_kind IN ('IMAGE_GENERATION', 'IMAGE_EDITING')",
            name="ck_candidate_slot_kind",
        ),
        CheckConstraint(
            "logical_identity_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_candidate_slot_hash",
        ),
        Index(
            "ix_candidate_slot_batch",
            "workspace_id",
            "generation_batch_id",
            "candidate_index",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    id: Mapped[str] = mapped_column(exact_string_sql_type(36), nullable=False)
    generation_batch_id: Mapped[str] = mapped_column(exact_string_sql_type(36), nullable=False)
    candidate_index: Mapped[int] = mapped_column(Integer, nullable=False)
    durable_operation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation_kind: Mapped[str] = mapped_column(exact_string_sql_type(40), nullable=False)
    logical_identity_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    operation_idempotency_key: Mapped[str] = mapped_column(
        exact_string_sql_type(128), nullable=False
    )
