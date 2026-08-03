import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime

import pytest
from commercevision_domain import AuthenticationError
from commercevision_mcp.identity import SignedMcpIdentityResolver

SECRET = "mcp-identity-unit-secret-at-least-thirty-two-bytes"


def _claims(*, issued_at: int | None = None) -> dict[str, object]:
    return {
        "workspace_id": "workspace-mcp",
        "actor_id": "agent-1",
        "workflow_id": "workflow-1",
        "invocation_id": "invocation-0001",
        "scopes": ["catalog.read"],
        "purpose": "CREATIVE_REFERENCE",
        "provider": "fixture-provider",
        "requires_derivative": False,
        "budget": {
            "max_result_count": 10,
            "max_candidate_count": 100,
            "max_output_bytes": 262144,
        },
        "issued_at": issued_at or int(datetime.now(UTC).timestamp()),
    }


def _sign(claims: dict[str, object], *, key_id: str = "current") -> str:
    encoded = (
        base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )
    signature = hmac.new(
        SECRET.encode(), f"{key_id}.{encoded}".encode(), hashlib.sha256
    ).hexdigest()
    return f"{key_id}.{encoded}.{signature}"


def _resolver() -> SignedMcpIdentityResolver:
    return SignedMcpIdentityResolver(
        keys={"current": SECRET, "previous": SECRET},
        max_age_seconds=300,
        future_skew_seconds=5,
    )


def test_signed_mcp_identity_accepts_current_and_rotation_key() -> None:
    assert _resolver().resolve(_sign(_claims())).workspace_id == "workspace-mcp"
    assert _resolver().resolve(_sign(_claims(), key_id="previous")).actor_id == "agent-1"


@pytest.mark.parametrize("mutation", ["tampered", "unknown-key", "oversized"])
def test_signed_mcp_identity_rejects_untrusted_tokens(mutation: str) -> None:
    token = _sign(_claims())
    if mutation == "tampered":
        token = f"{token[:-1]}{'0' if token[-1] != '0' else '1'}"
    elif mutation == "unknown-key":
        token = token.replace("current.", "missing.", 1)
    else:
        token = "x" * (32 * 1024 + 1)

    with pytest.raises(AuthenticationError):
        _resolver().resolve(token)


@pytest.mark.parametrize("offset", [-301, 6])
def test_signed_mcp_identity_rejects_stale_and_future_claims(offset: int) -> None:
    issued_at = int(datetime.now(UTC).timestamp()) + offset
    with pytest.raises(AuthenticationError):
        _resolver().resolve(_sign(_claims(issued_at=issued_at)))


def test_signed_mcp_identity_rejects_control_characters_and_extra_claims() -> None:
    claims = _claims()
    claims["actor_id"] = "agent\nspoof"
    claims["workspace_override"] = "other"
    with pytest.raises(AuthenticationError):
        _resolver().resolve(_sign(claims))
