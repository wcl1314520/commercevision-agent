"""SQLAlchemy models for mutable Brand Profile heads and immutable publications."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
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


class BrandProfileModel(Base):
    __tablename__ = "brand_profiles"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_brand_profiles_workspace_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "brand",
            "profile_key",
            name="uq_brand_profiles_workspace_identity",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "id", "current_version_id", "current_version_number"],
            [
                "brand_profile_versions.workspace_id",
                "brand_profile_versions.profile_id",
                "brand_profile_versions.id",
                "brand_profile_versions.version_number",
            ],
            name="fk_brand_profiles_current_version",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        CheckConstraint(
            "state IN ('DRAFT', 'ACTIVE', 'NEEDS_REPUBLISH', 'ARCHIVED')",
            name="ck_brand_profiles_state",
        ),
        CheckConstraint(
            "draft_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_brand_profiles_draft_sha256",
        ),
        CheckConstraint(
            "version > 0 AND current_version_number >= 0",
            name="ck_brand_profiles_versions_positive",
        ),
        CheckConstraint(
            "(current_version_id IS NULL AND current_version_number = 0) "
            "OR (current_version_id IS NOT NULL AND current_version_number > 0)",
            name="ck_brand_profiles_head_consistent",
        ),
        CheckConstraint(
            "(state = 'DRAFT' AND current_version_id IS NULL) "
            "OR (state IN ('ACTIVE', 'NEEDS_REPUBLISH') "
            "AND current_version_id IS NOT NULL) "
            "OR state = 'ARCHIVED'",
            name="ck_brand_profiles_draft_head",
        ),
        CheckConstraint(
            "(state = 'NEEDS_REPUBLISH' AND stale_at IS NOT NULL) "
            "OR (state <> 'NEEDS_REPUBLISH' AND stale_at IS NULL)",
            name="ck_brand_profiles_stale_state",
        ),
        Index(
            "ix_brand_profiles_workspace_state",
            "workspace_id",
            "state",
            "updated_at",
            "id",
        ),
        Index(
            "ix_brand_profiles_workspace_created",
            "workspace_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_brand_profiles_workspace_brand_created",
            "workspace_id",
            "brand",
            "created_at",
            "id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    brand: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    profile_key: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    draft_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    draft_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    current_version_id: Mapped[str | None] = mapped_column(String(36))
    current_version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    stale_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class BrandProfileVersionModel(Base):
    __tablename__ = "brand_profile_versions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_brand_profile_versions_workspace_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "profile_id",
            "id",
            "version_number",
            name="uq_brand_profile_versions_head_identity",
        ),
        UniqueConstraint(
            "profile_id",
            "version_number",
            name="uq_brand_profile_versions_number",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "profile_id"],
            ["brand_profiles.workspace_id", "brand_profiles.id"],
            name="fk_brand_profile_versions_profile",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "version_number > 0",
            name="ck_brand_profile_versions_number",
        ),
        CheckConstraint(
            "content_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_brand_profile_versions_content_sha256",
        ),
        Index(
            "ix_brand_profile_versions_history",
            "workspace_id",
            "profile_id",
            "version_number",
            "id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    profile_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    purpose: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    provider: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    requires_derivative: Mapped[bool] = mapped_column(Boolean, nullable=False)
    published_by: Mapped[str] = mapped_column(String(128), nullable=False)
    published_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class BrandProfileMemberModel(Base):
    __tablename__ = "brand_profile_members"
    __table_args__ = (
        PrimaryKeyConstraint(
            "profile_version_id",
            "ordinal",
            name="pk_brand_profile_members",
        ),
        UniqueConstraint(
            "profile_version_id",
            "asset_version_id",
            name="uq_brand_profile_members_asset_version",
        ),
        ForeignKeyConstraint(
            [
                "workspace_id",
                "profile_id",
                "profile_version_id",
                "profile_version_number",
            ],
            [
                "brand_profile_versions.workspace_id",
                "brand_profile_versions.profile_id",
                "brand_profile_versions.id",
                "brand_profile_versions.version_number",
            ],
            name="fk_brand_profile_members_profile_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "asset_version_id", "asset_id"],
            ["asset_versions.workspace_id", "asset_versions.id", "asset_versions.asset_id"],
            name="fk_brand_profile_members_asset_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "rights_record_id", "asset_id", "rights_record_version"],
            [
                "rights_records.workspace_id",
                "rights_records.id",
                "rights_records.asset_id",
                "rights_records.version_number",
            ],
            name="fk_brand_profile_members_rights_record",
            ondelete="RESTRICT",
        ),
        CheckConstraint("ordinal >= 0", name="ck_brand_profile_members_ordinal"),
        CheckConstraint(
            "profile_version_number > 0 AND rights_record_version > 0",
            name="ck_brand_profile_members_versions",
        ),
        CheckConstraint(
            "role IN ('LOGO', 'REQUIRED_MARK', 'VISUAL_REFERENCE', "
            "'PROMPT_TEMPLATE', 'MODEL_CONFIGURATION', 'LORA')",
            name="ck_brand_profile_members_role",
        ),
        Index(
            "ix_brand_profile_members_current_invalidation",
            "workspace_id",
            "asset_id",
            "profile_id",
            "profile_version_id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    profile_id: Mapped[str] = mapped_column(String(36), nullable=False)
    profile_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    profile_version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    asset_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    rights_record_id: Mapped[str] = mapped_column(String(36), nullable=False)
    rights_record_version: Mapped[int] = mapped_column(Integer, nullable=False)
