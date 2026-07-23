"""Shared HTTP boundary for canonical workspace identities."""

from typing import Annotated

from commercevision_domain import (
    WORKSPACE_ID_MAX_CHARACTERS,
    WORKSPACE_ID_PATTERN,
)
from fastapi import Header

WorkspaceHeader = Annotated[
    str,
    Header(
        alias="X-Workspace-Id",
        min_length=1,
        max_length=WORKSPACE_ID_MAX_CHARACTERS,
        pattern=WORKSPACE_ID_PATTERN,
    ),
]
