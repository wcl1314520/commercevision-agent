"""Canonical tenant-boundary identity contract."""

from __future__ import annotations

import re

WORKSPACE_ID_MAX_CHARACTERS = 128
WORKSPACE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_WORKSPACE_ID_RE = re.compile(WORKSPACE_ID_PATTERN, flags=re.ASCII)
_WORKSPACE_ID_ERROR = f"workspace_id must match {WORKSPACE_ID_PATTERN}"


def is_valid_workspace_id(value: object) -> bool:
    """Return whether value is an exact canonical workspace token."""

    return isinstance(value, str) and _WORKSPACE_ID_RE.fullmatch(value) is not None


def validate_workspace_id(value: object) -> str:
    """Return an unchanged canonical workspace token or reject it."""

    if not is_valid_workspace_id(value):
        raise ValueError(_WORKSPACE_ID_ERROR)
    return value
