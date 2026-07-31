from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from commercevision_application.brand_profile_cursors import BrandProfileCursorCodec

_CURRENT_SECRET = b"current-brand-profile-cursor-key!"
_PREVIOUS_SECRET = b"previous-brand-profile-cursor-key"
_NOW = datetime(2026, 7, 30, 12, 0, 0, 123456, tzinfo=UTC)
_CREATED_AT = datetime(2026, 7, 29, 8, 7, 6, 543210, tzinfo=UTC)
_WORKSPACE_ID = "workspace-1"
_PROFILE_ID = "0198a541-8e77-7000-8000-000000000001"


def _codec(
    *,
    now: datetime = _NOW,
    current_key_id: str | None = "current",
    current_secret: str | bytes | None = _CURRENT_SECRET,
    previous_key_id: str | None = None,
    previous_secret: str | bytes | None = None,
    max_age_seconds: int = 300,
    future_skew_seconds: int = 30,
) -> BrandProfileCursorCodec:
    return BrandProfileCursorCodec(
        current_key_id=current_key_id,
        current_secret=current_secret,
        previous_key_id=previous_key_id,
        previous_secret=previous_secret,
        max_age_seconds=max_age_seconds,
        future_skew_seconds=future_skew_seconds,
        clock=lambda: now,
    )


def _assert_invalid(call: object, token: str) -> None:
    with pytest.raises(ValueError, match=r"^Brand Profile cursor is invalid$") as exc_info:
        call()  # type: ignore[operator]
    assert token not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def _flip_base64url_character(value: str) -> str:
    replacement = "A" if value[0] != "A" else "B"
    return replacement + value[1:]


def test_profile_cursor_round_trip_preserves_microseconds_and_canonical_uuid() -> None:
    codec = _codec()

    token = codec.encode_profiles(
        workspace_id=_WORKSPACE_ID,
        brand="Acme",
        created_at=_CREATED_AT,
        profile_id=_PROFILE_ID,
    )

    assert codec.decode_profiles(
        token,
        workspace_id=_WORKSPACE_ID,
        brand="Acme",
    ) == (_CREATED_AT, _PROFILE_ID)
    version, key_id, payload, signature = token.split(".")
    assert (version, key_id) == ("v1", "current")
    assert "=" not in payload
    assert "=" not in signature
    assert len(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))) == 66
    assert len(base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))) == 32
    assert len(token) <= 256


def test_version_cursor_round_trip_supports_uint64_boundary() -> None:
    codec = _codec()
    version_number = (1 << 64) - 1

    token = codec.encode_versions(
        workspace_id=_WORKSPACE_ID,
        profile_id=_PROFILE_ID,
        version_number=version_number,
    )

    assert (
        codec.decode_versions(
            token,
            workspace_id=_WORKSPACE_ID,
            profile_id=_PROFILE_ID,
        )
        == version_number
    )
    assert len(token) <= 256


def test_largest_allowed_key_id_keeps_profile_token_within_transport_limit() -> None:
    codec = _codec(current_key_id="k" * 64)

    token = codec.encode_profiles(
        workspace_id=_WORKSPACE_ID,
        brand="Acme",
        created_at=_CREATED_AT,
        profile_id=_PROFILE_ID,
    )

    assert len(token.encode("ascii")) <= 256


@pytest.mark.parametrize(
    ("workspace_id", "brand"),
    [
        ("workspace-2", "Acme"),
        (_WORKSPACE_ID, "Other"),
        (_WORKSPACE_ID, None),
    ],
)
def test_profile_cursor_cannot_cross_query_scope(
    workspace_id: str,
    brand: str | None,
) -> None:
    codec = _codec()
    token = codec.encode_profiles(
        workspace_id=_WORKSPACE_ID,
        brand="Acme",
        created_at=_CREATED_AT,
        profile_id=_PROFILE_ID,
    )

    _assert_invalid(
        lambda: codec.decode_profiles(token, workspace_id=workspace_id, brand=brand),
        token,
    )


@pytest.mark.parametrize(
    ("workspace_id", "profile_id"),
    [
        ("workspace-2", _PROFILE_ID),
        (_WORKSPACE_ID, "0198a541-8e77-7000-8000-000000000002"),
    ],
)
def test_version_cursor_cannot_cross_query_scope(
    workspace_id: str,
    profile_id: str,
) -> None:
    codec = _codec()
    token = codec.encode_versions(
        workspace_id=_WORKSPACE_ID,
        profile_id=_PROFILE_ID,
        version_number=7,
    )

    _assert_invalid(
        lambda: codec.decode_versions(
            token,
            workspace_id=workspace_id,
            profile_id=profile_id,
        ),
        token,
    )


def test_cursor_kinds_cannot_be_cross_used() -> None:
    codec = _codec()
    profile_token = codec.encode_profiles(
        workspace_id=_WORKSPACE_ID,
        brand=None,
        created_at=_CREATED_AT,
        profile_id=_PROFILE_ID,
    )
    version_token = codec.encode_versions(
        workspace_id=_WORKSPACE_ID,
        profile_id=_PROFILE_ID,
        version_number=7,
    )

    _assert_invalid(
        lambda: codec.decode_versions(
            profile_token,
            workspace_id=_WORKSPACE_ID,
            profile_id=_PROFILE_ID,
        ),
        profile_token,
    )
    _assert_invalid(
        lambda: codec.decode_profiles(
            version_token,
            workspace_id=_WORKSPACE_ID,
            brand=None,
        ),
        version_token,
    )


def test_payload_signature_and_key_id_tampering_are_rejected() -> None:
    codec = _codec()
    token = codec.encode_versions(
        workspace_id=_WORKSPACE_ID,
        profile_id=_PROFILE_ID,
        version_number=7,
    )
    version, key_id, payload, signature = token.split(".")
    tampered_tokens = (
        ".".join((version, key_id, _flip_base64url_character(payload), signature)),
        ".".join((version, key_id, payload, _flip_base64url_character(signature))),
        ".".join((version, "currenA", payload, signature)),
    )

    for tampered in tampered_tokens:
        _assert_invalid(
            lambda tampered=tampered: codec.decode_versions(
                tampered,
                workspace_id=_WORKSPACE_ID,
                profile_id=_PROFILE_ID,
            ),
            tampered,
        )


def test_unknown_key_id_uses_the_same_public_failure() -> None:
    issuer = _codec(current_key_id="unknown-to-verifier")
    verifier = _codec()
    token = issuer.encode_versions(
        workspace_id=_WORKSPACE_ID,
        profile_id=_PROFILE_ID,
        version_number=7,
    )

    _assert_invalid(
        lambda: verifier.decode_versions(
            token,
            workspace_id=_WORKSPACE_ID,
            profile_id=_PROFILE_ID,
        ),
        token,
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda token: token + ".extra",
        lambda token: token.replace(".", ".=", 1),
        lambda token: token + ("A" * 257),
        lambda token: "v2" + token[2:],
        lambda token: token.replace("v1.", "v1.bad$key.", 1),
        lambda token: token.replace(".", ".\N{SNOWMAN}", 1),
    ],
)
def test_malformed_noncanonical_and_oversized_tokens_are_rejected(mutate: object) -> None:
    codec = _codec()
    token = codec.encode_versions(
        workspace_id=_WORKSPACE_ID,
        profile_id=_PROFILE_ID,
        version_number=7,
    )
    malformed = mutate(token)  # type: ignore[operator]

    _assert_invalid(
        lambda: codec.decode_versions(
            malformed,
            workspace_id=_WORKSPACE_ID,
            profile_id=_PROFILE_ID,
        ),
        malformed,
    )


def test_noncanonical_payload_and_signature_base64_are_rejected() -> None:
    codec = _codec()
    token = codec.encode_versions(
        workspace_id=_WORKSPACE_ID,
        profile_id=_PROFILE_ID,
        version_number=7,
    )
    version, key_id, payload, signature = token.split(".")

    for malformed in (
        ".".join((version, key_id, payload + "=", signature)),
        ".".join((version, key_id, payload, signature + "=")),
    ):
        _assert_invalid(
            lambda malformed=malformed: codec.decode_versions(
                malformed,
                workspace_id=_WORKSPACE_ID,
                profile_id=_PROFILE_ID,
            ),
            malformed,
        )


def test_expiry_boundary_is_inclusive_and_one_microsecond_later_is_rejected() -> None:
    issuer = _codec(now=_NOW, max_age_seconds=300)
    token = issuer.encode_versions(
        workspace_id=_WORKSPACE_ID,
        profile_id=_PROFILE_ID,
        version_number=7,
    )
    at_boundary = _codec(now=_NOW + timedelta(seconds=300), max_age_seconds=300)
    expired = _codec(
        now=_NOW + timedelta(seconds=300, microseconds=1),
        max_age_seconds=300,
    )

    assert (
        at_boundary.decode_versions(
            token,
            workspace_id=_WORKSPACE_ID,
            profile_id=_PROFILE_ID,
        )
        == 7
    )
    _assert_invalid(
        lambda: expired.decode_versions(
            token,
            workspace_id=_WORKSPACE_ID,
            profile_id=_PROFILE_ID,
        ),
        token,
    )


def test_future_skew_boundary_is_inclusive_and_one_microsecond_beyond_is_rejected() -> None:
    at_boundary = _codec(now=_NOW, future_skew_seconds=30)
    beyond_boundary = _codec(now=_NOW, future_skew_seconds=30)
    token_at_boundary = _codec(
        now=_NOW + timedelta(seconds=30),
        future_skew_seconds=30,
    ).encode_versions(
        workspace_id=_WORKSPACE_ID,
        profile_id=_PROFILE_ID,
        version_number=7,
    )
    token_beyond_boundary = _codec(
        now=_NOW + timedelta(seconds=30, microseconds=1),
        future_skew_seconds=30,
    ).encode_versions(
        workspace_id=_WORKSPACE_ID,
        profile_id=_PROFILE_ID,
        version_number=7,
    )

    assert (
        at_boundary.decode_versions(
            token_at_boundary,
            workspace_id=_WORKSPACE_ID,
            profile_id=_PROFILE_ID,
        )
        == 7
    )
    _assert_invalid(
        lambda: beyond_boundary.decode_versions(
            token_beyond_boundary,
            workspace_id=_WORKSPACE_ID,
            profile_id=_PROFILE_ID,
        ),
        token_beyond_boundary,
    )


def test_rotated_codec_accepts_previous_token_and_issues_only_with_current_key() -> None:
    old_codec = _codec(current_key_id="old", current_secret=_PREVIOUS_SECRET)
    old_token = old_codec.encode_versions(
        workspace_id=_WORKSPACE_ID,
        profile_id=_PROFILE_ID,
        version_number=6,
    )
    rotated = _codec(
        current_key_id="new",
        current_secret=_CURRENT_SECRET,
        previous_key_id="old",
        previous_secret=_PREVIOUS_SECRET,
    )

    assert (
        rotated.decode_versions(
            old_token,
            workspace_id=_WORKSPACE_ID,
            profile_id=_PROFILE_ID,
        )
        == 6
    )
    new_token = rotated.encode_versions(
        workspace_id=_WORKSPACE_ID,
        profile_id=_PROFILE_ID,
        version_number=7,
    )
    assert new_token.split(".", 2)[:2] == ["v1", "new"]
    _assert_invalid(
        lambda: old_codec.decode_versions(
            new_token,
            workspace_id=_WORKSPACE_ID,
            profile_id=_PROFILE_ID,
        ),
        new_token,
    )


def test_signing_uses_a_domain_separated_derived_key() -> None:
    codec = _codec()
    token = codec.encode_versions(
        workspace_id=_WORKSPACE_ID,
        profile_id=_PROFILE_ID,
        version_number=7,
    )
    version, key_id, payload, encoded_signature = token.split(".")
    actual_signature = base64.urlsafe_b64decode(
        encoded_signature + "=" * (-len(encoded_signature) % 4)
    )
    direct_signature = hmac.digest(
        _CURRENT_SECRET,
        f"{version}.{key_id}.{payload}".encode("ascii"),
        hashlib.sha256,
    )

    assert not hmac.compare_digest(actual_signature, direct_signature)


def test_profile_boundary_is_canonical_uuid_binary_not_uuid_text() -> None:
    codec = _codec()
    token = codec.encode_profiles(
        workspace_id=_WORKSPACE_ID,
        brand=None,
        created_at=_CREATED_AT,
        profile_id=_PROFILE_ID,
    )
    payload = base64.urlsafe_b64decode(token.split(".")[2] + "=" * (-len(token.split(".")[2]) % 4))

    assert UUID(_PROFILE_ID).bytes in payload
    assert _PROFILE_ID.encode("ascii") not in payload


@pytest.mark.parametrize(
    "kwargs",
    [
        {"current_key_id": "current", "current_secret": None},
        {"current_key_id": None, "current_secret": _CURRENT_SECRET},
        {"previous_key_id": "previous", "previous_secret": None},
        {"previous_key_id": None, "previous_secret": _PREVIOUS_SECRET},
        {
            "current_key_id": "same",
            "current_secret": _CURRENT_SECRET,
            "previous_key_id": "same",
            "previous_secret": _PREVIOUS_SECRET,
        },
        {"current_key_id": "bad.key", "current_secret": _CURRENT_SECRET},
        {"current_key_id": "é", "current_secret": _CURRENT_SECRET},
        {"current_key_id": "a" * 65, "current_secret": _CURRENT_SECRET},
        {"current_key_id": "current", "current_secret": b"too-short"},
        {"current_key_id": "current", "current_secret": "é" * 15},
        {"max_age_seconds": True},
        {"max_age_seconds": 0},
        {"future_skew_seconds": True},
        {"future_skew_seconds": -1},
    ],
)
def test_constructor_rejects_incomplete_weak_or_noncanonical_configuration(
    kwargs: dict[str, object],
) -> None:
    defaults: dict[str, object] = {
        "current_key_id": "current",
        "current_secret": _CURRENT_SECRET,
        "max_age_seconds": 300,
        "future_skew_seconds": 30,
    }

    with pytest.raises((TypeError, ValueError)):
        BrandProfileCursorCodec(**(defaults | kwargs))  # type: ignore[arg-type]


def test_secret_minimum_is_measured_in_bytes_and_bytes_or_text_are_supported() -> None:
    text_secret_codec = _codec(current_secret="é" * 16)
    bytes_secret_codec = _codec(current_secret=b"x" * 32)

    assert text_secret_codec.encode_versions(
        workspace_id=_WORKSPACE_ID,
        profile_id=_PROFILE_ID,
        version_number=1,
    )
    assert bytes_secret_codec.encode_versions(
        workspace_id=_WORKSPACE_ID,
        profile_id=_PROFILE_ID,
        version_number=1,
    )


def test_missing_current_key_fails_closed_for_encoding_and_decoding() -> None:
    codec = _codec(current_key_id=None, current_secret=None)

    with pytest.raises(RuntimeError, match="signing key is not configured"):
        codec.encode_versions(
            workspace_id=_WORKSPACE_ID,
            profile_id=_PROFILE_ID,
            version_number=1,
        )
    token = _codec().encode_versions(
        workspace_id=_WORKSPACE_ID,
        profile_id=_PROFILE_ID,
        version_number=1,
    )
    _assert_invalid(
        lambda: codec.decode_versions(
            token,
            workspace_id=_WORKSPACE_ID,
            profile_id=_PROFILE_ID,
        ),
        token,
    )


def test_previous_only_configuration_verifies_during_fail_closed_issuance() -> None:
    issuer = _codec(current_key_id="previous", current_secret=_PREVIOUS_SECRET)
    token = issuer.encode_versions(
        workspace_id=_WORKSPACE_ID,
        profile_id=_PROFILE_ID,
        version_number=1,
    )
    verifier = _codec(
        current_key_id=None,
        current_secret=None,
        previous_key_id="previous",
        previous_secret=_PREVIOUS_SECRET,
    )

    assert (
        verifier.decode_versions(
            token,
            workspace_id=_WORKSPACE_ID,
            profile_id=_PROFILE_ID,
        )
        == 1
    )
    with pytest.raises(RuntimeError, match="signing key is not configured"):
        verifier.encode_versions(
            workspace_id=_WORKSPACE_ID,
            profile_id=_PROFILE_ID,
            version_number=1,
        )


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        (
            "encode_profiles",
            {
                "workspace_id": "",
                "brand": None,
                "created_at": _CREATED_AT,
                "profile_id": _PROFILE_ID,
            },
        ),
        (
            "encode_profiles",
            {
                "workspace_id": _WORKSPACE_ID,
                "brand": "",
                "created_at": _CREATED_AT,
                "profile_id": _PROFILE_ID,
            },
        ),
        (
            "encode_profiles",
            {
                "workspace_id": _WORKSPACE_ID,
                "brand": None,
                "created_at": _CREATED_AT.replace(tzinfo=None),
                "profile_id": _PROFILE_ID,
            },
        ),
        (
            "encode_profiles",
            {
                "workspace_id": _WORKSPACE_ID,
                "brand": None,
                "created_at": _CREATED_AT,
                "profile_id": _PROFILE_ID.upper(),
            },
        ),
        (
            "encode_versions",
            {
                "workspace_id": _WORKSPACE_ID,
                "profile_id": _PROFILE_ID,
                "version_number": True,
            },
        ),
        (
            "encode_versions",
            {
                "workspace_id": _WORKSPACE_ID,
                "profile_id": _PROFILE_ID,
                "version_number": 0,
            },
        ),
        (
            "encode_versions",
            {
                "workspace_id": _WORKSPACE_ID,
                "profile_id": _PROFILE_ID,
                "version_number": 1 << 64,
            },
        ),
    ],
)
def test_encode_rejects_noncanonical_inputs(method: str, kwargs: dict[str, object]) -> None:
    codec = _codec()

    with pytest.raises((TypeError, ValueError)):
        getattr(codec, method)(**kwargs)


def test_decode_rejects_noncanonical_scope_inputs_with_uniform_failure() -> None:
    codec = _codec()
    token = codec.encode_versions(
        workspace_id=_WORKSPACE_ID,
        profile_id=_PROFILE_ID,
        version_number=1,
    )

    _assert_invalid(
        lambda: codec.decode_versions(
            token,
            workspace_id="",
            profile_id=_PROFILE_ID.upper(),
        ),
        token,
    )


def test_decode_rejects_non_string_token_with_uniform_failure() -> None:
    codec = _codec()

    _assert_invalid(
        lambda: codec.decode_versions(  # type: ignore[arg-type]
            None,
            workspace_id=_WORKSPACE_ID,
            profile_id=_PROFILE_ID,
        ),
        "not-the-token",
    )


def test_non_utc_clock_fails_encoding_and_cannot_bypass_decode_freshness() -> None:
    codec = _codec(now=_NOW)
    token = codec.encode_versions(
        workspace_id=_WORKSPACE_ID,
        profile_id=_PROFILE_ID,
        version_number=1,
    )
    bad_clock_codec = _codec(now=_NOW.replace(tzinfo=None))

    with pytest.raises(ValueError, match="clock must return a UTC datetime"):
        bad_clock_codec.encode_versions(
            workspace_id=_WORKSPACE_ID,
            profile_id=_PROFILE_ID,
            version_number=1,
        )
    _assert_invalid(
        lambda: bad_clock_codec.decode_versions(
            token,
            workspace_id=_WORKSPACE_ID,
            profile_id=_PROFILE_ID,
        ),
        token,
    )
