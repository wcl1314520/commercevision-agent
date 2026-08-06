from __future__ import annotations

from datetime import UTC, datetime

import pytest
from commercevision_domain import AssetVersion

NOW = datetime(2026, 8, 6, 14, 0, tzinfo=UTC)


def _asset_version(
    *,
    upload_session_id: str | None,
    generation_provider_call_id: str | None,
) -> AssetVersion:
    return AssetVersion(
        id="019b0000-0000-7000-8000-000000000901",
        workspace_id="workspace-phase4",
        asset_id="019b0000-0000-7000-8000-000000000902",
        version_number=1,
        upload_session_id=upload_session_id,
        filename="candidate.png",
        sha256="a" * 64,
        byte_size=128,
        declared_mime="image/png",
        detected_mime="image/png",
        image_format="PNG",
        width=8,
        height=8,
        frame_count=1,
        category="generated-candidate",
        role="candidate",
        integrity_policy_version="generated-image-integrity-v1",
        validation_policy_version="generated-image-validation-v1",
        created_at=NOW,
        generation_provider_call_id=generation_provider_call_id,
    )


def test_generated_asset_version_has_an_explicit_provider_call_origin() -> None:
    version = _asset_version(
        upload_session_id=None,
        generation_provider_call_id="019b0000-0000-7000-8000-000000000903",
    )

    assert version.upload_session_id is None
    assert version.generation_provider_call_id == ("019b0000-0000-7000-8000-000000000903")


@pytest.mark.parametrize(
    ("upload_session_id", "generation_provider_call_id"),
    [
        (None, None),
        (
            "019b0000-0000-7000-8000-000000000904",
            "019b0000-0000-7000-8000-000000000903",
        ),
    ],
)
def test_asset_version_rejects_missing_or_ambiguous_origin(
    upload_session_id: str | None,
    generation_provider_call_id: str | None,
) -> None:
    with pytest.raises(ValueError, match="exactly one origin"):
        _asset_version(
            upload_session_id=upload_session_id,
            generation_provider_call_id=generation_provider_call_id,
        )
