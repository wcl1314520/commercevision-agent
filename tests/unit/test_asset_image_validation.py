from __future__ import annotations

import io

import pytest
from commercevision_application.asset_image_metadata import (
    ImageDecodedBytesLimitExceededError,
    ImageDecodedBytesPolicy,
)
from commercevision_application.asset_local_validation import (
    AssetLocalValidationError,
    AssetLocalValidationRequest,
    AssetLocalValidator,
)
from commercevision_domain import AssetKind
from PIL import Image, PngImagePlugin


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
    }
    limits.update(changes)
    return AssetLocalValidator(**limits)


def _image_bytes(
    image_format: str,
    *,
    size: tuple[int, int] = (2, 3),
    frames: int = 1,
    metadata: str | None = None,
) -> bytes:
    target = io.BytesIO()
    images = [Image.new("RGB", size, color=(index * 20, 40, 80)) for index in range(frames)]
    options: dict[str, object] = {}
    if frames > 1:
        options.update(save_all=True, append_images=images[1:], duration=100, loop=0)
    if image_format == "PNG" and metadata is not None:
        png_info = PngImagePlugin.PngInfo()
        png_info.add_text("description", metadata)
        options["pnginfo"] = png_info
    images[0].save(target, format=image_format, **options)
    return target.getvalue()


def _jpeg_with_exif(exif: bytes) -> bytes:
    target = io.BytesIO()
    Image.new("RGB", (2, 3), color=(20, 40, 80)).save(
        target,
        format="JPEG",
        exif=exif,
    )
    return target.getvalue()


def _request(
    payload: bytes,
    *,
    filename: str,
    declared_mime: str,
) -> AssetLocalValidationRequest:
    return AssetLocalValidationRequest(
        asset_kind=AssetKind.IMAGE,
        filename=filename,
        declared_mime=declared_mime,
        byte_size=len(payload),
    )


@pytest.mark.parametrize(
    ("image_format", "filename", "declared_mime"),
    [
        ("JPEG", "product.jpg", "image/jpeg"),
        ("PNG", "product.png", "image/png"),
        ("WEBP", "product.webp", "image/webp"),
    ],
)
def test_image_validator_accepts_only_complete_raster_allowlist(
    image_format: str,
    filename: str,
    declared_mime: str,
) -> None:
    payload = _image_bytes(image_format)

    result = _validator().validate(
        _request(payload, filename=filename, declared_mime=declared_mime),
        io.BytesIO(payload),
    )

    assert result.detected_mime == declared_mime
    assert result.format_name == image_format
    assert result.facts["width"] == 2
    assert result.facts["height"] == 3
    assert result.facts["frame_count"] == 1


@pytest.mark.parametrize(
    "payload",
    [
        b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
        b"8BPS\x00\x01" + b"\0" * 32,
        b"PK\x03\x04" + b"\0" * 32,
        b"%PDF-1.7\n" + b"\0" * 32,
        b"\0\0\0\x18ftypmp42" + b"\0" * 32,
        b"MZ" + b"\0" * 32,
        b"GIF89a" + b"\0" * 32,
    ],
)
def test_image_validator_rejects_non_allowlisted_serializations(payload: bytes) -> None:
    with pytest.raises(AssetLocalValidationError) as caught:
        _validator().validate(
            _request(payload, filename="payload.png", declared_mime="image/png"),
            io.BytesIO(payload),
        )

    assert caught.value.code == "UNSAFE_IMAGE_FORMAT"


@pytest.mark.parametrize(
    ("transform", "filename", "declared_mime", "expected_code"),
    [
        (lambda value: value, "product.png", "image/jpeg", "DECLARED_MIME_MISMATCH"),
        (lambda value: value, "product.jpg", "image/png", "EXTENSION_MISMATCH"),
        (lambda value: value[:-8], "product.png", "image/png", "MALFORMED_IMAGE"),
        (lambda value: value + b"trailing", "product.png", "image/png", "MALFORMED_IMAGE"),
    ],
)
def test_image_validator_rejects_mismatch_truncation_and_trailing_bytes(
    transform: object,
    filename: str,
    declared_mime: str,
    expected_code: str,
) -> None:
    valid = _image_bytes("PNG")
    payload = transform(valid)  # type: ignore[operator]

    with pytest.raises(AssetLocalValidationError) as caught:
        _validator().validate(
            _request(payload, filename=filename, declared_mime=declared_mime),
            io.BytesIO(payload),
        )

    assert caught.value.code == expected_code


@pytest.mark.parametrize(
    ("payload", "limits", "expected_code"),
    [
        (
            _image_bytes("PNG"),
            {"maximum_image_bytes": len(_image_bytes("PNG")) - 1},
            "ASSET_TOO_LARGE",
        ),
        (
            _image_bytes("PNG", size=(9, 1)),
            {"maximum_image_dimension": 8},
            "IMAGE_DIMENSIONS_EXCEEDED",
        ),
        (
            _image_bytes("PNG", size=(5, 5)),
            {"maximum_image_pixels": 24},
            "IMAGE_PIXELS_EXCEEDED",
        ),
        (
            _image_bytes("WEBP", frames=2),
            {"maximum_image_frames": 1},
            "IMAGE_FRAMES_EXCEEDED",
        ),
        (
            _image_bytes("PNG", metadata="metadata-value"),
            {"maximum_metadata_bytes": 4},
            "IMAGE_METADATA_EXCEEDED",
        ),
        (
            _image_bytes("PNG", size=(1, 1)),
            {"maximum_image_decoded_bytes": 2},
            "IMAGE_DECOMPRESSION_LIMIT",
        ),
    ],
)
def test_image_validator_enforces_all_resource_bounds(
    payload: bytes,
    limits: dict[str, int],
    expected_code: str,
) -> None:
    with pytest.raises(AssetLocalValidationError) as caught:
        _validator(**limits).validate(
            _request(
                payload,
                filename="asset.webp" if payload[:4] == b"RIFF" else "asset.png",
                declared_mime="image/webp" if payload[:4] == b"RIFF" else "image/png",
            ),
            io.BytesIO(payload),
        )

    assert caught.value.code == expected_code


@pytest.mark.parametrize(
    ("mode", "bytes_per_sample"),
    [
        ("I;16", 2),
        ("I", 4),
    ],
)
def test_decoded_byte_policy_accounts_for_pillow_sample_width_at_exact_limit(
    mode: str,
    bytes_per_sample: int,
) -> None:
    image = Image.new(mode, (2, 3))
    exact_bytes = 2 * 3 * bytes_per_sample

    measurement = ImageDecodedBytesPolicy(maximum_bytes=exact_bytes).validate(
        image,
        width=2,
        height=3,
        frame_count=1,
    )
    with pytest.raises(ImageDecodedBytesLimitExceededError):
        ImageDecodedBytesPolicy(maximum_bytes=exact_bytes - 1).validate(
            image,
            width=2,
            height=3,
            frame_count=1,
        )

    assert measurement.byte_size == exact_bytes
    assert measurement.bytes_per_sample == bytes_per_sample


def test_16_bit_png_validator_enforces_decoded_bytes_at_exact_limit() -> None:
    target = io.BytesIO()
    Image.new("I;16", (2, 3)).save(target, format="PNG")
    payload = target.getvalue()

    accepted = _validator(maximum_image_decoded_bytes=12).validate(
        _request(payload, filename="depth.png", declared_mime="image/png"),
        io.BytesIO(payload),
    )
    with pytest.raises(AssetLocalValidationError) as failed:
        _validator(maximum_image_decoded_bytes=11).validate(
            _request(payload, filename="depth.png", declared_mime="image/png"),
            io.BytesIO(payload),
        )

    assert accepted.facts["decoded_bytes"] == 12
    assert failed.value.code == "IMAGE_DECOMPRESSION_LIMIT"


def test_image_validator_normalizes_byte_level_malformed_exif() -> None:
    payload = _jpeg_with_exif(b"Exif\x00\x00II*\x00\xff\xff\xff\xff")

    with pytest.raises(AssetLocalValidationError) as failed:
        _validator().validate(
            _request(payload, filename="malformed.jpg", declared_mime="image/jpeg"),
            io.BytesIO(payload),
        )

    assert failed.value.code == "MALFORMED_IMAGE"
