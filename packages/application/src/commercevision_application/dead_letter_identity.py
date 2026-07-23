"""Canonical dead-letter identity boundary shared by HTTP and application callers."""

from __future__ import annotations

import re
from uuid import UUID

from commercevision_domain import NotFoundError

_HYPHENATED_UUID_PATTERN = re.compile(
    r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}",
    re.ASCII,
)
_DEAD_LETTER_NOT_FOUND = "dead letter was not found"


def canonicalize_dead_letter_id(dead_letter_id: str) -> str:
    """Accept only exact hyphenated ASCII UUID text and return lowercase form."""
    if (
        not isinstance(dead_letter_id, str)
        or _HYPHENATED_UUID_PATTERN.fullmatch(dead_letter_id) is None
    ):
        raise NotFoundError(_DEAD_LETTER_NOT_FOUND)
    try:
        return str(UUID(dead_letter_id))
    except ValueError:
        raise NotFoundError(_DEAD_LETTER_NOT_FOUND) from None
