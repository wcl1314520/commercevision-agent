"""Process-wide dependency probes for the Celery Worker."""

import argparse
import json
import logging
import math
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from threading import Event, Lock, Thread, current_thread
from time import time
from typing import Protocol

from commercevision_contracts import Settings
from commercevision_contracts.config import (
    WORKER_READINESS_BROKER_CONNECT_TIMEOUT_SECONDS,
    WORKER_READINESS_MYSQL_QUERY_TIMEOUT_SECONDS,
    WORKER_READINESS_PROBE_INTERVAL_SECONDS,
    load_settings,
)
from commercevision_domain import StorageLocationClass
from commercevision_object_storage import build_object_storage, close_object_storage
from commercevision_persistence import create_readiness_database
from kombu import Connection
from sqlalchemy import text

from . import product_brief
from .asset_validation import build_malware_scanner
from .image_indexing import probe_image_indexing_dependencies
from .product_brief import validate_product_brief_vision_credential

logger = logging.getLogger(__name__)

READINESS_PROBE_INTERVAL_SECONDS = WORKER_READINESS_PROBE_INTERVAL_SECONDS
READINESS_FAILURE_THRESHOLD = 2
_REQUIRED_DEPENDENCY_STATES = {
    "broker": {"ok"},
    "mysql": {"ok"},
    "object_storage": {"ok", "not_required"},
    "malware_scanner": {"ok", "not_required"},
    "provider_result_storage": {"ok", "not_required"},
    "vision_credential": {"ok", "not_required"},
    "milvus": {"ok", "not_required"},
    "embedding_provider": {"ok", "not_required"},
}


class WorkerReadinessError(RuntimeError):
    """The Worker marker cannot prove current readiness."""


class _StopEvent(Protocol):
    def clear(self) -> None: ...

    def set(self) -> None: ...

    def wait(self, timeout: float) -> bool: ...


class _ThreadHandle(Protocol):
    def start(self) -> None: ...

    def is_alive(self) -> bool: ...

    def join(self, timeout: float) -> None: ...


def load_fresh_readiness_marker(
    path: Path,
    *,
    now: float,
    max_age_seconds: float,
    expected_master_pid: int,
) -> dict[str, object]:
    """Load a fresh, structurally healthy master readiness marker."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        checked_at = payload["checked_at"]
        fresh_until = payload["fresh_until"]
        marker_is_valid = (
            isinstance(payload, dict)
            and payload.get("ready") is True
            and payload.get("consumer_ready") is True
            and payload.get("master_pid") == expected_master_pid
            and payload.get("missing_kinds") == []
            and "error" not in payload
            and isinstance(checked_at, int | float)
            and not isinstance(checked_at, bool)
            and math.isfinite(checked_at)
            and isinstance(fresh_until, int | float)
            and not isinstance(fresh_until, bool)
            and math.isfinite(fresh_until)
            and checked_at <= now <= fresh_until
            and now - checked_at <= max_age_seconds
            and all(
                payload.get(name) in accepted
                for name, accepted in _REQUIRED_DEPENDENCY_STATES.items()
            )
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        marker_is_valid = False
        payload = None
    if not marker_is_valid or not isinstance(payload, dict):
        raise WorkerReadinessError("worker readiness is unavailable")
    return payload


def assert_worker_healthcheck(
    path: Path,
    *,
    now: float,
    max_age_seconds: float,
    expected_master_pid: int,
    minimum_ready_children: int,
    process_root: Path,
) -> None:
    """Fail closed unless the fresh master marker has enough live ready children."""

    load_fresh_readiness_marker(
        path,
        now=now,
        max_age_seconds=max_age_seconds,
        expected_master_pid=expected_master_pid,
    )
    try:
        command = (process_root / str(expected_master_pid) / "cmdline").read_bytes()
        child_pid_text = (
            process_root / str(expected_master_pid) / "task" / str(expected_master_pid) / "children"
        ).read_text(encoding="utf-8")
        live_child_pids = {
            int(value) for value in child_pid_text.split() if (process_root / value).exists()
        }
    except (OSError, ValueError):
        raise WorkerReadinessError("worker readiness is unavailable") from None
    if b"celery" not in command:
        raise WorkerReadinessError("worker readiness is unavailable")

    ready_child_pids: set[int] = set()
    child_directory = path.with_name(f"{path.name}.children")
    for child_path in child_directory.glob("*.json"):
        try:
            child = json.loads(child_path.read_text(encoding="utf-8"))
            child_pid = child["child_pid"]
            if (
                isinstance(child_pid, int)
                and not isinstance(child_pid, bool)
                and child.get("ready") is True
                and child.get("master_pid") == expected_master_pid
                and child.get("missing_kinds") == []
            ):
                ready_child_pids.add(child_pid)
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    if len(live_child_pids.intersection(ready_child_pids)) < minimum_ready_children:
        raise WorkerReadinessError("worker readiness is unavailable")


def main(argv: list[str] | None = None) -> int:
    """Run the Worker container healthcheck."""

    parser = argparse.ArgumentParser(prog="python -m commercevision_worker.readiness")
    parser.add_argument("command", choices=("healthcheck",))
    args = parser.parse_args(argv)
    if args.command != "healthcheck":  # pragma: no cover - argparse enforces choices
        return 1
    try:
        settings = load_settings("worker")
        minimum_ready_children = int(os.environ["CV_WORKER_CONCURRENCY"])
        if minimum_ready_children < 1:
            raise ValueError("Worker concurrency must be positive")
        assert_worker_healthcheck(
            Path(settings.worker_readiness_path),
            now=time(),
            max_age_seconds=settings.worker_readiness_max_age_seconds,
            expected_master_pid=1,
            minimum_ready_children=minimum_ready_children,
            process_root=Path("/proc"),
        )
    except Exception:
        return 1
    return 0


class WorkerReadinessSupervisor:
    """Refresh master readiness and terminate after consecutive probe failures."""

    def __init__(
        self,
        *,
        probe: Callable[[], Mapping[str, str]],
        publish: Callable[[Mapping[str, str], float], None],
        revoke: Callable[[], None],
        shutdown: Callable[[], None],
        interval_seconds: float,
        failure_threshold: int,
        clock: Callable[[], float],
        owner_pid: int | None = None,
        current_pid: Callable[[], int] = os.getpid,
        stop_event: _StopEvent | None = None,
        thread_factory: Callable[..., _ThreadHandle] = Thread,
        join_timeout_seconds: float = 30,
    ) -> None:
        self._probe = probe
        self._publish = publish
        self._revoke = revoke
        self._shutdown = shutdown
        self._interval_seconds = interval_seconds
        self._failure_threshold = failure_threshold
        self._clock = clock
        self._current_pid = current_pid
        self._owner_pid = owner_pid if owner_pid is not None else current_pid()
        self._stop_event = stop_event or Event()
        self._thread_factory = thread_factory
        self._join_timeout_seconds = join_timeout_seconds
        self._lifecycle_lock = Lock()
        self._thread: _ThreadHandle | None = None
        self._consecutive_failures = 0
        self._shutdown_requested = False
        self._stopping = False

    def check(self) -> bool:
        """Run one deterministic probe cycle."""

        if self._shutdown_requested or self._stopping:
            return False
        try:
            statuses = self._probe()
            if self._stopping:
                return False
            self._publish(statuses, self._clock())
        except Exception:
            self._consecutive_failures += 1
            logger.warning(
                "Worker readiness check failed (%d/%d)",
                self._consecutive_failures,
                self._failure_threshold,
            )
            if self._consecutive_failures >= self._failure_threshold:
                self._shutdown_requested = True
                logger.error("Worker readiness failure threshold reached; stopping master")
                try:
                    self._revoke()
                finally:
                    self._shutdown()
            return False
        if self._consecutive_failures:
            logger.info("Worker readiness recovered")
        self._consecutive_failures = 0
        return True

    def start(self) -> bool:
        """Start one recurring probe thread in the owning master process."""

        if self._current_pid() != self._owner_pid:
            return False
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stopping = False
            self._stop_event.clear()
            self._thread = self._thread_factory(
                target=self._run,
                name="worker-readiness-supervisor",
                daemon=True,
            )
            self._thread.start()
        return True

    def stop(self) -> None:
        """Stop and boundedly join the recurring probe thread."""

        with self._lifecycle_lock:
            self._stopping = True
            self._stop_event.set()
            thread = self._thread
            self._thread = None
        if thread is not None and thread.is_alive() and thread is not current_thread():
            thread.join(timeout=self._join_timeout_seconds)

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            self.check()
            if self._shutdown_requested:
                return


def probe_worker_dependencies(settings: Settings) -> dict[str, str]:
    """Verify remote dependencies before the Celery master starts consumers."""

    broker = Connection(
        settings.rabbitmq_url,
        connect_timeout=WORKER_READINESS_BROKER_CONNECT_TIMEOUT_SECONDS,
    )
    try:
        broker.ensure_connection(max_retries=0)
    finally:
        broker.collect(socket_timeout=0.0)

    database = create_readiness_database(settings)
    try:
        with database.engine.connect() as connection:
            query_timeout_ms = round(WORKER_READINESS_MYSQL_QUERY_TIMEOUT_SECONDS * 1000)
            statement = text(f"SELECT /*+ MAX_EXECUTION_TIME({query_timeout_ms}) */ 1")
            if connection.execute(statement).scalar_one() != 1:
                raise RuntimeError("MySQL readiness query returned an unexpected result")

        object_storage_status = "not_required"
        provider_result_storage_status = "not_required"
        if settings.worker_requires_object_storage:
            object_storage = build_object_storage(settings)
            try:
                required_locations = [
                    StorageLocationClass.QUARANTINE,
                    StorageLocationClass.TASK,
                    StorageLocationClass.FOUNDATION,
                ]
                if settings.asset_queue_name in settings.configured_worker_queues:
                    required_locations.append(StorageLocationClass.PROVIDER_RESULT)
                    provider_result_storage_status = "ok"
                object_storage.assert_ready(
                    tuple(required_locations),
                )
                if settings.asset_queue_name in settings.configured_worker_queues:
                    product_brief.probe_product_brief_artifact_targets(
                        settings=settings,
                        database=database,
                        storage=object_storage,
                    )
            finally:
                close_object_storage(object_storage)
            object_storage_status = "ok"
        malware_status = "not_required"
        if settings.worker_requires_asset_validation:
            scanner = build_malware_scanner(settings)
            scanner.assert_ready()
            malware_status = "ok"
        indexing_status = {
            "milvus": "not_required",
            "embedding_provider": "not_required",
        }
        if settings.index_queue_name in settings.configured_worker_queues:
            indexing_status = probe_image_indexing_dependencies(settings)
        return {
            "broker": "ok",
            "mysql": "ok",
            "object_storage": object_storage_status,
            "malware_scanner": malware_status,
            "provider_result_storage": provider_result_storage_status,
            "vision_credential": validate_product_brief_vision_credential(settings),
            **indexing_status,
        }
    finally:
        database.dispose()


if __name__ == "__main__":  # pragma: no cover - exercised by the container
    raise SystemExit(main())
