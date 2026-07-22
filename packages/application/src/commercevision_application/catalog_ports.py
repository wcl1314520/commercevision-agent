"""Typed seams used by the Product Catalog application module."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from commercevision_domain import SKU, Product


class ProductRepositoryPort(Protocol):
    def add(self, product: Product) -> None: ...
    def get(
        self,
        *,
        workspace_id: str,
        product_id: str,
        for_update: bool = False,
    ) -> Product | None: ...
    def get_by_external_identity(
        self,
        *,
        workspace_id: str,
        source_namespace: str,
        external_id: str,
    ) -> Product | None: ...
    def list(
        self,
        *,
        workspace_id: str,
        limit: int,
        cursor: tuple[datetime, str] | None,
    ) -> list[Product]: ...
    def save(self, product: Product) -> None: ...
    def delete(self, *, workspace_id: str, product_id: str, expected_version: int) -> None: ...


class SKURepositoryPort(Protocol):
    def add(self, sku: SKU) -> None: ...
    def get(
        self,
        *,
        workspace_id: str,
        product_id: str,
        sku_id: str,
        for_update: bool = False,
    ) -> SKU | None: ...
    def get_by_external_identity(
        self,
        *,
        workspace_id: str,
        source_namespace: str,
        external_id: str,
    ) -> SKU | None: ...
    def list_for_product(
        self,
        *,
        workspace_id: str,
        product_id: str,
        limit: int = 100,
    ) -> list[SKU]: ...
    def save(self, sku: SKU) -> None: ...
    def delete(
        self,
        *,
        workspace_id: str,
        product_id: str,
        sku_id: str,
        expected_version: int,
    ) -> None: ...


class CatalogIdempotencyPort(Protocol):
    def get(self, scope: str, key_hash: str, *, for_update: bool = False) -> Any | None: ...
    def claim(
        self,
        *,
        scope: str,
        key_hash: str,
        request_hash: str,
        expires_at: datetime,
    ) -> Any: ...
    def complete(
        self,
        *,
        scope: str,
        key_hash: str,
        request_hash: str,
        resource_type: str,
        resource_id: str,
        response_data: dict[str, Any],
    ) -> None: ...
    def add(
        self,
        *,
        scope: str,
        key_hash: str,
        request_hash: str,
        resource_type: str,
        resource_id: str,
        response_data: dict[str, Any] | None,
        expires_at: datetime,
    ) -> None: ...


class CatalogIdentityPort(Protocol):
    def reserve(
        self,
        *,
        workspace_id: str,
        source_namespace: str,
        external_id: str,
        owner_type: str,
        owner_id: str,
        created_at: datetime,
    ) -> None: ...
    def release(
        self,
        *,
        workspace_id: str,
        source_namespace: str,
        external_id: str,
        owner_type: str,
        owner_id: str,
    ) -> None: ...


class CatalogAuditPort(Protocol):
    def add(
        self,
        *,
        workspace_id: str,
        actor_type: str,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        trace_id: str,
        metadata: dict[str, Any],
        created_at: datetime,
        expires_at: datetime,
    ) -> None: ...


class CatalogUnitOfWorkPort(Protocol):
    products: ProductRepositoryPort
    skus: SKURepositoryPort
    identities: CatalogIdentityPort
    idempotency: CatalogIdempotencyPort
    audit: CatalogAuditPort

    def __enter__(self) -> CatalogUnitOfWorkPort: ...
    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None: ...
    def commit(self) -> None: ...


CatalogUnitOfWorkFactory = Callable[[], CatalogUnitOfWorkPort]
