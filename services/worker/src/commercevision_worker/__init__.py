"""Celery worker process."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from celery import Celery

    celery_app: Celery

__all__ = ["celery_app"]


def __getattr__(name: str) -> Any:
    """Load the Celery application without polluting healthcheck module startup."""

    if name == "celery_app":
        application = import_module(f"{__name__}.celery_app").celery_app
        globals()[name] = application
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
