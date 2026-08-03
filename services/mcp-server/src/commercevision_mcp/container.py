"""MCP composition root exposing only application-service ports to tools."""

from __future__ import annotations

from dataclasses import dataclass

from commercevision_application import (
    BrandProfileApplicationService,
    BrandProfileCursorCodec,
    CatalogApplicationService,
    ProductBriefApplicationService,
    ProductBriefPolicy,
    RetrievalApplicationService,
    VisionDataTransferPolicy,
)
from commercevision_bootstrap import build_retrieval
from commercevision_contracts import Settings
from commercevision_domain import StorageLocationClass
from commercevision_object_storage import (
    ObjectStorageReadiness,
    build_object_storage,
    close_object_storage,
)
from commercevision_observability import ProductBriefTelemetry
from commercevision_persistence import (
    Database,
    MySqlRetrievalPreviewService,
    MySqlRetrievalRunStore,
    SqlAlchemyBrandProfileUnitOfWork,
    SqlAlchemyCatalogUnitOfWork,
    SqlAlchemyProductBriefUnitOfWork,
    create_database,
)
from sqlalchemy import text

_PUBLIC_LOCAL_TRUST_SECRET = "local-web-gateway-secret-change-before-production"


class McpTrustConfigurationError(RuntimeError):
    """Raised when the MCP server cannot establish its inbound trust boundary."""


def _trust_material(settings: Settings) -> tuple[str | None, str | None, str | None, str | None]:
    current = settings.trusted_principal_current_hmac_secret
    previous = settings.trusted_principal_previous_hmac_secret
    material = (
        settings.trusted_principal_current_key_id,
        current.get_secret_value() if current is not None else None,
        settings.trusted_principal_previous_key_id,
        previous.get_secret_value() if previous is not None else None,
    )
    if settings.environment == "production":
        current_key_id, current_secret, _, previous_secret = material
        if current_key_id is None or current_secret is None:
            raise McpTrustConfigurationError(
                "production MCP server requires a current trusted-principal key"
            )
        if _PUBLIC_LOCAL_TRUST_SECRET in {current_secret, previous_secret}:
            raise McpTrustConfigurationError(
                "production MCP server trusted-principal key is not production-safe"
            )
    return material


@dataclass(slots=True)
class McpContainer:
    database: Database
    object_storage_readiness: ObjectStorageReadiness
    catalog: CatalogApplicationService
    product_briefs: ProductBriefApplicationService
    brand_profiles: BrandProfileApplicationService
    retrieval: RetrievalApplicationService
    retrieval_runs: MySqlRetrievalRunStore
    retrieval_previews: MySqlRetrievalPreviewService
    retrieval_closeables: tuple[object, ...]
    retrieval_policy_version: str

    @classmethod
    def build(cls, settings: Settings) -> McpContainer:
        current_key_id, current_secret, previous_key_id, previous_secret = _trust_material(settings)
        database = create_database(settings)
        storage = None
        retrieval_closeables: tuple[object, ...] = ()
        try:
            storage = build_object_storage(settings)
            built_retrieval = build_retrieval(
                settings=settings,
                database=database,
                storage=storage,
            )
            retrieval_closeables = built_retrieval.closeables

            def catalog_uow_factory() -> SqlAlchemyCatalogUnitOfWork:
                return SqlAlchemyCatalogUnitOfWork(database.session_factory)

            def product_brief_uow_factory() -> SqlAlchemyProductBriefUnitOfWork:
                return SqlAlchemyProductBriefUnitOfWork(database.session_factory)

            def brand_profile_uow_factory() -> SqlAlchemyBrandProfileUnitOfWork:
                return SqlAlchemyBrandProfileUnitOfWork(database.session_factory)

            cursor_codec = BrandProfileCursorCodec(
                current_key_id=current_key_id,
                current_secret=current_secret,
                previous_key_id=previous_key_id,
                previous_secret=previous_secret,
                max_age_seconds=settings.brand_profile_cursor_max_age_seconds,
                future_skew_seconds=settings.brand_profile_cursor_future_skew_seconds,
            )
            return cls(
                database=database,
                object_storage_readiness=storage,
                catalog=CatalogApplicationService(uow_factory=catalog_uow_factory),
                product_briefs=ProductBriefApplicationService(
                    uow_factory=product_brief_uow_factory,
                    policy=ProductBriefPolicy.from_settings(settings),
                    transfer_policy=VisionDataTransferPolicy.from_settings(settings),
                    observer=ProductBriefTelemetry(),
                ),
                brand_profiles=BrandProfileApplicationService(
                    uow_factory=brand_profile_uow_factory,
                    cursor_codec=cursor_codec,
                ),
                retrieval=built_retrieval.service,
                retrieval_runs=built_retrieval.runs,
                retrieval_previews=built_retrieval.previews,
                retrieval_closeables=built_retrieval.closeables,
                retrieval_policy_version=settings.retrieval_policy_version,
            )
        except Exception as startup_error:
            failures: list[Exception] = [startup_error]
            for resource in reversed(retrieval_closeables):
                close = getattr(resource, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception as exc:
                        failures.append(exc)
            if storage is not None:
                try:
                    close_object_storage(storage)
                except Exception as exc:
                    failures.append(exc)
            try:
                database.dispose()
            except Exception as exc:
                failures.append(exc)
            if len(failures) > 1:
                raise ExceptionGroup("MCP server startup and cleanup failed", failures) from None
            raise

    def assert_database_ready(self) -> None:
        with self.database.engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()

    def assert_object_storage_ready(self) -> None:
        self.object_storage_readiness.assert_ready(
            (StorageLocationClass.TASK, StorageLocationClass.FOUNDATION)
        )

    def close(self) -> None:
        failures: list[Exception] = []
        for resource in reversed(self.retrieval_closeables):
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    failures.append(exc)
        try:
            close_object_storage(self.object_storage_readiness)
        except Exception as exc:
            failures.append(exc)
        try:
            self.database.dispose()
        except Exception as exc:
            failures.append(exc)
        if failures:
            raise ExceptionGroup("MCP server resource cleanup failed", failures)
