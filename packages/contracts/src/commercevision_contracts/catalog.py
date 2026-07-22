"""Versioned public contracts for the Product Catalog."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CatalogContractV1(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProductCreateRequestV1(CatalogContractV1):
    source_namespace: str = Field(min_length=1, max_length=64)
    external_id: str = Field(min_length=1, max_length=128)
    source_version: str | None = Field(default=None, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    category_code: str = Field(min_length=1, max_length=128)
    brand: str = Field(min_length=1, max_length=128)
    attributes: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def require_aware_expiry(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("expires_at must include a timezone")
        return value


class ProductUpdateRequestV1(CatalogContractV1):
    expected_version: int = Field(ge=1)
    source_version: str | None = Field(default=None, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    category_code: str = Field(min_length=1, max_length=128)
    brand: str = Field(min_length=1, max_length=128)
    attributes: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None

    _require_aware_expiry = field_validator("expires_at")(
        ProductCreateRequestV1.require_aware_expiry.__func__
    )


class SKUCreateRequestV1(CatalogContractV1):
    source_namespace: str = Field(min_length=1, max_length=64)
    external_id: str = Field(min_length=1, max_length=128)
    source_version: str | None = Field(default=None, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    category_code: str = Field(min_length=1, max_length=128)
    brand: str = Field(min_length=1, max_length=128)
    attributes: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None

    _require_aware_expiry = field_validator("expires_at")(
        ProductCreateRequestV1.require_aware_expiry.__func__
    )


class SKUUpdateRequestV1(CatalogContractV1):
    expected_version: int = Field(ge=1)
    source_version: str | None = Field(default=None, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    category_code: str = Field(min_length=1, max_length=128)
    brand: str = Field(min_length=1, max_length=128)
    attributes: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None

    _require_aware_expiry = field_validator("expires_at")(
        ProductCreateRequestV1.require_aware_expiry.__func__
    )


class CatalogDeleteRequestV1(CatalogContractV1):
    expected_version: int = Field(ge=1)


class SKUResponseV1(BaseModel):
    id: str
    workspace_id: str
    product_id: str
    source_namespace: str
    external_id: str
    source_version: str | None
    title: str
    category_code: str
    brand: str
    attributes: dict[str, Any]
    expires_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class ProductSummaryResponseV1(BaseModel):
    id: str
    workspace_id: str
    source_namespace: str
    external_id: str
    source_version: str | None
    title: str
    category_code: str
    brand: str
    attributes: dict[str, Any]
    expires_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class ProductResponseV1(ProductSummaryResponseV1):
    skus: list[SKUResponseV1] = Field(default_factory=list)


class ProductListResponseV1(BaseModel):
    items: list[ProductSummaryResponseV1]
    next_cursor: str | None
