"""MySQL adapters for the Product Catalog seam."""

from __future__ import annotations

from datetime import datetime
from types import TracebackType

from commercevision_domain import (
    SKU,
    ConcurrencyError,
    DuplicateExternalIdentifierError,
    Product,
    UniqueConstraintError,
)
from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .database import enter_unit_of_work, exit_unit_of_work
from .integrity import (
    classify_database_error,
    database_constraint_name,
    execute_with_integrity_classification,
)
from .models import CatalogExternalIdentityModel, ProductModel, SKUModel
from .repositories import AuditRepository, IdempotencyRepository

_CATALOG_EXTERNAL_IDENTITY_CONSTRAINTS = {
    "catalog_external_identities.primary",
    "pk_catalog_external_identity",
    "uq_products_external_identity",
    "uq_skus_external_identity",
}


def _is_external_identity_constraint(constraint_name: str) -> bool:
    return any(
        constraint_name == expected or constraint_name.endswith(f".{expected}")
        for expected in _CATALOG_EXTERNAL_IDENTITY_CONSTRAINTS
    )


def _product_from_model(model: ProductModel) -> Product:
    return Product(
        id=model.id,
        workspace_id=model.workspace_id,
        source_namespace=model.source_namespace,
        external_id=model.external_id,
        source_version=model.source_version,
        title=model.title,
        category_code=model.category_code,
        brand=model.brand,
        attributes=dict(model.attributes_json),
        expires_at=model.expires_at,
        version=model.version,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _sku_from_model(model: SKUModel) -> SKU:
    return SKU(
        id=model.id,
        workspace_id=model.workspace_id,
        product_id=model.product_id,
        source_namespace=model.source_namespace,
        external_id=model.external_id,
        source_version=model.source_version,
        title=model.title,
        category_code=model.category_code,
        brand=model.brand,
        attributes=dict(model.attributes_json),
        expires_at=model.expires_at,
        version=model.version,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


class ProductRepository:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._loaded_versions: dict[str, int] = {}

    def add(self, product: Product) -> None:
        self._session.add(
            ProductModel(
                id=product.id,
                workspace_id=product.workspace_id,
                source_namespace=product.source_namespace,
                external_id=product.external_id,
                source_version=product.source_version,
                title=product.title,
                category_code=product.category_code,
                brand=product.brand,
                attributes_json=product.attributes,
                expires_at=product.expires_at,
                version=product.version,
                created_at=product.created_at,
                updated_at=product.updated_at,
            )
        )
        self._loaded_versions[product.id] = product.version

    def get(
        self,
        *,
        workspace_id: str,
        product_id: str,
        for_update: bool = False,
    ) -> Product | None:
        statement = select(ProductModel).where(
            ProductModel.workspace_id == workspace_id,
            ProductModel.id == product_id,
        )
        if for_update:
            statement = statement.with_for_update()
        model = self._session.scalar(statement)
        if model is None:
            return None
        self._loaded_versions[model.id] = model.version
        return _product_from_model(model)

    def get_by_external_identity(
        self,
        *,
        workspace_id: str,
        source_namespace: str,
        external_id: str,
    ) -> Product | None:
        model = self._session.scalar(
            select(ProductModel).where(
                ProductModel.workspace_id == workspace_id,
                ProductModel.source_namespace == source_namespace,
                ProductModel.external_id == external_id,
            )
        )
        if model is None:
            return None
        self._loaded_versions[model.id] = model.version
        return _product_from_model(model)

    def list(
        self,
        *,
        workspace_id: str,
        limit: int,
        cursor: tuple[datetime, str] | None,
    ) -> list[Product]:
        statement = (
            select(ProductModel)
            .where(ProductModel.workspace_id == workspace_id)
            .order_by(ProductModel.created_at.desc(), ProductModel.id.desc())
            .limit(limit)
        )
        if cursor is not None:
            created_at, product_id = cursor
            statement = statement.where(
                or_(
                    ProductModel.created_at < created_at,
                    and_(
                        ProductModel.created_at == created_at,
                        ProductModel.id < product_id,
                    ),
                )
            )
        models = list(self._session.scalars(statement))
        for model in models:
            self._loaded_versions[model.id] = model.version
        return [_product_from_model(model) for model in models]

    def save(self, product: Product) -> None:
        original_version = self._loaded_versions.get(product.id)
        if original_version is None:
            raise ConcurrencyError(f"product {product.id} was not loaded by this transaction")
        result = execute_with_integrity_classification(
            self._session,
            update(ProductModel)
            .where(
                ProductModel.workspace_id == product.workspace_id,
                ProductModel.id == product.id,
                ProductModel.version == original_version,
            )
            .values(
                source_version=product.source_version,
                title=product.title,
                category_code=product.category_code,
                brand=product.brand,
                attributes_json=product.attributes,
                expires_at=product.expires_at,
                version=product.version,
                updated_at=product.updated_at,
            ),
        )
        if result.rowcount != 1:
            raise ConcurrencyError(f"product {product.id} was concurrently modified")
        self._loaded_versions[product.id] = product.version

    def delete(self, *, workspace_id: str, product_id: str, expected_version: int) -> None:
        result = execute_with_integrity_classification(
            self._session,
            delete(ProductModel).where(
                ProductModel.workspace_id == workspace_id,
                ProductModel.id == product_id,
                ProductModel.version == expected_version,
            ),
        )
        if result.rowcount != 1:
            raise ConcurrencyError(f"product {product_id} was concurrently modified")


class SKURepository:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._loaded_versions: dict[str, int] = {}

    def add(self, sku: SKU) -> None:
        self._session.add(
            SKUModel(
                id=sku.id,
                workspace_id=sku.workspace_id,
                product_id=sku.product_id,
                source_namespace=sku.source_namespace,
                external_id=sku.external_id,
                source_version=sku.source_version,
                title=sku.title,
                category_code=sku.category_code,
                brand=sku.brand,
                attributes_json=sku.attributes,
                expires_at=sku.expires_at,
                version=sku.version,
                created_at=sku.created_at,
                updated_at=sku.updated_at,
            )
        )
        self._loaded_versions[sku.id] = sku.version

    def get(
        self,
        *,
        workspace_id: str,
        product_id: str,
        sku_id: str,
        for_update: bool = False,
    ) -> SKU | None:
        statement = select(SKUModel).where(
            SKUModel.workspace_id == workspace_id,
            SKUModel.product_id == product_id,
            SKUModel.id == sku_id,
        )
        if for_update:
            statement = statement.with_for_update()
        model = self._session.scalar(statement)
        if model is None:
            return None
        self._loaded_versions[model.id] = model.version
        return _sku_from_model(model)

    def get_by_external_identity(
        self,
        *,
        workspace_id: str,
        source_namespace: str,
        external_id: str,
    ) -> SKU | None:
        model = self._session.scalar(
            select(SKUModel).where(
                SKUModel.workspace_id == workspace_id,
                SKUModel.source_namespace == source_namespace,
                SKUModel.external_id == external_id,
            )
        )
        if model is None:
            return None
        self._loaded_versions[model.id] = model.version
        return _sku_from_model(model)

    def list_for_product(
        self,
        *,
        workspace_id: str,
        product_id: str,
        limit: int = 100,
    ) -> list[SKU]:
        models = list(
            self._session.scalars(
                select(SKUModel)
                .where(
                    SKUModel.workspace_id == workspace_id,
                    SKUModel.product_id == product_id,
                )
                .order_by(SKUModel.created_at, SKUModel.id)
                .limit(limit)
            )
        )
        for model in models:
            self._loaded_versions[model.id] = model.version
        return [_sku_from_model(model) for model in models]

    def save(self, sku: SKU) -> None:
        original_version = self._loaded_versions.get(sku.id)
        if original_version is None:
            raise ConcurrencyError(f"sku {sku.id} was not loaded by this transaction")
        result = execute_with_integrity_classification(
            self._session,
            update(SKUModel)
            .where(
                SKUModel.workspace_id == sku.workspace_id,
                SKUModel.product_id == sku.product_id,
                SKUModel.id == sku.id,
                SKUModel.version == original_version,
            )
            .values(
                source_version=sku.source_version,
                title=sku.title,
                category_code=sku.category_code,
                brand=sku.brand,
                attributes_json=sku.attributes,
                expires_at=sku.expires_at,
                version=sku.version,
                updated_at=sku.updated_at,
            ),
        )
        if result.rowcount != 1:
            raise ConcurrencyError(f"sku {sku.id} was concurrently modified")
        self._loaded_versions[sku.id] = sku.version

    def delete(
        self,
        *,
        workspace_id: str,
        product_id: str,
        sku_id: str,
        expected_version: int,
    ) -> None:
        result = execute_with_integrity_classification(
            self._session,
            delete(SKUModel).where(
                SKUModel.workspace_id == workspace_id,
                SKUModel.product_id == product_id,
                SKUModel.id == sku_id,
                SKUModel.version == expected_version,
            ),
        )
        if result.rowcount != 1:
            raise ConcurrencyError(f"sku {sku_id} was concurrently modified")


class CatalogIdentityRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def reserve(
        self,
        *,
        workspace_id: str,
        source_namespace: str,
        external_id: str,
        owner_type: str,
        owner_id: str,
        created_at: datetime,
    ) -> None:
        existing = self._session.scalar(
            select(CatalogExternalIdentityModel).where(
                CatalogExternalIdentityModel.workspace_id == workspace_id,
                CatalogExternalIdentityModel.source_namespace == source_namespace,
                CatalogExternalIdentityModel.external_id == external_id,
            )
        )
        if existing is not None:
            raise DuplicateExternalIdentifierError(
                "external identifier already exists in this workspace and source namespace"
            )
        self._session.add(
            CatalogExternalIdentityModel(
                workspace_id=workspace_id,
                source_namespace=source_namespace,
                external_id=external_id,
                owner_type=owner_type,
                owner_id=owner_id,
                created_at=created_at,
            )
        )

    def release(
        self,
        *,
        workspace_id: str,
        source_namespace: str,
        external_id: str,
        owner_type: str,
        owner_id: str,
    ) -> None:
        result = execute_with_integrity_classification(
            self._session,
            delete(CatalogExternalIdentityModel).where(
                CatalogExternalIdentityModel.workspace_id == workspace_id,
                CatalogExternalIdentityModel.source_namespace == source_namespace,
                CatalogExternalIdentityModel.external_id == external_id,
                CatalogExternalIdentityModel.owner_type == owner_type,
                CatalogExternalIdentityModel.owner_id == owner_id,
            ),
        )
        if result.rowcount != 1:
            raise ConcurrencyError("catalog external identity reservation was not found")


class SqlAlchemyCatalogUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._depth_token: object | None = None
        self._committed = False

    def __enter__(self) -> SqlAlchemyCatalogUnitOfWork:
        self._session = self._session_factory()
        self._depth_token = enter_unit_of_work()
        self.products = ProductRepository(self._session)
        self.skus = SKURepository(self._session)
        self.identities = CatalogIdentityRepository(self._session)
        self.idempotency = IdempotencyRepository(self._session)
        self.audit = AuditRepository(self._session)
        return self

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("catalog unit of work is not active")
        try:
            self._session.commit()
        except DBAPIError as exc:
            self._session.rollback()
            classified = classify_database_error(exc)
            if isinstance(classified, UniqueConstraintError) and (
                _is_external_identity_constraint(database_constraint_name(exc))
            ):
                raise DuplicateExternalIdentifierError(
                    "external identifier already exists in this workspace and source namespace"
                ) from exc
            if classified is None:
                raise
            raise classified from exc
        self._committed = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if self._session is not None and (exc_type is not None or not self._committed):
                self._session.rollback()
        finally:
            if self._session is not None:
                self._session.close()
            if self._depth_token is not None:
                exit_unit_of_work(self._depth_token)
