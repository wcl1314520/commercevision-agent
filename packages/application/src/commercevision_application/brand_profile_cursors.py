"""Authenticated, query-bound pagination cursors for Brand Profiles."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import struct
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

_TOKEN_VERSION = "v1"
_SCHEMA_VERSION = 1
_PROFILE_KIND = 1
_VERSION_KIND = 2
_MAX_TOKEN_BYTES = 256
_MAX_KEY_ID_BYTES = 64
_MAX_SECRET_DURATION_SECONDS = ((1 << 63) - 1) // 1_000_000
_INVALID_CURSOR = "Brand Profile cursor is invalid"
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$", flags=re.ASCII)
_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$", flags=re.ASCII)
_WORKSPACE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", flags=re.ASCII)
_COMMON_PAYLOAD = struct.Struct(">BBq32s")
_PROFILE_BOUNDARY = struct.Struct(">q16s")
_VERSION_BOUNDARY = struct.Struct(">Q")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_KEY_DERIVATION_DOMAIN = b"CommerceVision.BrandProfileCursor.KeyDerivation.v1"
_PROFILE_SCOPE_DOMAIN = b"CommerceVision.BrandProfileCursor.ProfileScope.v1"
_VERSION_SCOPE_DOMAIN = b"CommerceVision.BrandProfileCursor.VersionScope.v1"
_PROFILE_SORT_SCHEMA = b"created_at-desc,id-desc/v1"
_VERSION_SORT_SCHEMA = b"version_number-desc/v1"
_UNKNOWN_ROOT_KEY = hashlib.sha256(b"CommerceVision.BrandProfileCursor.UnknownKey.v1").digest()


@dataclass(frozen=True, slots=True)
class _KeyMaterial:
    signing_key: bytes
    scope_key: bytes


class BrandProfileCursorCodec:
    """Issue and verify short-lived opaque cursors bound to an exact query scope."""

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
        _validate_duration(max_age_seconds, field="max_age_seconds", allow_zero=False)
        _validate_duration(future_skew_seconds, field="future_skew_seconds", allow_zero=True)
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")

        current_pair = _validate_key_pair(
            key_id=current_key_id,
            secret=current_secret,
            label="current",
        )
        previous_pair = _validate_key_pair(
            key_id=previous_key_id,
            secret=previous_secret,
            label="previous",
        )
        if (
            current_pair is not None
            and previous_pair is not None
            and current_pair[0] == previous_pair[0]
        ):
            raise ValueError("Brand Profile cursor key ids must be distinct")

        self._keys: dict[str, _KeyMaterial] = {}
        self._current_key_id: str | None = None
        if current_pair is not None:
            self._current_key_id = current_pair[0]
            self._keys[current_pair[0]] = _derive_key_material(*current_pair)
        if previous_pair is not None:
            self._keys[previous_pair[0]] = _derive_key_material(*previous_pair)
        self._unknown_key = _derive_key_material("unknown", _UNKNOWN_ROOT_KEY)
        self._max_age_microseconds = max_age_seconds * 1_000_000
        self._future_skew_microseconds = future_skew_seconds * 1_000_000
        self._clock = clock or (lambda: datetime.now(UTC))

    def encode_profiles(
        self,
        *,
        workspace_id: str,
        brand: str | None,
        created_at: datetime,
        profile_id: str,
    ) -> str:
        """Encode the final `(created_at, id)` item of a Brand Profile page."""

        key_id, key = self._current_signing_key()
        workspace_id = _validate_workspace_id(workspace_id)
        brand = _validate_brand(brand)
        created_at_microseconds = _datetime_to_microseconds(
            _validate_utc_datetime(created_at, field="created_at")
        )
        canonical_profile_id = _validate_canonical_uuid(profile_id)
        issued_at_microseconds = self._clock_microseconds()
        scope_digest = _profile_scope_digest(
            key=key.scope_key,
            workspace_id=workspace_id,
            brand=brand,
        )
        payload = _COMMON_PAYLOAD.pack(
            _SCHEMA_VERSION,
            _PROFILE_KIND,
            issued_at_microseconds,
            scope_digest,
        ) + _PROFILE_BOUNDARY.pack(
            created_at_microseconds,
            UUID(canonical_profile_id).bytes,
        )
        return _sign_token(key_id=key_id, signing_key=key.signing_key, payload=payload)

    def decode_profiles(
        self,
        token: str,
        *,
        workspace_id: str,
        brand: str | None,
    ) -> tuple[datetime, str]:
        """Verify a profile-list cursor and return its keyset boundary."""

        try:
            workspace_id = _validate_workspace_id(workspace_id)
            brand = _validate_brand(brand)
            key, payload = self._authenticate(token)
            if len(payload) != _COMMON_PAYLOAD.size + _PROFILE_BOUNDARY.size:
                raise ValueError
            schema, kind, issued_at, actual_scope = _COMMON_PAYLOAD.unpack_from(payload)
            if schema != _SCHEMA_VERSION or kind != _PROFILE_KIND:
                raise ValueError
            self._validate_freshness(issued_at)
            expected_scope = _profile_scope_digest(
                key=key.scope_key,
                workspace_id=workspace_id,
                brand=brand,
            )
            if not hmac.compare_digest(actual_scope, expected_scope):
                raise ValueError
            created_at, profile_id_bytes = _PROFILE_BOUNDARY.unpack_from(
                payload,
                _COMMON_PAYLOAD.size,
            )
            return _microseconds_to_datetime(created_at), str(UUID(bytes=profile_id_bytes))
        except Exception:
            raise ValueError(_INVALID_CURSOR) from None

    def encode_versions(
        self,
        *,
        workspace_id: str,
        profile_id: str,
        version_number: int,
    ) -> str:
        """Encode the final version number of a Brand Profile history page."""

        key_id, key = self._current_signing_key()
        workspace_id = _validate_workspace_id(workspace_id)
        canonical_profile_id = _validate_canonical_uuid(profile_id)
        version_number = _validate_version_number(version_number)
        issued_at_microseconds = self._clock_microseconds()
        scope_digest = _version_scope_digest(
            key=key.scope_key,
            workspace_id=workspace_id,
            profile_id=canonical_profile_id,
        )
        payload = _COMMON_PAYLOAD.pack(
            _SCHEMA_VERSION,
            _VERSION_KIND,
            issued_at_microseconds,
            scope_digest,
        ) + _VERSION_BOUNDARY.pack(version_number)
        return _sign_token(key_id=key_id, signing_key=key.signing_key, payload=payload)

    def decode_versions(
        self,
        token: str,
        *,
        workspace_id: str,
        profile_id: str,
    ) -> int:
        """Verify a version-list cursor and return its keyset boundary."""

        try:
            workspace_id = _validate_workspace_id(workspace_id)
            canonical_profile_id = _validate_canonical_uuid(profile_id)
            key, payload = self._authenticate(token)
            if len(payload) != _COMMON_PAYLOAD.size + _VERSION_BOUNDARY.size:
                raise ValueError
            schema, kind, issued_at, actual_scope = _COMMON_PAYLOAD.unpack_from(payload)
            if schema != _SCHEMA_VERSION or kind != _VERSION_KIND:
                raise ValueError
            self._validate_freshness(issued_at)
            expected_scope = _version_scope_digest(
                key=key.scope_key,
                workspace_id=workspace_id,
                profile_id=canonical_profile_id,
            )
            if not hmac.compare_digest(actual_scope, expected_scope):
                raise ValueError
            (version_number,) = _VERSION_BOUNDARY.unpack_from(
                payload,
                _COMMON_PAYLOAD.size,
            )
            return _validate_version_number(version_number)
        except Exception:
            raise ValueError(_INVALID_CURSOR) from None

    def _current_signing_key(self) -> tuple[str, _KeyMaterial]:
        if self._current_key_id is None:
            raise RuntimeError("Brand Profile cursor signing key is not configured")
        return self._current_key_id, self._keys[self._current_key_id]

    def _clock_microseconds(self) -> int:
        now = _validate_utc_datetime(self._clock(), field="clock")
        return _datetime_to_microseconds(now)

    def _authenticate(self, token: str) -> tuple[_KeyMaterial, bytes]:
        if type(token) is not str or not token or len(token) > _MAX_TOKEN_BYTES:
            raise ValueError
        token.encode("ascii")
        segments = token.split(".")
        if len(segments) != 4:
            raise ValueError
        version, key_id, encoded_payload, encoded_signature = segments
        if version != _TOKEN_VERSION:
            raise ValueError
        _validate_key_id(key_id)

        supplied_signature = _canonical_base64url_decode(encoded_signature)
        if len(supplied_signature) != hashlib.sha256().digest_size:
            raise ValueError
        known_key = self._keys.get(key_id)
        verification_key = known_key or self._unknown_key
        signed_portion = f"{version}.{key_id}.{encoded_payload}".encode("ascii")
        expected_signature = hmac.digest(
            verification_key.signing_key,
            signed_portion,
            hashlib.sha256,
        )
        signature_valid = hmac.compare_digest(supplied_signature, expected_signature)
        if not (signature_valid & (known_key is not None)):
            raise ValueError

        payload = _canonical_base64url_decode(encoded_payload)
        return verification_key, payload

    def _validate_freshness(self, issued_at_microseconds: int) -> None:
        now_microseconds = self._clock_microseconds()
        if issued_at_microseconds > now_microseconds + self._future_skew_microseconds:
            raise ValueError
        if issued_at_microseconds < now_microseconds - self._max_age_microseconds:
            raise ValueError


def _validate_key_pair(
    *,
    key_id: str | None,
    secret: str | bytes | None,
    label: str,
) -> tuple[str, bytes] | None:
    if (key_id is None) != (secret is None):
        raise ValueError(
            f"{label} Brand Profile cursor key id and secret must be configured together"
        )
    if key_id is None or secret is None:
        return None
    key_id = _validate_key_id(key_id)
    if type(secret) is str:
        encoded_secret = secret.encode("utf-8")
    elif type(secret) is bytes:
        encoded_secret = secret
    else:
        raise TypeError(f"{label} Brand Profile cursor secret must be text or bytes")
    if len(encoded_secret) < 32:
        raise ValueError(f"{label} Brand Profile cursor secret must contain at least 32 bytes")
    return key_id, encoded_secret


def _validate_key_id(key_id: object) -> str:
    if (
        type(key_id) is not str
        or not key_id
        or len(key_id) > _MAX_KEY_ID_BYTES
        or _KEY_ID_RE.fullmatch(key_id) is None
    ):
        raise ValueError("Brand Profile cursor key id is invalid")
    key_id.encode("ascii")
    return key_id


def _validate_duration(value: object, *, field: str, allow_zero: bool) -> int:
    minimum = 0 if allow_zero else 1
    if type(value) is not int or value < minimum or value > _MAX_SECRET_DURATION_SECONDS:
        raise ValueError(f"{field} is invalid")
    return value


def _derive_key_material(key_id: str, root_key: bytes) -> _KeyMaterial:
    key_id_bytes = key_id.encode("ascii")
    return _KeyMaterial(
        signing_key=hmac.digest(
            root_key,
            _length_prefixed(_KEY_DERIVATION_DOMAIN, b"signing", key_id_bytes),
            hashlib.sha256,
        ),
        scope_key=hmac.digest(
            root_key,
            _length_prefixed(_KEY_DERIVATION_DOMAIN, b"scope", key_id_bytes),
            hashlib.sha256,
        ),
    )


def _profile_scope_digest(
    *,
    key: bytes,
    workspace_id: str,
    brand: str | None,
) -> bytes:
    brand_scope = b"\x00" if brand is None else b"\x01" + brand.encode("utf-8")
    scope = _length_prefixed(
        _PROFILE_SCOPE_DOMAIN,
        b"profiles",
        _PROFILE_SORT_SCHEMA,
        workspace_id.encode("ascii"),
        brand_scope,
    )
    return hmac.digest(key, scope, hashlib.sha256)


def _version_scope_digest(
    *,
    key: bytes,
    workspace_id: str,
    profile_id: str,
) -> bytes:
    scope = _length_prefixed(
        _VERSION_SCOPE_DOMAIN,
        b"versions",
        _VERSION_SORT_SCHEMA,
        workspace_id.encode("ascii"),
        profile_id.encode("ascii"),
    )
    return hmac.digest(key, scope, hashlib.sha256)


def _length_prefixed(*values: bytes) -> bytes:
    framed = bytearray()
    for value in values:
        framed.extend(struct.pack(">I", len(value)))
        framed.extend(value)
    return bytes(framed)


def _sign_token(*, key_id: str, signing_key: bytes, payload: bytes) -> str:
    encoded_payload = _canonical_base64url_encode(payload)
    signed_portion = f"{_TOKEN_VERSION}.{key_id}.{encoded_payload}"
    signature = hmac.digest(
        signing_key,
        signed_portion.encode("ascii"),
        hashlib.sha256,
    )
    token = f"{signed_portion}.{_canonical_base64url_encode(signature)}"
    if len(token) > _MAX_TOKEN_BYTES:
        raise RuntimeError("Brand Profile cursor exceeds its transport limit")
    return token


def _canonical_base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _canonical_base64url_decode(value: str) -> bytes:
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
    if _canonical_base64url_encode(decoded) != value:
        raise ValueError
    return decoded


def _validate_workspace_id(value: object) -> str:
    if type(value) is not str or _WORKSPACE_ID_RE.fullmatch(value) is None:
        raise ValueError("workspace_id is invalid")
    return value


def _validate_brand(value: object) -> str | None:
    if value is None:
        return None
    if (
        type(value) is not str
        or not value
        or len(value) > 128
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("brand is invalid")
    value.encode("utf-8")
    return value


def _validate_canonical_uuid(value: object) -> str:
    if type(value) is not str:
        raise ValueError("profile_id is invalid")
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError):
        raise ValueError("profile_id is invalid") from None
    if canonical != value:
        raise ValueError("profile_id is invalid")
    return canonical


def _validate_version_number(value: object) -> int:
    if type(value) is not int or not 1 <= value <= (1 << 64) - 1:
        raise ValueError("version_number is invalid")
    return value


def _validate_utc_datetime(value: object, *, field: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(
            f"{field} must return a UTC datetime" if field == "clock" else f"{field} must be UTC"
        )
    return value.astimezone(UTC)


def _datetime_to_microseconds(value: datetime) -> int:
    delta = value - _EPOCH
    microseconds = (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
    if not -(1 << 63) <= microseconds <= (1 << 63) - 1:
        raise ValueError("datetime is outside the cursor range")
    return microseconds


def _microseconds_to_datetime(value: int) -> datetime:
    return _EPOCH + timedelta(microseconds=value)
