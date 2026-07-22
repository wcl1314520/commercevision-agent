"""Versioned event routing shared by the scheduler and durable worker."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from commercevision_contracts.events import EVENT_CONTRACTS, EventContract, EventQueue
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from pydantic import ValidationError


class EventRoutingError(RuntimeError):
    """Base class for permanent event contract failures."""

    reason: str

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class UnknownEventTypeError(EventRoutingError):
    def __init__(self, event_type: str) -> None:
        super().__init__(
            f"event type {event_type!r} is not registered",
            reason="unknown_event_type",
        )


class UnsupportedSchemaVersionError(EventRoutingError):
    def __init__(self, event_type: str, schema_version: int) -> None:
        super().__init__(
            f"event type {event_type!r} does not support schema version {schema_version}",
            reason="unsupported_schema_version",
        )


class MalformedEventPayloadError(EventRoutingError):
    def __init__(self, event_type: str, schema_version: int, error_count: int) -> None:
        super().__init__(
            (
                f"event type {event_type!r} schema version {schema_version} "
                f"has {error_count} payload validation error(s)"
            ),
            reason="malformed_event_payload",
        )


class UnhandledEventError(EventRoutingError):
    def __init__(self, event_type: str, schema_version: int) -> None:
        super().__init__(
            f"event type {event_type!r} schema version {schema_version} has no handler",
            reason="unhandled_event",
        )


class DuplicateEventRegistrationError(EventRoutingError):
    def __init__(self, event_type: str, schema_version: int) -> None:
        super().__init__(
            f"event type {event_type!r} schema version {schema_version} is already registered",
            reason="duplicate_event_registration",
        )


EventHandler = Callable[[OutboxEvent], None]


@dataclass(frozen=True, slots=True)
class EventRoute:
    contract: EventContract
    queue: str
    handler: EventHandler | None


class EventRoutingRegistry:
    """The single public registry for versioned event routes and handlers."""

    def __init__(self) -> None:
        self._routes: dict[tuple[str, int], EventRoute] = {}

    def register(
        self,
        *,
        contract: EventContract,
        queue: str,
        handler: EventHandler | None,
    ) -> None:
        key = (contract.event_type.value, contract.schema_version)
        if key in self._routes:
            raise DuplicateEventRegistrationError(*key)
        self._routes[key] = EventRoute(
            contract=contract,
            queue=queue,
            handler=handler,
        )

    def register_handler(
        self,
        *,
        contract: EventContract,
        handler: EventHandler,
    ) -> None:
        key = (contract.event_type.value, contract.schema_version)
        route = self._routes.get(key)
        if route is None:
            raise UnknownEventTypeError(contract.event_type.value)
        if route.handler is not None:
            raise DuplicateEventRegistrationError(*key)
        self._routes[key] = replace(route, handler=handler)

    def queue_for(self, envelope: EventEnvelope) -> str:
        return self._route_for(envelope).queue

    def resolve(self, envelope: EventEnvelope) -> EventHandler:
        route = self._route_for(envelope)
        if route.handler is None:
            raise UnhandledEventError(envelope.event_type, envelope.schema_version)
        return route.handler

    def _route_for(self, envelope: EventEnvelope) -> EventRoute:
        if not any(event_type == envelope.event_type for event_type, _ in self._routes):
            raise UnknownEventTypeError(envelope.event_type)
        route = self._routes.get((envelope.event_type, envelope.schema_version))
        if route is None:
            raise UnsupportedSchemaVersionError(
                envelope.event_type,
                envelope.schema_version,
            )
        try:
            route.contract.validate_payload(envelope.payload)
        except ValidationError as exc:
            raise MalformedEventPayloadError(
                envelope.event_type,
                envelope.schema_version,
                exc.error_count(),
            ) from exc
        return route


def build_event_routing_registry(
    queue_names: Mapping[EventQueue, str],
) -> EventRoutingRegistry:
    registry = EventRoutingRegistry()
    for contract in EVENT_CONTRACTS:
        registry.register(
            contract=contract,
            queue=queue_names[contract.queue],
            handler=None,
        )
    return registry
