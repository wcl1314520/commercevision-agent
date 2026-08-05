"""Authenticated, query-bound cursors for Creative Plan version history."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import struct
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from commercevision_domain import canonicalize_uuid, validate_workspace_id

_TOKEN_VERSION = "v1"
_SCHEMA_VERSION = 1
_MAX_TOKEN_BYTES = 256
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$", flags=re.ASCII)
_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$", flags=re.ASCII)
_PAYLOAD = struct.Struct(">BqQ")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_KEY_DOMAIN = b"CommerceVision.CreativePlanCursor.Key.v1"
_SCOPE_DOMAIN = b"CommerceVision.CreativePlanCursor.Scope.v1"
_SORT_SCHEMA = b"version_number-asc/v1"
_INVALID_CURSOR = "Creative Plan cursor is invalid"
_UNKNOWN_SECRET = hashlib.sha256(b"CommerceVision.CreativePlanCursor.Unknown.v1").digest()


class CreativePlanCursorCodec:
    """Issue short-lived cursors bound to one tenant, Workflow, and plan."""

    def __init__(
        self,
        *,
        current_key_id: str | None,
        current_secret: str | bytes | None,
        previous_key_id: str | None = None,
        previous_secret: str | bytes | None = None,
        max_age_seconds: int,
        future_skew_seconds: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 604_800:
            raise ValueError("Creative Plan cursor max age is invalid")
        if type(future_skew_seconds) is not int or not 0 <= future_skew_seconds <= 300:
            raise ValueError("Creative Plan cursor future skew is invalid")
        current = _validate_key_pair(current_key_id, current_secret, label="current")
        previous = _validate_key_pair(previous_key_id, previous_secret, label="previous")
        if current is not None and previous is not None and current[0] == previous[0]:
            raise ValueError("Creative Plan cursor key ids must be distinct")
        self._keys: dict[str, bytes] = {}
        self._current_key_id: str | None = None
        for key_pair, is_current in ((current, True), (previous, False)):
            if key_pair is None:
                continue
            key_id, secret = key_pair
            self._keys[key_id] = _derive_key(key_id, secret)
            if is_current:
                self._current_key_id = key_id
        self._unknown_key = _derive_key("unknown", _UNKNOWN_SECRET)
        self._max_age_microseconds = max_age_seconds * 1_000_000
        self._future_skew_microseconds = future_skew_seconds * 1_000_000
        self._clock = clock or (lambda: datetime.now(UTC))

    def encode(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
        version_number: int,
    ) -> str:
        if self._current_key_id is None:
            raise RuntimeError("Creative Plan cursor signing key is not configured")
        scope = _scope(workspace_id, workflow_id, creative_plan_id)
        version_number = _validate_version_number(version_number)
        payload = _PAYLOAD.pack(
            _SCHEMA_VERSION,
            _datetime_to_microseconds(_validate_utc(self._clock())),
            version_number,
        )
        encoded_payload = _base64url_encode(payload)
        signed = f"{_TOKEN_VERSION}.{self._current_key_id}.{encoded_payload}"
        signature = hmac.digest(
            self._keys[self._current_key_id],
            _frame(signed.encode("ascii"), scope),
            hashlib.sha256,
        )
        token = f"{signed}.{_base64url_encode(signature)}"
        if len(token) > _MAX_TOKEN_BYTES:
            raise RuntimeError("Creative Plan cursor exceeds its transport limit")
        return token

    def decode(
        self,
        token: str,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
    ) -> int:
        try:
            if type(token) is not str or not token or len(token) > _MAX_TOKEN_BYTES:
                raise ValueError
            token.encode("ascii")
            scope = _scope(workspace_id, workflow_id, creative_plan_id)
            parts = token.split(".")
            if len(parts) != 4 or parts[0] != _TOKEN_VERSION:
                raise ValueError
            _, key_id, encoded_payload, encoded_signature = parts
            _validate_key_id(key_id)
            signature = _base64url_decode(encoded_signature)
            known_key = self._keys.get(key_id)
            verification_key = known_key or self._unknown_key
            signed = f"{_TOKEN_VERSION}.{key_id}.{encoded_payload}"
            expected = hmac.digest(
                verification_key,
                _frame(signed.encode("ascii"), scope),
                hashlib.sha256,
            )
            if not (hmac.compare_digest(signature, expected) & (known_key is not None)):
                raise ValueError
            payload = _base64url_decode(encoded_payload)
            if len(payload) != _PAYLOAD.size:
                raise ValueError
            schema, issued_at, version_number = _PAYLOAD.unpack(payload)
            if schema != _SCHEMA_VERSION:
                raise ValueError
            now = _datetime_to_microseconds(_validate_utc(self._clock()))
            if (
                issued_at > now + self._future_skew_microseconds
                or issued_at < now - self._max_age_microseconds
            ):
                raise ValueError
            return _validate_version_number(version_number)
        except Exception:
            raise ValueError(_INVALID_CURSOR) from None


def _validate_key_pair(
    key_id: str | None,
    secret: str | bytes | None,
    *,
    label: str,
) -> tuple[str, bytes] | None:
    if (key_id is None) != (secret is None):
        raise ValueError(f"{label} Creative Plan cursor key is incomplete")
    if key_id is None or secret is None:
        return None
    key_id = _validate_key_id(key_id)
    if isinstance(secret, str):
        secret_bytes = secret.encode("utf-8")
    elif isinstance(secret, bytes):
        secret_bytes = secret
    else:
        raise TypeError(f"{label} Creative Plan cursor secret is invalid")
    if len(secret_bytes) < 32:
        raise ValueError(f"{label} Creative Plan cursor secret is too short")
    return key_id, secret_bytes


def _validate_key_id(value: object) -> str:
    if type(value) is not str or _KEY_ID_RE.fullmatch(value) is None:
        raise ValueError("Creative Plan cursor key id is invalid")
    return value


def _derive_key(key_id: str, secret: bytes) -> bytes:
    return hmac.digest(secret, _frame(_KEY_DOMAIN, key_id.encode("ascii")), hashlib.sha256)


def _scope(workspace_id: str, workflow_id: str, creative_plan_id: str) -> bytes:
    validate_workspace_id(workspace_id)
    workflow_id = canonicalize_uuid(workflow_id)
    creative_plan_id = canonicalize_uuid(creative_plan_id)
    return _frame(
        _SCOPE_DOMAIN,
        _SORT_SCHEMA,
        workspace_id.encode("ascii"),
        UUID(workflow_id).bytes,
        UUID(creative_plan_id).bytes,
    )


def _validate_version_number(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 1_000_000:
        raise ValueError("Creative Plan cursor version is invalid")
    return value


def _validate_utc(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("Creative Plan cursor clock must return UTC")
    return value


def _datetime_to_microseconds(value: datetime) -> int:
    delta = value - _EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def _frame(*values: bytes) -> bytes:
    framed = bytearray()
    for value in values:
        framed.extend(struct.pack(">I", len(value)))
        framed.extend(value)
    return bytes(framed)


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    if not value or "=" in value or _BASE64URL_RE.fullmatch(value) is None:
        raise ValueError
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error):
        raise ValueError from None
    if _base64url_encode(decoded) != value:
        raise ValueError
    return decoded
