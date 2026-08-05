"""SQLAlchemy models for Creative Plan identities and immutable versions."""

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
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, UTCDateTime
from .workspace_identity import exact_string_sql_type, workspace_id_sql_type


class CreativePlanVersionModel(Base):
    __tablename__ = "creative_plan_versions"
    __table_args__ = (
        PrimaryKeyConstraint(
            "workspace_id",
            "id",
            name="pk_creative_plan_versions",
        ),
        UniqueConstraint(
            "workspace_id",
            "workflow_id",
            "creative_plan_id",
            "version_number",
            name="uq_creative_plan_versions_logical",
        ),
        UniqueConstraint(
            "workspace_id",
            "workflow_id",
            "creative_plan_id",
            "id",
            name="uq_creative_plan_versions_identity",
        ),
        UniqueConstraint(
            "workspace_id",
            "workflow_id",
            "creative_plan_id",
            "id",
            "version_number",
            name="uq_creative_plan_versions_head_target",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_creative_plan_versions_workflow",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "workspace_id",
                "workflow_id",
                "creative_plan_id",
                "supersedes_version_id",
            ],
            [
                "creative_plan_versions.workspace_id",
                "creative_plan_versions.workflow_id",
                "creative_plan_versions.creative_plan_id",
                "creative_plan_versions.id",
            ],
            name="fk_creative_plan_versions_supersedes",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "version_number > 0",
            name="ck_creative_plan_versions_number",
        ),
        CheckConstraint(
            "(version_number = 1 AND supersedes_version_id IS NULL) OR "
            "(version_number > 1 AND supersedes_version_id IS NOT NULL)",
            name="ck_creative_plan_versions_lineage",
        ),
        CheckConstraint(
            "source IN ('AGENT', 'USER')",
            name="ck_creative_plan_versions_source",
        ),
        CheckConstraint(
            "(source = 'AGENT' AND revision_reason IS NULL) OR "
            "(source = 'USER' AND revision_reason IS NOT NULL)",
            name="ck_creative_plan_versions_revision_reason",
        ),
        CheckConstraint(
            "payload_sha256 REGEXP '^[0-9a-f]{64}$' AND "
            "product_brief_sha256 REGEXP '^[0-9a-f]{64}$' AND "
            "context_sha256 REGEXP '^[0-9a-f]{64}$' AND "
            "prompt_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_creative_plan_versions_hashes",
        ),
        CheckConstraint(
            "(brand_profile_id IS NULL AND brand_profile_version IS NULL "
            "AND brand_profile_sha256 IS NULL) OR "
            "(brand_profile_id IS NOT NULL AND brand_profile_version > 0 "
            "AND brand_profile_sha256 REGEXP '^[0-9a-f]{64}$')",
            name="ck_creative_plan_versions_brand_provenance",
        ),
        CheckConstraint(
            "product_brief_version > 0",
            name="ck_creative_plan_versions_product_brief_version",
        ),
        Index(
            "ix_creative_plan_versions_history",
            "workspace_id",
            "workflow_id",
            "creative_plan_id",
            "version_number",
        ),
        Index(
            "ix_creative_plan_versions_retention",
            "workspace_id",
            "retain_until",
            "creative_plan_id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), nullable=False)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(36), nullable=False)
    creative_plan_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_version_id: Mapped[str | None] = mapped_column(String(36))
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    product_brief_id: Mapped[str] = mapped_column(String(36), nullable=False)
    product_brief_version: Mapped[int] = mapped_column(Integer, nullable=False)
    product_brief_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    brand_profile_id: Mapped[str | None] = mapped_column(String(36))
    brand_profile_version: Mapped[int | None] = mapped_column(Integer)
    brand_profile_sha256: Mapped[str | None] = mapped_column(exact_string_sql_type(64))
    retrieval_run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    retrieval_citation_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    context_policy_version: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    context_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    prompt_id: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    prompt_revision: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    prompt_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    revision_reason: Mapped[str | None] = mapped_column(String(512))
    retain_until: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class CreativePlanModel(Base):
    __tablename__ = "creative_plans"
    __table_args__ = (
        PrimaryKeyConstraint(
            "workspace_id",
            "id",
            name="pk_creative_plans",
        ),
        UniqueConstraint(
            "workspace_id",
            "workflow_id",
            "id",
            name="uq_creative_plans_workspace_workflow_id",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_creative_plans_workflow",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "workspace_id",
                "workflow_id",
                "id",
                "current_version_id",
                "current_version_number",
            ],
            [
                "creative_plan_versions.workspace_id",
                "creative_plan_versions.workflow_id",
                "creative_plan_versions.creative_plan_id",
                "creative_plan_versions.id",
                "creative_plan_versions.version_number",
            ],
            name="fk_creative_plans_current_version",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        CheckConstraint(
            "current_version_number > 0 AND version = current_version_number",
            name="ck_creative_plans_head_version",
        ),
        Index(
            "ix_creative_plans_workspace_workflow",
            "workspace_id",
            "workflow_id",
            "updated_at",
            "id",
        ),
        Index(
            "ix_creative_plans_retention",
            "workspace_id",
            "retain_until",
            "id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), nullable=False)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(36), nullable=False)
    current_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    current_version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    retain_until: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
