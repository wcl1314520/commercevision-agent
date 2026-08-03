"""Strict structured contracts for rights-first retrieval."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit

from commercevision_domain import RetrievalChannel, VectorKind, canonicalize_uuid
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .workspace_identity import validate_workspace_id

_IDENTITY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}")
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_HEADER_NAME_PATTERN = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]{1,128}")
_MAXIMUM_QUERY_BYTES = 4096
StrictBool = Annotated[bool, Field(strict=True)]
JsonVectorKind = Annotated[VectorKind, Field(strict=False)]


def controlled_retrieval_text(
    value: str,
    *,
    field: str,
    maximum_bytes: int,
    casefold: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    normalized = unicodedata.normalize("NFKC", value)
    safe = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in normalized
    )
    collapsed = " ".join(safe.split())
    if casefold:
        collapsed = collapsed.casefold()
    if not collapsed:
        raise ValueError(f"{field} must not be empty")
    if len(collapsed.encode("utf-8")) > maximum_bytes:
        raise ValueError(f"{field} exceeds its byte limit")
    return collapsed


class RetrievalContractV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class RetrievalQueryV1(RetrievalContractV1):
    workspace_id: str
    requester_id: str
    product_id: str | None = None
    product_brief_id: str | None = None
    category: str | None = Field(default=None, max_length=128)
    brand: str | None = Field(default=None, max_length=128)
    purpose: str
    provider: str
    requires_derivative: StrictBool
    roles: list[str] = Field(default_factory=list, max_length=32)
    vector_kinds: list[JsonVectorKind] = Field(min_length=1, max_length=2)
    query_text: str | None = Field(default=None, max_length=4096)
    query_image_asset_version_id: str | None = None
    explicit_reference_asset_version_ids: list[str] = Field(
        default_factory=list,
        max_length=50,
    )
    brand_profile_id: str | None = None
    brand_profile_version: int | None = Field(default=None, strict=True, ge=1)
    result_limit: int = Field(strict=True, ge=1, le=50)
    candidate_limit: int = Field(strict=True, ge=1, le=1_000)
    retrieval_policy_version: str

    @field_validator("workspace_id")
    @classmethod
    def require_workspace(cls, value: str) -> str:
        return validate_workspace_id(value)

    @field_validator("requester_id")
    @classmethod
    def require_requester_identity(cls, value: str) -> str:
        if _IDENTITY_PATTERN.fullmatch(value) is None:
            raise ValueError("requester identity is invalid")
        return value

    @field_validator(
        "product_id",
        "product_brief_id",
        "query_image_asset_version_id",
        "brand_profile_id",
    )
    @classmethod
    def require_canonical_optional_uuid(cls, value: str | None) -> str | None:
        return canonicalize_uuid(value) if value is not None else None

    @field_validator("explicit_reference_asset_version_ids")
    @classmethod
    def require_unique_explicit_references(cls, values: list[str]) -> list[str]:
        canonical = [canonicalize_uuid(value) for value in values]
        if len(set(canonical)) != len(canonical):
            raise ValueError("explicit reference identities must be unique")
        return canonical

    @field_validator("purpose", "provider", "retrieval_policy_version")
    @classmethod
    def require_policy_token(cls, value: str) -> str:
        if _TOKEN_PATTERN.fullmatch(value) is None:
            raise ValueError("retrieval token is invalid")
        return value

    @field_validator("roles")
    @classmethod
    def require_unique_roles(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values) or any(
            _TOKEN_PATTERN.fullmatch(value) is None for value in values
        ):
            raise ValueError("retrieval roles must be unique canonical tokens")
        return values

    @field_validator("vector_kinds")
    @classmethod
    def require_unique_vector_kinds(cls, values: list[VectorKind]) -> list[VectorKind]:
        if len(set(values)) != len(values):
            raise ValueError("retrieval vector kinds must be unique")
        if any(kind not in {VectorKind.IMAGE, VectorKind.PRODUCT_FUSED} for kind in values):
            raise ValueError("retrieval vector kind is unsupported")
        return values

    @field_validator("query_text")
    @classmethod
    def normalize_query_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return controlled_retrieval_text(
            value,
            field="query text",
            maximum_bytes=_MAXIMUM_QUERY_BYTES,
            casefold=True,
        )

    @field_validator("category", "brand")
    @classmethod
    def normalize_optional_filter(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return controlled_retrieval_text(value, field="retrieval filter", maximum_bytes=512)

    @model_validator(mode="after")
    def require_coherent_query(self) -> RetrievalQueryV1:
        if self.product_id is None and self.product_brief_id is None:
            raise ValueError("retrieval query requires a product or ProductBrief reference")
        if (self.brand_profile_id is None) != (self.brand_profile_version is None):
            raise ValueError("Brand Profile identity and version must be supplied together")
        if not (
            self.query_text
            or self.query_image_asset_version_id
            or self.explicit_reference_asset_version_ids
            or self.brand_profile_id
        ):
            raise ValueError("retrieval query requires at least one query signal")
        if VectorKind.IMAGE in self.vector_kinds and self.query_image_asset_version_id is None:
            raise ValueError("IMAGE recall requires a query image Asset Version")
        if self.candidate_limit < self.result_limit:
            raise ValueError("candidate limit must be at least the result limit")
        return self


class RetrievalScoreBreakdownV1(RetrievalContractV1):
    channel_ranks: dict[RetrievalChannel, int]
    channel_raw_scores: dict[RetrievalChannel, float] = Field(default_factory=dict)
    reciprocal_rank_fusion: float = Field(ge=0)
    business_adjustment: float = Field(ge=-1, le=1)
    final_score: float
    rerank_position: int | None = Field(default=None, strict=True, ge=1)

    @model_validator(mode="after")
    def require_score_identity(self) -> RetrievalScoreBreakdownV1:
        if not self.channel_ranks or any(rank < 1 for rank in self.channel_ranks.values()):
            raise ValueError("retrieval score requires positive channel ranks")
        if set(self.channel_raw_scores) - set(self.channel_ranks):
            raise ValueError("raw scores cannot introduce retrieval channels")
        expected = self.reciprocal_rank_fusion + self.business_adjustment
        if not abs(self.final_score - expected) <= 1e-9:
            raise ValueError("retrieval final score is inconsistent")
        return self


class RetrievalCitationV1(RetrievalContractV1):
    asset_id: str
    asset_version_id: str
    rights_record_id: str
    rights_record_version: int = Field(strict=True, ge=1)
    retrieval_policy_version: str
    brand_profile_version: int | None = Field(default=None, strict=True, ge=1)
    channels: list[RetrievalChannel] = Field(min_length=1, max_length=5)
    score: RetrievalScoreBreakdownV1
    rank: int = Field(strict=True, ge=1)
    reason: str = Field(min_length=1, max_length=512)
    decided_at: datetime
    preview_reference_token: str | None = Field(
        default=None,
        min_length=32,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
        repr=False,
    )

    @field_validator("asset_id", "asset_version_id", "rights_record_id")
    @classmethod
    def require_canonical_uuid(cls, value: str) -> str:
        return canonicalize_uuid(value)

    @field_validator("retrieval_policy_version")
    @classmethod
    def require_policy_version(cls, value: str) -> str:
        if _TOKEN_PATTERN.fullmatch(value) is None:
            raise ValueError("retrieval policy version is invalid")
        return value

    @field_validator("channels")
    @classmethod
    def require_unique_channels(cls, values: list[RetrievalChannel]) -> list[RetrievalChannel]:
        if len(set(values)) != len(values):
            raise ValueError("retrieval citation channels must be unique")
        return values

    @field_validator("reason")
    @classmethod
    def require_controlled_reason(cls, value: str) -> str:
        return controlled_retrieval_text(value, field="retrieval reason", maximum_bytes=2048)

    @field_validator("decided_at")
    @classmethod
    def require_utc_decision_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("retrieval decision time must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def require_consistent_channel_evidence(self) -> RetrievalCitationV1:
        if set(self.channels) != set(self.score.channel_ranks):
            raise ValueError("retrieval citation channels and score ranks are inconsistent")
        if (RetrievalChannel.BRAND_PROFILE in self.channels) != (
            self.brand_profile_version is not None
        ):
            raise ValueError("Brand Profile channel requires its immutable version")
        return self


class RetrievalDegradationV1(RetrievalContractV1):
    component: str
    code: str
    message: str = Field(min_length=1, max_length=512)

    @field_validator("component", "code")
    @classmethod
    def require_degradation_token(cls, value: str) -> str:
        if _TOKEN_PATTERN.fullmatch(value) is None:
            raise ValueError("retrieval degradation token is invalid")
        return value

    @field_validator("message")
    @classmethod
    def require_controlled_message(cls, value: str) -> str:
        return controlled_retrieval_text(value, field="degradation message", maximum_bytes=2048)


class RetrievalResponseV1(RetrievalContractV1):
    retrieval_run_id: str | None = None
    retrieval_policy_version: str
    complete_hybrid: StrictBool
    degradations: list[RetrievalDegradationV1] = Field(max_length=16)
    eligible_asset_version_count: int = Field(strict=True, ge=0, le=2_147_483_647)
    fused_candidate_count: int = Field(strict=True, ge=0, le=1_000)
    final_authorized_candidate_count: int = Field(strict=True, ge=0, le=1_000)
    latency_ms: int = Field(strict=True, ge=0, le=3_600_000)
    citations: list[RetrievalCitationV1] = Field(max_length=50)

    @field_validator("retrieval_policy_version")
    @classmethod
    def require_response_policy_version(cls, value: str) -> str:
        if _TOKEN_PATTERN.fullmatch(value) is None:
            raise ValueError("retrieval policy version is invalid")
        return value

    @field_validator("retrieval_run_id")
    @classmethod
    def require_optional_run_id(cls, value: str | None) -> str | None:
        return canonicalize_uuid(value) if value is not None else None

    @model_validator(mode="after")
    def require_consistent_response(self) -> RetrievalResponseV1:
        if not (
            len(self.citations)
            <= self.final_authorized_candidate_count
            <= self.fused_candidate_count
            <= self.eligible_asset_version_count
        ):
            raise ValueError("retrieval candidate counts are inconsistent")
        if [citation.rank for citation in self.citations] != list(
            range(1, len(self.citations) + 1)
        ):
            raise ValueError("retrieval citation ranks must be ordered and contiguous")
        if len({citation.asset_version_id for citation in self.citations}) != len(self.citations):
            raise ValueError("retrieval citations must contain unique Asset Versions")
        if any(
            citation.retrieval_policy_version != self.retrieval_policy_version
            for citation in self.citations
        ):
            raise ValueError("retrieval citation policy version is inconsistent")
        if self.complete_hybrid and self.degradations:
            raise ValueError("complete hybrid retrieval cannot contain degradations")
        return self


class RetrievalTemporaryReferenceV1(RetrievalContractV1):
    method: Literal["GET"]
    url: str = Field(min_length=1, max_length=8192, repr=False)
    required_headers: dict[str, str] = Field(
        default_factory=dict,
        max_length=32,
        repr=False,
    )
    expires_at: datetime

    @field_validator("url")
    @classmethod
    def require_http_url_without_credentials(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("retrieval reference URL is invalid")
        return value

    @field_validator("required_headers")
    @classmethod
    def require_safe_bounded_headers(cls, value: dict[str, str]) -> dict[str, str]:
        if any(
            _HEADER_NAME_PATTERN.fullmatch(name) is None
            or not isinstance(header_value, str)
            or len(header_value.encode("utf-8")) > 1024
            or any(unicodedata.category(character).startswith("C") for character in header_value)
            for name, header_value in value.items()
        ):
            raise ValueError("retrieval reference headers are invalid")
        return value

    @field_validator("expires_at")
    @classmethod
    def require_utc_expiry(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("retrieval reference expiry must be timezone-aware")
        return value.astimezone(UTC)


class RetrievalPreviewExchangeV1(RetrievalContractV1):
    preview_reference_token: str = Field(
        min_length=32,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
        repr=False,
    )
