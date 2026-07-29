"""Shared observability setup."""

from .logging import configure_logging, get_logger
from .product_briefs import ProductBriefTelemetry

__all__ = ["ProductBriefTelemetry", "configure_logging", "get_logger"]
