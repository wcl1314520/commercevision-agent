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


def _safetensors_bytes(header: dict[str, object], data: bytes) -> bytes:
    encoded_header = json.dumps(
        header,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return struct.pack("<Q", len(encoded_header)) + encoded_header + data


def _raw_safetensors_bytes(header: bytes, data: bytes) -> bytes:
    return struct.pack("<Q", len(header)) + header + data


def _validator() -> AssetLocalValidator:
    return AssetLocalValidator(
        maximum_image_bytes=10 * 1024 * 1024,
        maximum_image_dimension=1280,
        maximum_image_pixels=1280 * 1280,
        maximum_image_frames=1,
        maximum_image_decoded_bytes=32 * 1024 * 1024,
        maximum_metadata_bytes=256 * 1024,
        maximum_lora_bytes=100 * 1024 * 1024,
        maximum_safetensors_header_bytes=1024 * 1024,
        maximum_safetensors_tensors=4096,
        maximum_safetensors_rank=8,
        maximum_safetensors_dimension=1_000_000,
        maximum_safetensors_elements=100_000_000,
        maximum_prompt_bytes=64 * 1024,
        maximum_model_configuration_bytes=256 * 1024,
    )


def _lora_request(payload: bytes, *, filename: str = "model.safetensors"):
    return AssetLocalValidationRequest(
        asset_kind=AssetKind.LORA,
        filename=filename,
        declared_mime="application/octet-stream",
        byte_size=len(payload),
    )


def test_lora_validator_accepts_strict_contiguous_safetensors_without_deserializing() -> None:
    payload = _safetensors_bytes(
        {
            "down.weight": {
                "dtype": "F16",
                "shape": [2],
                "data_offsets": [0, 4],
            },
            "up.weight": {
                "dtype": "F32",
                "shape": [1],
                "data_offsets": [4, 8],
            },
            "__metadata__": {"architecture": "lora"},
        },
        b"\x00" * 8,
    )
    result = _validator().validate(
        _lora_request(payload, filename="catalog-style.safetensors"),
        io.BytesIO(payload),
    )

    assert result.detected_mime == "application/x-safetensors"
    assert result.format_name == "SAFETENSORS"
    assert result.facts == {
        "data_bytes": 8,
        "metadata_entries": 1,
        "tensor_count": 2,
    }


@pytest.mark.parametrize(
    ("header", "data"),
    [
        (
            b'{"weight":{"dtype":"F32","shape":[1],"data_offsets":[0,4]},'
            b'"weight":{"dtype":"F32","shape":[1],"data_offsets":[0,4]}}',
            b"\0" * 4,
        ),
        (
            b'{"weight":{"dtype":"F32","shape":[NaN],"data_offsets":[0,4]}}',
            b"\0" * 4,
        ),
        (
            b'{"weight":{"dtype":"F32","shape":[Infinity],"data_offsets":[0,4]}}',
            b"\0" * 4,
        ),
        (
            b'{"weight":{"dtype":"F32","shape":[true],"data_offsets":[0,4]}}',
            b"\0" * 4,
        ),
        (
            b'{"weight":{"dtype":"F32","shape":[1],"data_offsets":[false,4]}}',
            b"\0" * 4,
        ),
        (
            b'{"weight":{"dtype":"F32","shape":[' + b"9" * 5000 + b'],"data_offsets":[0,4]}}',
            b"\0" * 4,
        ),
        (
            b'{"weight":{"dtype":"F32","shape":[1],"data_offsets":[0,' + b"9" * 40 + b"]}}",
            b"\0" * 4,
        ),
        (
            b'{"weight":{"dtype":"F32","shape":[1000001,0],"data_offsets":[0,0]}}',
            b"",
        ),
        (
            b'{"weight":{"dtype":"F32","shape":[10000,10000,2],"data_offsets":[0,0]}}',
            b"",
        ),
        (
            b'{"a":{"dtype":"F32","shape":[1],"data_offsets":[0,4]},'
            b'"b":{"dtype":"F32","shape":[1],"data_offsets":[3,7]}}',
            b"\0" * 7,
        ),
        (
            b'{"a":{"dtype":"F32","shape":[1],"data_offsets":[0,4]},'
            b'"b":{"dtype":"F32","shape":[1],"data_offsets":[5,9]}}',
            b"\0" * 9,
        ),
        (
            b'{"weight":{"dtype":"F32","shape":[1],"data_offsets":[0,8]}}',
            b"\0" * 4,
        ),
        (
            b'{"weight":{"dtype":"F32","shape":[1],"data_offsets":[0,4],"device":"cpu"}}',
            b"\0" * 4,
        ),
        (
            b'{"weight":{"dtype":"F32","shape":[1],"data_offsets":[0,4]},'
            b'"__metadata__":{"source":1}}',
            b"\0" * 4,
        ),
    ],
)
def test_lora_validator_rejects_malformed_safetensors_header(
    header: bytes,
    data: bytes,
) -> None:
    payload = _raw_safetensors_bytes(header, data)

    with pytest.raises(AssetLocalValidationError) as caught:
        _validator().validate(_lora_request(payload), io.BytesIO(payload))

    assert caught.value.code == "MALFORMED_SAFETENSORS"


def test_lora_validator_rejects_truncated_header_data_and_pickle_formats() -> None:
    truncated_header = struct.pack("<Q", 20) + b'{"short":true}'
    truncated_data = _safetensors_bytes(
        {"weight": {"dtype": "F32", "shape": [2], "data_offsets": [0, 8]}},
        b"\0" * 4,
    )
    valid = _safetensors_bytes(
        {"weight": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}},
        b"\0" * 4,
    )

    for payload in (truncated_header, truncated_data):
        with pytest.raises(AssetLocalValidationError) as caught:
            _validator().validate(_lora_request(payload), io.BytesIO(payload))
        assert caught.value.code == "MALFORMED_SAFETENSORS"

    with pytest.raises(AssetLocalValidationError) as caught:
        _validator().validate(
            _lora_request(valid, filename="legacy-model.ckpt"),
            io.BytesIO(valid),
        )
    assert caught.value.code == "PICKLE_FORMAT_REJECTED"


def test_lora_validator_accepts_valid_space_padded_header() -> None:
    header = (
        b'{"weight":{"dtype":"F32","shape":[1],"data_offsets":[0,4]},'
        b'"__metadata__":{"source":"test"}}   '
    )
    payload = _raw_safetensors_bytes(header, b"\0" * 4)

    result = _validator().validate(_lora_request(payload), io.BytesIO(payload))

    assert result.facts["tensor_count"] == 1
