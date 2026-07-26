"""Control API dependency composition."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from datetime import timedelta

from commercevision_application import (
    AssetRegistryApplicationService,
    CatalogApplicationService,
    DeadLetterOperatorService,
    OperationApplicationService,
    ValidationDataTransferPolicy,
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
from commercevision_persistence import (
    Database,
    SqlAlchemyAssetUnitOfWork,
    SqlAlchemyCatalogUnitOfWork,
    SqlAlchemyOperationUnitOfWork,
    SqlAlchemyOperatorUnitOfWork,
    SqlAlchemyUnitOfWork,
    create_database,
    is_unit_of_work_active,
)

from .identity import PrincipalAccessPolicy, SignedTrustedPrincipalResolver


@dataclass(slots=True)
class ApiContainer:
    database: Database
    assets: AssetRegistryApplicationService
    catalog: CatalogApplicationService
    operations: OperationApplicationService
    dead_letters: DeadLetterOperatorService
    workflows: WorkflowApplicationService
    principal_resolver: SignedTrustedPrincipalResolver
    access_policy: PrincipalAccessPolicy
    object_storage_readiness: ObjectStorageReadiness

    @classmethod
    def build(cls, settings: Settings) -> ApiContainer:
        database = create_database(settings)

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(database.session_factory)

        def catalog_uow_factory() -> SqlAlchemyCatalogUnitOfWork:
            return SqlAlchemyCatalogUnitOfWork(database.session_factory)

        def asset_uow_factory() -> SqlAlchemyAssetUnitOfWork:
            return SqlAlchemyAssetUnitOfWork(database.session_factory)

        def operation_uow_factory() -> SqlAlchemyOperationUnitOfWork:
            return SqlAlchemyOperationUnitOfWork(database.session_factory)

        def operator_uow_factory() -> SqlAlchemyOperatorUnitOfWork:
            return SqlAlchemyOperatorUnitOfWork(database.session_factory)

        access_policy = PrincipalAccessPolicy()
        current_secret = settings.trusted_principal_current_hmac_secret
        previous_secret = settings.trusted_principal_previous_hmac_secret
        principal_resolver = SignedTrustedPrincipalResolver(
            current_key_id=settings.trusted_principal_current_key_id,
            current_secret=(
                current_secret.get_secret_value() if current_secret is not None else None
            ),
            previous_key_id=settings.trusted_principal_previous_key_id,
            previous_secret=(
                previous_secret.get_secret_value() if previous_secret is not None else None
            ),
            max_age_seconds=settings.trusted_principal_max_age_seconds,
            future_skew_seconds=settings.trusted_principal_future_skew_seconds,
        )
        object_storage = build_object_storage(settings)
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
            catalog=CatalogApplicationService(uow_factory=catalog_uow_factory),
            operations=OperationApplicationService(
                uow_factory=operation_uow_factory,
                execution_max_elapsed=timedelta(
                    seconds=settings.operation_retry_max_elapsed_seconds
                ),
            ),
            dead_letters=DeadLetterOperatorService(
                uow_factory=operator_uow_factory,
                access_policy=access_policy,
            ),
            workflows=WorkflowApplicationService(uow_factory=uow_factory),
            principal_resolver=principal_resolver,
            access_policy=access_policy,
            object_storage_readiness=object_storage,
        )

    def close(self) -> None:
        close_object_storage(self.object_storage_readiness)
        self.database.dispose()
