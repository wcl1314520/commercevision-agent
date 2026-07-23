"""Database representation for opaque workspace identities."""

from typing import Any

from commercevision_domain import (
    WORKSPACE_ID_MAX_CHARACTERS,
    validate_workspace_id,
)
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

MYSQL_WORKSPACE_ID_COLLATION = "utf8mb4_0900_bin"


def exact_string_sql_type(length: int) -> String:
    """Return an exact, case-sensitive MySQL string representation."""

    return String(
        length,
        collation=MYSQL_WORKSPACE_ID_COLLATION,
    )


class WorkspaceIdSqlType(TypeDecorator[str]):
    """Validate canonical workspace tokens before SQL execution."""

    impl = String
    cache_ok = True

    def __init__(self) -> None:
        super().__init__(
            length=WORKSPACE_ID_MAX_CHARACTERS,
            collation=MYSQL_WORKSPACE_ID_COLLATION,
        )

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        if value is None:
            return None
        return validate_workspace_id(value)


def workspace_id_sql_type() -> WorkspaceIdSqlType:
    """Return the exact SQL type used by every workspace column."""

    return WorkspaceIdSqlType()
