"""Workspace-scoped product catalog domain."""

from .entities import SKU, Product
from .errors import DuplicateExternalIdentifierError

__all__ = ["DuplicateExternalIdentifierError", "Product", "SKU"]
