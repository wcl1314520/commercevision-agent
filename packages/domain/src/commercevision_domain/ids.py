"""Ordered identifier generation without infrastructure dependencies."""

from __future__ import annotations

import re
import secrets
import time
from uuid import UUID

UUID_PATTERN = (
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)
_HYPHENATED_UUID_PATTERN = re.compile(UUID_PATTERN, re.ASCII)


def canonicalize_uuid(value: object) -> str:
    """Accept exact hyphenated ASCII UUID text and return lowercase form."""

    if not isinstance(value, str) or _HYPHENATED_UUID_PATTERN.fullmatch(value) is None:
        raise ValueError("identifier must be an exact hyphenated ASCII UUID")
    try:
        return str(UUID(value))
    except ValueError:
        raise ValueError("identifier must be a valid UUID") from None


def new_uuid7() -> str:
    """Return an RFC 9562 UUIDv7 string suitable for ordered database keys."""

    unix_ms = time.time_ns() // 1_000_000
    if unix_ms >= 1 << 48:
        raise OverflowError("current Unix timestamp does not fit UUIDv7")

    random_a = secrets.randbits(12)
    random_b = secrets.randbits(62)
    value = (unix_ms << 80) | (0x7 << 76) | (random_a << 64) | (0b10 << 62) | random_b
    return str(UUID(int=value))
