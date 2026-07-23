"""Translate database integrity failures into transport-independent domain errors."""

from __future__ import annotations

import re
from typing import Any

from commercevision_domain import (
    DataIntegrityError,
    InvalidDataError,
    ReferenceConstraintError,
    UniqueConstraintError,
)
from sqlalchemy.engine import Result
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql import Executable

_UNIQUE_ERROR_NUMBERS = {1022, 1062}
_REFERENCE_ERROR_NUMBERS = {1451, 1452}
_INVALID_DATA_ERROR_NUMBERS = {
    1048,  # Column cannot be null.
    1138,  # Invalid use of NULL.
    1264,  # Numeric value out of range.
    1364,  # Required column has no default.
    1366,  # Invalid value for the column type.
    1406,  # Value too long.
    3819,  # CHECK constraint violation.
    4025,  # MariaDB CHECK constraint violation.
}
_CONSTRAINT_PATTERN = re.compile(
    r"(?:constraint|for key)\s+[`'\"](?P<name>[^`'\"]+)[`'\"]",
    re.IGNORECASE,
)
_SQLSTATE_PATTERN = re.compile(r"SQLSTATE(?:\[|\s+)(?P<state>[0-9A-Z]{5})", re.IGNORECASE)


def classify_database_error(exc: DBAPIError) -> DataIntegrityError | None:
    """Return a safe domain error for known data-integrity failures."""

    error_number = _error_number(exc)
    message = str(exc.orig)
    message_folded = message.casefold()
    constraint_name = _constraint_name(message)
    sqlstate = _sqlstate(exc, message)

    if (
        error_number in _UNIQUE_ERROR_NUMBERS
        or constraint_name.startswith(("uq_", "pk_"))
        or constraint_name == "primary"
        or "duplicate entry" in message_folded
    ):
        return UniqueConstraintError("database unique constraint was violated")
    if (
        error_number in _REFERENCE_ERROR_NUMBERS
        or constraint_name.startswith("fk_")
        or "foreign key constraint fails" in message_folded
    ):
        return ReferenceConstraintError("database reference constraint was violated")
    if (
        error_number in _INVALID_DATA_ERROR_NUMBERS
        or constraint_name.startswith("ck_")
        or "check constraint" in message_folded
        or "cannot be null" in message_folded
    ):
        return InvalidDataError("database rejected invalid data")
    if isinstance(exc, IntegrityError) or sqlstate in {"22001", "22003", "23000"}:
        return InvalidDataError("database rejected invalid data")
    return None


def database_constraint_name(exc: DBAPIError) -> str:
    """Return the normalized constraint identifier reported by the database."""

    return _constraint_name(str(exc.orig))


def execute_with_integrity_classification(
    session: Session,
    statement: Executable,
) -> Result[Any]:
    """Execute a statement and translate immediate database integrity failures."""

    try:
        return session.execute(statement)
    except DBAPIError as exc:
        session.rollback()
        classified = classify_database_error(exc)
        if classified is None:
            raise
        raise classified from exc


def flush_with_integrity_classification(session: Session) -> None:
    """Flush pending writes and translate database integrity failures."""

    try:
        session.flush()
    except DBAPIError as exc:
        session.rollback()
        classified = classify_database_error(exc)
        if classified is None:
            raise
        raise classified from exc


def _error_number(exc: DBAPIError) -> int | None:
    error_number = getattr(exc.orig, "errno", None)
    if isinstance(error_number, int):
        return error_number
    args = getattr(exc.orig, "args", ())
    if args and isinstance(args[0], int):
        return args[0]
    return None


def _constraint_name(message: str) -> str:
    match = _CONSTRAINT_PATTERN.search(message)
    return match.group("name").casefold() if match else ""


def _sqlstate(exc: DBAPIError, message: str) -> str | None:
    value = getattr(exc.orig, "sqlstate", None)
    if isinstance(value, str):
        return value.upper()
    match = _SQLSTATE_PATTERN.search(message)
    return match.group("state").upper() if match else None
