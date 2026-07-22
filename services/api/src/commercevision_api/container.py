"""Control API dependency composition."""

from __future__ import annotations

from dataclasses import dataclass

from commercevision_application import WorkflowApplicationService
from commercevision_contracts import Settings
from commercevision_persistence import Database, SqlAlchemyUnitOfWork, create_database


@dataclass(slots=True)
class ApiContainer:
    database: Database
    workflows: WorkflowApplicationService

    @classmethod
    def build(cls, settings: Settings) -> ApiContainer:
        database = create_database(settings)

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(database.session_factory)

        return cls(
            database=database,
            workflows=WorkflowApplicationService(uow_factory=uow_factory),
        )

    def close(self) -> None:
        self.database.dispose()
