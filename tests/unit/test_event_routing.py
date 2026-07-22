from __future__ import annotations

import pytest
from commercevision_application import (
    DuplicateEventRegistrationError,
    EventRoutingRegistry,
    MalformedEventPayloadError,
    UnknownEventTypeError,
    UnsupportedSchemaVersionError,
)
from commercevision_contracts.events import (
    WORKFLOW_RUN_REQUESTED_V1,
    EventType,
    event_contract_for,
)
from commercevision_domain.messaging import EventEnvelope, OutboxEvent


def _event(
    *,
    event_type: str = "asset.validation.requested",
    schema_version: int = 1,
) -> OutboxEvent:
    envelope = EventEnvelope.create(
        event_type=event_type,
        aggregate_type="asset",
        aggregate_id="asset-1",
        aggregate_version=1,
        trace_id="trace-1",
        payload={},
        schema_version=schema_version,
    )
    return OutboxEvent(envelope=envelope, available_at=envelope.occurred_at)


def test_event_routes_by_type_and_schema_version() -> None:
    handled: list[str] = []
    registry = EventRoutingRegistry()
    registry.register(
        contract=event_contract_for(EventType.ASSET_VALIDATION_REQUESTED, 1),
        queue="configured.asset",
        handler=lambda event: handled.append(event.envelope.event_id),
    )

    event = _event()

    assert registry.queue_for(event.envelope) == "configured.asset"
    registry.resolve(event.envelope)(event)
    assert handled == [event.envelope.event_id]


def test_event_registration_rejects_duplicate_type_and_version() -> None:
    registry = EventRoutingRegistry()
    contract = event_contract_for(EventType.ASSET_VALIDATION_REQUESTED, 1)
    registry.register(
        contract=contract,
        queue="configured.asset",
        handler=lambda _event: None,
    )

    with pytest.raises(DuplicateEventRegistrationError):
        registry.register(
            contract=contract,
            queue="configured.asset",
            handler=lambda _event: None,
        )


def test_unknown_event_type_is_not_routable() -> None:
    registry = EventRoutingRegistry()

    with pytest.raises(UnknownEventTypeError):
        registry.queue_for(_event(event_type="asset.event.never-registered").envelope)


def test_unsupported_schema_version_is_not_routable() -> None:
    registry = EventRoutingRegistry()
    registry.register(
        contract=event_contract_for(EventType.ASSET_VALIDATION_REQUESTED, 1),
        queue="configured.asset",
        handler=lambda _event: None,
    )

    with pytest.raises(UnsupportedSchemaVersionError):
        registry.queue_for(_event(schema_version=2).envelope)


def test_malformed_payload_is_not_routable() -> None:
    registry = EventRoutingRegistry()
    registry.register(
        contract=WORKFLOW_RUN_REQUESTED_V1,
        queue="configured.workflow",
        handler=lambda _event: None,
    )
    event = _event(event_type=EventType.WORKFLOW_RUN_REQUESTED)

    with pytest.raises(MalformedEventPayloadError) as error:
        registry.queue_for(event.envelope)

    assert error.value.reason == "malformed_event_payload"
