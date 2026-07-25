from __future__ import annotations

from importlib import import_module

import pytest
from commercevision_contracts import Settings
from commercevision_domain import StorageLocationClass

readiness_module = import_module("commercevision_worker.readiness")


class ScalarResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class FakeConnection:
    def __init__(self, value: int) -> None:
        self._value = value
        self.queries: list[str] = []

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: object) -> ScalarResult:
        self.queries.append(str(statement))
        return ScalarResult(self._value)


class FakeEngine:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection

    def connect(self) -> FakeConnection:
        return self._connection


class FakeDatabase:
    def __init__(self, connection: FakeConnection) -> None:
        self.engine = FakeEngine(connection)
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


class FakeStorage:
    def __init__(self) -> None:
        self.probed_locations: tuple[StorageLocationClass, ...] | None = None
        self.closed = False

    def assert_ready(
        self,
        required_locations: tuple[StorageLocationClass, ...],
    ) -> None:
        self.probed_locations = required_locations

    def close(self) -> None:
        self.closed = True


def test_worker_dependency_probe_queries_mysql_and_closes_all_clients(
    monkeypatch,
) -> None:
    connection = FakeConnection(1)
    database = FakeDatabase(connection)
    storage = FakeStorage()
    monkeypatch.setattr(readiness_module, "create_database", lambda _settings: database)
    monkeypatch.setattr(readiness_module, "build_object_storage", lambda _settings: storage)

    assert readiness_module.probe_worker_dependencies(Settings(environment="ci")) == {
        "mysql": "ok",
        "object_storage": "ok",
    }
    assert connection.queries == ["SELECT 1"]
    assert database.disposed is True
    assert storage.probed_locations == (
        StorageLocationClass.QUARANTINE,
        StorageLocationClass.TASK,
        StorageLocationClass.FOUNDATION,
    )
    assert storage.closed is True


def test_worker_dependency_probe_disposes_mysql_on_unexpected_result(
    monkeypatch,
) -> None:
    database = FakeDatabase(FakeConnection(0))
    storage_built = False
    monkeypatch.setattr(readiness_module, "create_database", lambda _settings: database)

    def build_storage(_settings: Settings) -> FakeStorage:
        nonlocal storage_built
        storage_built = True
        return FakeStorage()

    monkeypatch.setattr(readiness_module, "build_object_storage", build_storage)

    with pytest.raises(RuntimeError, match="unexpected result"):
        readiness_module.probe_worker_dependencies(Settings(environment="ci"))

    assert database.disposed is True
    assert storage_built is False


def test_workflow_only_worker_does_not_probe_object_storage(monkeypatch) -> None:
    database = FakeDatabase(FakeConnection(1))
    storage_built = False
    monkeypatch.setattr(readiness_module, "create_database", lambda _settings: database)

    def build_storage(_settings: Settings) -> FakeStorage:
        nonlocal storage_built
        storage_built = True
        return FakeStorage()

    monkeypatch.setattr(readiness_module, "build_object_storage", build_storage)
    settings = Settings(
        environment="ci",
        worker_queues=["commercevision.workflow"],
    )

    assert readiness_module.probe_worker_dependencies(settings) == {
        "mysql": "ok",
        "object_storage": "not_required",
    }
    assert database.disposed is True
    assert storage_built is False
