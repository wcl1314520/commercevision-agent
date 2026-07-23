from __future__ import annotations

from importlib import import_module
from pathlib import Path

import pytest
from celery.worker.worker import WorkController
from commercevision_contracts import Settings
from commercevision_domain import OperationKind
from commercevision_worker.runtime import WorkerRuntime

worker_module = import_module("commercevision_worker.celery_app")
executor_module = import_module("commercevision_worker.executors")


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
        worker_required_operation_kinds=[OperationKind.ASSET_VALIDATION],
        worker_readiness_path=str(readiness_path),
    )


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
    assert worker_module.worker_bootstrap_readiness() == {
        "ready": True,
        "initialized": True,
        "required_kinds": [OperationKind.ASSET_VALIDATION.value],
        "registered_kinds": [OperationKind.ASSET_VALIDATION.value],
        "missing_kinds": [],
        "error": None,
    }
    assert readiness_path.is_file()
    worker_module.close_runtime()
    assert runtime.closed is True


def test_celery_task_never_lazily_initializes_runtime(monkeypatch) -> None:
    monkeypatch.setattr(worker_module, "_runtime", None)

    with pytest.raises(RuntimeError, match="worker process is not initialized"):
        worker_module.process_outbox_event.run("event-before-bootstrap")
