from __future__ import annotations

from importlib import import_module

worker_module = import_module("commercevision_worker.celery_app")


class FakeRuntime:
    def process_event(self, event_id: str) -> str:
        return f"handled:{event_id}"


def test_celery_task_delegates_without_business_retry(monkeypatch) -> None:
    monkeypatch.setattr(worker_module, "_get_runtime", lambda: FakeRuntime())

    assert worker_module.process_outbox_event.run("event-1") == "handled:event-1"
    assert worker_module.celery_app.conf.task_acks_late is True
    assert worker_module.celery_app.conf.task_acks_on_failure_or_timeout is False
    assert worker_module.celery_app.conf.task_reject_on_worker_lost is True
    assert {queue.name for queue in worker_module.celery_app.conf.task_queues} == set(
        worker_module.settings.configured_worker_queues
    )
