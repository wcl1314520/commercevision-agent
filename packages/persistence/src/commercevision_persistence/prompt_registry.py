"""MySQL Prompt Registry repository and short-lived Unit of Work."""

from __future__ import annotations

from datetime import UTC, datetime
from types import TracebackType
from typing import Any, cast

from commercevision_domain import (
    ConcurrencyError,
    PromptProductionPointer,
    PromptRevision,
    PromptRevisionStatus,
    PromptTemplateVariable,
)
from sqlalchemy import literal_column, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .database import enter_unit_of_work, exit_unit_of_work
from .integrity import (
    classify_database_error,
    execute_with_integrity_classification,
    flush_with_integrity_classification,
)
from .prompt_registry_models import PromptProductionPointerModel, PromptRevisionModel
from .repositories import AuditRepository, IdempotencyRepository, OutboxRepository


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _revision_from_model(model: PromptRevisionModel) -> PromptRevision:
    return PromptRevision(
        id=model.id,
        workspace_id=model.workspace_id,
        prompt_id=model.prompt_id,
        semantic_revision=model.semantic_revision,
        node=model.node,
        category_applicability=tuple(model.category_applicability_json),
        model_family_applicability=tuple(model.model_family_applicability_json),
        input_schema_version=model.input_schema_version,
        output_schema_version=model.output_schema_version,
        policy_version=model.policy_version,
        content=model.content,
        variables=tuple(
            PromptTemplateVariable(name=str(item["name"]), required=item["required"])
            for item in model.variables_json
        ),
        content_sha256=model.content_sha256,
        status=PromptRevisionStatus(model.status),
        version=model.version,
        created_by=model.created_by,
        change_summary=model.change_summary,
        created_at=_require_time(_utc(model.created_at)),
        updated_at=_require_time(_utc(model.updated_at)),
        submitted_by=model.submitted_by,
        submitted_at=_utc(model.submitted_at),
        reviewed_by=model.reviewed_by,
        reviewed_at=_utc(model.reviewed_at),
        published_by=model.published_by,
        published_at=_utc(model.published_at),
        deprecated_by=model.deprecated_by,
        deprecated_at=_utc(model.deprecated_at),
    )


def _pointer_from_model(model: PromptProductionPointerModel) -> PromptProductionPointer:
    return PromptProductionPointer(
        workspace_id=model.workspace_id,
        prompt_id=model.prompt_id,
        node=model.node,
        revision_id=model.revision_id,
        semantic_revision=model.semantic_revision,
        content_sha256=model.content_sha256,
        version=model.version,
        updated_by=model.updated_by,
        updated_at=_require_time(_utc(model.updated_at)),
    )


def _require_time(value: datetime | None) -> datetime:
    if value is None:
        raise RuntimeError("persisted Prompt Registry timestamp is missing")
    return value


class PromptRevisionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, revision: PromptRevision) -> None:
        self._session.add(
            PromptRevisionModel(
                id=revision.id,
                workspace_id=revision.workspace_id,
                prompt_id=revision.prompt_id,
                semantic_revision=revision.semantic_revision,
                node=revision.node,
                category_applicability_json=list(revision.category_applicability),
                model_family_applicability_json=list(revision.model_family_applicability),
                input_schema_version=revision.input_schema_version,
                output_schema_version=revision.output_schema_version,
                policy_version=revision.policy_version,
                content=revision.content,
                variables_json=[
                    {"name": item.name, "required": item.required} for item in revision.variables
                ],
                content_sha256=revision.content_sha256,
                status=revision.status.value,
                version=revision.version,
                created_by=revision.created_by,
                change_summary=revision.change_summary,
                created_at=revision.created_at,
                updated_at=revision.updated_at,
                submitted_by=revision.submitted_by,
                submitted_at=revision.submitted_at,
                reviewed_by=revision.reviewed_by,
                reviewed_at=revision.reviewed_at,
                published_by=revision.published_by,
                published_at=revision.published_at,
                deprecated_by=revision.deprecated_by,
                deprecated_at=revision.deprecated_at,
            )
        )
        flush_with_integrity_classification(self._session)

    def get(
        self, *, workspace_id: str, revision_id: str, for_update: bool = False
    ) -> PromptRevision | None:
        statement = select(PromptRevisionModel).where(
            PromptRevisionModel.workspace_id == workspace_id,
            PromptRevisionModel.id == revision_id,
        )
        if for_update:
            statement = statement.with_for_update()
        model = self._session.scalar(statement)
        return _revision_from_model(model) if model is not None else None

    def get_by_semantic_revision(
        self,
        *,
        workspace_id: str,
        prompt_id: str,
        semantic_revision: str,
        for_update: bool = False,
    ) -> PromptRevision | None:
        statement = select(PromptRevisionModel).where(
            PromptRevisionModel.workspace_id == workspace_id,
            PromptRevisionModel.prompt_id == prompt_id,
            PromptRevisionModel.semantic_revision == semantic_revision,
        )
        if for_update:
            statement = statement.with_for_update()
        model = self._session.scalar(statement)
        return _revision_from_model(model) if model is not None else None

    def save_lifecycle(self, revision: PromptRevision, *, expected_version: int) -> None:
        result = cast(
            CursorResult[Any],
            execute_with_integrity_classification(
                self._session,
                update(PromptRevisionModel)
                .where(
                    PromptRevisionModel.workspace_id == revision.workspace_id,
                    PromptRevisionModel.id == revision.id,
                    PromptRevisionModel.version == expected_version,
                )
                .values(
                    status=revision.status.value,
                    version=revision.version,
                    updated_at=revision.updated_at,
                    submitted_by=revision.submitted_by,
                    submitted_at=revision.submitted_at,
                    reviewed_by=revision.reviewed_by,
                    reviewed_at=revision.reviewed_at,
                    published_by=revision.published_by,
                    published_at=revision.published_at,
                    deprecated_by=revision.deprecated_by,
                    deprecated_at=revision.deprecated_at,
                ),
            ),
        )
        if result.rowcount != 1:
            raise ConcurrencyError("Prompt Revision lifecycle changed concurrently")

    def get_pointer(
        self, *, workspace_id: str, prompt_id: str, for_update: bool = False
    ) -> PromptProductionPointer | None:
        statement = select(PromptProductionPointerModel).where(
            PromptProductionPointerModel.workspace_id == workspace_id,
            PromptProductionPointerModel.prompt_id == prompt_id,
        )
        if for_update:
            statement = statement.with_for_update()
        model = self._session.scalar(statement)
        return _pointer_from_model(model) if model is not None else None

    def add_pointer(self, pointer: PromptProductionPointer) -> None:
        self._session.add(
            PromptProductionPointerModel(
                workspace_id=pointer.workspace_id,
                prompt_id=pointer.prompt_id,
                node=pointer.node,
                revision_id=pointer.revision_id,
                semantic_revision=pointer.semantic_revision,
                content_sha256=pointer.content_sha256,
                version=pointer.version,
                updated_by=pointer.updated_by,
                updated_at=pointer.updated_at,
            )
        )
        flush_with_integrity_classification(self._session)

    def save_pointer(self, pointer: PromptProductionPointer, *, expected_version: int) -> None:
        result = cast(
            CursorResult[Any],
            execute_with_integrity_classification(
                self._session,
                update(PromptProductionPointerModel)
                .where(
                    PromptProductionPointerModel.workspace_id == pointer.workspace_id,
                    PromptProductionPointerModel.prompt_id == pointer.prompt_id,
                    PromptProductionPointerModel.version == expected_version,
                )
                .values(
                    node=pointer.node,
                    revision_id=pointer.revision_id,
                    semantic_revision=pointer.semantic_revision,
                    content_sha256=pointer.content_sha256,
                    version=pointer.version,
                    updated_by=pointer.updated_by,
                    updated_at=pointer.updated_at,
                ),
            ),
        )
        if result.rowcount != 1:
            raise ConcurrencyError("Prompt production pointer changed concurrently")

    def resolve_production(
        self,
        *,
        workspace_id: str,
        prompt_id: str,
        node: str,
        category: str,
        model_family: str,
    ) -> PromptRevision | None:
        pointer = self.get_pointer(workspace_id=workspace_id, prompt_id=prompt_id)
        if pointer is None or pointer.node != node:
            return None
        revision = self.get(workspace_id=workspace_id, revision_id=pointer.revision_id)
        if (
            revision is None
            or revision.status != PromptRevisionStatus.PRODUCTION
            or revision.semantic_revision != pointer.semantic_revision
            or revision.content_sha256 != pointer.content_sha256
            or category not in revision.category_applicability
            or model_family not in revision.model_family_applicability
        ):
            return None
        return revision


class SqlAlchemyPromptRegistryUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self.session: Session | None = None
        self._depth_token: object | None = None
        self._committed = False

    def __enter__(self) -> SqlAlchemyPromptRegistryUnitOfWork:
        self.session = self._session_factory()
        self._depth_token = enter_unit_of_work()
        self.prompt_revisions = PromptRevisionRepository(self.session)
        self.idempotency = IdempotencyRepository(self.session)
        self.outbox = OutboxRepository(self.session)
        self.audit = AuditRepository(self.session)
        return self

    def database_now(self) -> datetime:
        if self.session is None:
            raise RuntimeError("unit of work is not active")
        value = self.session.scalar(select(literal_column("UTC_TIMESTAMP(6)")))
        if not isinstance(value, datetime):
            raise RuntimeError("database did not return a timestamp")
        return _require_time(_utc(value))

    def commit(self) -> None:
        if self.session is None:
            raise RuntimeError("unit of work is not active")
        try:
            self.session.commit()
        except DBAPIError as exc:
            self.session.rollback()
            classified = classify_database_error(exc)
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
            if self.session is not None and (exc_type is not None or not self._committed):
                self.session.rollback()
        finally:
            if self.session is not None:
                self.session.close()
            if self._depth_token is not None:
                exit_unit_of_work(self._depth_token)
