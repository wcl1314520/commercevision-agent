"""SQLAlchemy authority for Provider capabilities, routing policy, and observations."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
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

_SCORE_TYPE = Numeric(7, 6)
_MONEY_TYPE = Numeric(20, 6)


class ProviderIdentityModel(Base):
    __tablename__ = "provider_identities"
    __table_args__ = ({"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},)

    id: Mapped[str] = mapped_column(exact_string_sql_type(128), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_by: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ProviderEndpointCapabilityVersionModel(Base):
    __tablename__ = "provider_endpoint_capability_versions"
    __table_args__ = (
        UniqueConstraint(
            "provider_id",
            "endpoint_id",
            "version_number",
            name="uq_provider_capability_version_number",
        ),
        UniqueConstraint(
            "provider_id",
            "endpoint_id",
            "id",
            name="uq_provider_capability_endpoint_id",
        ),
        ForeignKeyConstraint(
            ["provider_id"],
            ["provider_identities.id"],
            name="fk_provider_capability_provider",
            ondelete="RESTRICT",
        ),
        Index("ix_provider_capability_created", "provider_id", "endpoint_id", "created_at"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(exact_string_sql_type(36), primary_key=True)
    provider_id: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    endpoint_id: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    capability_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    configuration_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    secret_reference: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    capability_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(_MONEY_TYPE, nullable=False)
    currency: Mapped[str] = mapped_column(exact_string_sql_type(3), nullable=False)
    created_by: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ProviderEndpointCapabilityHeadModel(Base):
    __tablename__ = "provider_endpoint_capability_heads"
    __table_args__ = (
        PrimaryKeyConstraint(
            "provider_id",
            "endpoint_id",
            name="pk_provider_endpoint_capability_head",
        ),
        ForeignKeyConstraint(
            ["provider_id"],
            ["provider_identities.id"],
            name="fk_provider_capability_head_provider",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["provider_id", "endpoint_id", "current_version_id"],
            [
                "provider_endpoint_capability_versions.provider_id",
                "provider_endpoint_capability_versions.endpoint_id",
                "provider_endpoint_capability_versions.id",
            ],
            name="fk_provider_capability_head_current",
            ondelete="RESTRICT",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    provider_id: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    endpoint_id: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    current_version_id: Mapped[str | None] = mapped_column(exact_string_sql_type(36))
    current_version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    latest_version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(exact_string_sql_type(128))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ProviderDiscoveryCandidateModel(Base):
    __tablename__ = "provider_discovery_candidates"
    __table_args__ = (
        PrimaryKeyConstraint("workspace_id", "id", name="pk_provider_discovery_candidate"),
        UniqueConstraint(
            "workspace_id",
            "provider_id",
            "endpoint_id",
            "discovery_sha256",
            name="uq_provider_discovery_candidate_evidence",
        ),
        ForeignKeyConstraint(
            ["provider_id"],
            ["provider_identities.id"],
            name="fk_provider_discovery_provider",
            ondelete="RESTRICT",
        ),
        Index("ix_provider_discovery_review", "workspace_id", "state", "discovered_at"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    id: Mapped[str] = mapped_column(exact_string_sql_type(36), nullable=False)
    provider_id: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    endpoint_id: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    discovered_model_id: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    discovery_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    discovery_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    discovered_by: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(exact_string_sql_type(128))
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ModelRoutePolicyVersionModel(Base):
    __tablename__ = "model_route_policy_versions"
    __table_args__ = (
        PrimaryKeyConstraint("workspace_id", "id", name="pk_model_route_policy_version"),
        UniqueConstraint(
            "workspace_id",
            "policy_key",
            "version_number",
            name="uq_model_route_policy_version_number",
        ),
        UniqueConstraint(
            "workspace_id",
            "policy_key",
            "id",
            name="uq_model_route_policy_key_id",
        ),
        Index("ix_model_route_policy_created", "workspace_id", "policy_key", "created_at"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    id: Mapped[str] = mapped_column(exact_string_sql_type(36), nullable=False)
    policy_key: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    policy_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    policy_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    quality_weight: Mapped[Decimal] = mapped_column(_SCORE_TYPE, nullable=False)
    availability_weight: Mapped[Decimal] = mapped_column(_SCORE_TYPE, nullable=False)
    latency_weight: Mapped[Decimal] = mapped_column(_SCORE_TYPE, nullable=False)
    quota_weight: Mapped[Decimal] = mapped_column(_SCORE_TYPE, nullable=False)
    price_weight: Mapped[Decimal] = mapped_column(_SCORE_TYPE, nullable=False)
    created_by: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ModelRoutePolicyHeadModel(Base):
    __tablename__ = "model_route_policy_heads"
    __table_args__ = (
        PrimaryKeyConstraint("workspace_id", "policy_key", name="pk_model_route_policy_head"),
        ForeignKeyConstraint(
            ["workspace_id", "policy_key", "current_version_id"],
            [
                "model_route_policy_versions.workspace_id",
                "model_route_policy_versions.policy_key",
                "model_route_policy_versions.id",
            ],
            name="fk_model_route_policy_head_current",
            ondelete="RESTRICT",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    policy_key: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    current_version_id: Mapped[str | None] = mapped_column(exact_string_sql_type(36))
    current_version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    latest_version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(exact_string_sql_type(128))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ProviderEndpointObservationModel(Base):
    __tablename__ = "provider_endpoint_observations"
    __table_args__ = (
        PrimaryKeyConstraint("workspace_id", "id", name="pk_provider_endpoint_observation"),
        UniqueConstraint(
            "workspace_id",
            "idempotency_key_sha256",
            name="uq_provider_endpoint_observation_idempotency",
        ),
        ForeignKeyConstraint(
            ["endpoint_capability_version_id"],
            ["provider_endpoint_capability_versions.id"],
            name="fk_provider_endpoint_observation_capability",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_provider_endpoint_observation_latest",
            "workspace_id",
            "endpoint_capability_version_id",
            "observed_at",
            "id",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    workspace_id: Mapped[str] = mapped_column(workspace_id_sql_type(), nullable=False)
    id: Mapped[str] = mapped_column(exact_string_sql_type(36), nullable=False)
    endpoint_capability_version_id: Mapped[str] = mapped_column(
        exact_string_sql_type(36), nullable=False
    )
    quality_score: Mapped[Decimal] = mapped_column(_SCORE_TYPE, nullable=False)
    availability_score: Mapped[Decimal] = mapped_column(_SCORE_TYPE, nullable=False)
    latency_score: Mapped[Decimal] = mapped_column(_SCORE_TYPE, nullable=False)
    quota_score: Mapped[Decimal] = mapped_column(_SCORE_TYPE, nullable=False)
    circuit_state: Mapped[str] = mapped_column(String(24), nullable=False)
    remaining_quota_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    observation_source: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key_sha256: Mapped[str] = mapped_column(exact_string_sql_type(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_by: Mapped[str] = mapped_column(exact_string_sql_type(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
