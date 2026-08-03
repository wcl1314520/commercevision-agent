"""Closed contracts for the Product Catalog and Asset MCP boundary."""

from __future__ import annotations

import re
import unicodedata
from typing import Annotated, Literal

from commercevision_domain import VectorKind, canonicalize_uuid
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .brand_profiles import BrandProfileVersionResponseV1
from .catalog import ProductResponseV1
from .product_briefs import ProductBriefVersionResponseV1
from .retrieval import (
    RetrievalResponseV1,
    RetrievalTemporaryReferenceV1,
    controlled_retrieval_text,
)
from .workspace_identity import validate_workspace_id

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
StrictInt = Annotated[int, Field(strict=True)]
JsonVectorKind = Annotated[VectorKind, Field(strict=False)]


class McpToolContractV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class McpToolBudgetV1(McpToolContractV1):
    max_result_count: StrictInt = Field(ge=1, le=50)
    max_candidate_count: StrictInt = Field(ge=1, le=1_000)
    max_output_bytes: StrictInt = Field(ge=1_024, le=2 * 1024 * 1024)

    @model_validator(mode="after")
    def require_candidate_budget(self) -> McpToolBudgetV1:
        if self.max_candidate_count < self.max_result_count:
            raise ValueError("candidate budget must be at least the result budget")
        return self


class McpToolIdentityV1(McpToolContractV1):
    workspace_id: str
    actor_id: str = Field(min_length=1, max_length=128)
    workflow_id: str = Field(min_length=1, max_length=128)
    invocation_id: str = Field(min_length=8, max_length=128)
    scopes: tuple[str, ...] = Field(min_length=1, max_length=32)
    purpose: str
    provider: str
    requires_derivative: bool = Field(strict=True)
    budget: McpToolBudgetV1
    issued_at: StrictInt

    @field_validator("workspace_id")
    @classmethod
    def require_workspace_id(cls, value: str) -> str:
        return validate_workspace_id(value)

    @field_validator("actor_id")
    @classmethod
    def require_actor_id(cls, value: str) -> str:
        if not value.strip() or any(unicodedata.category(character) == "Cc" for character in value):
            raise ValueError("MCP actor identity is invalid")
        return value

    @field_validator("workflow_id", "invocation_id")
    @classmethod
    def require_identity_token(cls, value: str) -> str:
        if _TOKEN.fullmatch(value) is None:
            raise ValueError("MCP identity token is invalid")
        return value

    @field_validator("purpose", "provider")
    @classmethod
    def require_token(cls, value: str) -> str:
        if _TOKEN.fullmatch(value) is None:
            raise ValueError("MCP identity policy token is invalid")
        return value

    @field_validator("scopes", mode="before")
    @classmethod
    def accept_json_scopes(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("scopes")
    @classmethod
    def require_scopes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(_TOKEN.fullmatch(item) is None for item in value):
            raise ValueError("MCP identity scopes must be unique policy tokens")
        return value


class _CanonicalIdInput(McpToolContractV1):
    @staticmethod
    def canonical_id(value: str) -> str:
        return canonicalize_uuid(value)


class CatalogGetProductInputV1(_CanonicalIdInput):
    product_id: str

    _product_id = field_validator("product_id")(_CanonicalIdInput.canonical_id)


class CatalogGetProductBriefInputV1(_CanonicalIdInput):
    product_brief_id: str
    product_brief_version_id: str | None = None

    @field_validator("product_brief_id", "product_brief_version_id")
    @classmethod
    def canonical_optional_id(cls, value: str | None) -> str | None:
        return canonicalize_uuid(value) if value is not None else None


class BrandGetProfileInputV1(_CanonicalIdInput):
    profile_id: str
    version_number: StrictInt | None = Field(default=None, ge=1, le=2_147_483_647)

    _profile_id = field_validator("profile_id")(_CanonicalIdInput.canonical_id)


class AssetsSearchInputV1(_CanonicalIdInput):
    product_id: str | None = None
    product_brief_id: str | None = None
    category: str | None = Field(default=None, max_length=128)
    brand: str | None = Field(default=None, max_length=128)
    roles: tuple[str, ...] = Field(default=(), max_length=32)
    vector_kinds: tuple[JsonVectorKind, ...] = Field(min_length=1, max_length=2)
    query_text: str | None = Field(default=None, max_length=4096)
    query_image_asset_version_id: str | None = None
    explicit_reference_asset_version_ids: tuple[str, ...] = Field(default=(), max_length=50)
    brand_profile_id: str | None = None
    brand_profile_version: StrictInt | None = Field(default=None, ge=1)
    top_k: StrictInt = Field(default=10, ge=1, le=50)

    @field_validator("roles", "vector_kinds", "explicit_reference_asset_version_ids", mode="before")
    @classmethod
    def accept_json_arrays(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("query_text")
    @classmethod
    def normalize_query_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return controlled_retrieval_text(
            value,
            field="query text",
            maximum_bytes=4096,
            casefold=True,
        )

    @field_validator("category", "brand")
    @classmethod
    def normalize_filters(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return controlled_retrieval_text(
            value,
            field="retrieval filter",
            maximum_bytes=512,
        )

    @field_validator(
        "product_id",
        "product_brief_id",
        "query_image_asset_version_id",
        "brand_profile_id",
    )
    @classmethod
    def canonical_optional_id(cls, value: str | None) -> str | None:
        return canonicalize_uuid(value) if value is not None else None

    @field_validator("explicit_reference_asset_version_ids")
    @classmethod
    def canonical_references(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        result = tuple(canonicalize_uuid(item) for item in value)
        if len(set(result)) != len(result):
            raise ValueError("explicit Asset Version references must be unique")
        return result

    @field_validator("roles")
    @classmethod
    def canonical_roles(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(_TOKEN.fullmatch(item) is None for item in value):
            raise ValueError("asset roles must be unique policy tokens")
        return value

    @field_validator("vector_kinds")
    @classmethod
    def supported_vectors(cls, value: tuple[VectorKind, ...]) -> tuple[VectorKind, ...]:
        if len(set(value)) != len(value) or any(
            item not in {VectorKind.IMAGE, VectorKind.PRODUCT_FUSED} for item in value
        ):
            raise ValueError("MCP retrieval vector kinds are invalid")
        return value

    @model_validator(mode="after")
    def coherent_search(self) -> AssetsSearchInputV1:
        if self.product_id is None and self.product_brief_id is None:
            raise ValueError("asset search requires a Product or ProductBrief")
        if (self.brand_profile_id is None) != (self.brand_profile_version is None):
            raise ValueError("Brand Profile identity and version must be supplied together")
        if not (
            self.query_text
            or self.query_image_asset_version_id
            or self.explicit_reference_asset_version_ids
            or self.brand_profile_id
        ):
            raise ValueError("asset search requires at least one controlled query signal")
        if VectorKind.IMAGE in self.vector_kinds and self.query_image_asset_version_id is None:
            raise ValueError("IMAGE recall requires a query image Asset Version")
        return self


class AssetsGetTemporaryReferenceInputV1(_CanonicalIdInput):
    retrieval_run_id: str
    rank: StrictInt = Field(ge=1, le=50)
    preview_reference_token: str = Field(
        min_length=32,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
        repr=False,
    )

    _run_id = field_validator("retrieval_run_id")(_CanonicalIdInput.canonical_id)


class McpToolOutputV1(McpToolContractV1):
    tool_name: str
    tool_version: str
    policy_version: str
    data_classification: str = "UNTRUSTED_BUSINESS_DATA"


class CatalogGetProductOutputV1(McpToolOutputV1):
    product: ProductResponseV1


class CatalogGetProductBriefOutputV1(McpToolOutputV1):
    product_brief_id: str
    confirmation_status: Literal["CONFIRMED"]
    version: ProductBriefVersionResponseV1


class BrandGetProfileOutputV1(McpToolOutputV1):
    profile: BrandProfileVersionResponseV1


class AssetsSearchOutputV1(McpToolOutputV1):
    retrieval: RetrievalResponseV1


class AssetsGetTemporaryReferenceOutputV1(McpToolOutputV1):
    reference: RetrievalTemporaryReferenceV1
