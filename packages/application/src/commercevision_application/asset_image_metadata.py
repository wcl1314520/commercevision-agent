"""Canonical bounded metadata accounting for decoded raster images."""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass

from PIL import Image, ImageMode


class ImageMetadataError(ValueError):
    """Base class for canonical image metadata policy failures."""


class MalformedImageMetadataError(ImageMetadataError):
    pass


class ImageMetadataLimitExceededError(ImageMetadataError):
    pass


class ImageDecodedBytesError(ValueError):
    """Base class for decoded-image accounting failures."""


class MalformedImageModeError(ImageDecodedBytesError):
    pass


class ImageDecodedBytesLimitExceededError(ImageDecodedBytesError):
    pass


@dataclass(frozen=True, slots=True)
class ImageMetadataMeasurement:
    byte_size: int
    entry_count: int


@dataclass(frozen=True, slots=True)
class ImageDecodedBytesMeasurement:
    byte_size: int
    band_count: int
    bytes_per_sample: int


class ImageDecodedBytesPolicy:
    """Conservatively account for Pillow's decoded in-memory sample width."""

    def __init__(self, *, maximum_bytes: int) -> None:
        if maximum_bytes < 1:
            raise ValueError("image decoded-byte limit must be positive")
        self._maximum_bytes = maximum_bytes

    def validate(
        self,
        image: Image.Image,
        *,
        width: int,
        height: int,
        frame_count: int,
    ) -> ImageDecodedBytesMeasurement:
        if width < 1 or height < 1 or frame_count < 1:
            raise MalformedImageModeError("image decoded dimensions are invalid")
        try:
            mode = ImageMode.getmode(image.mode)
            type_width = mode.typestr[2:]
            if (
                len(mode.typestr) < 3
                or mode.typestr[0] not in "<>=|"
                or mode.typestr[1] not in "biufc"
                or not type_width.isdecimal()
            ):
                raise ValueError("unsupported Pillow mode descriptor")
            bytes_per_sample = int(type_width)
            band_count = len(mode.bands)
        except (KeyError, TypeError, ValueError) as exc:
            raise MalformedImageModeError("image mode cannot be safely accounted") from exc
        if bytes_per_sample < 1 or band_count < 1:
            raise MalformedImageModeError("image mode cannot be safely accounted")

        byte_size = width * height * frame_count * band_count * bytes_per_sample
        if byte_size > self._maximum_bytes:
            raise ImageDecodedBytesLimitExceededError(
                "image decoded byte estimate exceeds the configured limit"
            )
        return ImageDecodedBytesMeasurement(
            byte_size=byte_size,
            band_count=band_count,
            bytes_per_sample=bytes_per_sample,
        )


class ImageMetadataPolicy:
    """Parse and account for metadata exactly once across every validation seam."""

    def __init__(self, *, maximum_bytes: int) -> None:
        if maximum_bytes < 1:
            raise ValueError("image metadata limit must be positive")
        self._maximum_bytes = maximum_bytes

    def validate(self, image: Image.Image) -> ImageMetadataMeasurement:
        byte_size = 0
        for key, value in image.info.items():
            byte_size += len(str(key).encode("utf-8"))
            byte_size += _metadata_value_bytes(value)
            if byte_size > self._maximum_bytes:
                raise ImageMetadataLimitExceededError("image metadata exceeds the configured limit")

        raw_exif = image.info.get("exif")
        if raw_exif is not None:
            try:
                if not isinstance(raw_exif, bytes | bytearray | memoryview):
                    raise TypeError("EXIF metadata is not byte-oriented")
                with warnings.catch_warnings():
                    warnings.simplefilter("error")
                    exif = Image.Exif()
                    exif.load(bytes(raw_exif))
                    exif.tobytes()
            except (OSError, RuntimeError, SyntaxError, TypeError, ValueError, Warning) as exc:
                raise MalformedImageMetadataError("image metadata is malformed") from exc

        return ImageMetadataMeasurement(
            byte_size=byte_size,
            entry_count=len(image.info),
        )


def _metadata_value_bytes(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, bytearray | memoryview):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, bool | int):
        return len(str(value).encode("ascii"))
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MalformedImageMetadataError("image metadata is malformed")
        return len(repr(value).encode("ascii"))
    if isinstance(value, tuple | list):
        return sum(_metadata_value_bytes(item) for item in value)
    if isinstance(value, dict):
        return sum(
            len(str(key).encode("utf-8")) + _metadata_value_bytes(item)
            for key, item in value.items()
        )
    raise MalformedImageMetadataError("image metadata contains an unsupported value")
