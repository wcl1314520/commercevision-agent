"""SQLAlchemy model for immutable model route decisions."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, UTCDateTime
from .workspace_identity import exact_string_sql_type, workspace_id_sql_type


class ModelRouteDecisionModel(Base):
    __tablename__ = "model_route_decisions"
    __table_args__ = (
        PrimaryKeyConstraint(
            "workspace_id",
            "decision_sha256",
            name="pk_model_route_decision",
        ),
        UniqueConstraint(
            "workspace_id",
            "idempotency_scope_sha256",
            "idempotency_key_sha256",
            name="uq_model_route_decision_idempotency",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            name="fk_model_route_decision_workflow",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "creative_plan_version_id"],
            ["creative_plan_versions.workspace_id", "creative_plan_versions.id"],
            name="fk_model_route_decision_plan_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["plan_approval_id"],
            ["workflow_approvals.id"],
            name="fk_model_route_decision_approval",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "policy_key", "policy_version_id"],
            [
                "model_route_policy_versions.workspace_id",
                "model_route_policy_versions.policy_key",
                "model_route_policy_versions.id",
            ],
            name="fk_model_route_decision_policy",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["endpoint_capability_version_id"],
            ["provider_endpoint_capability_versions.id"],
            name="fk_model_route_decision_endpoint",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "decision_sha256 REGEXP '^[0-9a-f]{64}$' AND "
            "idempotency_scope_sha256 REGEXP '^[0-9a-f]{64}$' AND "
            "idempotency_key_sha256 REGEXP '^[0-9a-f]{64}$' AND "
            "route_request_sha256 REGEXP '^[0-9a-f]{64}$'",
            name="ck_model_route_decision_hashes",
        ),
        Index(
            "ix_model_route_decision_workflow",
            "workspace_id",
            "workflow_id",
            "decided_at",
            "decision_sha256",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    decision_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    idempotency_scope_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    idempotency_key_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(36), nullable=False)
    creative_plan_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    plan_approval_id: Mapped[str] = mapped_column(String(36), nullable=False)
    route_request_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    route_request_json: Mapped[dict[str, object] | None] = mapped_column(JSON)
    authorized_asset_version_ids_json: Mapped[list[str] | None] = mapped_column(JSON)
    route_candidate_count: Mapped[int | None] = mapped_column(Integer)
    policy_key: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    policy_version_id: Mapped[str] = mapped_column(exact_string_sql_type(36), nullable=False)
    route_policy_version: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    endpoint_capability_version_id: Mapped[str] = mapped_column(
        exact_string_sql_type(36), nullable=False
    )
    fallback_endpoint_capability_version_ids_json: Mapped[list[str]] = mapped_column(
        JSON, nullable=False
    )
    candidate_scores_json: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False)
    rejection_counts_json: Mapped[list[dict[str, str | int]]] = mapped_column(JSON, nullable=False)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    currency: Mapped[str] = mapped_column(exact_string_sql_type(3), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_by: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
