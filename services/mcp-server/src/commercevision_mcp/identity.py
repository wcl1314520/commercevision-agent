"""Request-local, signed identity context for MCP tool calls."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from datetime import UTC, datetime

from commercevision_contracts import McpToolIdentityV1
from commercevision_domain import AuthenticationError


class SignedMcpIdentityResolver:
    def __init__(self, *, keys: dict[str, str], max_age_seconds: int, future_skew_seconds: int):
        self._keys = {key_id: secret.encode() for key_id, secret in keys.items()}
        self._unknown = hashlib.sha256(b"commercevision-mcp-unknown-key").digest()
        self._max_age_seconds = max_age_seconds
        self._future_skew_seconds = future_skew_seconds

    def resolve(self, token: str | None) -> McpToolIdentityV1:
        if token is None or not self._keys:
            raise AuthenticationError("a signed MCP identity context is required")
        if len(token) > 32 * 1024:
            raise AuthenticationError("MCP identity context is invalid")
        try:
            key_id, encoded, signature = token.split(".")
            secret = self._keys.get(key_id)
            expected = hmac.new(
                secret or self._unknown,
                f"{key_id}.{encoded}".encode(),
                hashlib.sha256,
            ).hexdigest()
            if secret is None or not hmac.compare_digest(signature, expected):
                raise AuthenticationError("MCP identity signature is invalid")
            padded = encoded + "=" * (-len(encoded) % 4)
            claims = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
            identity = McpToolIdentityV1.model_validate(claims)
        except AuthenticationError:
            raise
        except (ValueError, TypeError, json.JSONDecodeError, binascii.Error) as exc:
            raise AuthenticationError("MCP identity context is invalid") from exc
        now = int(datetime.now(UTC).timestamp())
        if identity.issued_at > now + self._future_skew_seconds:
            raise AuthenticationError("MCP identity context was issued in the future")
        if identity.issued_at < now - self._max_age_seconds:
            raise AuthenticationError("MCP identity context has expired")
        return identity


def identity_from_request(request, resolver: SignedMcpIdentityResolver) -> McpToolIdentityV1:
    """Resolve identity from MCP's transport request, including across task boundaries."""
    if request is None:
        raise AuthenticationError("HTTP MCP transport identity is required")
    return resolver.resolve(request.headers.get("x-trusted-principal"))
