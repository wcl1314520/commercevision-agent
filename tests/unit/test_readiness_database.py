from __future__ import annotations

import socket
from threading import Event, Thread
from time import monotonic
from typing import Any

import pytest
from commercevision_contracts import Settings
from commercevision_persistence import database as database_module
from sqlalchemy.exc import OperationalError


def test_readiness_database_bounds_socket_io_without_changing_runtime_database(
    monkeypatch,
) -> None:
    engine_calls: list[dict[str, Any]] = []

    def build_engine(_url: object, **kwargs: Any) -> object:
        engine_calls.append(kwargs)
        return object()

    monkeypatch.setattr(database_module, "create_engine", build_engine)
    monkeypatch.setattr(
        database_module,
        "sessionmaker",
        lambda **_kwargs: object(),
    )
    settings = Settings(mysql_connect_timeout_seconds=7)

    database_module.create_database(settings)
    database_module.create_readiness_database(settings)

    runtime_options, readiness_options = engine_calls
    assert runtime_options["connect_args"] == {"connect_timeout": 7}
    assert readiness_options["connect_args"] == {
        "connect_timeout": 7,
        "read_timeout": 5,
        "write_timeout": 5,
    }
    assert readiness_options["pool_pre_ping"] is False
    assert readiness_options["pool_size"] == 1
    assert readiness_options["max_overflow"] == 0
    assert readiness_options["isolation_level"] == "AUTOCOMMIT"
    assert readiness_options["skip_autocommit_rollback"] is True
    assert runtime_options["isolation_level"] == "READ COMMITTED"
    assert "skip_autocommit_rollback" not in runtime_options


def test_readiness_database_abandons_a_stalled_mysql_handshake_within_the_socket_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    accepted = Event()
    release_server = Event()

    def stall_after_accept() -> None:
        connection, _address = listener.accept()
        with connection:
            accepted.set()
            release_server.wait(timeout=2)

    server = Thread(target=stall_after_accept, daemon=True)
    server.start()
    monkeypatch.setattr(
        database_module,
        "WORKER_READINESS_MYSQL_QUERY_TIMEOUT_SECONDS",
        0.1,
    )
    database = database_module.create_readiness_database(
        Settings(
            mysql_dsn=(
                f"mysql+pymysql://commercevision:commercevision@127.0.0.1:{port}/commercevision"
            ),
            mysql_connect_timeout_seconds=1,
        )
    )

    started_at = monotonic()
    try:
        with pytest.raises(OperationalError):
            database.engine.connect()
        elapsed = monotonic() - started_at
        assert accepted.wait(timeout=0.5)
        assert elapsed < 1.0
    finally:
        database.dispose()
        release_server.set()
        listener.close()
        server.join(timeout=1)

    assert not server.is_alive()
