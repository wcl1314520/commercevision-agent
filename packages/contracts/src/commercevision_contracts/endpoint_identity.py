"""Canonical network endpoint identities used by outbound-transfer policy."""

from __future__ import annotations

import ipaddress
import re

_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", flags=re.ASCII)


def validate_canonical_endpoint_host(value: str) -> str:
    """Return an exact canonical DNS host or fail closed.

    Provider endpoints are host identities, not URLs. Schemes, ports, paths,
    wildcards, IP literals, Unicode, case folding, and whitespace normalization
    are deliberately excluded from the policy language.
    """

    if (
        not isinstance(value, str)
        or not value
        or len(value) > 253
        or not value.isascii()
        or value != value.strip()
        or value != value.lower()
        or value.endswith(".")
        or "." not in value
    ):
        raise ValueError("endpoint host must be an exact canonical DNS hostname")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        raise ValueError("endpoint host must not be an IP literal")
    if any(_DNS_LABEL.fullmatch(label) is None for label in value.split(".")):
        raise ValueError("endpoint host must be an exact canonical DNS hostname")
    return value


__all__ = ["validate_canonical_endpoint_host"]
