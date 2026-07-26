"""Celery application configured for reliable at-least-once delivery."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

from celery import Celery
from celery.signals import (
    worker_init,
    worker_process_init,
    worker_process_shutdown,
    worker_ready,
    worker_shutdown,
)
from commercevision_application import OperationExecutor
from commercevision_contracts.config import load_settings
from commercevision_domain import OperationKind
from commercevision_observability import configure_logging
from kombu import Queue

from .executors import (
    OperationExecutorFactory,
    available_builtin_operation_kinds,
    build_operation_executors,
    discover_operation_executor_factories,
)
from .readiness import probe_worker_dependencies
from .runtime import WorkerRuntime

settings = load_settings("worker")
configure_logging(settings.log_level)

celery_app = Celery("commercevision-worker", broker=settings.rabbitmq_url)
celery_app.conf.update(
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
    enable_utc=True,
    result_backend=None,
    task_acks_late=True,
    task_acks_on_failure_or_timeout=False,
    task_default_queue=settings.workflow_queue_name,
    task_queues=tuple(Queue(queue_name) for queue_name in settings.configured_worker_queues),
    task_publish_retry=True,
    task_publish_retry_policy={
        "max_retries": 5,
        "interval_start": 0,
        "interval_step": 1,
        "interval_max": 5,
    },
    task_reject_on_worker_lost=True,
    task_serializer="json",
    task_track_started=True,
    timezone="UTC",
    worker_enable_remote_control=False,
    worker_prefetch_multiplier=1,
)

_runtime: WorkerRuntime | None = None
_validated_factories: Mapping[OperationKind, OperationExecutorFactory] | None = None
_dependency_readiness: dict[str, str] | None = None
_consumer_ready = False
_master_pid: int | None = None
_startup_error: str | None = None


def _missing_executor_message(
    *,
    required: set[OperationKind],
    registered: set[OperationKind],
) -> str | None:
    missing = required.difference(registered)
    if not missing:
        return None
    values = ", ".join(sorted(kind.value for kind in missing))
    return f"required operation executors are unavailable: {values}"


def _remove_readiness_file() -> None:
    Path(settings.worker_readiness_path).unlink(missing_ok=True)


def _child_readiness_directory() -> Path:
    path = Path(settings.worker_readiness_path)
    return path.with_name(f"{path.name}.children")


def _child_readiness_path(pid: int | None = None) -> Path:
    return _child_readiness_directory() / f"{pid or os.getpid()}.json"


def _clear_child_readiness_files() -> None:
    directory = _child_readiness_directory()
    if not directory.exists():
        return
    for marker in directory.glob("*.json"):
        marker.unlink(missing_ok=True)
    with suppress(OSError):
        directory.rmdir()


@worker_init.connect(weak=False)
def validate_worker_startup(**_: Any) -> None:
    """Fail the Celery master before its consumer blueprint can start."""

    global _consumer_ready, _dependency_readiness, _master_pid, _startup_error
    global _validated_factories
    _consumer_ready = False
    _dependency_readiness = None
    _master_pid = os.getpid()
    try:
        _remove_readiness_file()
        _clear_child_readiness_files()
        factories = discover_operation_executor_factories()
        error = _missing_executor_message(
            required=set(settings.worker_required_operation_kinds),
            registered=set(factories).union(available_builtin_operation_kinds(settings)),
        )
        if error is not None:
            raise RuntimeError(error)
        dependency_readiness = probe_worker_dependencies(settings)
    except Exception as exc:
        _consumer_ready = False
        _dependency_readiness = None
        _validated_factories = None
        _startup_error = str(exc)
        # Celery's signal dispatcher catches Exception. SystemExit propagates and
        # aborts WorkController construction before any consumer is created.
        raise SystemExit(f"worker bootstrap failed: {exc}") from exc
    _dependency_readiness = dependency_readiness
    _validated_factories = factories
    _startup_error = None


def _write_readiness_file(readiness: dict[str, object]) -> None:
    path = Path(settings.worker_readiness_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary_path.write_text(
        json.dumps(readiness, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def _write_child_readiness_file(readiness: dict[str, object]) -> None:
    path = _child_readiness_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(readiness, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary_path.replace(path)


@worker_process_init.connect(weak=False)
def initialize_worker_process(**_: Any) -> None:
    """Build local runtime state inside each pool child without remote probes."""

    global _runtime, _startup_error
    runtime: WorkerRuntime | None = None
    try:
        if _validated_factories is None or _dependency_readiness is None:
            raise RuntimeError("Celery master bootstrap did not complete")
        assert _validated_factories is not None
        executors: dict[OperationKind, OperationExecutor] = build_operation_executors(
            settings=settings,
            factories=_validated_factories,
        )
        runtime = WorkerRuntime.build(settings, operation_executors=executors)
        readiness = runtime.operation_executor_readiness()
        if not readiness["ready"]:
            missing = ", ".join(readiness["missing_kinds"])
            raise RuntimeError(f"required operation executors are unavailable: {missing}")
    except SystemExit:
        raise
    except Exception as exc:
        if runtime is not None:
            runtime.close()
        _runtime = None
        _startup_error = str(exc)
        _child_readiness_path().unlink(missing_ok=True)
        raise SystemExit(f"worker process bootstrap failed: {exc}") from exc
    _runtime = runtime
    _startup_error = None
    try:
        _write_child_readiness_file(
            {
                "ready": True,
                "child_pid": os.getpid(),
                "master_pid": _master_pid,
                "required_kinds": readiness["required_kinds"],
                "registered_kinds": readiness["registered_kinds"],
                "missing_kinds": readiness["missing_kinds"],
            }
        )
    except Exception as exc:
        runtime.close()
        _runtime = None
        _startup_error = str(exc)
        _child_readiness_path().unlink(missing_ok=True)
        raise SystemExit(f"worker child readiness publication failed: {exc}") from exc


def _get_runtime() -> WorkerRuntime:
    if _runtime is None:
        raise RuntimeError("worker process is not initialized")
    return _runtime


def worker_bootstrap_readiness() -> dict[str, object]:
    required = set(settings.worker_required_operation_kinds)
    registered = set(_validated_factories or {}).union(available_builtin_operation_kinds(settings))
    missing = required.difference(registered)
    dependencies_ready = (
        (_dependency_readiness or {}).get("mysql") == "ok"
        and (_dependency_readiness or {}).get("object_storage") in {"ok", "not_required"}
        and (_dependency_readiness or {}).get("malware_scanner") in {"ok", "not_required"}
    )
    return {
        "ready": _consumer_ready and dependencies_ready and not missing,
        "consumer_ready": _consumer_ready,
        "master_pid": _master_pid,
        "required_kinds": sorted(kind.value for kind in required),
        "registered_kinds": sorted(kind.value for kind in registered),
        "missing_kinds": sorted(kind.value for kind in missing),
        "mysql": (_dependency_readiness or {}).get("mysql", "not_checked"),
        "object_storage": (_dependency_readiness or {}).get(
            "object_storage",
            "not_checked",
        ),
        "malware_scanner": (_dependency_readiness or {}).get(
            "malware_scanner",
            "not_checked",
        ),
        "error": _startup_error,
    }


@worker_ready.connect(weak=False)
def mark_worker_ready(**_: Any) -> None:
    """Publish one master-owned marker only after the consumer is ready."""

    global _consumer_ready, _startup_error
    if _master_pid is None or os.getpid() != _master_pid:
        raise SystemExit("worker readiness can only be published by the Celery master")
    _consumer_ready = True
    readiness = worker_bootstrap_readiness()
    if not readiness["ready"]:
        _consumer_ready = False
        raise SystemExit("worker consumer became ready without validated dependencies")
    try:
        _write_readiness_file(readiness)
    except Exception as exc:
        _consumer_ready = False
        _startup_error = str(exc)
        _remove_readiness_file()
        raise SystemExit(f"worker readiness publication failed: {exc}") from exc


@worker_shutdown.connect(weak=False)
def remove_worker_readiness(**_: Any) -> None:
    """Remove only the master-owned readiness marker."""

    global _consumer_ready
    if _master_pid is not None and os.getpid() == _master_pid:
        _consumer_ready = False
        _remove_readiness_file()
        _clear_child_readiness_files()


@celery_app.task(name="commercevision.process_outbox_event")
def process_outbox_event(event_id: str) -> str:
    return _get_runtime().process_event(event_id)


@worker_process_shutdown.connect
def close_runtime(**_: Any) -> None:
    global _runtime
    if _runtime is not None:
        _runtime.close()
        _runtime = None
    _child_readiness_path().unlink(missing_ok=True)
