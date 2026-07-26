from __future__ import annotations

import io
import json
import struct

import pytest
from commercevision_application.asset_local_validation import (
    AssetLocalValidationError,
    AssetLocalValidationRequest,
    AssetLocalValidator,
)
from commercevision_domain import AssetKind


def _validator(**changes: int) -> AssetLocalValidator:
    limits = {
        "maximum_image_bytes": 10 * 1024 * 1024,
        "maximum_image_dimension": 1280,
        "maximum_image_pixels": 1280 * 1280,
        "maximum_image_frames": 1,
        "maximum_image_decoded_bytes": 32 * 1024 * 1024,
        "maximum_metadata_bytes": 256 * 1024,
        "maximum_lora_bytes": 100 * 1024 * 1024,
        "maximum_safetensors_header_bytes": 1024 * 1024,
        "maximum_safetensors_tensors": 4096,
        "maximum_safetensors_rank": 8,
        "maximum_safetensors_dimension": 1_000_000,
        "maximum_safetensors_elements": 100_000_000,
        "maximum_prompt_bytes": 64 * 1024,
        "maximum_model_configuration_bytes": 256 * 1024,
        "maximum_json_depth": 32,
        "maximum_json_nodes": 10_000,
    }
    limits.update(changes)
    return AssetLocalValidator(**limits)


def _request(
    *,
    asset_kind: AssetKind,
    payload: bytes,
    filename: str,
    declared_mime: str = "application/json",
) -> AssetLocalValidationRequest:
    return AssetLocalValidationRequest(
        asset_kind=asset_kind,
        filename=filename,
        declared_mime=declared_mime,
        byte_size=len(payload),
    )


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()


def _nested_json(depth: int) -> bytes:
    return b'{"nested":' + (b"[" * depth) + b"0" + (b"]" * depth) + b"}"


@pytest.mark.parametrize(
    ("asset_kind", "filename", "declared_mime", "expected_code"),
    [
        (
            AssetKind.LORA,
            "nested.safetensors",
            "application/x-safetensors",
            "MALFORMED_SAFETENSORS",
        ),
        (
            AssetKind.PROMPT_TEMPLATE,
            "nested.prompt.json",
            "application/json",
            "MALFORMED_PROMPT_TEMPLATE",
        ),
        (
            AssetKind.MODEL_CONFIGURATION,
            "nested.model.json",
            "application/json",
            "MALFORMED_MODEL_CONFIGURATION",
        ),
    ],
)
def test_structured_asset_validator_normalizes_deep_json_failure(
    asset_kind: AssetKind,
    filename: str,
    declared_mime: str,
    expected_code: str,
) -> None:
    document = _nested_json(1500)
    payload = (
        struct.pack("<Q", len(document)) + document if asset_kind == AssetKind.LORA else document
    )

    with pytest.raises(AssetLocalValidationError) as caught:
        _validator().validate(
            _request(
                asset_kind=asset_kind,
                payload=payload,
                filename=filename,
                declared_mime=declared_mime,
            ),
            io.BytesIO(payload),
        )

    assert caught.value.code == expected_code


@pytest.mark.parametrize(
    ("asset_kind", "filename", "declared_mime", "expected_code"),
    [
        (
            AssetKind.LORA,
            "bounded.safetensors",
            "application/x-safetensors",
            "MALFORMED_SAFETENSORS",
        ),
        (
            AssetKind.PROMPT_TEMPLATE,
            "bounded.prompt.json",
            "application/json",
            "MALFORMED_PROMPT_TEMPLATE",
        ),
        (
            AssetKind.MODEL_CONFIGURATION,
            "bounded.model.json",
            "application/json",
            "MALFORMED_MODEL_CONFIGURATION",
        ),
    ],
)
def test_structured_asset_validator_enforces_configured_json_depth(
    asset_kind: AssetKind,
    filename: str,
    declared_mime: str,
    expected_code: str,
) -> None:
    document = _nested_json(12)
    payload = (
        struct.pack("<Q", len(document)) + document if asset_kind == AssetKind.LORA else document
    )

    with pytest.raises(AssetLocalValidationError) as caught:
        _validator(maximum_json_depth=8).validate(
            _request(
                asset_kind=asset_kind,
                payload=payload,
                filename=filename,
                declared_mime=declared_mime,
            ),
            io.BytesIO(payload),
        )

    assert caught.value.code == expected_code


@pytest.mark.parametrize(
    ("asset_kind", "filename", "declared_mime", "expected_code"),
    [
        (
            AssetKind.LORA,
            "wide.safetensors",
            "application/x-safetensors",
            "MALFORMED_SAFETENSORS",
        ),
        (
            AssetKind.PROMPT_TEMPLATE,
            "wide.prompt.json",
            "application/json",
            "MALFORMED_PROMPT_TEMPLATE",
        ),
        (
            AssetKind.MODEL_CONFIGURATION,
            "wide.model.json",
            "application/json",
            "MALFORMED_MODEL_CONFIGURATION",
        ),
    ],
)
def test_structured_asset_validator_enforces_configured_json_nodes(
    asset_kind: AssetKind,
    filename: str,
    declared_mime: str,
    expected_code: str,
) -> None:
    document = _json_bytes({"wide": list(range(32))})
    payload = (
        struct.pack("<Q", len(document)) + document if asset_kind == AssetKind.LORA else document
    )

    with pytest.raises(AssetLocalValidationError) as caught:
        _validator(maximum_json_nodes=16).validate(
            _request(
                asset_kind=asset_kind,
                payload=payload,
                filename=filename,
                declared_mime=declared_mime,
            ),
            io.BytesIO(payload),
        )

    assert caught.value.code == expected_code


def test_prompt_template_validator_accepts_exact_schema_and_declared_variables() -> None:
    payload = _json_bytes(
        {
            "schema_version": "commercevision.prompt-template.v1",
            "name": "product-hero",
            "template": "Create a catalog image for {{ product_name }}.",
            "variables": [{"name": "product_name", "required": True}],
        }
    )

    result = _validator().validate(
        _request(
            asset_kind=AssetKind.PROMPT_TEMPLATE,
            payload=payload,
            filename="product-hero.prompt.json",
        ),
        io.BytesIO(payload),
    )

    assert result.detected_mime == "application/json"
    assert result.format_name == "PROMPT_TEMPLATE_JSON"
    assert result.facts == {
        "character_count": 46,
        "schema_version": "commercevision.prompt-template.v1",
        "variable_count": 1,
    }


@pytest.mark.parametrize(
    ("payload", "filename", "mime", "expected_code"),
    [
        (
            b'{"schema_version":"commercevision.prompt-template.v1",'
            b'"name":"one","name":"two","template":"x","variables":[]}',
            "template.prompt.json",
            "application/json",
            "MALFORMED_PROMPT_TEMPLATE",
        ),
        (
            _json_bytes(
                {
                    "schema_version": "commercevision.prompt-template.v1",
                    "name": "template",
                    "template": "Hello",
                    "variables": [],
                    "extra": "not allowed",
                }
            ),
            "template.prompt.json",
            "application/json",
            "INVALID_PROMPT_TEMPLATE_SCHEMA",
        ),
        (
            _json_bytes(
                {
                    "schema_version": "commercevision.prompt-template.v1",
                    "name": "template",
                    "template": "Hello {{ missing }}",
                    "variables": [],
                }
            ),
            "template.prompt.json",
            "application/json",
            "INVALID_PROMPT_TEMPLATE_SCHEMA",
        ),
        (
            _json_bytes(
                {
                    "schema_version": "commercevision.prompt-template.v1",
                    "name": "template",
                    "template": "{% include 'remote' %}",
                    "variables": [],
                }
            ),
            "template.prompt.json",
            "application/json",
            "UNSAFE_PROMPT_TEMPLATE",
        ),
        (
            b"\xff\xfe{}",
            "template.prompt.json",
            "application/json",
            "MALFORMED_PROMPT_TEMPLATE",
        ),
        (
            _json_bytes({}),
            "template.txt",
            "application/json",
            "UNSAFE_DOCUMENT_FORMAT",
        ),
        (
            _json_bytes({}),
            "template.prompt.json",
            "text/plain",
            "DECLARED_MIME_MISMATCH",
        ),
    ],
)
def test_prompt_template_validator_rejects_ambiguous_or_unsafe_documents(
    payload: bytes,
    filename: str,
    mime: str,
    expected_code: str,
) -> None:
    with pytest.raises(AssetLocalValidationError) as caught:
        _validator().validate(
            _request(
                asset_kind=AssetKind.PROMPT_TEMPLATE,
                payload=payload,
                filename=filename,
                declared_mime=mime,
            ),
            io.BytesIO(payload),
        )

    assert caught.value.code == expected_code


def test_prompt_template_validator_enforces_size_limit() -> None:
    payload = _json_bytes(
        {
            "schema_version": "commercevision.prompt-template.v1",
            "name": "large",
            "template": "x" * 100,
            "variables": [],
        }
    )

    with pytest.raises(AssetLocalValidationError) as caught:
        _validator(maximum_prompt_bytes=len(payload) - 1).validate(
            _request(
                asset_kind=AssetKind.PROMPT_TEMPLATE,
                payload=payload,
                filename="large.prompt.json",
            ),
            io.BytesIO(payload),
        )

    assert caught.value.code == "ASSET_TOO_LARGE"


def test_model_configuration_validator_accepts_bounded_exact_schema() -> None:
    payload = _json_bytes(
        {
            "schema_version": "commercevision.model-configuration.v1",
            "provider": "alibaba",
            "model_id": "wanx-v1",
            "model_revision": "2026-07-01",
            "parameters": {
                "guidance_scale": 7.5,
                "height": 1024,
                "seed": 42,
                "steps": 30,
                "width": 1024,
            },
        }
    )

    result = _validator().validate(
        _request(
            asset_kind=AssetKind.MODEL_CONFIGURATION,
            payload=payload,
            filename="wanx.model.json",
        ),
        io.BytesIO(payload),
    )

    assert result.detected_mime == "application/json"
    assert result.format_name == "MODEL_CONFIGURATION_JSON"
    assert result.facts == {
        "model_id": "wanx-v1",
        "parameter_count": 5,
        "provider": "alibaba",
        "schema_version": "commercevision.model-configuration.v1",
    }


@pytest.mark.parametrize(
    "payload",
    [
        b'{"schema_version":"commercevision.model-configuration.v1",'
        b'"provider":"alibaba","model_id":"one","model_id":"two",'
        b'"model_revision":"v1","parameters":{}}',
        _json_bytes(
            {
                "schema_version": "commercevision.model-configuration.v1",
                "provider": "alibaba",
                "model_id": "wanx-v1",
                "model_revision": "v1",
                "parameters": {"unknown": 1},
            }
        ),
        _json_bytes(
            {
                "schema_version": "commercevision.model-configuration.v1",
                "provider": "alibaba",
                "model_id": "wanx-v1",
                "model_revision": "v1",
                "parameters": {"width": 1281},
            }
        ),
        _json_bytes(
            {
                "schema_version": "commercevision.model-configuration.v1",
                "provider": "alibaba",
                "model_id": "wanx-v1",
                "model_revision": "v1",
                "parameters": {"seed": True},
            }
        ),
        b'{"schema_version":"commercevision.model-configuration.v1",'
        b'"provider":"alibaba","model_id":"wanx-v1","model_revision":"v1",'
        b'"parameters":{"guidance_scale":NaN}}',
    ],
)
def test_model_configuration_validator_rejects_noncanonical_or_unsafe_schema(
    payload: bytes,
) -> None:
    with pytest.raises(AssetLocalValidationError) as caught:
        _validator().validate(
            _request(
                asset_kind=AssetKind.MODEL_CONFIGURATION,
                payload=payload,
                filename="model.model.json",
            ),
            io.BytesIO(payload),
        )

    assert caught.value.code in {
        "MALFORMED_MODEL_CONFIGURATION",
        "INVALID_MODEL_CONFIGURATION_SCHEMA",
    }


def test_model_configuration_validator_enforces_size_limit() -> None:
    payload = _json_bytes(
        {
            "schema_version": "commercevision.model-configuration.v1",
            "provider": "alibaba",
            "model_id": "wanx-v1",
            "model_revision": "v1",
            "parameters": {},
        }
    )

    with pytest.raises(AssetLocalValidationError) as caught:
        _validator(maximum_model_configuration_bytes=len(payload) - 1).validate(
            _request(
                asset_kind=AssetKind.MODEL_CONFIGURATION,
                payload=payload,
                filename="model.model.json",
            ),
            io.BytesIO(payload),
        )

    assert caught.value.code == "ASSET_TOO_LARGE"
