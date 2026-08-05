from __future__ import annotations

from sqlalchemy import text


def test_mysql_pool_reconnects_after_an_invalidated_phase3_connection(
    integration_database,
) -> None:
    with integration_database.engine.connect() as connection:
        original = connection.connection.dbapi_connection
        assert connection.scalar(text("SELECT 1")) == 1
        connection.invalidate()

    with integration_database.engine.connect() as replacement:
        assert replacement.scalar(text("SELECT 1")) == 1
        assert replacement.connection.dbapi_connection is not original
