"""Provider-neutral operational readiness boundary for object storage."""

from collections.abc import Iterable
from typing import Protocol

from commercevision_domain import StorageLocationClass


class ObjectStorageReadinessError(RuntimeError):
    """Configured object storage cannot safely accept application traffic."""


class ObjectStorageReadiness(Protocol):
    def assert_ready(
        self,
        required_locations: Iterable[StorageLocationClass] | None = None,
    ) -> None: ...


def close_object_storage(storage: object) -> None:
    """Close provider SDK pools when an adapter leaves its process boundary."""

    close = getattr(storage, "close", None)
    if callable(close):
        close()
