"""SQLAlchemy model for retained immutable Planning Context snapshots."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, UTCDateTime
from .workspace_identity import exact_string_sql_type, workspace_id_sql_type


class PlanningContextSnapshotModel(Base):
    __tablename__ = "planning_context_snapshots"
    __table_args__ = (
        PrimaryKeyConstraint(
            "workspace_id",
            "workflow_id",
            "context_sha256",
            name="pk_planning_context_snapshots",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_planning_context_snapshot_workflow",
            ondelete="RESTRICT",
        ),
        CheckConstraint("source_count BETWEEN 1 AND 104", name="ck_planning_context_source_count"),
        CheckConstraint(
            "context_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_planning_context_context_sha256",
        ),
        CheckConstraint(
            "storage_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_planning_context_storage_sha256",
        ),
        Index(
            "ix_planning_context_snapshots_retention",
            "workspace_id",
            "retain_until",
            "workflow_id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(36), nullable=False)
    context_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    storage_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    policy_version: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_count: Mapped[int] = mapped_column(Integer, nullable=False)
    retain_until: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
