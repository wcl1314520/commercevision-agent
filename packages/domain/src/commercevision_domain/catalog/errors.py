"""Catalog-specific domain failures."""

from commercevision_domain.workflow.errors import DomainError


class DuplicateExternalIdentifierError(DomainError):
    """An external identity already exists in the workspace and source namespace."""
