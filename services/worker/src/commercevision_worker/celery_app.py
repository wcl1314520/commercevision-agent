"""Celery application configured for reliable at-least-once delivery."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from celery import Celery
from celery.signals import worker_init, worker_process_init, worker_process_shutdown
from commercevision_application import OperationExecutor
from commercevision_contracts.config import load_settings
from commercevision_domain import OperationKind
from commercevision_observability import configure_logging
from kombu import Queue

from .executors import (
    OperationExecutorFactory,
    build_operation_executors,
    discover_operation_executor_factories,
)
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


@worker_init.connect(weak=False)
def validate_worker_startup(**_: Any) -> None:
    """Fail the Celery master before its consumer blueprint can start."""

    global _startup_error, _validated_factories
    _remove_readiness_file()
    try:
        factories = discover_operation_executor_factories()
        error = _missing_executor_message(
            required=set(settings.worker_required_operation_kinds),
            registered=set(factories),
        )
        if error is not None:
            raise RuntimeError(error)
    except Exception as exc:
        _validated_factories = None
        _startup_error = str(exc)
        # Celery's signal dispatcher catches Exception. SystemExit propagates and
        # aborts WorkController construction before any consumer is created.
        raise SystemExit(f"worker bootstrap failed: {exc}") from exc
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


@worker_process_init.connect(weak=False)
def initialize_worker_process(**_: Any) -> None:
    """Build the complete runtime before this worker process can receive tasks."""

    global _runtime, _startup_error
    try:
        if _validated_factories is None:
            validate_worker_startup()
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
        _runtime = None
        _startup_error = str(exc)
        _remove_readiness_file()
        raise SystemExit(f"worker process bootstrap failed: {exc}") from exc
    _runtime = runtime
    _startup_error = None
    _write_readiness_file(worker_bootstrap_readiness())


def _get_runtime() -> WorkerRuntime:
    if _runtime is None:
        raise RuntimeError("worker process is not initialized")
    return _runtime


def worker_bootstrap_readiness() -> dict[str, object]:
    required = set(settings.worker_required_operation_kinds)
    registered = set(_validated_factories or {})
    if _runtime is not None:
        runtime_readiness = _runtime.operation_executor_readiness()
        return {
            "ready": runtime_readiness["ready"],
            "initialized": True,
            "required_kinds": runtime_readiness["required_kinds"],
            "registered_kinds": runtime_readiness["registered_kinds"],
            "missing_kinds": runtime_readiness["missing_kinds"],
            "error": _startup_error,
        }
    return {
        "ready": False,
        "initialized": False,
        "required_kinds": sorted(kind.value for kind in required),
        "registered_kinds": sorted(kind.value for kind in registered),
        "missing_kinds": sorted(kind.value for kind in required.difference(registered)),
        "error": _startup_error,
    }


@celery_app.task(name="commercevision.process_outbox_event")
def process_outbox_event(event_id: str) -> str:
    return _get_runtime().process_event(event_id)


@worker_process_shutdown.connect
def close_runtime(**_: Any) -> None:
    global _runtime
    if _runtime is not None:
        _runtime.close()
        _runtime = None
