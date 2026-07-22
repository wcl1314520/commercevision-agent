"""Celery worker process."""

from .celery_app import celery_app

__all__ = ["celery_app"]
