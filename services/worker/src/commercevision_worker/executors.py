"""Production discovery for Durable Operation executor providers."""

from __future__ import annotations

from collections.abc import Mapping
from importlib.metadata import entry_points
from typing import Protocol, cast

from commercevision_application import OperationExecutor
from commercevision_contracts import Settings
from commercevision_domain import OperationKind

OPERATION_EXECUTOR_ENTRY_POINT_GROUP = "commercevision.operation_executors"


class OperationExecutorFactory(Protocol):
    def __call__(self, settings: Settings) -> OperationExecutor: ...


def discover_operation_executor_factories() -> dict[OperationKind, OperationExecutorFactory]:
    """Load typed executor factories registered by installed provider packages."""

    factories: dict[OperationKind, OperationExecutorFactory] = {}
    for entry_point in entry_points(group=OPERATION_EXECUTOR_ENTRY_POINT_GROUP):
        try:
            kind = OperationKind(entry_point.name)
        except ValueError as exc:
            raise RuntimeError(
                f"unknown operation executor entry point kind: {entry_point.name}"
            ) from exc
        if kind in factories:
            raise RuntimeError(f"duplicate operation executor entry point: {kind.value}")
        factory = entry_point.load()
        if not callable(factory):
            raise RuntimeError(f"operation executor factory is not callable: {kind.value}")
        factories[kind] = cast(OperationExecutorFactory, factory)
    return factories


def build_operation_executors(
    *,
    settings: Settings,
    factories: Mapping[OperationKind, OperationExecutorFactory],
) -> dict[OperationKind, OperationExecutor]:
    executors: dict[OperationKind, OperationExecutor] = {}
    for kind, factory in factories.items():
        executor = factory(settings)
        if not callable(getattr(executor, "execute", None)) or not callable(
            getattr(executor, "reconcile", None)
        ):
            raise RuntimeError(
                f"operation executor does not implement execute/reconcile: {kind.value}"
            )
        executors[kind] = executor
    return executors
