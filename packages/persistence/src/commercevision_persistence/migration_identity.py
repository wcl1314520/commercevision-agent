"""Resolve the privileged database identity used only by schema migrations."""

from __future__ import annotations

import os

from commercevision_contracts.config import load_settings
from sqlalchemy.engine import URL
from sqlalchemy.exc import ArgumentError

from .database import sync_mysql_url

_DEPLOYED_ENVIRONMENTS = frozenset({"staging", "demo", "production"})


def resolve_migration_mysql_url() -> URL:
    """Return a synchronous MySQL URL without falling back in deployed environments."""

    configured = os.environ.get("CV_MIGRATION_MYSQL_DSN")
    if configured is not None:
        if not configured or configured != configured.strip():
            raise RuntimeError("CV_MIGRATION_MYSQL_DSN must be a non-empty canonical DSN")
        dsn = configured
    else:
        settings = load_settings("migration")
        if settings.environment in _DEPLOYED_ENVIRONMENTS:
            raise RuntimeError("CV_MIGRATION_MYSQL_DSN is required for deployed schema migrations")
        dsn = settings.mysql_dsn

    try:
        return sync_mysql_url(dsn)
    except (ArgumentError, ValueError) as exc:
        raise RuntimeError("migration identity must be a supported MySQL DSN") from exc
