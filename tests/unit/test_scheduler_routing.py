from __future__ import annotations

from commercevision_contracts import Settings
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from commercevision_scheduler import runtime as scheduler_runtime


class CapturingCelery:
    def __init__(self, *_args, **_kwargs) -> None:
        self.calls: list[dict[str, object]] = []
        self.conf = self

    def conf_update(self, **_kwargs) -> None:
        return None

    def update(self, **_kwargs) -> None:
        return None

    def send_task(self, name: str, **kwargs) -> None:
        self.calls.append({"name": name, **kwargs})


def test_scheduler_publishes_to_queue_declared_by_event_contract(monkeypatch) -> None:
    client = CapturingCelery()
    monkeypatch.setattr(scheduler_runtime, "Celery", lambda *_args, **_kwargs: client)
    settings = Settings(
        workflow_queue_name="cv.workflow.v2",
        asset_queue_name="cv.asset.v2",
        index_queue_name="cv.index.v2",
        maintenance_queue_name="cv.maintenance.v2",
    )
    publisher = scheduler_runtime.CeleryMessagePublisher(settings)
    assert not hasattr(publisher, "publish")
    envelope = EventEnvelope.create(
        event_type="asset.validation.requested",
        aggregate_type="asset",
        aggregate_id="asset-1",
        aggregate_version=1,
        trace_id="trace-1",
        payload={},
    )

    publisher.publish_event(OutboxEvent(envelope=envelope, available_at=envelope.occurred_at))

    assert client.calls == [
        {
            "name": "commercevision.process_outbox_event",
            "args": [envelope.event_id],
            "task_id": envelope.event_id,
            "queue": "cv.asset.v2",
        }
    ]


def test_scheduler_sends_invalid_event_contract_to_configured_maintenance_queue(
    monkeypatch,
) -> None:
    client = CapturingCelery()
    monkeypatch.setattr(scheduler_runtime, "Celery", lambda *_args, **_kwargs: client)
    settings = Settings(maintenance_queue_name="cv.maintenance.v2")
    publisher = scheduler_runtime.CeleryMessagePublisher(settings)
    envelope = EventEnvelope.create(
        event_type="event.never-registered",
        aggregate_type="asset",
        aggregate_id="asset-1",
        aggregate_version=1,
        trace_id="trace-1",
        payload={},
    )

    publisher.publish_event(OutboxEvent(envelope=envelope, available_at=envelope.occurred_at))

    assert client.calls[0]["queue"] == "cv.maintenance.v2"


def test_scheduler_sends_malformed_known_event_to_configured_maintenance_queue(
    monkeypatch,
) -> None:
    client = CapturingCelery()
    monkeypatch.setattr(scheduler_runtime, "Celery", lambda *_args, **_kwargs: client)
    settings = Settings(maintenance_queue_name="cv.maintenance.v2")
    publisher = scheduler_runtime.CeleryMessagePublisher(settings)
    envelope = EventEnvelope.create(
        event_type="workflow.run.requested",
        aggregate_type="workflow",
        aggregate_id="workflow-1",
        aggregate_version=1,
        trace_id="trace-1",
        payload={"action": "start"},
    )

    publisher.publish_event(OutboxEvent(envelope=envelope, available_at=envelope.occurred_at))

    assert client.calls[0]["queue"] == "cv.maintenance.v2"
