"""Explicit short-lived SQLAlchemy Unit of Work."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .database import enter_unit_of_work, exit_unit_of_work
from .integrity import classify_database_error, flush_with_integrity_classification
from .repositories import (
    ApprovalRepository,
    AttemptRepository,
    AuditRepository,
    DeadLetterRepository,
    IdempotencyRepository,
    InboxRepository,
    OutboxRepository,
    StepRepository,
    WorkflowRepository,
)


class SqlAlchemyUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self.session: Session | None = None
        self._depth_token: object | None = None
        self._committed = False

    def __enter__(self) -> SqlAlchemyUnitOfWork:
        self.session = self._session_factory()
        self._depth_token = enter_unit_of_work()
        self.workflows = WorkflowRepository(self.session)
        self.steps = StepRepository(self.session)
        self.attempts = AttemptRepository(self.session)
        self.approvals = ApprovalRepository(self.session)
        self.idempotency = IdempotencyRepository(self.session)
        self.outbox = OutboxRepository(self.session)
        self.inbox = InboxRepository(self.session)
        self.dead_letters = DeadLetterRepository(self.session)
        self.audit = AuditRepository(self.session)
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
