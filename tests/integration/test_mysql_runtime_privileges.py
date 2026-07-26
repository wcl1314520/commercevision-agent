from __future__ import annotations

import os
import re
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, OperationalError

pytestmark = pytest.mark.integration

_REQUIRED_SCHEMA_PRIVILEGES = {"SELECT", "INSERT", "UPDATE", "DELETE"}
_GRANT_PATTERN = re.compile(r"^GRANT (?P<privileges>.+) ON (?P<scope>.+) TO ")


def _runtime_target() -> tuple[str, bool]:
    configured = os.getenv("CV_RUNTIME_MYSQL_DSN") or os.getenv("CV_MYSQL_DSN")
    return (
        configured
        or "mysql+pymysql://commercevision:commercevision@127.0.0.1:13316/commercevision",
        configured is not None or os.getenv("CI", "").lower() == "true",
    )


def _privileges(raw: str) -> set[str]:
    return {part.strip() for part in raw.split(",")}


def test_explicit_runtime_privilege_target_is_mandatory(monkeypatch) -> None:
    configured = "mysql+pymysql://runtime:secret@db.example/commercevision"
    monkeypatch.setenv("CV_RUNTIME_MYSQL_DSN", configured)

    assert _runtime_target() == (configured, True)


def test_default_local_runtime_privilege_target_is_optional(monkeypatch) -> None:
    monkeypatch.delenv("CV_RUNTIME_MYSQL_DSN", raising=False)
    monkeypatch.delenv("CV_MYSQL_DSN", raising=False)
    monkeypatch.delenv("CI", raising=False)

    _, required = _runtime_target()

    assert required is False


def test_runtime_mysql_identity_is_dml_only_and_cannot_create_schema_objects() -> None:
    runtime_dsn, required = _runtime_target()
    engine = create_engine(runtime_dsn, pool_pre_ping=True)
    probe_table = f"runtime_ddl_probe_{uuid4().hex}"
    try:
        with engine.connect() as connection:
            database_name = connection.execute(text("SELECT DATABASE()")).scalar_one()
            grants = [
                str(row[0])
                for row in connection.execute(text("SHOW GRANTS FOR CURRENT_USER")).all()
            ]
    except OperationalError as exc:
        engine.dispose()
        if required:
            pytest.fail(f"required runtime MySQL identity is unavailable: {exc}")
        pytest.skip(f"runtime MySQL identity is unavailable: {exc}")

    schema_scope = f"`{database_name}`.*"
    schema_privileges: set[str] = set()
    for grant in grants:
        match = _GRANT_PATTERN.match(grant)
        assert match is not None, grant
        scope = match.group("scope")
        privileges = _privileges(match.group("privileges"))
        if scope == schema_scope:
            schema_privileges.update(privileges)
        elif scope == "*.*":
            assert privileges == {"USAGE"}
        else:
            pytest.fail(f"runtime identity has an unexpected grant scope: {grant}")

    assert schema_privileges == _REQUIRED_SCHEMA_PRIVILEGES
    assert all("GRANT OPTION" not in grant for grant in grants)

    try:
        with engine.begin() as connection:
            connection.execute(text(f"CREATE TABLE `{probe_table}` (id INTEGER NOT NULL)"))
    except DBAPIError as exc:
        assert getattr(exc.orig, "args", (None,))[0] == 1142
    else:
        with engine.begin() as connection:
            connection.execute(text(f"DROP TABLE IF EXISTS `{probe_table}`"))
        pytest.fail("runtime identity unexpectedly created a table")
    finally:
        engine.dispose()
