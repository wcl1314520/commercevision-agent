"""Shared composition helpers used by independently deployed services."""

from .retrieval import BuiltRetrieval, build_retrieval

__all__ = ["BuiltRetrieval", "build_retrieval"]
