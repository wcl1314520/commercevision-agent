"""Product Catalog commands and queries."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from commercevision_contracts import (
    CatalogDeleteRequestV1,
    ProductCreateRequestV1,
    ProductListResponseV1,
    ProductResponseV1,
    ProductSummaryResponseV1,
    ProductUpdateRequestV1,
    SKUCreateRequestV1,
    SKUResponseV1,
    SKUUpdateRequestV1,
)
from commercevision_domain import (
    SKU,
    ConcurrencyError,
    NotFoundError,
    Product,
    validate_workspace_id,
)
from commercevision_domain.workflow.errors import IdempotencyConflictError

from .catalog_ports import CatalogUnitOfWorkFactory


def _canonical_hash(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _key_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _scope(operation: str, workspace_id: str, resource_id: str | None = None) -> str:
    workspace_hash = hashlib.sha256(workspace_id.encode()).hexdigest()[:16]
    suffix = f":{resource_id}" if resource_id else ""
    return f"catalog:{operation}:{workspace_hash}{suffix}"


def _encode_cursor(created_at: datetime, product_id: str) -> str:
    value = json.dumps(
        {"created_at": created_at.isoformat(), "id": product_id},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    if cursor is None:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded).decode())
        created_at = datetime.fromisoformat(value["created_at"])
        if created_at.tzinfo is None:
            raise ValueError
        return created_at, str(value["id"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid product cursor") from exc


def _sku_response(sku: SKU) -> SKUResponseV1:
    return SKUResponseV1(**asdict(sku))


def _product_summary(product: Product) -> ProductSummaryResponseV1:
    return ProductSummaryResponseV1(**asdict(product))


def _product_response(product: Product, skus: list[SKU] | None = None) -> ProductResponseV1:
    return ProductResponseV1(
        **asdict(product),
        skus=[_sku_response(sku) for sku in skus or []],
    )


class CatalogApplicationService:
    def __init__(self, *, uow_factory: CatalogUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def create_product(
        self,
        *,
        request: ProductCreateRequestV1,
        workspace_id: str,
        actor_id: str,
        idempotency_key: str,
        trace_id: str,
    ) -> ProductResponseV1:
        validate_workspace_id(workspace_id)
        scope = _scope("product-create", workspace_id)
        key_hash = _key_hash(idempotency_key)
        request_hash = _canonical_hash(request.model_dump(mode="json"))
        now = datetime.now(UTC)
        with self._uow_factory() as uow:
            record = self._claim_idempotency(
                uow=uow,
                scope=scope,
                key_hash=key_hash,
                request_hash=request_hash,
                now=now,
            )
            if record is not None:
                return self._product_replay(record)
            product = Product.create(workspace_id=workspace_id, now=now, **request.model_dump())
            uow.identities.reserve(
                workspace_id=workspace_id,
                source_namespace=product.source_namespace,
                external_id=product.external_id,
                owner_type="PRODUCT",
                owner_id=product.id,
                created_at=now,
            )
            uow.products.add(product)
            response = _product_response(product)
            uow.idempotency.complete(
                scope=scope,
                key_hash=key_hash,
                request_hash=request_hash,
                resource_type="product",
                resource_id=product.id,
                response_data=response.model_dump(mode="json"),
            )
            uow.audit.add(
                workspace_id=workspace_id,
                actor_type="USER",
                actor_id=actor_id,
                action="catalog.product.created",
                resource_type="product",
                resource_id=product.id,
                trace_id=trace_id,
                metadata={
                    "source_namespace": product.source_namespace,
                    "external_id": product.external_id,
                },
                created_at=now,
                expires_at=now + timedelta(days=180),
            )
            uow.commit()
        return response

    def update_product(
        self,
        *,
        product_id: str,
        request: ProductUpdateRequestV1,
        workspace_id: str,
        actor_id: str,
        idempotency_key: str,
        trace_id: str,
    ) -> ProductResponseV1:
        scope = _scope("product-update", workspace_id, product_id)
        key_hash = _key_hash(idempotency_key)
        request_hash = _canonical_hash(request.model_dump(mode="json"))
        now = datetime.now(UTC)
        with self._uow_factory() as uow:
            record = self._claim_idempotency(
                uow=uow,
                scope=scope,
                key_hash=key_hash,
                request_hash=request_hash,
                now=now,
            )
            if record is not None:
                return self._product_replay(record)
            product = uow.products.get(
                workspace_id=workspace_id,
                product_id=product_id,
                for_update=True,
            )
            if product is None:
                raise NotFoundError(f"product {product_id} was not found")
            product.update(now=now, **request.model_dump())
            uow.products.save(product)
            skus = uow.skus.list_for_product(
                workspace_id=workspace_id,
                product_id=product.id,
            )
            response = _product_response(product, skus)
            uow.idempotency.complete(
                scope=scope,
                key_hash=key_hash,
                request_hash=request_hash,
                resource_type="product",
                resource_id=product.id,
                response_data=response.model_dump(mode="json"),
            )
            uow.audit.add(
                workspace_id=workspace_id,
                actor_type="USER",
                actor_id=actor_id,
                action="catalog.product.updated",
                resource_type="product",
                resource_id=product.id,
                trace_id=trace_id,
                metadata={"version": product.version},
                created_at=now,
                expires_at=now + timedelta(days=180),
            )
            uow.commit()
        return response

    def delete_product(
        self,
        *,
        product_id: str,
        request: CatalogDeleteRequestV1,
        workspace_id: str,
        actor_id: str,
        idempotency_key: str,
        trace_id: str,
    ) -> None:
        scope = _scope("product-delete", workspace_id, product_id)
        key_hash = _key_hash(idempotency_key)
        request_hash = _canonical_hash(request.model_dump(mode="json"))
        now = datetime.now(UTC)
        with self._uow_factory() as uow:
            record = self._claim_idempotency(
                uow=uow,
                scope=scope,
                key_hash=key_hash,
                request_hash=request_hash,
                now=now,
            )
            if record is not None:
                return
            product = uow.products.get(
                workspace_id=workspace_id,
                product_id=product_id,
                for_update=True,
            )
            if product is None:
                raise NotFoundError(f"product {product_id} was not found")
            product.assert_version(request.expected_version)
            if uow.skus.list_for_product(
                workspace_id=workspace_id,
                product_id=product_id,
            ):
                raise ConcurrencyError("product cannot be deleted while it has SKUs")
            uow.products.delete(
                workspace_id=workspace_id,
                product_id=product_id,
                expected_version=request.expected_version,
            )
            uow.identities.release(
                workspace_id=workspace_id,
                source_namespace=product.source_namespace,
                external_id=product.external_id,
                owner_type="PRODUCT",
                owner_id=product.id,
            )
            uow.idempotency.complete(
                scope=scope,
                key_hash=key_hash,
                request_hash=request_hash,
                resource_type="product",
                resource_id=product_id,
                response_data={"status_code": 204},
            )
            uow.audit.add(
                workspace_id=workspace_id,
                actor_type="USER",
                actor_id=actor_id,
                action="catalog.product.deleted",
                resource_type="product",
                resource_id=product_id,
                trace_id=trace_id,
                metadata={"expected_version": request.expected_version},
                created_at=now,
                expires_at=now + timedelta(days=180),
            )
            uow.commit()

    def create_sku(
        self,
        *,
        product_id: str,
        request: SKUCreateRequestV1,
        workspace_id: str,
        actor_id: str,
        idempotency_key: str,
        trace_id: str,
    ) -> SKUResponseV1:
        scope = _scope("sku-create", workspace_id, product_id)
        key_hash = _key_hash(idempotency_key)
        request_hash = _canonical_hash(request.model_dump(mode="json"))
        now = datetime.now(UTC)
        with self._uow_factory() as uow:
            record = self._claim_idempotency(
                uow=uow,
                scope=scope,
                key_hash=key_hash,
                request_hash=request_hash,
                now=now,
            )
            if record is not None:
                return self._sku_replay(record)
            product = uow.products.get(
                workspace_id=workspace_id,
                product_id=product_id,
                for_update=True,
            )
            if product is None:
                raise NotFoundError(f"product {product_id} was not found")
            sku = SKU.create(
                workspace_id=workspace_id,
                product_id=product_id,
                now=now,
                **request.model_dump(),
            )
            uow.identities.reserve(
                workspace_id=workspace_id,
                source_namespace=sku.source_namespace,
                external_id=sku.external_id,
                owner_type="SKU",
                owner_id=sku.id,
                created_at=now,
            )
            uow.skus.add(sku)
            response = _sku_response(sku)
            uow.idempotency.complete(
                scope=scope,
                key_hash=key_hash,
                request_hash=request_hash,
                resource_type="sku",
                resource_id=sku.id,
                response_data=response.model_dump(mode="json"),
            )
            uow.audit.add(
                workspace_id=workspace_id,
                actor_type="USER",
                actor_id=actor_id,
                action="catalog.sku.created",
                resource_type="sku",
                resource_id=sku.id,
                trace_id=trace_id,
                metadata={"product_id": product.id},
                created_at=now,
                expires_at=now + timedelta(days=180),
            )
            uow.commit()
        return response

    def update_sku(
        self,
        *,
        product_id: str,
        sku_id: str,
        request: SKUUpdateRequestV1,
        workspace_id: str,
        actor_id: str,
        idempotency_key: str,
        trace_id: str,
    ) -> SKUResponseV1:
        scope = _scope("sku-update", workspace_id, sku_id)
        key_hash = _key_hash(idempotency_key)
        request_hash = _canonical_hash(request.model_dump(mode="json"))
        now = datetime.now(UTC)
        with self._uow_factory() as uow:
            record = self._claim_idempotency(
                uow=uow,
                scope=scope,
                key_hash=key_hash,
                request_hash=request_hash,
                now=now,
            )
            if record is not None:
                return self._sku_replay(record)
            product = uow.products.get(
                workspace_id=workspace_id,
                product_id=product_id,
            )
            if product is None:
                raise NotFoundError(f"product {product_id} was not found")
            sku = uow.skus.get(
                workspace_id=workspace_id,
                product_id=product_id,
                sku_id=sku_id,
                for_update=True,
            )
            if sku is None:
                raise NotFoundError(f"sku {sku_id} was not found")
            sku.update(now=now, **request.model_dump())
            uow.skus.save(sku)
            response = _sku_response(sku)
            uow.idempotency.complete(
                scope=scope,
                key_hash=key_hash,
                request_hash=request_hash,
                resource_type="sku",
                resource_id=sku.id,
                response_data=response.model_dump(mode="json"),
            )
            uow.audit.add(
                workspace_id=workspace_id,
                actor_type="USER",
                actor_id=actor_id,
                action="catalog.sku.updated",
                resource_type="sku",
                resource_id=sku.id,
                trace_id=trace_id,
                metadata={"product_id": product.id, "version": sku.version},
                created_at=now,
                expires_at=now + timedelta(days=180),
            )
            uow.commit()
        return response

    def delete_sku(
        self,
        *,
        product_id: str,
        sku_id: str,
        request: CatalogDeleteRequestV1,
        workspace_id: str,
        actor_id: str,
        idempotency_key: str,
        trace_id: str,
    ) -> None:
        scope = _scope("sku-delete", workspace_id, sku_id)
        key_hash = _key_hash(idempotency_key)
        request_hash = _canonical_hash(request.model_dump(mode="json"))
        now = datetime.now(UTC)
        with self._uow_factory() as uow:
            record = self._claim_idempotency(
                uow=uow,
                scope=scope,
                key_hash=key_hash,
                request_hash=request_hash,
                now=now,
            )
            if record is not None:
                return
            product = uow.products.get(
                workspace_id=workspace_id,
                product_id=product_id,
            )
            if product is None:
                raise NotFoundError(f"product {product_id} was not found")
            sku = uow.skus.get(
                workspace_id=workspace_id,
                product_id=product_id,
                sku_id=sku_id,
                for_update=True,
            )
            if sku is None:
                raise NotFoundError(f"sku {sku_id} was not found")
            sku.assert_version(request.expected_version)
            uow.skus.delete(
                workspace_id=workspace_id,
                product_id=product_id,
                sku_id=sku_id,
                expected_version=request.expected_version,
            )
            uow.identities.release(
                workspace_id=workspace_id,
                source_namespace=sku.source_namespace,
                external_id=sku.external_id,
                owner_type="SKU",
                owner_id=sku.id,
            )
            uow.idempotency.complete(
                scope=scope,
                key_hash=key_hash,
                request_hash=request_hash,
                resource_type="sku",
                resource_id=sku_id,
                response_data={"status_code": 204},
            )
            uow.audit.add(
                workspace_id=workspace_id,
                actor_type="USER",
                actor_id=actor_id,
                action="catalog.sku.deleted",
                resource_type="sku",
                resource_id=sku_id,
                trace_id=trace_id,
                metadata={"product_id": product_id, "expected_version": request.expected_version},
                created_at=now,
                expires_at=now + timedelta(days=180),
            )
            uow.commit()

    def get_product(self, *, workspace_id: str, product_id: str) -> ProductResponseV1:
        with self._uow_factory() as uow:
            product = uow.products.get(workspace_id=workspace_id, product_id=product_id)
            if product is None:
                raise NotFoundError(f"product {product_id} was not found")
            skus = uow.skus.list_for_product(workspace_id=workspace_id, product_id=product_id)
        return _product_response(product, skus)

    def list_products(
        self,
        *,
        workspace_id: str,
        limit: int,
        cursor: str | None,
    ) -> ProductListResponseV1:
        bounded_limit = min(max(limit, 1), 100)
        with self._uow_factory() as uow:
            products = uow.products.list(
                workspace_id=workspace_id,
                limit=bounded_limit + 1,
                cursor=_decode_cursor(cursor),
            )
        has_more = len(products) > bounded_limit
        products = products[:bounded_limit]
        return ProductListResponseV1(
            items=[_product_summary(product) for product in products],
            next_cursor=(
                _encode_cursor(products[-1].created_at, products[-1].id)
                if has_more and products
                else None
            ),
        )

    @staticmethod
    def _claim_idempotency(
        *,
        uow: Any,
        scope: str,
        key_hash: str,
        request_hash: str,
        now: datetime,
    ) -> Any | None:
        record = uow.idempotency.claim(
            scope=scope,
            key_hash=key_hash,
            request_hash=request_hash,
            expires_at=now + timedelta(days=30),
        )
        if record.request_hash != request_hash:
            raise IdempotencyConflictError(
                "idempotency key was already used with a different request"
            )
        if record.status == "COMPLETED":
            return record
        if record.status != "PENDING":
            raise ConcurrencyError("idempotency record has an unsupported status")
        return None

    @staticmethod
    def _product_replay(record: Any) -> ProductResponseV1:
        if record.resource_type != "product" or not isinstance(record.response_data, dict):
            raise ConcurrencyError("idempotency record does not contain a product response")
        return ProductResponseV1.model_validate(record.response_data)

    @staticmethod
    def _sku_replay(record: Any) -> SKUResponseV1:
        if record.resource_type != "sku" or not isinstance(record.response_data, dict):
            raise ConcurrencyError("idempotency record does not contain a SKU response")
        return SKUResponseV1.model_validate(record.response_data)
