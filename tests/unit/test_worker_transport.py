from __future__ import annotations

import json
import os
from importlib import import_module
from pathlib import Path

import pytest
from celery.worker.worker import WorkController
from commercevision_contracts import Settings
from commercevision_domain import OperationKind
from commercevision_worker.runtime import WorkerRuntime

worker_module = import_module("commercevision_worker.celery_app")
executor_module = import_module("commercevision_worker.executors")
runtime_module = import_module("commercevision_worker.runtime")


class FakeRuntime:
    def __init__(self) -> None:
        self.closed = False

    def process_event(self, event_id: str) -> str:
        return f"handled:{event_id}"

    def operation_executor_readiness(self) -> dict[str, object]:
        return {
            "ready": True,
            "required_kinds": [OperationKind.ASSET_VALIDATION.value],
            "registered_kinds": [OperationKind.ASSET_VALIDATION.value],
            "missing_kinds": [],
        }

    def close(self) -> None:
        self.closed = True


class FakeExecutor:
    def execute(self, request):  # pragma: no cover - bootstrap contract only
        raise AssertionError(f"unexpected execution for {request.operation_id}")

    def reconcile(self, request):  # pragma: no cover - bootstrap contract only
        raise AssertionError(f"unexpected reconciliation for {request.operation_id}")


class FakeExecutorEntryPoint:
    name = OperationKind.ASSET_VALIDATION.value

    def __init__(self, executor: FakeExecutor) -> None:
        self._executor = executor

    def load(self):
        return lambda _settings: self._executor


def _production_worker_settings(readiness_path: Path) -> Settings:
    return Settings(
        environment="production",
        object_store_endpoint="https://object-storage.internal.example",
        object_store_presign_endpoint="https://uploads.example",
        object_store_secret_key="production-worker-object-store-secret",
        object_store_require_encryption=True,
        worker_required_operation_kinds=[OperationKind.ASSET_VALIDATION],
        worker_readiness_path=str(readiness_path),
    )


def _dependencies_ready(_settings: Settings) -> dict[str, str]:
    return {
        "mysql": "ok",
        "object_storage": "ok",
    }


def test_celery_task_delegates_without_business_retry(monkeypatch) -> None:
    monkeypatch.setattr(worker_module, "_get_runtime", lambda: FakeRuntime())

    assert worker_module.process_outbox_event.run("event-1") == "handled:event-1"
    assert worker_module.celery_app.conf.task_acks_late is True
    assert worker_module.celery_app.conf.task_acks_on_failure_or_timeout is False
    assert worker_module.celery_app.conf.task_reject_on_worker_lost is True
    assert {queue.name for queue in worker_module.celery_app.conf.task_queues} == set(
        worker_module.settings.configured_worker_queues
    )


def test_worker_fails_before_startup_when_required_executor_is_missing() -> None:
    settings = Settings(
        environment="ci",
        worker_required_operation_kinds=[OperationKind.ASSET_VALIDATION],
    )

    with pytest.raises(RuntimeError, match="ASSET_VALIDATION"):
        WorkerRuntime.build(settings)


def test_workflow_only_runtime_does_not_construct_object_storage(monkeypatch) -> None:
    storage_built = False

    def build_storage(_settings: Settings):
        nonlocal storage_built
        storage_built = True
        raise AssertionError("workflow-only Worker must not construct object storage")

    monkeypatch.setattr(runtime_module, "build_object_storage", build_storage)
    runtime = WorkerRuntime.build(
        Settings(
            environment="ci",
            worker_queues=["commercevision.workflow"],
        )
    )
    try:
        assert OperationKind.ASSET_DELETION not in runtime.operation_executors.registered_kinds
        assert runtime.object_storage is None
        assert storage_built is False
    finally:
        runtime.close()


def test_celery_worker_fails_fast_before_consumer_when_executor_is_missing(
    monkeypatch,
    tmp_path,
) -> None:
    settings = _production_worker_settings(tmp_path / "worker-ready.json")
    monkeypatch.setattr(worker_module, "settings", settings)
    monkeypatch.setattr(executor_module, "entry_points", lambda **_kwargs: ())

    with pytest.raises(SystemExit, match="ASSET_VALIDATION"):
        WorkController(
            app=worker_module.celery_app,
            pool="solo",
            concurrency=1,
            hostname="bootstrap-test@localhost",
        )

    assert worker_module.worker_bootstrap_readiness()["ready"] is False
    assert not Path(settings.worker_readiness_path).exists()


@pytest.mark.parametrize(
    ("dependency", "message"),
    (
        ("mysql", "MySQL control plane is unavailable"),
        ("object_storage", "object storage bucket is not accessible"),
    ),
)
def test_celery_master_fails_before_consumer_when_dependency_probe_fails(
    monkeypatch,
    tmp_path,
    dependency: str,
    message: str,
) -> None:
    settings = _production_worker_settings(tmp_path / f"{dependency}-worker-ready.json")
    executor = FakeExecutor()
    runtime_built = False

    monkeypatch.setattr(worker_module, "settings", settings)
    monkeypatch.setattr(
        executor_module,
        "entry_points",
        lambda **_kwargs: (FakeExecutorEntryPoint(executor),),
    )

    def fail_dependencies(_settings: Settings) -> dict[str, str]:
        raise RuntimeError(message)

    def fail_if_runtime_is_built(*_args, **_kwargs):
        nonlocal runtime_built
        runtime_built = True
        raise AssertionError("runtime must not be built after a master dependency failure")

    monkeypatch.setattr(worker_module, "probe_worker_dependencies", fail_dependencies)
    monkeypatch.setattr(WorkerRuntime, "build", fail_if_runtime_is_built)

    with pytest.raises(SystemExit, match=message):
        WorkController(
            app=worker_module.celery_app,
            pool="solo",
            concurrency=1,
            hostname=f"bootstrap-{dependency}-failure-test@localhost",
        )

    assert runtime_built is False
    assert worker_module.worker_bootstrap_readiness()["ready"] is False
    assert not Path(settings.worker_readiness_path).exists()


def test_worker_process_eagerly_builds_runtime_with_discovered_executors(
    monkeypatch,
    tmp_path,
) -> None:
    readiness_path = tmp_path / "worker-ready.json"
    settings = _production_worker_settings(readiness_path)
    executor = FakeExecutor()
    runtime = FakeRuntime()
    built_with: list[dict[OperationKind, FakeExecutor]] = []

    monkeypatch.setattr(worker_module, "settings", settings)
    monkeypatch.setattr(
        executor_module,
        "entry_points",
        lambda **_kwargs: (FakeExecutorEntryPoint(executor),),
    )
    monkeypatch.setattr(worker_module, "probe_worker_dependencies", _dependencies_ready)

    def build_runtime(_settings, *, operation_executors):
        built_with.append(operation_executors)
        return runtime

    monkeypatch.setattr(WorkerRuntime, "build", build_runtime)

    controller = WorkController(
        app=worker_module.celery_app,
        pool="solo",
        concurrency=1,
        hostname="bootstrap-success-test@localhost",
    )
    assert controller.blueprint is not None

    assert built_with == [{OperationKind.ASSET_VALIDATION: executor}]
    assert worker_module.process_outbox_event.run("event-eager") == "handled:event-eager"
    assert readiness_path.exists() is False
    assert worker_module._child_readiness_path().is_file()
    assert worker_module.worker_bootstrap_readiness()["ready"] is False

    worker_module.mark_worker_ready()

    assert worker_module.worker_bootstrap_readiness() == {
        "ready": True,
        "consumer_ready": True,
        "master_pid": os.getpid(),
        "required_kinds": [OperationKind.ASSET_VALIDATION.value],
        "registered_kinds": [
            OperationKind.ASSET_DELETION.value,
            OperationKind.ASSET_VALIDATION.value,
        ],
        "missing_kinds": [],
        "mysql": "ok",
        "object_storage": "ok",
        "error": None,
    }
    assert readiness_path.is_file()
    assert json.loads(readiness_path.read_text(encoding="utf-8")) == (
        worker_module.worker_bootstrap_readiness()
    )
    worker_module.close_runtime()
    assert runtime.closed is True
    assert worker_module._child_readiness_path().exists() is False
    assert readiness_path.is_file()
    worker_module.remove_worker_readiness()
    assert readiness_path.exists() is False


def test_pool_child_initialization_never_runs_network_dependency_probes(
    monkeypatch,
    tmp_path,
) -> None:
    readiness_path = tmp_path / "worker-ready.json"
    settings = _production_worker_settings(readiness_path)
    executor = FakeExecutor()
    runtime = FakeRuntime()
    probe_count = 0

    monkeypatch.setattr(worker_module, "settings", settings)
    monkeypatch.setattr(
        executor_module,
        "entry_points",
        lambda **_kwargs: (FakeExecutorEntryPoint(executor),),
    )
    monkeypatch.setattr(worker_module, "probe_worker_dependencies", _dependencies_ready)
    monkeypatch.setattr(
        WorkerRuntime,
        "build",
        lambda _settings, *, operation_executors: runtime,
    )

    worker_module.validate_worker_startup()

    def fail_if_probed(_settings: Settings) -> dict[str, str]:
        nonlocal probe_count
        probe_count += 1
        raise AssertionError("pool child must not perform network readiness probes")

    monkeypatch.setattr(worker_module, "probe_worker_dependencies", fail_if_probed)
    worker_module.initialize_worker_process()

    assert probe_count == 0
    assert worker_module.worker_bootstrap_readiness()["ready"] is False
    assert readiness_path.exists() is False
    assert worker_module._child_readiness_path().is_file()
    worker_module.mark_worker_ready()
    assert worker_module.worker_bootstrap_readiness()["ready"] is True
    assert readiness_path.is_file()
    worker_module.close_runtime()
    assert runtime.closed is True
    assert worker_module._child_readiness_path().exists() is False
    assert readiness_path.is_file()
    worker_module.remove_worker_readiness()


def test_pool_child_bootstrap_failure_does_not_remove_master_readiness(
    monkeypatch,
    tmp_path,
) -> None:
    readiness_path = tmp_path / "worker-ready.json"
    settings = _production_worker_settings(readiness_path)
    executor = FakeExecutor()

    monkeypatch.setattr(worker_module, "settings", settings)
    monkeypatch.setattr(
        executor_module,
        "entry_points",
        lambda **_kwargs: (FakeExecutorEntryPoint(executor),),
    )
    monkeypatch.setattr(worker_module, "probe_worker_dependencies", _dependencies_ready)
    worker_module.validate_worker_startup()
    worker_module.mark_worker_ready()
    assert readiness_path.is_file()

    def fail_runtime(*_args, **_kwargs):
        raise RuntimeError("local child runtime failed")

    monkeypatch.setattr(WorkerRuntime, "build", fail_runtime)
    with pytest.raises(SystemExit, match="local child runtime failed"):
        worker_module.initialize_worker_process()

    assert readiness_path.is_file()
    assert worker_module._child_readiness_path().exists() is False
    worker_module.remove_worker_readiness()


def test_celery_task_never_lazily_initializes_runtime(monkeypatch) -> None:
    monkeypatch.setattr(worker_module, "_runtime", None)

    with pytest.raises(RuntimeError, match="worker process is not initialized"):
        worker_module.process_outbox_event.run("event-before-bootstrap")
