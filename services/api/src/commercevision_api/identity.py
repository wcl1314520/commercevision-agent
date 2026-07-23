"""Trusted-gateway identity adapter and workspace authorization policy."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import UTC, datetime

from commercevision_application import AuthenticatedPrincipal
from commercevision_domain import (
    AdminRequiredError,
    AuthenticationError,
    WorkspaceAccessError,
    is_valid_workspace_id,
    validate_workspace_id,
)

_MAX_ACTOR_ID_CHARACTERS = 128


class SignedTrustedPrincipalResolver:
    """Verify short-lived principals signed by a trusted ingress gateway."""

    def __init__(
        self,
        *,
        current_key_id: str | None,
        current_secret: str | None,
        previous_key_id: str | None = None,
        previous_secret: str | None = None,
        max_age_seconds: int,
        future_skew_seconds: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._keys: dict[str, bytes] = {}
        if current_key_id is not None and current_secret is not None:
            self._keys[current_key_id] = current_secret.encode()
        if previous_key_id is not None and previous_secret is not None:
            if previous_key_id in self._keys:
                raise ValueError("trusted-principal verification key ids must be distinct")
            self._keys[previous_key_id] = previous_secret.encode()
        self._unknown_key_secret = hashlib.sha256(
            b"commercevision-trusted-principal-unknown-key"
        ).digest()
        self._max_age_seconds = max_age_seconds
        self._future_skew_seconds = future_skew_seconds
        self._clock = clock or (lambda: datetime.now(UTC))

    def resolve(self, token: str | None) -> AuthenticatedPrincipal:
        if not self._keys or token is None:
            raise AuthenticationError("a trusted principal is required")
        try:
            key_id, encoded, signature = token.split(".")
            secret = self._keys.get(key_id)
            verification_secret = secret or self._unknown_key_secret
            expected = hmac.new(
                verification_secret,
                f"{key_id}.{encoded}".encode(),
                hashlib.sha256,
            ).hexdigest()
            signature_valid = hmac.compare_digest(signature, expected)
            if secret is None or not signature_valid:
                raise AuthenticationError("trusted principal signature is invalid")
            padded = encoded + "=" * (-len(encoded) % 4)
            claims = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True).decode())
            principal = self._parse_claims(claims)
        except AuthenticationError:
            raise
        except (
            ValueError,
            TypeError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            binascii.Error,
        ) as exc:
            raise AuthenticationError("trusted principal is invalid") from exc

        now = int(self._clock().timestamp())
        issued_at = claims["issued_at"]
        if issued_at > now + self._future_skew_seconds:
            raise AuthenticationError("trusted principal was issued in the future")
        if issued_at < now - self._max_age_seconds:
            raise AuthenticationError("trusted principal has expired")
        return principal

    @staticmethod
    def _parse_claims(claims: object) -> AuthenticatedPrincipal:
        if not isinstance(claims, dict):
            raise AuthenticationError("trusted principal claims are invalid")
        actor_id = claims.get("actor_id")
        workspace_ids = claims.get("workspace_ids")
        admin_workspace_ids = claims.get("admin_workspace_ids")
        system_admin = claims.get("system_admin")
        issued_at = claims.get("issued_at")
        if (
            not _valid_actor_id(actor_id)
            or not _valid_workspace_id_list(workspace_ids)
            or not _valid_workspace_id_list(admin_workspace_ids)
            or not isinstance(system_admin, bool)
            or not isinstance(issued_at, int)
            or isinstance(issued_at, bool)
        ):
            raise AuthenticationError("trusted principal claims are invalid")
        workspaces = frozenset(workspace_ids)
        admin_workspaces = frozenset(admin_workspace_ids)
        if not admin_workspaces.issubset(workspaces):
            raise AuthenticationError("administrator grants require workspace membership")
        # A system administrator may be workspace-less for the read-only legacy API.
        return AuthenticatedPrincipal(
            actor_id=actor_id,
            workspace_ids=workspaces,
            admin_workspace_ids=admin_workspaces,
            system_admin=system_admin,
        )


class PrincipalAccessPolicy:
    def require_workspace(
        self,
        *,
        workspace_id: str,
        principal: AuthenticatedPrincipal,
    ) -> None:
        validate_workspace_id(workspace_id)
        if workspace_id not in principal.workspace_ids:
            raise WorkspaceAccessError("workspace membership is required")

    def require_admin(
        self,
        *,
        workspace_id: str,
        principal: AuthenticatedPrincipal,
    ) -> None:
        self.require_workspace(workspace_id=workspace_id, principal=principal)
        if workspace_id not in principal.admin_workspace_ids:
            raise AdminRequiredError("workspace administrator privileges are required")

    def require_system_admin(self, *, principal: AuthenticatedPrincipal) -> None:
        if not principal.system_admin:
            raise AdminRequiredError("system administrator privileges are required")


def _valid_actor_id(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= _MAX_ACTOR_ID_CHARACTERS


def _valid_workspace_id_list(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(is_valid_workspace_id(item) for item in value)
        and len(value) == len(set(value))
    )
