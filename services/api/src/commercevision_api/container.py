"""Control API dependency composition."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from commercevision_application import (
    CatalogApplicationService,
    DeadLetterOperatorService,
    OperationApplicationService,
    WorkflowApplicationService,
)
from commercevision_contracts import Settings
from commercevision_persistence import (
    Database,
    SqlAlchemyCatalogUnitOfWork,
    SqlAlchemyOperationUnitOfWork,
    SqlAlchemyOperatorUnitOfWork,
    SqlAlchemyUnitOfWork,
    create_database,
)

from .identity import PrincipalAccessPolicy, SignedTrustedPrincipalResolver


@dataclass(slots=True)
class ApiContainer:
    database: Database
    catalog: CatalogApplicationService
    operations: OperationApplicationService
    dead_letters: DeadLetterOperatorService
    workflows: WorkflowApplicationService
    principal_resolver: SignedTrustedPrincipalResolver
    access_policy: PrincipalAccessPolicy

    @classmethod
    def build(cls, settings: Settings) -> ApiContainer:
        database = create_database(settings)

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(database.session_factory)

        def catalog_uow_factory() -> SqlAlchemyCatalogUnitOfWork:
            return SqlAlchemyCatalogUnitOfWork(database.session_factory)

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
        return cls(
            database=database,
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
        )

    def close(self) -> None:
        self.database.dispose()
