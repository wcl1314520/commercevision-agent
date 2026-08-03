from __future__ import annotations

import json
import subprocess
import sys
from importlib import import_module
from pathlib import Path

import pytest
from commercevision_contracts import Settings
from commercevision_contracts.product_briefs import ProviderArtifactPhysicalTarget
from commercevision_domain import OperationKind, StorageBackend, StorageLocationClass

readiness_module = import_module("commercevision_worker.readiness")


def test_worker_package_import_does_not_eagerly_load_healthcheck_dependencies() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import commercevision_worker; "
                "assert 'commercevision_worker.celery_app' not in sys.modules; "
                "assert 'commercevision_worker.readiness' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_worker_package_preserves_lazy_celery_app_export() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from commercevision_worker import celery_app; "
                "from commercevision_worker.celery_app import celery_app as module_app; "
                "assert celery_app is module_app"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


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


class FakeMalwareScanner:
    def __init__(self) -> None:
        self.probed = False

    def assert_ready(self) -> str:
        self.probed = True
        return "deterministic-clamav-v1"


class FakeBrokerConnection:
    def __init__(self) -> None:
        self.connect_attempts: list[int] = []
        self.collect_calls: list[float] = []
        self.released = False

    def ensure_connection(self, *, max_retries: int) -> None:
        self.connect_attempts.append(max_retries)

    def release(self) -> None:
        self.released = True

    def collect(self, *, socket_timeout: float) -> None:
        self.collect_calls.append(socket_timeout)


def test_worker_dependency_probe_queries_mysql_and_closes_all_clients(
    monkeypatch,
) -> None:
    connection = FakeConnection(1)
    database = FakeDatabase(connection)
    storage = FakeStorage()
    scanner = FakeMalwareScanner()
    broker = FakeBrokerConnection()
    artifact_target_probes: list[tuple[Settings, FakeDatabase, FakeStorage]] = []
    index_probes: list[Settings] = []
    monkeypatch.setattr(
        readiness_module,
        "Connection",
        lambda *_args, **_kwargs: broker,
        raising=False,
    )
    monkeypatch.setattr(
        readiness_module,
        "create_database",
        lambda _settings: pytest.fail("readiness must not use the runtime database engine"),
        raising=False,
    )
    monkeypatch.setattr(
        readiness_module,
        "create_readiness_database",
        lambda _settings: database,
        raising=False,
    )
    monkeypatch.setattr(readiness_module, "build_object_storage", lambda _settings: storage)
    monkeypatch.setattr(
        readiness_module,
        "build_malware_scanner",
        lambda _settings: scanner,
        raising=False,
    )
    monkeypatch.setattr(
        readiness_module,
        "validate_product_brief_vision_credential",
        lambda _settings: "ok",
        raising=False,
    )
    monkeypatch.setattr(
        readiness_module.product_brief,
        "probe_product_brief_artifact_targets",
        lambda *, settings, database, storage: artifact_target_probes.append(
            (settings, database, storage)
        ),
    )
    monkeypatch.setattr(
        readiness_module,
        "probe_image_indexing_dependencies",
        lambda settings: (
            index_probes.append(settings) or {"milvus": "ok", "embedding_provider": "not_required"}
        ),
    )

    settings = Settings(environment="ci")
    assert readiness_module.probe_worker_dependencies(settings) == {
        "broker": "ok",
        "mysql": "ok",
        "object_storage": "ok",
        "malware_scanner": "ok",
        "provider_result_storage": "ok",
        "vision_credential": "ok",
        "milvus": "ok",
        "embedding_provider": "not_required",
    }
    assert broker.connect_attempts == [0]
    assert broker.collect_calls == [0.0]
    assert broker.released is False
    assert connection.queries == ["SELECT /*+ MAX_EXECUTION_TIME(5000) */ 1"]
    assert database.disposed is True
    assert storage.probed_locations == (
        StorageLocationClass.QUARANTINE,
        StorageLocationClass.TASK,
        StorageLocationClass.FOUNDATION,
        StorageLocationClass.PROVIDER_RESULT,
    )
    assert storage.closed is True
    assert scanner.probed is True
    assert artifact_target_probes == [(settings, database, storage)]
    assert index_probes == [settings]


def test_worker_dependency_probe_checks_configured_unsettled_historical_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TargetStorage(FakeStorage):
        backend = StorageBackend.MINIO

        def __init__(self, bucket: str) -> None:
            super().__init__()
            self.bucket = bucket
            self.close_count = 0

        def configured_bucket(self, location: StorageLocationClass) -> str:
            assert location is StorageLocationClass.PROVIDER_RESULT
            return self.bucket

        def close(self) -> None:
            self.close_count += 1
            super().close()

    class TargetReadinessQuery:
        def __init__(self, _session_factory: object) -> None:
            pass

        @staticmethod
        def list_reconciliation_targets(
            *,
            limit: int,
        ) -> tuple[ProviderArtifactPhysicalTarget, ...]:
            assert limit == 2
            target = ProviderArtifactPhysicalTarget(
                storage_backend=StorageBackend.MINIO,
                location=StorageLocationClass.PROVIDER_RESULT,
                bucket="provider-results-legacy",
            )
            return (target,)

    connection = FakeConnection(1)
    database = FakeDatabase(connection)
    database.session_factory = object()
    current_storage = TargetStorage("provider-results")
    historical_storage = TargetStorage("provider-results-legacy")
    scanner = FakeMalwareScanner()
    broker = FakeBrokerConnection()
    monkeypatch.setattr(
        readiness_module,
        "Connection",
        lambda *_args, **_kwargs: broker,
        raising=False,
    )
    monkeypatch.setattr(
        readiness_module,
        "create_readiness_database",
        lambda _settings: database,
    )
    monkeypatch.setattr(
        readiness_module,
        "build_object_storage",
        lambda _settings: current_storage,
    )
    monkeypatch.setattr(
        readiness_module.product_brief,
        "build_object_storage",
        lambda _settings: historical_storage,
    )
    monkeypatch.setattr(
        readiness_module.product_brief,
        "SqlAlchemyProviderArtifactTargetReadinessQuery",
        TargetReadinessQuery,
    )
    monkeypatch.setattr(
        readiness_module,
        "build_malware_scanner",
        lambda _settings: scanner,
        raising=False,
    )
    monkeypatch.setattr(
        readiness_module,
        "validate_product_brief_vision_credential",
        lambda _settings: "not_required",
        raising=False,
    )
    settings = Settings(
        service_name="worker",
        environment="ci",
        worker_queues=["commercevision.asset"],
        worker_required_operation_kinds=[OperationKind.PRODUCT_BRIEF_ANALYSIS],
        provider_artifact_reconciliation_targets=[
            {
                "object_store_backend": "minio",
                "object_store_provider_result_bucket": "provider-results-legacy",
            }
        ],
    )

    readiness = readiness_module.probe_worker_dependencies(settings)

    assert readiness["provider_result_storage"] == "ok"
    assert historical_storage.probed_locations == (StorageLocationClass.PROVIDER_RESULT,)
    assert historical_storage.close_count == 1
    assert current_storage.close_count == 1


def test_worker_dependency_probe_rejects_an_unregistered_unsettled_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TargetStorage(FakeStorage):
        backend = StorageBackend.MINIO

        def __init__(self) -> None:
            super().__init__()
            self.close_count = 0

        @staticmethod
        def configured_bucket(location: StorageLocationClass) -> str:
            assert location is StorageLocationClass.PROVIDER_RESULT
            return "provider-results"

        def close(self) -> None:
            self.close_count += 1
            super().close()

    class UnknownTargetQuery:
        def __init__(self, _session_factory: object) -> None:
            pass

        @staticmethod
        def list_reconciliation_targets(
            *,
            limit: int,
        ) -> tuple[ProviderArtifactPhysicalTarget, ...]:
            assert limit == 1
            target = ProviderArtifactPhysicalTarget(
                storage_backend=StorageBackend.MINIO,
                location=StorageLocationClass.PROVIDER_RESULT,
                bucket="provider-results-unknown",
            )
            return (target,)

    database = FakeDatabase(FakeConnection(1))
    database.session_factory = object()
    storage = TargetStorage()
    monkeypatch.setattr(
        readiness_module,
        "Connection",
        lambda *_args, **_kwargs: FakeBrokerConnection(),
        raising=False,
    )
    monkeypatch.setattr(
        readiness_module,
        "create_readiness_database",
        lambda _settings: database,
    )
    monkeypatch.setattr(
        readiness_module,
        "build_object_storage",
        lambda _settings: storage,
    )
    monkeypatch.setattr(
        readiness_module.product_brief,
        "SqlAlchemyProviderArtifactTargetReadinessQuery",
        UnknownTargetQuery,
    )
    monkeypatch.setattr(
        readiness_module,
        "build_malware_scanner",
        lambda _settings: FakeMalwareScanner(),
        raising=False,
    )
    monkeypatch.setattr(
        readiness_module,
        "validate_product_brief_vision_credential",
        lambda _settings: "not_required",
        raising=False,
    )
    settings = Settings(
        service_name="worker",
        environment="ci",
        worker_queues=["commercevision.asset"],
        worker_required_operation_kinds=[OperationKind.PRODUCT_BRIEF_ANALYSIS],
    )

    with pytest.raises(
        RuntimeError,
        match="provider artifact reconciliation target registration is incomplete",
    ):
        readiness_module.probe_worker_dependencies(settings)

    assert database.disposed is True
    assert storage.close_count == 1


def test_worker_dependency_probe_disposes_mysql_on_unexpected_result(
    monkeypatch,
) -> None:
    database = FakeDatabase(FakeConnection(0))
    storage_built = False
    monkeypatch.setattr(
        readiness_module,
        "Connection",
        lambda *_args, **_kwargs: FakeBrokerConnection(),
    )
    monkeypatch.setattr(
        readiness_module,
        "create_readiness_database",
        lambda _settings: database,
    )

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
    monkeypatch.setattr(
        readiness_module,
        "Connection",
        lambda *_args, **_kwargs: FakeBrokerConnection(),
    )
    monkeypatch.setattr(
        readiness_module,
        "create_readiness_database",
        lambda _settings: database,
    )

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
        "broker": "ok",
        "mysql": "ok",
        "object_storage": "not_required",
        "malware_scanner": "not_required",
        "provider_result_storage": "not_required",
        "vision_credential": "not_required",
        "milvus": "not_required",
        "embedding_provider": "not_required",
    }
    assert database.disposed is True
    assert storage_built is False


def test_index_only_worker_probes_mysql_storage_milvus_and_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = FakeDatabase(FakeConnection(1))
    storage = FakeStorage()
    index_probes: list[Settings] = []
    monkeypatch.setattr(
        readiness_module,
        "Connection",
        lambda *_args, **_kwargs: FakeBrokerConnection(),
    )
    monkeypatch.setattr(
        readiness_module,
        "create_readiness_database",
        lambda _settings: database,
    )
    monkeypatch.setattr(
        readiness_module,
        "build_object_storage",
        lambda _settings: storage,
    )
    monkeypatch.setattr(
        readiness_module,
        "probe_image_indexing_dependencies",
        lambda settings: (
            index_probes.append(settings) or {"milvus": "ok", "embedding_provider": "not_required"}
        ),
    )
    settings = Settings(
        environment="ci",
        worker_queues=["commercevision.index"],
        worker_required_operation_kinds=[OperationKind.ASSET_INDEXING],
    )

    assert readiness_module.probe_worker_dependencies(settings) == {
        "broker": "ok",
        "mysql": "ok",
        "object_storage": "ok",
        "malware_scanner": "not_required",
        "provider_result_storage": "not_required",
        "vision_credential": "not_required",
        "milvus": "ok",
        "embedding_provider": "not_required",
    }
    assert index_probes == [settings]
    assert storage.probed_locations == (
        StorageLocationClass.QUARANTINE,
        StorageLocationClass.TASK,
        StorageLocationClass.FOUNDATION,
    )
    assert storage.closed is True
    assert database.disposed is True


def test_live_supervisor_revokes_readiness_before_shutdown_after_failure_threshold(
    tmp_path,
) -> None:
    marker = tmp_path / "worker-ready.json"
    marker.write_text('{"ready":true}', encoding="utf-8")
    lifecycle_events: list[str] = []

    def probe() -> dict[str, str]:
        raise RuntimeError("dependency response body must stay private")

    def revoke() -> None:
        marker.unlink(missing_ok=True)
        lifecycle_events.append("revoke")

    supervisor = readiness_module.WorkerReadinessSupervisor(
        probe=probe,
        publish=lambda _statuses, _checked_at: None,
        revoke=revoke,
        shutdown=lambda: lifecycle_events.append("shutdown"),
        interval_seconds=5,
        failure_threshold=2,
        clock=lambda: 100.0,
    )

    assert supervisor.check() is False
    assert marker.is_file()
    assert lifecycle_events == []

    assert supervisor.check() is False
    assert marker.exists() is False
    assert lifecycle_events == ["revoke", "shutdown"]


def test_live_supervisor_resets_transient_failure_budget_after_success() -> None:
    outcomes = iter(("failure", "success", "failure", "failure"))
    published: list[tuple[dict[str, str], float]] = []
    lifecycle_events: list[str] = []

    def probe() -> dict[str, str]:
        if next(outcomes) == "failure":
            raise RuntimeError("temporary dependency failure")
        return {"mysql": "ok"}

    supervisor = readiness_module.WorkerReadinessSupervisor(
        probe=probe,
        publish=lambda statuses, checked_at: published.append((dict(statuses), checked_at)),
        revoke=lambda: lifecycle_events.append("revoke"),
        shutdown=lambda: lifecycle_events.append("shutdown"),
        interval_seconds=readiness_module.READINESS_PROBE_INTERVAL_SECONDS,
        failure_threshold=readiness_module.READINESS_FAILURE_THRESHOLD,
        clock=lambda: 123.0,
    )

    assert supervisor.check() is False
    assert supervisor.check() is True
    assert published == [({"mysql": "ok"}, 123.0)]
    assert supervisor.check() is False
    assert lifecycle_events == []
    assert supervisor.check() is False
    assert lifecycle_events == ["revoke", "shutdown"]


def test_readiness_marker_fails_closed_when_missing_or_stale(tmp_path: Path) -> None:
    marker = tmp_path / "worker-ready.json"

    with pytest.raises(readiness_module.WorkerReadinessError, match="unavailable"):
        readiness_module.load_fresh_readiness_marker(
            marker,
            now=100.0,
            max_age_seconds=20.0,
            expected_master_pid=7,
        )

    marker.write_text(
        json.dumps(
            {
                "ready": True,
                "consumer_ready": True,
                "master_pid": 7,
                "missing_kinds": [],
                "broker": "ok",
                "mysql": "ok",
                "object_storage": "ok",
                "malware_scanner": "ok",
                "provider_result_storage": "ok",
                "vision_credential": "ok",
                "milvus": "ok",
                "embedding_provider": "not_required",
                "checked_at": 100.0,
                "fresh_until": 120.0,
            }
        ),
        encoding="utf-8",
    )

    assert (
        readiness_module.load_fresh_readiness_marker(
            marker,
            now=119.0,
            max_age_seconds=20.0,
            expected_master_pid=7,
        )["checked_at"]
        == 100.0
    )

    payload_with_error = json.loads(marker.read_text(encoding="utf-8"))
    payload_with_error["error"] = "private dependency response body"
    marker.write_text(json.dumps(payload_with_error), encoding="utf-8")
    with pytest.raises(readiness_module.WorkerReadinessError, match="unavailable"):
        readiness_module.load_fresh_readiness_marker(
            marker,
            now=119.0,
            max_age_seconds=20.0,
            expected_master_pid=7,
        )

    del payload_with_error["error"]
    marker.write_text(json.dumps(payload_with_error), encoding="utf-8")
    with pytest.raises(readiness_module.WorkerReadinessError, match="unavailable"):
        readiness_module.load_fresh_readiness_marker(
            marker,
            now=121.0,
            max_age_seconds=20.0,
            expected_master_pid=7,
        )


def test_healthcheck_uses_the_configured_master_marker_lease(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = Settings(
        service_name="worker",
        worker_queues=["commercevision.workflow"],
        worker_readiness_path=str(tmp_path / "worker-ready.json"),
        worker_readiness_max_age_seconds=73,
    )
    observed: dict[str, object] = {}

    def capture_healthcheck(
        path: Path,
        *,
        now: float,
        max_age_seconds: float,
        expected_master_pid: int,
        minimum_ready_children: int,
        process_root: Path,
    ) -> None:
        observed.update(
            {
                "path": path,
                "max_age_seconds": max_age_seconds,
                "expected_master_pid": expected_master_pid,
                "minimum_ready_children": minimum_ready_children,
                "process_root": process_root,
            }
        )

    monkeypatch.setattr(readiness_module, "load_settings", lambda _service_name: settings)
    monkeypatch.setattr(readiness_module, "assert_worker_healthcheck", capture_healthcheck)
    monkeypatch.setenv("CV_WORKER_CONCURRENCY", "2")

    assert readiness_module.main(["healthcheck"]) == 0
    assert observed == {
        "path": Path(settings.worker_readiness_path),
        "max_age_seconds": 73,
        "expected_master_pid": 1,
        "minimum_ready_children": 2,
        "process_root": Path("/proc"),
    }


def test_supervisor_thread_lifecycle_is_master_owned_and_idempotent() -> None:
    current_pid = 41
    built_threads: list[FakeThread] = []

    class FakeThread:
        def __init__(self, *, target, name: str, daemon: bool) -> None:
            self.target = target
            self.name = name
            self.daemon = daemon
            self.started = False
            self.join_timeouts: list[float] = []
            built_threads.append(self)

        def start(self) -> None:
            self.started = True

        def is_alive(self) -> bool:
            return self.started and not self.join_timeouts

        def join(self, timeout: float) -> None:
            self.join_timeouts.append(timeout)

    class FakeStopEvent:
        def __init__(self) -> None:
            self.set_count = 0
            self.clear_count = 0

        def clear(self) -> None:
            self.clear_count += 1

        def set(self) -> None:
            self.set_count += 1

        @staticmethod
        def wait(_timeout: float) -> bool:
            return True

    stop_event = FakeStopEvent()
    supervisor = readiness_module.WorkerReadinessSupervisor(
        probe=lambda: {},
        publish=lambda _statuses, _checked_at: None,
        revoke=lambda: None,
        shutdown=lambda: None,
        interval_seconds=5,
        failure_threshold=2,
        clock=lambda: 100.0,
        owner_pid=41,
        current_pid=lambda: current_pid,
        stop_event=stop_event,
        thread_factory=FakeThread,
        join_timeout_seconds=9,
    )

    assert supervisor.start() is True
    assert supervisor.start() is False
    assert len(built_threads) == 1
    assert built_threads[0].started is True
    assert built_threads[0].daemon is True

    supervisor.stop()
    assert stop_event.set_count == 1
    assert built_threads[0].join_timeouts == [9]

    current_pid = 42
    assert supervisor.start() is False
    assert len(built_threads) == 1


def test_healthcheck_requires_fresh_master_and_live_ready_prefork_children(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "worker-ready.json"
    marker.write_text(
        json.dumps(
            {
                "ready": True,
                "consumer_ready": True,
                "master_pid": 1,
                "missing_kinds": [],
                "broker": "ok",
                "mysql": "ok",
                "object_storage": "ok",
                "malware_scanner": "ok",
                "provider_result_storage": "ok",
                "vision_credential": "ok",
                "milvus": "ok",
                "embedding_provider": "not_required",
                "checked_at": 100.0,
                "fresh_until": 120.0,
            }
        ),
        encoding="utf-8",
    )
    process_root = tmp_path / "proc"
    (process_root / "1" / "task" / "1").mkdir(parents=True)
    (process_root / "1" / "cmdline").write_bytes(b"celery\x00worker")
    (process_root / "1" / "task" / "1" / "children").write_text(
        "11 12",
        encoding="utf-8",
    )
    for child_pid in (11, 12):
        (process_root / str(child_pid)).mkdir()
        child_marker = marker.with_name(f"{marker.name}.children") / f"{child_pid}.json"
        child_marker.parent.mkdir(exist_ok=True)
        child_marker.write_text(
            json.dumps(
                {
                    "ready": True,
                    "child_pid": child_pid,
                    "master_pid": 1,
                    "missing_kinds": [],
                }
            ),
            encoding="utf-8",
        )

    readiness_module.assert_worker_healthcheck(
        marker,
        now=110.0,
        max_age_seconds=20.0,
        expected_master_pid=1,
        minimum_ready_children=2,
        process_root=process_root,
    )

    (marker.with_name(f"{marker.name}.children") / "12.json").unlink()
    with pytest.raises(readiness_module.WorkerReadinessError, match="unavailable"):
        readiness_module.assert_worker_healthcheck(
            marker,
            now=110.0,
            max_age_seconds=20.0,
            expected_master_pid=1,
            minimum_ready_children=2,
            process_root=process_root,
        )
