"""SQLAlchemy models for Prompt Registry revisions and production pointers."""

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
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, UTCDateTime
from .workspace_identity import exact_string_sql_type, workspace_id_sql_type


class PromptRevisionModel(Base):
    __tablename__ = "prompt_revisions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "prompt_id",
            "semantic_revision",
            name="uq_prompt_revisions_workspace_semantic",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_prompt_revisions_workspace_id"),
        CheckConstraint("version > 0", name="ck_prompt_revisions_version"),
        CheckConstraint(
            "status IN ('DRAFT','REVIEW','STAGING','PRODUCTION','DEPRECATED')",
            name="ck_prompt_revisions_status",
        ),
        Index(
            "ix_prompt_revisions_workspace_status",
            "workspace_id",
            "prompt_id",
            "status",
            "semantic_revision",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    prompt_id: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    semantic_revision: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    node: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    category_applicability_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    model_family_applicability_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    input_schema_version: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    output_schema_version: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    policy_version: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    content: Mapped[str] = mapped_column(MEDIUMTEXT(), nullable=False)
    variables_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    content_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    change_summary: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    submitted_by: Mapped[str | None] = mapped_column(String(128))
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reviewed_by: Mapped[str | None] = mapped_column(String(128))
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    published_by: Mapped[str | None] = mapped_column(String(128))
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    deprecated_by: Mapped[str | None] = mapped_column(String(128))
    deprecated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class PromptProductionPointerModel(Base):
    __tablename__ = "prompt_production_pointers"
    __table_args__ = (
        PrimaryKeyConstraint("workspace_id", "prompt_id", name="pk_prompt_production_pointers"),
        ForeignKeyConstraint(
            ["workspace_id", "revision_id"],
            ["prompt_revisions.workspace_id", "prompt_revisions.id"],
            name="fk_prompt_pointer_exact_revision",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version > 0", name="ck_prompt_pointer_version"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    prompt_id: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    node: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    semantic_revision: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    content_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
