"""Control API dependency composition."""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from datetime import timedelta

from commercevision_application import (
    AssetDeletionPolicy,
    AssetRegistryApplicationService,
    AssetRetentionApplicationService,
    AssetRightsApplicationService,
    BrandProfileApplicationService,
    BrandProfileCursorCodec,
    CatalogApplicationService,
    DeadLetterOperatorService,
    ImageIndexStatusApplicationService,
    OperationApplicationService,
    ProductBriefApplicationService,
    ProductBriefPolicy,
    ProductBriefViewApplicationService,
    RetrievalApplicationService,
    ValidationDataTransferPolicy,
    VisionDataTransferPolicy,
    WorkflowApplicationService,
)
from commercevision_application.asset_cleanup_dispatch import UploadCleanupPolicy
from commercevision_application.asset_integrity import UploadIntegrityVerifier
from commercevision_application.asset_validation_dispatch import AssetValidationPolicy
from commercevision_contracts import Settings
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
    SqlAlchemyAssetUnitOfWork,
    SqlAlchemyBrandProfileUnitOfWork,
    SqlAlchemyCatalogUnitOfWork,
    SqlAlchemyImageIndexStatusQueries,
    SqlAlchemyOperationUnitOfWork,
    SqlAlchemyOperatorUnitOfWork,
    SqlAlchemyProductBriefUnitOfWork,
    SqlAlchemyProductBriefViewQueries,
    SqlAlchemyUnitOfWork,
    create_database,
    is_unit_of_work_active,
)

from .identity import PrincipalAccessPolicy, SignedTrustedPrincipalResolver
from .retrieval_runtime import build_retrieval

_PUBLIC_LOCAL_TRUST_KEY_PAIRS = frozenset(
    {
        (
            "local-web-gateway",
            "local-web-gateway-secret-change-before-production",
        ),
    }
)
_PUBLIC_LOCAL_TRUST_SECRETS = frozenset(secret for _, secret in _PUBLIC_LOCAL_TRUST_KEY_PAIRS)


def _is_production_safe_trust_pair(key_id: str | None, secret: str | None) -> bool:
    if key_id is None or secret is None or not key_id.strip() or not secret.strip():
        return False
    return (
        key_id,
        secret,
    ) not in _PUBLIC_LOCAL_TRUST_KEY_PAIRS and secret not in _PUBLIC_LOCAL_TRUST_SECRETS


class ApiTrustConfigurationError(RuntimeError):
    """Raised when the Control API cannot establish its inbound trust boundary."""


@dataclass(frozen=True, slots=True)
class ApiTrustKeyRing:
    """Validated key material shared by principal verification and cursor signing."""

    current_key_id: str | None
    current_secret: str | None = field(repr=False)
    previous_key_id: str | None
    previous_secret: str | None = field(repr=False)

    @classmethod
    def from_settings(cls, settings: Settings) -> ApiTrustKeyRing:
        current_secret = settings.trusted_principal_current_hmac_secret
        previous_secret = settings.trusted_principal_previous_hmac_secret
        key_ring = cls(
            current_key_id=settings.trusted_principal_current_key_id,
            current_secret=(
                current_secret.get_secret_value() if current_secret is not None else None
            ),
            previous_key_id=settings.trusted_principal_previous_key_id,
            previous_secret=(
                previous_secret.get_secret_value() if previous_secret is not None else None
            ),
        )
        if settings.environment == "production":
            if key_ring.current_key_id is None or key_ring.current_secret is None:
                raise ApiTrustConfigurationError(
                    "production Control API requires a current trusted-principal key"
                )
            configured_pairs = (
                (key_ring.current_key_id, key_ring.current_secret),
                (key_ring.previous_key_id, key_ring.previous_secret),
            )
            for key_id, secret in configured_pairs:
                if key_id is None and secret is None:
                    continue
                if not _is_production_safe_trust_pair(key_id, secret):
                    raise ApiTrustConfigurationError(
                        "production Control API trusted-principal key is not production-safe"
                    )
        return key_ring


@dataclass(slots=True)
class ApiContainer:
    database: Database
    assets: AssetRegistryApplicationService
    rights: AssetRightsApplicationService
    asset_retention: AssetRetentionApplicationService
    brand_profiles: BrandProfileApplicationService
    catalog: CatalogApplicationService
    operations: OperationApplicationService
    product_briefs: ProductBriefApplicationService
    product_brief_views: ProductBriefViewApplicationService
    dead_letters: DeadLetterOperatorService
    workflows: WorkflowApplicationService
    principal_resolver: SignedTrustedPrincipalResolver
    access_policy: PrincipalAccessPolicy
    object_storage_readiness: ObjectStorageReadiness
    image_index_status: ImageIndexStatusApplicationService
    retrieval: RetrievalApplicationService
    retrieval_runs: MySqlRetrievalRunStore
    retrieval_previews: MySqlRetrievalPreviewService
    retrieval_closeables: tuple[object, ...]

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        trust_key_ring: ApiTrustKeyRing,
    ) -> ApiContainer:
        database = create_database(settings)

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(database.session_factory)

        def catalog_uow_factory() -> SqlAlchemyCatalogUnitOfWork:
            return SqlAlchemyCatalogUnitOfWork(database.session_factory)

        def asset_uow_factory() -> SqlAlchemyAssetUnitOfWork:
            return SqlAlchemyAssetUnitOfWork(database.session_factory)

        def brand_profile_uow_factory() -> SqlAlchemyBrandProfileUnitOfWork:
            return SqlAlchemyBrandProfileUnitOfWork(database.session_factory)

        def operation_uow_factory() -> SqlAlchemyOperationUnitOfWork:
            return SqlAlchemyOperationUnitOfWork(database.session_factory)

        def operator_uow_factory() -> SqlAlchemyOperatorUnitOfWork:
            return SqlAlchemyOperatorUnitOfWork(database.session_factory)

        def product_brief_uow_factory() -> SqlAlchemyProductBriefUnitOfWork:
            return SqlAlchemyProductBriefUnitOfWork(database.session_factory)

        access_policy = PrincipalAccessPolicy()
        principal_resolver = SignedTrustedPrincipalResolver(
            current_key_id=trust_key_ring.current_key_id,
            current_secret=trust_key_ring.current_secret,
            previous_key_id=trust_key_ring.previous_key_id,
            previous_secret=trust_key_ring.previous_secret,
            max_age_seconds=settings.trusted_principal_max_age_seconds,
            future_skew_seconds=settings.trusted_principal_future_skew_seconds,
        )
        brand_profile_cursor_codec = BrandProfileCursorCodec(
            current_key_id=trust_key_ring.current_key_id,
            current_secret=trust_key_ring.current_secret,
            previous_key_id=trust_key_ring.previous_key_id,
            previous_secret=trust_key_ring.previous_secret,
            max_age_seconds=settings.brand_profile_cursor_max_age_seconds,
            future_skew_seconds=settings.brand_profile_cursor_future_skew_seconds,
        )
        object_storage = build_object_storage(settings)
        built_retrieval = build_retrieval(
            settings=settings,
            database=database,
            storage=object_storage,
        )
        integrity_verifier = UploadIntegrityVerifier(
            storage=object_storage,
            transaction_active=is_unit_of_work_active,
            maximum_bytes=settings.upload_max_bytes,
            maximum_dimension=settings.upload_max_image_dimension,
            maximum_pixels=settings.upload_max_image_pixels,
            maximum_frames=settings.upload_max_image_frames,
            maximum_metadata_bytes=settings.upload_max_metadata_bytes,
            maximum_lora_bytes=settings.upload_max_lora_bytes,
            maximum_prompt_template_bytes=settings.upload_max_prompt_template_bytes,
            maximum_model_configuration_bytes=(settings.upload_max_model_configuration_bytes),
        )
        deletion_policy = AssetDeletionPolicy(
            max_attempts=settings.asset_deletion_max_attempts,
            max_reconciliation_attempts=(settings.asset_deletion_max_reconciliation_attempts),
            execution_max_elapsed=timedelta(seconds=settings.operation_retry_max_elapsed_seconds),
        )
        return cls(
            database=database,
            assets=AssetRegistryApplicationService(
                uow_factory=asset_uow_factory,
                storage=object_storage,
                verifier=integrity_verifier,
                quarantine_bucket=settings.object_store_quarantine_bucket,
                task_bucket=settings.object_store_task_bucket,
                foundation_bucket=settings.object_store_foundation_bucket,
                upload_session_lifetime=timedelta(seconds=settings.upload_session_expiry_seconds),
                finalize_lease_duration=timedelta(seconds=settings.upload_finalize_lease_seconds),
                upload_policy_version=settings.upload_policy_version,
                integrity_policy_version=settings.upload_integrity_policy_version,
                maximum_bytes=settings.upload_max_bytes,
                maximum_lora_bytes=settings.upload_max_lora_bytes,
                maximum_prompt_template_bytes=(settings.upload_max_prompt_template_bytes),
                maximum_model_configuration_bytes=(settings.upload_max_model_configuration_bytes),
                validation_policy=AssetValidationPolicy(
                    policy_version=settings.asset_validation_policy_version,
                    max_attempts=settings.asset_validation_max_attempts,
                    execution_max_elapsed=timedelta(
                        seconds=settings.operation_retry_max_elapsed_seconds
                    ),
                ),
                validation_transfer_policy=ValidationDataTransferPolicy.from_settings(settings),
                cleanup_policy=UploadCleanupPolicy(
                    max_attempts=settings.upload_cleanup_max_attempts,
                    max_reconciliation_attempts=(settings.upload_cleanup_reconcile_max_attempts),
                    execution_max_elapsed=timedelta(
                        seconds=settings.operation_retry_max_elapsed_seconds
                    ),
                    presign_replay_grace=timedelta(
                        seconds=settings.upload_cleanup_presign_grace_seconds
                    ),
                    reconciliation_horizon=timedelta(
                        seconds=settings.upload_cleanup_reconcile_horizon_seconds
                    ),
                ),
                lease_owner=f"{socket.gethostname()}:{settings.service_name}",
            ),
            rights=AssetRightsApplicationService(
                uow_factory=asset_uow_factory,
                deletion_policy=deletion_policy,
            ),
            asset_retention=AssetRetentionApplicationService(
                uow_factory=asset_uow_factory,
                policy=deletion_policy,
            ),
            brand_profiles=BrandProfileApplicationService(
                uow_factory=brand_profile_uow_factory,
                cursor_codec=brand_profile_cursor_codec,
            ),
            catalog=CatalogApplicationService(uow_factory=catalog_uow_factory),
            operations=OperationApplicationService(
                uow_factory=operation_uow_factory,
                execution_max_elapsed=timedelta(
                    seconds=settings.operation_retry_max_elapsed_seconds
                ),
            ),
            product_briefs=ProductBriefApplicationService(
                uow_factory=product_brief_uow_factory,
                policy=ProductBriefPolicy.from_settings(settings),
                transfer_policy=VisionDataTransferPolicy.from_settings(settings),
                observer=ProductBriefTelemetry(),
            ),
            product_brief_views=ProductBriefViewApplicationService(
                queries=SqlAlchemyProductBriefViewQueries(database.session_factory),
            ),
            dead_letters=DeadLetterOperatorService(
                uow_factory=operator_uow_factory,
                access_policy=access_policy,
            ),
            workflows=WorkflowApplicationService(uow_factory=uow_factory),
            principal_resolver=principal_resolver,
            access_policy=access_policy,
            object_storage_readiness=object_storage,
            image_index_status=ImageIndexStatusApplicationService(
                SqlAlchemyImageIndexStatusQueries(database.session_factory)
            ),
            retrieval=built_retrieval.service,
            retrieval_runs=built_retrieval.runs,
            retrieval_previews=built_retrieval.previews,
            retrieval_closeables=built_retrieval.closeables,
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
            raise ExceptionGroup("Control API resource cleanup failed", failures)
