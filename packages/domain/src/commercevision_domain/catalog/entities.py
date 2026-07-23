"""Product and SKU aggregates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from commercevision_domain.ids import new_uuid7
from commercevision_domain.workflow.errors import ConcurrencyError
from commercevision_domain.workspace_identity import validate_workspace_id


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _validate_expiry(expires_at: datetime | None) -> None:
    if expires_at is not None and expires_at.tzinfo is None:
        raise ValueError("catalog expiry must be timezone-aware")


@dataclass(slots=True)
class Product:
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

    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)

    @classmethod
    def create(
        cls,
        *,
        workspace_id: str,
        source_namespace: str,
        external_id: str,
        source_version: str | None,
        title: str,
        category_code: str,
        brand: str,
        attributes: dict[str, Any],
        expires_at: datetime | None,
        now: datetime | None = None,
    ) -> Product:
        _validate_expiry(expires_at)
        created_at = now or _utc_now()
        return cls(
            id=new_uuid7(),
            workspace_id=workspace_id,
            source_namespace=source_namespace,
            external_id=external_id,
            source_version=source_version,
            title=title,
            category_code=category_code,
            brand=brand,
            attributes=attributes,
            expires_at=expires_at,
            version=1,
            created_at=created_at,
            updated_at=created_at,
        )

    def update(
        self,
        *,
        expected_version: int,
        source_version: str | None,
        title: str,
        category_code: str,
        brand: str,
        attributes: dict[str, Any],
        expires_at: datetime | None,
        now: datetime | None = None,
    ) -> None:
        self.assert_version(expected_version)
        _validate_expiry(expires_at)
        self.source_version = source_version
        self.title = title
        self.category_code = category_code
        self.brand = brand
        self.attributes = attributes
        self.expires_at = expires_at
        self.version += 1
        self.updated_at = now or _utc_now()

    def assert_version(self, expected_version: int) -> None:
        if self.version != expected_version:
            raise ConcurrencyError(
                f"product {self.id} version is {self.version}, expected {expected_version}"
            )


@dataclass(slots=True)
class SKU:
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

    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)

    @classmethod
    def create(
        cls,
        *,
        workspace_id: str,
        product_id: str,
        source_namespace: str,
        external_id: str,
        source_version: str | None,
        title: str,
        category_code: str,
        brand: str,
        attributes: dict[str, Any],
        expires_at: datetime | None,
        now: datetime | None = None,
    ) -> SKU:
        _validate_expiry(expires_at)
        created_at = now or _utc_now()
        return cls(
            id=new_uuid7(),
            workspace_id=workspace_id,
            product_id=product_id,
            source_namespace=source_namespace,
            external_id=external_id,
            source_version=source_version,
            title=title,
            category_code=category_code,
            brand=brand,
            attributes=attributes,
            expires_at=expires_at,
            version=1,
            created_at=created_at,
            updated_at=created_at,
        )

    def update(
        self,
        *,
        expected_version: int,
        source_version: str | None,
        title: str,
        category_code: str,
        brand: str,
        attributes: dict[str, Any],
        expires_at: datetime | None,
        now: datetime | None = None,
    ) -> None:
        self.assert_version(expected_version)
        _validate_expiry(expires_at)
        self.source_version = source_version
        self.title = title
        self.category_code = category_code
        self.brand = brand
        self.attributes = attributes
        self.expires_at = expires_at
        self.version += 1
        self.updated_at = now or _utc_now()

    def assert_version(self, expected_version: int) -> None:
        if self.version != expected_version:
            raise ConcurrencyError(
                f"sku {self.id} version is {self.version}, expected {expected_version}"
            )
