"""Shared sanitizers for telemetry identity and bounded dimensions."""

from __future__ import annotations

import hashlib
import re
from typing import Protocol

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_DIMENSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")


class StructuredLogger(Protocol):
    def info(self, event: str, **values: object) -> object: ...

    def warning(self, event: str, **values: object) -> object: ...

    def error(self, event: str, **values: object) -> object: ...


def safe_token(value: str | None, *, always_hash: bool = False) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("telemetry identifiers must be strings")
    if not always_hash and _SAFE_IDENTIFIER.fullmatch(value):
        return value
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"


def bounded_dimension(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _SAFE_DIMENSION.fullmatch(value) is None:
        raise ValueError(f"telemetry {name} must be a bounded symbolic value")
    return value
