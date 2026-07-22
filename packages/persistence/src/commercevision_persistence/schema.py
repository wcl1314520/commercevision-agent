"""Alembic schema comparison rules for persistence-specific types."""

from __future__ import annotations

from typing import Any

from alembic.runtime.migration import MigrationContext
from sqlalchemy import Column
from sqlalchemy.dialects.mysql import DATETIME as MYSQL_DATETIME
from sqlalchemy.sql.type_api import TypeEngine

from .models import MYSQL_DATETIME_FSP, UTCDateTime


def compare_mysql_datetime_precision(
    context: MigrationContext,
    _inspected_column: Column[Any],
    _metadata_column: Column[Any],
    inspected_type: TypeEngine[Any],
    metadata_type: TypeEngine[Any],
) -> bool | None:
    """Detect MySQL fractional-second drift hidden by TypeDecorator comparison."""

    if context.dialect.name != "mysql" or not isinstance(metadata_type, UTCDateTime):
        return None
    if not isinstance(inspected_type, MYSQL_DATETIME):
        return None
    return (inspected_type.fsp or 0) != MYSQL_DATETIME_FSP
