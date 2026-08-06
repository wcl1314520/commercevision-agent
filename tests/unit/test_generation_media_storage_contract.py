from __future__ import annotations

import hashlib

import pytest
from commercevision_contracts.object_storage import (
    ConditionalWriteRequest,
    GenerationMediaWriteRequest,
    ObjectReference,
)
from commercevision_domain import StorageLocationClass
from pydantic import ValidationError


def _reference(*, version_id: str | None = None) -> ObjectReference:
    return ObjectReference(
        location=StorageLocationClass.TASK,
        key="generated/workspace-phase4/operation-1/candidate-1.png",
        version_id=version_id,
    )


def _request(payload: bytes, *, version_id: str | None = None) -> GenerationMediaWriteRequest:
    return GenerationMediaWriteRequest(
        reference=_reference(version_id=version_id),
        payload=payload,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        content_type="image/png",
        durable_operation_id="019b0000-0000-7000-8000-000000000911",
        candidate_slot_id="019b0000-0000-7000-8000-000000000912",
        provider_call_id="019b0000-0000-7000-8000-000000000913",
    )


def test_generation_media_write_has_a_separate_bounded_contract() -> None:
    payload = b"x" * (2 * 1024 * 1024 + 1)

    media = _request(payload)

    assert media.payload == payload
    with pytest.raises(ValidationError):
        ConditionalWriteRequest(
            reference=_reference(),
            payload=payload,
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            content_type="image/png",
        )


def test_generation_media_write_rejects_oversize_or_non_image_payloads() -> None:
    with pytest.raises(ValidationError):
        _request(b"x" * (32 * 1024 * 1024 + 1))
    with pytest.raises(ValidationError):
        GenerationMediaWriteRequest(
            reference=_reference(),
            payload=b"image",
            expected_sha256=hashlib.sha256(b"image").hexdigest(),
            content_type="application/octet-stream",
            durable_operation_id="019b0000-0000-7000-8000-000000000911",
            candidate_slot_id="019b0000-0000-7000-8000-000000000912",
            provider_call_id="019b0000-0000-7000-8000-000000000913",
        )
    with pytest.raises(ValidationError, match="unversioned"):
        _request(b"image", version_id="provider-version-1")


def test_generation_media_write_rejects_non_task_destination() -> None:
    payload = b"image"

    with pytest.raises(ValidationError, match="Task destination"):
        GenerationMediaWriteRequest(
            reference=ObjectReference(
                location=StorageLocationClass.PROVIDER_RESULT,
                key="generation/operation-1/candidate-1.png",
            ),
            payload=payload,
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            content_type="image/png",
            durable_operation_id="019b0000-0000-7000-8000-000000000911",
            candidate_slot_id="019b0000-0000-7000-8000-000000000912",
            provider_call_id="019b0000-0000-7000-8000-000000000913",
        )
