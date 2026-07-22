"""Celery application configured for reliable at-least-once delivery."""

from typing import Any

from celery import Celery
from celery.signals import worker_process_shutdown
from commercevision_contracts.config import load_settings
from commercevision_observability import configure_logging

settings = load_settings("worker")
configure_logging(settings.log_level)

celery_app = Celery("commercevision-worker", broker=settings.rabbitmq_url)
celery_app.conf.update(
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
    enable_utc=True,
    result_backend=None,
    task_acks_late=True,
    task_default_queue="commercevision.workflow",
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

_runtime: Any | None = None


def _get_runtime():
    global _runtime
    if _runtime is None:
        from .runtime import WorkerRuntime

        _runtime = WorkerRuntime.build(settings)
    return _runtime


@celery_app.task(
    bind=True,
    name="commercevision.process_outbox_event",
    max_retries=settings.workflow_message_max_attempts,
)
def process_outbox_event(self, event_id: str) -> str:
    try:
        return _get_runtime().process_event(event_id)
    except Exception as exc:
        countdown = min(60, 2 ** min(self.request.retries, 6))
        raise self.retry(exc=exc, countdown=countdown) from exc


@worker_process_shutdown.connect
def close_runtime(**_: Any) -> None:
    global _runtime
    if _runtime is not None:
        _runtime.close()
        _runtime = None
