"""Immutable deletion tombstones and append-only convergence evidence."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, UTCDateTime
from .workspace_identity import exact_string_sql_type, workspace_id_sql_type


class AssetDeletionTombstoneModel(Base):
    __tablename__ = "asset_deletion_tombstones"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_asset_deletion_tombstones_workspace_id"),
        UniqueConstraint("asset_id", "deletion_generation", name="uq_asset_deletion_generation"),
        UniqueConstraint("operation_id", name="uq_asset_deletion_operation"),
        ForeignKeyConstraint(
            ["workspace_id", "asset_id"],
            ["assets.workspace_id", "assets.id"],
            name="fk_asset_deletion_tombstone_asset",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "target_asset_version_id", "asset_id"],
            ["asset_versions.workspace_id", "asset_versions.id", "asset_versions.asset_id"],
            name="fk_asset_deletion_tombstone_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "operation_id"],
            ["durable_operations.workspace_id", "durable_operations.id"],
            name="fk_asset_deletion_tombstone_operation",
            ondelete="RESTRICT",
        ),
        CheckConstraint("deletion_generation > 0", name="ck_asset_deletion_generation"),
        CheckConstraint(
            "reason IN ('RETENTION_EXPIRED', 'RIGHTS_EXPIRED', 'ADMINISTRATOR_DELETE')",
            name="ck_asset_deletion_reason",
        ),
        Index("ix_asset_deletion_tombstones_requested", "requested_at", "workspace_id", "id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    target_asset_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    deletion_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    operation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_by: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class AssetDeletionProgressModel(Base):
    __tablename__ = "asset_deletion_progress"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "tombstone_id"],
            ["asset_deletion_tombstones.workspace_id", "asset_deletion_tombstones.id"],
            name="fk_asset_deletion_progress_tombstone",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "component IN ('OBJECTS', 'VECTORS', 'SEARCH_DOCUMENTS', 'PROVIDER_ARTIFACTS', "
            "'TEMPORARY_REFERENCES', 'CACHES', 'PRODUCT_BRIEFS', 'RETRIEVAL_RUNS', "
            "'CHECKPOINTS', 'QUARANTINE', 'OPERATIONS')",
            name="ck_asset_deletion_progress_component",
        ),
        CheckConstraint(
            "state IN ('PENDING', 'CONVERGED', 'RETRYABLE_FAILED')",
            name="ck_asset_deletion_progress_state",
        ),
        CheckConstraint(
            "observed_count >= 0 AND converged_count >= 0 AND converged_count <= observed_count",
            name="ck_asset_deletion_progress_counts",
        ),
        Index(
            "ix_asset_deletion_progress_tombstone",
            "workspace_id",
            "tombstone_id",
            "created_at",
            "id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    tombstone_id: Mapped[str] = mapped_column(String(36), nullable=False)
    component: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    cursor_value: Mapped[str | None] = mapped_column(exact_string_sql_type(512))
    observed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    converged_count: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ProviderArtifactDeletionProgressModel(Base):
    __tablename__ = "provider_artifact_deletion_progress"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "provider_artifact_id"],
            [
                "product_brief_provider_artifacts.workspace_id",
                "product_brief_provider_artifacts.id",
            ],
            name="fk_provider_artifact_deletion_progress_artifact",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "tombstone_id"],
            ["asset_deletion_tombstones.workspace_id", "asset_deletion_tombstones.id"],
            name="fk_provider_artifact_deletion_progress_tombstone",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "state IN ('DISCOVERED', 'CONVERGED', 'RETRYABLE_FAILED')",
            name="ck_provider_artifact_deletion_progress_state",
        ),
        Index(
            "ix_provider_artifact_deletion_progress_artifact",
            "workspace_id",
            "provider_artifact_id",
            "created_at",
            "id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    provider_artifact_id: Mapped[str] = mapped_column(String(36), nullable=False)
    tombstone_id: Mapped[str] = mapped_column(String(36), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    provider_version_id: Mapped[str | None] = mapped_column(exact_string_sql_type(256))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
