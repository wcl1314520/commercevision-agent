"""Public administration contracts for versioned Milvus rebuilds."""

from __future__ import annotations

from datetime import UTC, datetime

from commercevision_domain import CollectionRebuildState, CollectionSpec, VectorKind
from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator


class CollectionRebuildContractV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CollectionRebuildRequestV1(CollectionRebuildContractV1):
    vector_kind: VectorKind
    model_family: str = Field(min_length=1, max_length=128)
    model_id: str = Field(min_length=1, max_length=128)
    pinned_revision: str = Field(min_length=1, max_length=128)
    dimension: int = Field(ge=1, le=32_768, strict=True)
    schema_version: int = Field(ge=1, strict=True)
    index_spec_version: str = Field(min_length=1, max_length=128)
    expected_active_collection_version: int = Field(ge=1, strict=True)
    expected_policy_pointer_version: int = Field(ge=1, strict=True)

    @field_validator("vector_kind", mode="before")
    @classmethod
    def parse_vector_kind(cls, value: object) -> VectorKind:
        try:
            return VectorKind(value)
        except (TypeError, ValueError):
            raise ValueError("vector_kind must be IMAGE or PRODUCT_FUSED") from None

    @computed_field
    @property
    def collection_spec(self) -> CollectionSpec:
        return CollectionSpec.create(
            model_family=self.model_family,
            pinned_revision=self.pinned_revision,
            dimension=self.dimension,
            vector_kind=self.vector_kind,
            schema_version=self.schema_version,
            index_spec_version=self.index_spec_version,
        )

    @model_validator(mode="after")
    def validate_spec(self) -> CollectionRebuildRequestV1:
        _ = self.collection_spec
        return self


class CollectionRebuildActionRequestV1(CollectionRebuildContractV1):
    expected_version: int = Field(ge=1, strict=True)


class CollectionRebuildValidationV1(CollectionRebuildContractV1):
    expected_row_count: int = Field(ge=0, strict=True)
    actual_row_count: int = Field(ge=0, strict=True)
    missing_primary_key_count: int = Field(ge=0, strict=True)
    unexpected_primary_key_count: int = Field(ge=0, strict=True)
    sampled_visibility_count: int = Field(ge=0, strict=True)
    sampled_visibility_failures: int = Field(ge=0, strict=True)
    ann_recall_at_10: float = Field(ge=0, le=1)
    minimum_ann_recall_at_10: float = Field(ge=0, le=1)
    fixed_query_pass_count: int = Field(ge=0, strict=True)
    fixed_query_total_count: int = Field(ge=0, strict=True)
    unauthorized_result_count: int = Field(ge=0, strict=True)
    queries_with_unauthorized_results: int = Field(ge=0, strict=True)
    accepted: bool

    @model_validator(mode="after")
    def require_fail_closed_acceptance(self) -> CollectionRebuildValidationV1:
        if self.sampled_visibility_failures > self.sampled_visibility_count:
            raise ValueError("sampled visibility failures cannot exceed the sample")
        visible_count = self.sampled_visibility_count - self.sampled_visibility_failures
        if self.fixed_query_total_count != visible_count:
            raise ValueError("fixed query total must equal the visible validation sample")
        if self.fixed_query_pass_count > self.fixed_query_total_count:
            raise ValueError("fixed query passes cannot exceed the fixed query total")
        gates_pass = (
            self.expected_row_count == self.actual_row_count
            and self.missing_primary_key_count == 0
            and self.unexpected_primary_key_count == 0
            and self.sampled_visibility_failures == 0
            and self.ann_recall_at_10 >= self.minimum_ann_recall_at_10
            and self.fixed_query_pass_count == self.fixed_query_total_count
            and self.unauthorized_result_count == 0
            and self.queries_with_unauthorized_results == 0
        )
        if self.accepted and not gates_pass:
            raise ValueError(
                "accepted rebuild validation cannot contain unauthorized or failed gates"
            )
        return self


class CollectionRebuildProgressV1(CollectionRebuildContractV1):
    sequence: int = Field(ge=1, strict=True)
    state: CollectionRebuildState
    processed_count: int = Field(ge=0, strict=True)
    message_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.astimezone(UTC).utcoffset() is None:
            raise ValueError("rebuild timestamps must be timezone-aware")
        return value.astimezone(UTC)


class CollectionRebuildResponseV1(CollectionRebuildContractV1):
    id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    operation_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    vector_kind: VectorKind
    state: CollectionRebuildState
    version: int = Field(ge=1, strict=True)
    snapshot_watermark: datetime
    replay_watermark: datetime | None
    backfill_cursor: str | None = Field(default=None, max_length=36)
    replay_cursor: str | None = Field(default=None, max_length=36)
    processed_count: int = Field(ge=0, strict=True)
    validation: CollectionRebuildValidationV1 | None
    failure_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    retire_after: datetime | None
    created_at: datetime
    updated_at: datetime
    progress: list[CollectionRebuildProgressV1] = Field(default_factory=list, max_length=200)

    @field_validator("vector_kind", mode="before")
    @classmethod
    def parse_vector_kind(cls, value: object) -> VectorKind:
        try:
            return VectorKind(value)
        except (TypeError, ValueError):
            raise ValueError("vector_kind must be IMAGE or PRODUCT_FUSED") from None

    @model_validator(mode="after")
    def validate_state_projection(self) -> CollectionRebuildResponseV1:
        post_validation_states = {
            CollectionRebuildState.READY,
            CollectionRebuildState.ACTIVATING,
            CollectionRebuildState.ACTIVE,
            CollectionRebuildState.RETIRING,
            CollectionRebuildState.RETIRED,
        }
        if self.state in post_validation_states and (
            self.validation is None or not self.validation.accepted
        ):
            raise ValueError("post-validation rebuild states require an accepted validation report")
        if self.state is CollectionRebuildState.FAILED and self.failure_code is None:
            raise ValueError("failed rebuilds require a stable failure code")
        return self
