"""Retained Retrieval Run and short-lived preview grant models."""

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
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, UTCDateTime
from .workspace_identity import exact_string_sql_type, workspace_id_sql_type


class RetrievalRunModel(Base):
    __tablename__ = "retrieval_runs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_retrieval_runs_workspace_id"),
        CheckConstraint(
            "query_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_retrieval_runs_query_sha256",
        ),
        CheckConstraint(
            "eligible_asset_version_count >= fused_candidate_count "
            "AND fused_candidate_count >= final_authorized_candidate_count "
            "AND final_authorized_candidate_count >= 0",
            name="ck_retrieval_runs_candidate_counts",
        ),
        CheckConstraint("latency_ms >= 0", name="ck_retrieval_runs_latency_ms"),
        Index("ix_retrieval_runs_workspace_created", "workspace_id", "created_at", "id"),
        Index("ix_retrieval_runs_expiry", "expires_at", "id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    requester_id: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    query_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    query_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    retrieval_policy_version: Mapped[str] = mapped_column(
        exact_string_sql_type(128), nullable=False
    )
    complete_hybrid: Mapped[bool] = mapped_column(nullable=False)
    degradations_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    eligible_asset_version_count: Mapped[int] = mapped_column(Integer, nullable=False)
    fused_candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    final_authorized_candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class RetrievalResultModel(Base):
    __tablename__ = "retrieval_results"
    __table_args__ = (
        PrimaryKeyConstraint("retrieval_run_id", "result_rank", name="pk_retrieval_results"),
        ForeignKeyConstraint(
            ["workspace_id", "retrieval_run_id"],
            ["retrieval_runs.workspace_id", "retrieval_runs.id"],
            name="fk_retrieval_results_run",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "preview_token_sha256",
            name="uq_retrieval_results_preview_token_sha256",
        ),
        CheckConstraint("result_rank > 0", name="ck_retrieval_results_rank"),
        CheckConstraint(
            "preview_token_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_retrieval_results_preview_token_sha256",
        ),
        Index("ix_retrieval_results_asset", "workspace_id", "asset_version_id"),
        Index("ix_retrieval_results_preview_expiry", "preview_expires_at"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    retrieval_run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    rank: Mapped[int] = mapped_column("result_rank", Integer, nullable=False)
    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    asset_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    rights_record_id: Mapped[str] = mapped_column(String(36), nullable=False)
    rights_record_version: Mapped[int] = mapped_column(Integer, nullable=False)
    brand_profile_version: Mapped[int | None] = mapped_column(Integer)
    channels_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    score_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    preview_token_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    preview_expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
