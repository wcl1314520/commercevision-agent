"""MySQL adapter for the Durable Operation module."""

from __future__ import annotations

from datetime import datetime
from types import TracebackType

from commercevision_application.operation_ports import OperationCursor, OperationLogicalKey
from commercevision_domain import ConcurrencyError
from commercevision_domain.operations import DurableOperation, OperationState
from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .database import enter_unit_of_work, exit_unit_of_work
from .integrity import (
    classify_database_error,
    execute_with_integrity_classification,
    flush_with_integrity_classification,
)
from .models import DurableOperationModel, OutboxEventModel
from .operation_mappers import operation_from_model, operation_to_model
from .repositories import DeadLetterRepository, OutboxRepository


class OperationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._loaded_versions: dict[str, int] = {}

    def add(self, operation: DurableOperation) -> None:
        self.session.add(operation_to_model(operation))
        self._loaded_versions[operation.id] = operation.version

    def get(
        self,
        operation_id: str,
        *,
        workspace_id: str | None = None,
        for_update: bool = False,
    ) -> DurableOperation | None:
        statement = select(DurableOperationModel).where(DurableOperationModel.id == operation_id)
        if workspace_id is not None:
            statement = statement.where(DurableOperationModel.workspace_id == workspace_id)
        if for_update:
            statement = statement.with_for_update()
        model = self.session.scalar(statement)
        if model is None:
            return None
        self._loaded_versions[model.id] = model.version
        return operation_from_model(model)

    def get_by_logical_key(
        self,
        logical_key: OperationLogicalKey,
        *,
        for_update: bool = False,
    ) -> DurableOperation | None:
        workspace_id, kind, target_type, target_id, target_version, input_hash = logical_key
        statement = select(DurableOperationModel).where(
            DurableOperationModel.workspace_id == workspace_id,
            DurableOperationModel.kind == kind.value,
            DurableOperationModel.target_type == target_type,
            DurableOperationModel.target_id == target_id,
            DurableOperationModel.target_version == target_version,
            DurableOperationModel.input_hash == input_hash,
        )
        if for_update:
            statement = statement.with_for_update()
        model = self.session.scalar(statement)
        if model is None:
            return None
        self._loaded_versions[model.id] = model.version
        return operation_from_model(model)

    def list(
        self,
        *,
        workspace_id: str,
        limit: int,
        cursor: OperationCursor | None,
    ) -> list[DurableOperation]:
        statement = select(DurableOperationModel).where(
            DurableOperationModel.workspace_id == workspace_id
        )
        if cursor is not None:
            created_at, operation_id = cursor
            statement = statement.where(
                or_(
                    DurableOperationModel.created_at < created_at,
                    and_(
                        DurableOperationModel.created_at == created_at,
                        DurableOperationModel.id < operation_id,
                    ),
                )
            )
        models = list(
            self.session.scalars(
                statement.order_by(
                    DurableOperationModel.created_at.desc(),
                    DurableOperationModel.id.desc(),
                ).limit(limit)
            )
        )
        for model in models:
            self._loaded_versions[model.id] = model.version
        return [operation_from_model(model) for model in models]

    def save(self, operation: DurableOperation) -> None:
        original_version = self._loaded_versions.get(operation.id)
        if original_version is None:
            raise ConcurrencyError(f"operation {operation.id} was not loaded by this unit of work")
        values = operation_to_model(operation)
        result = execute_with_integrity_classification(
            self.session,
            update(DurableOperationModel)
            .where(
                DurableOperationModel.id == operation.id,
                DurableOperationModel.version == original_version,
            )
            .values(
                output_ref=values.output_ref,
                provider_request_id=values.provider_request_id,
                state=values.state,
                lease_owner=values.lease_owner,
                lease_token=values.lease_token,
                lease_expires_at=values.lease_expires_at,
                attempt_count=values.attempt_count,
                max_attempts=values.max_attempts,
                next_attempt_at=values.next_attempt_at,
                execution_deadline_at=values.execution_deadline_at,
                reconciliation_attempt_count=values.reconciliation_attempt_count,
                max_reconciliation_attempts=values.max_reconciliation_attempts,
                next_reconciliation_at=values.next_reconciliation_at,
                reconciliation_started_at=values.reconciliation_started_at,
                reconciliation_deadline_at=values.reconciliation_deadline_at,
                reconciliation_required=values.reconciliation_required,
                reconciliation_outcome=values.reconciliation_outcome,
                dead_letter_id=values.dead_letter_id,
                replay_source_dead_letter_id=values.replay_source_dead_letter_id,
                replay_attempt=values.replay_attempt,
                recovery_generation=values.recovery_generation,
                recovery_consumed_generation=values.recovery_consumed_generation,
                error_code=values.error_code,
                error_category=values.error_category,
                error_message=values.error_message,
                error_retryable=values.error_retryable,
                error_provider_request_id=values.error_provider_request_id,
                updated_at=values.updated_at,
                last_attempt_at=values.last_attempt_at,
                started_at=values.started_at,
                completed_at=values.completed_at,
                version=values.version,
            ),
        )
        if result.rowcount != 1:
            raise ConcurrencyError(f"operation {operation.id} was concurrently modified")
        self._loaded_versions[operation.id] = operation.version

    def claim_recoverable(
        self,
        *,
        now: datetime,
        limit: int,
        pending_event_type: str,
    ) -> list[DurableOperation]:
        pending_recovery_event = exists(
            select(OutboxEventModel.id).where(
                OutboxEventModel.aggregate_id == DurableOperationModel.id,
                OutboxEventModel.event_type == pending_event_type,
                OutboxEventModel.published_at.is_(None),
            )
        )
        models = list(
            self.session.scalars(
                select(DurableOperationModel)
                .where(
                    or_(
                        and_(
                            DurableOperationModel.state.in_(
                                [
                                    OperationState.CLAIMED.value,
                                    OperationState.RUNNING.value,
                                ]
                            ),
                            DurableOperationModel.lease_expires_at <= now,
                        ),
                        and_(
                            DurableOperationModel.recovery_pending.is_(False),
                            or_(
                                and_(
                                    DurableOperationModel.state
                                    == OperationState.RETRYABLE_FAILED.value,
                                    DurableOperationModel.next_attempt_at <= now,
                                ),
                                and_(
                                    DurableOperationModel.state == OperationState.RECONCILING.value,
                                    or_(
                                        DurableOperationModel.next_reconciliation_at <= now,
                                        DurableOperationModel.reconciliation_deadline_at <= now,
                                        and_(
                                            DurableOperationModel.next_reconciliation_at.is_(None),
                                            DurableOperationModel.lease_expires_at <= now,
                                        ),
                                    ),
                                    or_(
                                        DurableOperationModel.lease_expires_at.is_(None),
                                        DurableOperationModel.lease_expires_at <= now,
                                    ),
                                ),
                            ),
                        ),
                    ),
                    ~pending_recovery_event,
                )
                .order_by(DurableOperationModel.updated_at, DurableOperationModel.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for model in models:
            self._loaded_versions[model.id] = model.version
        return [operation_from_model(model) for model in models]


class SqlAlchemyOperationUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self.session: Session | None = None
        self._depth_token: object | None = None
        self._committed = False

    def __enter__(self) -> SqlAlchemyOperationUnitOfWork:
        self.session = self._session_factory()
        self._depth_token = enter_unit_of_work()
        self.operations = OperationRepository(self.session)
        self.outbox = OutboxRepository(self.session)
        self.dead_letters = DeadLetterRepository(self.session)
        return self

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

    def flush(self) -> None:
        if self.session is None:
            raise RuntimeError("unit of work is not active")
        flush_with_integrity_classification(self.session)

    def rollback(self) -> None:
        if self.session is not None:
            self.session.rollback()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if exc_type is not None or not self._committed:
                self.rollback()
        finally:
            if self.session is not None:
                self.session.close()
            if self._depth_token is not None:
                exit_unit_of_work(self._depth_token)
