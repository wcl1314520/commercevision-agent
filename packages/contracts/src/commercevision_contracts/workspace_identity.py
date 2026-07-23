"""Typed contract for canonical workspace identities."""

from typing import Annotated

from commercevision_domain import (
    WORKSPACE_ID_MAX_CHARACTERS,
    WORKSPACE_ID_PATTERN,
    is_valid_workspace_id,
    validate_workspace_id,
)
from pydantic import Field

WorkspaceId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=WORKSPACE_ID_MAX_CHARACTERS,
        pattern=WORKSPACE_ID_PATTERN,
    ),
]

__all__ = [
    "WORKSPACE_ID_MAX_CHARACTERS",
    "WORKSPACE_ID_PATTERN",
    "WorkspaceId",
    "is_valid_workspace_id",
    "validate_workspace_id",
]
