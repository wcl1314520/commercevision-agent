"""Control API dependency composition."""

from __future__ import annotations

from dataclasses import dataclass

from commercevision_application import CatalogApplicationService, WorkflowApplicationService
from commercevision_contracts import Settings
from commercevision_persistence import (
    Database,
    SqlAlchemyCatalogUnitOfWork,
    SqlAlchemyUnitOfWork,
    create_database,
)


@dataclass(slots=True)
class ApiContainer:
    database: Database
    catalog: CatalogApplicationService
    workflows: WorkflowApplicationService

    @classmethod
    def build(cls, settings: Settings) -> ApiContainer:
        database = create_database(settings)

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(database.session_factory)

        def catalog_uow_factory() -> SqlAlchemyCatalogUnitOfWork:
            return SqlAlchemyCatalogUnitOfWork(database.session_factory)

        return cls(
            database=database,
            catalog=CatalogApplicationService(uow_factory=catalog_uow_factory),
            workflows=WorkflowApplicationService(uow_factory=uow_factory),
        )

    def close(self) -> None:
        self.database.dispose()
