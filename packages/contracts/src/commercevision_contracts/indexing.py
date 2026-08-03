"""Typed provider and vector-index contracts for IMAGE indexing."""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from typing import Literal
from unicodedata import category as unicode_category
from uuid import UUID

from commercevision_domain import CollectionSpec, VectorKind
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

FLOAT32_MAX = 3.4028234663852886e38


def _validate_float32_vector(values: list[float]) -> list[float]:
    if any(not math.isfinite(value) or abs(value) > FLOAT32_MAX for value in values):
        raise ValueError("embedding vector values must fit finite IEEE-754 float32")
    return values


def _validate_canonical_uuid(value: str) -> str:
    try:
        parsed = UUID(value)
    except ValueError:
        raise ValueError("identity must be a canonical lowercase UUID") from None
    if str(parsed) != value:
        raise ValueError("identity must be a canonical lowercase UUID")
    return value


class IndexingContractV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AssetIndexStatusResponseV1(IndexingContractV1):
    asset_id: str = Field(min_length=1, max_length=36)
    asset_version_id: str | None = Field(min_length=1, max_length=36)
    state: Literal[
        "NOT_REQUESTED",
        "PENDING",
        "PROCESSING",
        "INDEXED",
        "RETRYABLE_FAILED",
        "PERMANENT_FAILED",
        "STALE",
        "DELETE_PENDING",
        "DELETED",
    ]
    retryable: bool
    failure_reason: str | None = Field(min_length=1, max_length=128)
    indexed_at: datetime | None
    updated_at: datetime | None


class EmbeddingProviderErrorV1(IndexingContractV1):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    category: Literal[
        "THROTTLED",
        "TIMEOUT",
        "UNAVAILABLE",
        "INVALID_RESPONSE",
        "REJECTED",
        "AUTHENTICATION",
        "UNKNOWN",
    ]
    safe_message: str = Field(min_length=1, max_length=512)
    retryable: bool
    retry_after_seconds: int | None = Field(default=None, ge=0, le=86_400)
    provider_request_id: str | None = Field(default=None, min_length=1, max_length=256)
    outcome_unknown: bool

    @field_validator("safe_message", "provider_request_id")
    @classmethod
    def require_safe_trimmed_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value != value.strip() or any(
            unicode_category(character) == "Cc" for character in value
        ):
            raise ValueError(
                "provider error text must be trimmed and contain no control characters"
            )
        return value

    @model_validator(mode="after")
    def validate_retry_semantics(self) -> EmbeddingProviderErrorV1:
        if self.retry_after_seconds is not None and not self.retryable:
            raise ValueError("retry_after_seconds requires a retryable provider error")
        if self.outcome_unknown and self.retry_after_seconds is not None:
            raise ValueError("unknown provider outcomes cannot prescribe a retry delay")
        return self


class EmbeddingProviderFailure(Exception):
    """Provider-neutral normalized failure; raw SDK payloads never cross this seam."""

    def __init__(self, error: EmbeddingProviderErrorV1) -> None:
        super().__init__(error.safe_message)
        self.error = error


class MilvusCollectionFieldV1(IndexingContractV1):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    data_type: Literal["VARCHAR", "INT64", "FLOAT_VECTOR"]
    primary_key: bool = False
    maximum_length: int | None = Field(default=None, ge=1, le=2048)
    dimension: int | None = Field(default=None, ge=1, le=32_768)


class MilvusCollectionCreateRequestV1(IndexingContractV1):
    collection_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,254}$")
    dynamic_fields_enabled: Literal[False]
    metric_type: Literal["COSINE"]
    index_type: Literal["HNSW"]
    index_spec_version: str = Field(min_length=1, max_length=128)
    fields: list[MilvusCollectionFieldV1] = Field(min_length=1, max_length=32)


class EmbeddingImageInputV1(IndexingContractV1):
    asset_version_id: str = Field(min_length=1, max_length=36)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(ge=1, le=5 * 1024 * 1024)
    url: SecretStr = Field(repr=False)
    required_headers: dict[str, SecretStr] = Field(default_factory=dict, repr=False)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def require_utc_expiry(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("embedding image expiry must be timezone-aware UTC")
        return value


class EmbeddingProviderRequestV1(IndexingContractV1):
    provider: str = Field(min_length=1, max_length=64)
    model_id: str = Field(min_length=1, max_length=128)
    pinned_revision: str = Field(min_length=1, max_length=128)
    model_configuration_version: str = Field(min_length=1, max_length=128)
    preprocessing_version: str = Field(min_length=1, max_length=128)
    vector_kind: VectorKind
    expected_dimension: int = Field(ge=1, le=32_768)
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    images: list[EmbeddingImageInputV1] = Field(max_length=16)
    controlled_text: str | None = Field(default=None, min_length=1, max_length=32 * 1024)

    @field_validator("controlled_text")
    @classmethod
    def require_canonical_controlled_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        has_control_character = any(unicode_category(character) == "Cc" for character in value)
        if value != value.strip() or has_control_character:
            raise ValueError("controlled text must be trimmed and contain no control characters")
        return value

    @model_validator(mode="after")
    def validate_vector_kind_input(self) -> EmbeddingProviderRequestV1:
        if self.vector_kind is VectorKind.IMAGE:
            if not self.images:
                raise ValueError("IMAGE embedding requires at least one image")
            if self.controlled_text is not None:
                raise ValueError("IMAGE embedding cannot contain controlled text")
        elif self.vector_kind is VectorKind.PRODUCT_FUSED:
            if self.controlled_text is None and not self.images:
                raise ValueError("PRODUCT_FUSED embedding requires text or an image")
            if len(self.images) > 1:
                raise ValueError("PRODUCT_FUSED embedding accepts at most one image")
        return self


class EmbeddingVectorV1(IndexingContractV1):
    values: list[float] = Field(min_length=1, max_length=32_768)

    @field_validator("values")
    @classmethod
    def require_float32_values(cls, values: list[float]) -> list[float]:
        return _validate_float32_vector(values)


class EmbeddingProviderResultV1(IndexingContractV1):
    vectors: list[EmbeddingVectorV1] = Field(min_length=1)
    provider: str = Field(min_length=1, max_length=64)
    provider_request_id: str = Field(min_length=1, max_length=256)
    actual_model: str = Field(min_length=1, max_length=256)
    latency_ms: int = Field(ge=0)
    usage: dict[str, int] = Field(default_factory=dict)

    def validate_for(self, request: EmbeddingProviderRequestV1) -> None:
        if self.provider != request.provider:
            raise ValueError("embedding result provider does not match the configured provider")
        expected_vectors = (
            1 if request.vector_kind is VectorKind.PRODUCT_FUSED else len(request.images)
        )
        if len(self.vectors) != expected_vectors:
            raise ValueError("embedding result count does not match the request")
        for vector in self.vectors:
            if len(vector.values) != request.expected_dimension:
                raise ValueError("embedding result dimension does not match the collection")
            _validate_float32_vector(vector.values)


class MilvusVectorRowV1(IndexingContractV1):
    embedding_record_id: str = Field(min_length=1, max_length=36)
    milvus_primary_key: str = Field(min_length=1, max_length=64)
    asset_version_id: str = Field(min_length=1, max_length=36)
    workspace_id: str = Field(min_length=1, max_length=128)
    rights_record_version: int = Field(ge=1)
    category: str = Field(min_length=1, max_length=128)
    brand: str = Field(max_length=128)
    asset_role: str = Field(min_length=1, max_length=64)
    vector_kind: VectorKind
    model_configuration_version: str = Field(min_length=1, max_length=128)
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    embedding_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    write_generation: int = Field(ge=1)
    indexed_at_epoch_micros: int = Field(ge=0)
    vector: list[float] = Field(min_length=1, max_length=32_768)

    @field_validator("vector")
    @classmethod
    def require_float32_vector(cls, values: list[float]) -> list[float]:
        return _validate_float32_vector(values)


class MilvusUpsertRequestV1(IndexingContractV1):
    collection_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,254}$")
    row: MilvusVectorRowV1


class MilvusCollectionSnapshotV1(IndexingContractV1):
    row_count: int = Field(ge=0, strict=True)
    rows: list[MilvusVectorRowV1]

    @model_validator(mode="after")
    def require_exact_row_count(self) -> MilvusCollectionSnapshotV1:
        if self.row_count != len(self.rows):
            raise ValueError("Milvus snapshot row count does not match its rows")
        primary_keys = [row.milvus_primary_key for row in self.rows]
        if len(primary_keys) != len(set(primary_keys)):
            raise ValueError("Milvus snapshot contains duplicate primary keys")
        return self


class MilvusAnnSearchRequestV1(IndexingContractV1):
    collection_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,254}$")
    workspace_id: str = Field(min_length=1, max_length=128)
    vector_kind: VectorKind
    eligible_embedding_record_ids: list[str] = Field(min_length=1, max_length=1_000)
    query_vector: list[float] = Field(min_length=1, max_length=32_768)
    limit: int = Field(ge=1, le=100)

    @field_validator("query_vector")
    @classmethod
    def require_float32_query_vector(cls, values: list[float]) -> list[float]:
        return _validate_float32_vector(values)

    @field_validator("eligible_embedding_record_ids")
    @classmethod
    def require_canonical_eligible_records(cls, values: list[str]) -> list[str]:
        return [_validate_canonical_uuid(value) for value in values]

    @model_validator(mode="after")
    def require_unique_eligible_records(self) -> MilvusAnnSearchRequestV1:
        if len(set(self.eligible_embedding_record_ids)) != len(self.eligible_embedding_record_ids):
            raise ValueError("eligible embedding record identities must be unique")
        if self.limit > len(self.eligible_embedding_record_ids):
            raise ValueError("ANN limit cannot exceed the MySQL-eligible identity count")
        return self


class MilvusAnnSearchHitV1(IndexingContractV1):
    embedding_record_id: str = Field(min_length=1, max_length=36)
    asset_version_id: str = Field(min_length=1, max_length=36)
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    embedding_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    write_generation: int = Field(ge=1)
    score: float = Field(ge=-1, le=1)

    @field_validator("embedding_record_id", "asset_version_id")
    @classmethod
    def require_canonical_identities(cls, value: str) -> str:
        return _validate_canonical_uuid(value)


class MilvusVectorIdentityV1(IndexingContractV1):
    collection_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,254}$")
    embedding_record_id: str = Field(min_length=1, max_length=36)
    milvus_primary_key: str = Field(min_length=1, max_length=64)
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    embedding_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    write_generation: int = Field(ge=1)


class MilvusVectorProofV1(IndexingContractV1):
    exists: bool
    milvus_primary_key: str | None = Field(default=None, min_length=1, max_length=64)
    input_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    embedding_spec_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    write_generation: int | None = Field(default=None, ge=1)

    def matches(self, identity: MilvusVectorIdentityV1) -> bool:
        return (
            self.exists
            and self.milvus_primary_key == identity.milvus_primary_key
            and self.input_hash == identity.input_hash
            and self.embedding_spec_sha256 == identity.embedding_spec_sha256
            and self.write_generation == identity.write_generation
        )


def collection_create_request(
    spec: CollectionSpec,
    *,
    collection_name: str | None = None,
) -> MilvusCollectionCreateRequestV1:
    resolved_name = collection_name or spec.physical_name
    candidate_pattern = rf"{re.escape(spec.physical_name)}_[0-9a-f]{{12}}"
    if (
        resolved_name != spec.physical_name
        and re.fullmatch(candidate_pattern, resolved_name) is None
    ):
        raise ValueError("collection name must be derived from the immutable spec")
    scalar_fields = (
        ("milvus_primary_key", "VARCHAR", True, 64),
        ("embedding_record_id", "VARCHAR", False, 36),
        ("asset_version_id", "VARCHAR", False, 36),
        ("workspace_id", "VARCHAR", False, 128),
        ("rights_record_version", "INT64", False, None),
        ("category", "VARCHAR", False, 128),
        ("brand", "VARCHAR", False, 128),
        ("asset_role", "VARCHAR", False, 64),
        ("vector_kind", "VARCHAR", False, 32),
        ("model_configuration_version", "VARCHAR", False, 128),
        ("input_hash", "VARCHAR", False, 64),
        ("embedding_spec_sha256", "VARCHAR", False, 64),
        ("write_generation", "INT64", False, None),
        ("indexed_at_epoch_micros", "INT64", False, None),
    )
    fields = [
        MilvusCollectionFieldV1(
            name=name,
            data_type=data_type,
            primary_key=primary_key,
            maximum_length=maximum_length,
        )
        for name, data_type, primary_key, maximum_length in scalar_fields
    ]
    fields.append(
        MilvusCollectionFieldV1(
            name="vector",
            data_type="FLOAT_VECTOR",
            dimension=spec.dimension,
        )
    )
    return MilvusCollectionCreateRequestV1(
        collection_name=resolved_name,
        dynamic_fields_enabled=False,
        metric_type="COSINE",
        index_type="HNSW",
        index_spec_version=spec.index_spec_version,
        fields=fields,
    )
