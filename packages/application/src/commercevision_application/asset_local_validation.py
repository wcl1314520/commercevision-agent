"""Strict local validation for quarantined Asset Version bytes."""

from __future__ import annotations

import io
import json
import math
import re
import struct
import warnings
from dataclasses import dataclass
from pathlib import PurePath
from typing import BinaryIO

from commercevision_domain import AssetKind
from commercevision_domain.workflow.errors import DomainError
from PIL import Image, UnidentifiedImageError

from .asset_image_metadata import (
    ImageDecodedBytesLimitExceededError,
    ImageDecodedBytesPolicy,
    ImageMetadataLimitExceededError,
    ImageMetadataPolicy,
    MalformedImageMetadataError,
    MalformedImageModeError,
)

_SAFETENSORS_DTYPES = {
    "BOOL": 1,
    "I8": 1,
    "U8": 1,
    "F8_E4M3": 1,
    "F8_E5M2": 1,
    "I16": 2,
    "U16": 2,
    "F16": 2,
    "BF16": 2,
    "I32": 4,
    "U32": 4,
    "F32": 4,
    "F64": 8,
    "I64": 8,
    "U64": 8,
}
_PICKLE_EXTENSIONS = frozenset({".bin", ".ckpt", ".pkl", ".pickle", ".pt", ".pth"})
_MAX_SAFE_JSON_INTEGER = (1 << 63) - 1
_MAX_SAFE_JSON_INTEGER_DIGITS = len(str(_MAX_SAFE_JSON_INTEGER))
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_END = b"\x00\x00\x00\x00IEND\xaeB`\x82"
_IMAGE_FORMATS = {
    "JPEG": ("image/jpeg", frozenset({".jpg", ".jpeg"})),
    "PNG": ("image/png", frozenset({".png"})),
    "WEBP": ("image/webp", frozenset({".webp"})),
}
_PROMPT_SCHEMA_VERSION = "commercevision.prompt-template.v1"
_MODEL_CONFIGURATION_SCHEMA_VERSION = "commercevision.model-configuration.v1"
_DOCUMENT_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_PROVIDER_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PLACEHOLDER = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]{0,63})\s*}}")
_MODEL_PARAMETER_RANGES: dict[str, tuple[float, float, type[int] | type[float]]] = {
    "guidance_scale": (0.0, 30.0, float),
    "height": (1, 1280, int),
    "max_output_tokens": (1, 32768, int),
    "seed": (0, (1 << 31) - 1, int),
    "steps": (1, 200, int),
    "temperature": (0.0, 2.0, float),
    "top_p": (0.0, 1.0, float),
    "width": (1, 1280, int),
}
_MODEL_STRING_PARAMETERS = {
    "negative_prompt": 8192,
}
_MODEL_ENUM_PARAMETERS = {
    "output_format": frozenset({"jpeg", "png", "webp"}),
}


class AssetLocalValidationError(DomainError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class AssetLocalValidationRequest:
    asset_kind: AssetKind
    filename: str
    declared_mime: str
    byte_size: int


@dataclass(frozen=True, slots=True)
class AssetLocalValidationResult:
    detected_mime: str
    format_name: str
    facts: dict[str, int | str]


class _DuplicateJsonKey(ValueError):
    pass


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _bounded_json_integer(raw_value: str) -> int:
    digits = raw_value.removeprefix("-")
    if len(digits) > _MAX_SAFE_JSON_INTEGER_DIGITS:
        raise ValueError("JSON integer exceeds the SafeTensors arithmetic bound")
    value = int(raw_value)
    if abs(value) > _MAX_SAFE_JSON_INTEGER:
        raise ValueError("JSON integer exceeds the SafeTensors arithmetic bound")
    return value


def _reject_json_number(raw_value: str) -> float:
    raise ValueError(f"unsupported JSON numeric literal: {raw_value}")


def _bounded_json_float(raw_value: str) -> float:
    if len(raw_value) > 64:
        raise ValueError("JSON floating-point literal exceeds the configured bound")
    value = float(raw_value)
    if not math.isfinite(value):
        raise ValueError("JSON floating-point literal must be finite")
    return value


def _enforce_json_complexity(
    value: object,
    *,
    maximum_depth: int,
    maximum_nodes: int,
) -> None:
    pending: list[tuple[object, int]] = [(value, 1)]
    visited = 0
    while pending:
        current, depth = pending.pop()
        visited += 1
        if visited > maximum_nodes:
            raise ValueError("JSON node count exceeds the configured bound")
        if isinstance(current, dict):
            child_count = len(current)
            children = current.values()
        elif isinstance(current, list):
            child_count = len(current)
            children = current
        else:
            continue
        if child_count == 0:
            continue
        if depth >= maximum_depth:
            raise ValueError("JSON nesting depth exceeds the configured bound")
        if visited + len(pending) + child_count > maximum_nodes:
            raise ValueError("JSON node count exceeds the configured bound")
        pending.extend((child, depth + 1) for child in children)


class AssetLocalValidator:
    """Validate supported Asset kinds without executing or deserializing payloads."""

    def __init__(
        self,
        *,
        maximum_image_bytes: int,
        maximum_image_dimension: int,
        maximum_image_pixels: int,
        maximum_image_frames: int,
        maximum_image_decoded_bytes: int,
        maximum_metadata_bytes: int,
        maximum_lora_bytes: int,
        maximum_safetensors_header_bytes: int,
        maximum_safetensors_tensors: int,
        maximum_safetensors_rank: int,
        maximum_safetensors_dimension: int,
        maximum_safetensors_elements: int,
        maximum_prompt_bytes: int,
        maximum_model_configuration_bytes: int,
        maximum_json_depth: int = 32,
        maximum_json_nodes: int = 10_000,
    ) -> None:
        limits = (
            maximum_image_bytes,
            maximum_image_dimension,
            maximum_image_pixels,
            maximum_image_frames,
            maximum_image_decoded_bytes,
            maximum_metadata_bytes,
            maximum_lora_bytes,
            maximum_safetensors_header_bytes,
            maximum_safetensors_tensors,
            maximum_safetensors_rank,
            maximum_safetensors_dimension,
            maximum_safetensors_elements,
            maximum_prompt_bytes,
            maximum_model_configuration_bytes,
            maximum_json_depth,
            maximum_json_nodes,
        )
        if any(limit < 1 for limit in limits):
            raise ValueError("local Asset validation limits must be positive")
        self._maximum_image_bytes = maximum_image_bytes
        self._maximum_image_dimension = maximum_image_dimension
        self._maximum_image_pixels = maximum_image_pixels
        self._maximum_image_frames = maximum_image_frames
        self._decoded_bytes_policy = ImageDecodedBytesPolicy(
            maximum_bytes=maximum_image_decoded_bytes,
        )
        self._metadata_policy = ImageMetadataPolicy(
            maximum_bytes=maximum_metadata_bytes,
        )
        self._maximum_lora_bytes = maximum_lora_bytes
        self._maximum_safetensors_header_bytes = maximum_safetensors_header_bytes
        self._maximum_safetensors_tensors = maximum_safetensors_tensors
        self._maximum_safetensors_rank = maximum_safetensors_rank
        self._maximum_safetensors_dimension = maximum_safetensors_dimension
        self._maximum_safetensors_elements = maximum_safetensors_elements
        self._maximum_prompt_bytes = maximum_prompt_bytes
        self._maximum_model_configuration_bytes = maximum_model_configuration_bytes
        self._maximum_json_depth = maximum_json_depth
        self._maximum_json_nodes = maximum_json_nodes

    def validate(
        self,
        request: AssetLocalValidationRequest,
        stream: BinaryIO,
    ) -> AssetLocalValidationResult:
        if request.byte_size < 1:
            self._reject("EMPTY_ASSET", "Asset content must not be empty")
        actual_size = self._stream_size(stream)
        if actual_size != request.byte_size:
            self._reject("BYTE_SIZE_MISMATCH", "Asset content length changed during validation")
        if request.asset_kind == AssetKind.IMAGE:
            return self._validate_image(request, stream)
        if request.asset_kind == AssetKind.LORA:
            return self._validate_safetensors(request, stream)
        if request.asset_kind == AssetKind.PROMPT_TEMPLATE:
            return self._validate_prompt_template(request, stream)
        if request.asset_kind == AssetKind.MODEL_CONFIGURATION:
            return self._validate_model_configuration(request, stream)
        self._reject(
            "UNSUPPORTED_ASSET_KIND",
            f"local validation is not registered for {request.asset_kind.value}",
        )

    def _validate_image(
        self,
        request: AssetLocalValidationRequest,
        stream: BinaryIO,
    ) -> AssetLocalValidationResult:
        if request.byte_size > self._maximum_image_bytes:
            self._reject("ASSET_TOO_LARGE", "image exceeds the configured byte limit")
        image_format = self._detect_image_format(stream, byte_size=request.byte_size)
        detected_mime, extensions = _IMAGE_FORMATS[image_format]
        if request.declared_mime.lower() != detected_mime:
            self._reject(
                "DECLARED_MIME_MISMATCH",
                "declared MIME does not match detected image bytes",
            )
        if PurePath(request.filename).suffix.lower() not in extensions:
            self._reject(
                "EXTENSION_MISMATCH",
                "filename extension does not match detected image bytes",
            )

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                warnings.simplefilter("error", UserWarning)
                stream.seek(0)
                with Image.open(stream) as probe:
                    if str(probe.format or "").upper() != image_format:
                        self._reject(
                            "MALFORMED_IMAGE",
                            "image decoder format conflicts with its magic bytes",
                        )
                    width, height = probe.size
                    frame_count = int(getattr(probe, "n_frames", 1))
                    metadata_bytes = self._validate_image_metadata(probe)
                    decoded_bytes = self._assert_image_limits(
                        image=probe,
                        width=width,
                        height=height,
                        frame_count=frame_count,
                        metadata_bytes=metadata_bytes,
                    )
                    probe.verify()

                stream.seek(0)
                with Image.open(stream) as image:
                    if (
                        str(image.format or "").upper() != image_format
                        or image.size != (width, height)
                        or int(getattr(image, "n_frames", 1)) != frame_count
                    ):
                        self._reject(
                            "MALFORMED_IMAGE",
                            "image facts changed during complete decode",
                        )
                    metadata_bytes = max(
                        metadata_bytes,
                        self._validate_image_metadata(image),
                    )
                    decoded_bytes = self._assert_image_limits(
                        image=image,
                        width=width,
                        height=height,
                        frame_count=frame_count,
                        metadata_bytes=metadata_bytes,
                    )
                    for frame_number in range(frame_count):
                        image.seek(frame_number)
                        if image.size != (width, height):
                            self._reject(
                                "MALFORMED_IMAGE",
                                "image frames do not have stable dimensions",
                            )
                        image.load()
        except AssetLocalValidationError:
            raise
        except (
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            UnidentifiedImageError,
            OSError,
            RuntimeError,
            SyntaxError,
            ValueError,
            Warning,
        ) as exc:
            raise AssetLocalValidationError(
                "MALFORMED_IMAGE",
                "image cannot be decoded completely",
            ) from exc

        return AssetLocalValidationResult(
            detected_mime=detected_mime,
            format_name=image_format,
            facts={
                "decoded_bytes": decoded_bytes,
                "frame_count": frame_count,
                "height": height,
                "metadata_bytes": metadata_bytes,
                "width": width,
            },
        )

    def _detect_image_format(self, stream: BinaryIO, *, byte_size: int) -> str:
        stream.seek(0)
        prefix = stream.read(12)
        if prefix.startswith(b"\xff\xd8\xff"):
            stream.seek(-2, io.SEEK_END)
            if stream.read(2) != b"\xff\xd9":
                self._reject("MALFORMED_IMAGE", "JPEG serialization is incomplete")
            return "JPEG"
        if prefix.startswith(_PNG_SIGNATURE):
            if byte_size < len(_PNG_END):
                self._reject("MALFORMED_IMAGE", "PNG serialization is incomplete")
            stream.seek(-len(_PNG_END), io.SEEK_END)
            if stream.read(len(_PNG_END)) != _PNG_END:
                self._reject("MALFORMED_IMAGE", "PNG serialization is incomplete")
            return "PNG"
        if prefix[:4] == b"RIFF" and prefix[8:12] == b"WEBP":
            if byte_size < 12 or struct.unpack("<I", prefix[4:8])[0] != byte_size - 8:
                self._reject("MALFORMED_IMAGE", "WebP RIFF length is invalid")
            return "WEBP"
        self._reject(
            "UNSAFE_IMAGE_FORMAT",
            "image bytes are not an allowed JPEG, PNG, or WebP serialization",
        )

    def _assert_image_limits(
        self,
        *,
        image: Image.Image,
        width: int,
        height: int,
        frame_count: int,
        metadata_bytes: int,
    ) -> int:
        if width < 1 or height < 1:
            self._reject("MALFORMED_IMAGE", "image dimensions must be positive")
        if width > self._maximum_image_dimension or height > self._maximum_image_dimension:
            self._reject(
                "IMAGE_DIMENSIONS_EXCEEDED",
                "image dimensions exceed the configured limit",
            )
        if frame_count < 1 or frame_count > self._maximum_image_frames:
            self._reject(
                "IMAGE_FRAMES_EXCEEDED",
                "image frame count exceeds the configured limit",
            )
        pixels = width * height * frame_count
        if pixels > self._maximum_image_pixels:
            self._reject(
                "IMAGE_PIXELS_EXCEEDED",
                "image decoded pixels exceed the configured limit",
            )
        try:
            return self._decoded_bytes_policy.validate(
                image,
                width=width,
                height=height,
                frame_count=frame_count,
            ).byte_size
        except ImageDecodedBytesLimitExceededError as exc:
            self._reject(
                "IMAGE_DECOMPRESSION_LIMIT",
                "image decoded byte estimate exceeds the configured limit",
            )
            raise AssertionError("unreachable") from exc
        except MalformedImageModeError as exc:
            self._reject(
                "MALFORMED_IMAGE",
                "image mode cannot be safely accounted",
            )
            raise AssertionError("unreachable") from exc

    def _validate_image_metadata(self, image: Image.Image) -> int:
        try:
            return self._metadata_policy.validate(image).byte_size
        except ImageMetadataLimitExceededError as exc:
            raise AssetLocalValidationError(
                "IMAGE_METADATA_EXCEEDED",
                "image metadata exceeds the configured limit",
            ) from exc
        except MalformedImageMetadataError as exc:
            raise AssetLocalValidationError(
                "MALFORMED_IMAGE",
                "image metadata is malformed",
            ) from exc

    def _validate_safetensors(
        self,
        request: AssetLocalValidationRequest,
        stream: BinaryIO,
    ) -> AssetLocalValidationResult:
        extension = PurePath(request.filename).suffix.lower()
        if extension in _PICKLE_EXTENSIONS:
            self._reject("PICKLE_FORMAT_REJECTED", "pickle-based model formats are not accepted")
        if extension != ".safetensors":
            self._reject(
                "UNSAFE_MODEL_FORMAT",
                "LoRA registration requires the .safetensors format",
            )
        if request.declared_mime.lower() not in {
            "application/octet-stream",
            "application/x-safetensors",
        }:
            self._reject(
                "DECLARED_MIME_MISMATCH",
                "LoRA declared MIME is not an allowed SafeTensors MIME",
            )
        if request.byte_size > self._maximum_lora_bytes:
            self._reject("ASSET_TOO_LARGE", "LoRA exceeds the configured byte limit")
        if request.byte_size < 10:
            self._reject("MALFORMED_SAFETENSORS", "SafeTensors content is truncated")

        stream.seek(0)
        raw_header_length = stream.read(8)
        if len(raw_header_length) != 8:
            self._reject("MALFORMED_SAFETENSORS", "SafeTensors header length is truncated")
        header_length = struct.unpack("<Q", raw_header_length)[0]
        if (
            header_length < 2
            or header_length > self._maximum_safetensors_header_bytes
            or header_length > request.byte_size - 8
        ):
            self._reject("MALFORMED_SAFETENSORS", "SafeTensors header length is invalid")
        encoded_header = stream.read(header_length)
        if len(encoded_header) != header_length or not encoded_header.startswith(b"{"):
            self._reject("MALFORMED_SAFETENSORS", "SafeTensors JSON header is invalid")
        try:
            header = json.loads(
                encoded_header.decode("utf-8"),
                object_pairs_hook=_strict_json_object,
                parse_int=_bounded_json_integer,
                parse_float=_reject_json_number,
                parse_constant=_reject_json_number,
            )
            _enforce_json_complexity(
                header,
                maximum_depth=self._maximum_json_depth,
                maximum_nodes=self._maximum_json_nodes,
            )
        except (RecursionError, UnicodeDecodeError, ValueError) as exc:
            raise AssetLocalValidationError(
                "MALFORMED_SAFETENSORS",
                "SafeTensors JSON header is invalid",
            ) from exc
        if not isinstance(header, dict):
            self._reject("MALFORMED_SAFETENSORS", "SafeTensors header must be an object")

        data_bytes = request.byte_size - 8 - header_length
        tensor_count, metadata_entries = self._validate_safetensors_header(
            header,
            data_bytes=data_bytes,
        )
        return AssetLocalValidationResult(
            detected_mime="application/x-safetensors",
            format_name="SAFETENSORS",
            facts={
                "data_bytes": data_bytes,
                "metadata_entries": metadata_entries,
                "tensor_count": tensor_count,
            },
        )

    def _validate_safetensors_header(
        self,
        header: dict[str, object],
        *,
        data_bytes: int,
    ) -> tuple[int, int]:
        metadata = header.get("__metadata__", {})
        if not isinstance(metadata, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in metadata.items()
        ):
            self._reject(
                "MALFORMED_SAFETENSORS",
                "SafeTensors metadata must contain only string values",
            )
        tensor_items = [(name, value) for name, value in header.items() if name != "__metadata__"]
        if not tensor_items or len(tensor_items) > self._maximum_safetensors_tensors:
            self._reject(
                "MALFORMED_SAFETENSORS",
                "SafeTensors tensor count is outside the configured bound",
            )

        intervals: list[tuple[int, int]] = []
        for name, raw_tensor in tensor_items:
            if not name or not isinstance(raw_tensor, dict):
                self._reject("MALFORMED_SAFETENSORS", "SafeTensors tensor entry is invalid")
            if set(raw_tensor) != {"dtype", "shape", "data_offsets"}:
                self._reject(
                    "MALFORMED_SAFETENSORS",
                    "SafeTensors tensor schema contains unsupported fields",
                )
            dtype = raw_tensor["dtype"]
            shape = raw_tensor["shape"]
            offsets = raw_tensor["data_offsets"]
            if not isinstance(dtype, str) or dtype not in _SAFETENSORS_DTYPES:
                self._reject("MALFORMED_SAFETENSORS", "SafeTensors dtype is unsupported")
            if (
                not isinstance(shape, list)
                or len(shape) > self._maximum_safetensors_rank
                or any(
                    not isinstance(dimension, int)
                    or isinstance(dimension, bool)
                    or dimension < 0
                    or dimension > self._maximum_safetensors_dimension
                    for dimension in shape
                )
            ):
                self._reject("MALFORMED_SAFETENSORS", "SafeTensors shape is invalid")
            if (
                not isinstance(offsets, list)
                or len(offsets) != 2
                or any(
                    not isinstance(offset, int)
                    or isinstance(offset, bool)
                    or offset < 0
                    or offset > _MAX_SAFE_JSON_INTEGER
                    for offset in offsets
                )
            ):
                self._reject("MALFORMED_SAFETENSORS", "SafeTensors offsets are invalid")
            start, end = offsets
            if start > end or end > data_bytes:
                self._reject("MALFORMED_SAFETENSORS", "SafeTensors offsets exceed data")
            element_count = 1
            for dimension in shape:
                if (
                    dimension > 0
                    and element_count > self._maximum_safetensors_elements // dimension
                ):
                    self._reject(
                        "MALFORMED_SAFETENSORS",
                        "SafeTensors tensor element count exceeds the configured bound",
                    )
                element_count *= dimension
            if element_count * _SAFETENSORS_DTYPES[dtype] != end - start:
                self._reject(
                    "MALFORMED_SAFETENSORS",
                    "SafeTensors shape does not match its byte range",
                )
            intervals.append((start, end))

        position = 0
        for start, end in sorted(intervals):
            if start != position:
                self._reject(
                    "MALFORMED_SAFETENSORS",
                    "SafeTensors data offsets overlap or leave holes",
                )
            position = end
        if position != data_bytes:
            self._reject(
                "MALFORMED_SAFETENSORS",
                "SafeTensors data offsets do not cover the data buffer",
            )
        return len(tensor_items), len(metadata)

    def _validate_prompt_template(
        self,
        request: AssetLocalValidationRequest,
        stream: BinaryIO,
    ) -> AssetLocalValidationResult:
        document = self._load_json_document(
            request,
            stream,
            expected_filename_suffix=".prompt.json",
            maximum_bytes=self._maximum_prompt_bytes,
            malformed_code="MALFORMED_PROMPT_TEMPLATE",
        )
        expected_fields = {"schema_version", "name", "template", "variables"}
        if set(document) != expected_fields:
            self._reject(
                "INVALID_PROMPT_TEMPLATE_SCHEMA",
                "Prompt template fields do not match the registered schema",
            )
        schema_version = document["schema_version"]
        name = document["name"]
        template = document["template"]
        variables = document["variables"]
        if schema_version != _PROMPT_SCHEMA_VERSION:
            self._reject(
                "INVALID_PROMPT_TEMPLATE_SCHEMA",
                "Prompt template schema version is unsupported",
            )
        if not self._bounded_text(name, maximum_characters=128, allow_line_breaks=False):
            self._reject("INVALID_PROMPT_TEMPLATE_SCHEMA", "Prompt template name is invalid")
        if not self._bounded_text(
            template,
            maximum_characters=32768,
            allow_line_breaks=True,
        ):
            self._reject("INVALID_PROMPT_TEMPLATE_SCHEMA", "Prompt template text is invalid")
        if not isinstance(variables, list) or len(variables) > 64:
            self._reject(
                "INVALID_PROMPT_TEMPLATE_SCHEMA",
                "Prompt template variables are invalid",
            )

        variable_names: set[str] = set()
        for variable in variables:
            if not isinstance(variable, dict) or set(variable) != {"name", "required"}:
                self._reject(
                    "INVALID_PROMPT_TEMPLATE_SCHEMA",
                    "Prompt template variable schema is invalid",
                )
            variable_name = variable["name"]
            required = variable["required"]
            if (
                not isinstance(variable_name, str)
                or _DOCUMENT_IDENTIFIER.fullmatch(variable_name) is None
                or not isinstance(required, bool)
                or variable_name in variable_names
            ):
                self._reject(
                    "INVALID_PROMPT_TEMPLATE_SCHEMA",
                    "Prompt template variable is invalid",
                )
            variable_names.add(variable_name)

        assert isinstance(template, str)
        if "{%" in template or "{#" in template:
            self._reject(
                "UNSAFE_PROMPT_TEMPLATE",
                "Prompt templates may contain substitutions only",
            )
        placeholder_names = set(_PLACEHOLDER.findall(template))
        stripped_template = _PLACEHOLDER.sub("", template)
        if (
            "{{" in stripped_template
            or "}}" in stripped_template
            or placeholder_names != variable_names
        ):
            self._reject(
                "INVALID_PROMPT_TEMPLATE_SCHEMA",
                "Prompt template placeholders do not match declared variables",
            )
        return AssetLocalValidationResult(
            detected_mime="application/json",
            format_name="PROMPT_TEMPLATE_JSON",
            facts={
                "character_count": len(template),
                "schema_version": _PROMPT_SCHEMA_VERSION,
                "variable_count": len(variable_names),
            },
        )

    def _validate_model_configuration(
        self,
        request: AssetLocalValidationRequest,
        stream: BinaryIO,
    ) -> AssetLocalValidationResult:
        document = self._load_json_document(
            request,
            stream,
            expected_filename_suffix=".model.json",
            maximum_bytes=self._maximum_model_configuration_bytes,
            malformed_code="MALFORMED_MODEL_CONFIGURATION",
        )
        expected_fields = {
            "schema_version",
            "provider",
            "model_id",
            "model_revision",
            "parameters",
        }
        if set(document) != expected_fields:
            self._reject(
                "INVALID_MODEL_CONFIGURATION_SCHEMA",
                "model configuration fields do not match the registered schema",
            )
        schema_version = document["schema_version"]
        provider = document["provider"]
        model_id = document["model_id"]
        model_revision = document["model_revision"]
        parameters = document["parameters"]
        if schema_version != _MODEL_CONFIGURATION_SCHEMA_VERSION:
            self._reject(
                "INVALID_MODEL_CONFIGURATION_SCHEMA",
                "model configuration schema version is unsupported",
            )
        if not isinstance(provider, str) or _PROVIDER_IDENTIFIER.fullmatch(provider) is None:
            self._reject(
                "INVALID_MODEL_CONFIGURATION_SCHEMA",
                "model provider identity is invalid",
            )
        for value, field in ((model_id, "model_id"), (model_revision, "model_revision")):
            if not self._bounded_text(value, maximum_characters=128, allow_line_breaks=False):
                self._reject(
                    "INVALID_MODEL_CONFIGURATION_SCHEMA",
                    f"{field} is invalid",
                )
        if not isinstance(parameters, dict) or len(parameters) > 32:
            self._reject(
                "INVALID_MODEL_CONFIGURATION_SCHEMA",
                "model parameters must be a bounded object",
            )
        allowed_parameters = (
            set(_MODEL_PARAMETER_RANGES)
            | set(_MODEL_STRING_PARAMETERS)
            | set(_MODEL_ENUM_PARAMETERS)
        )
        if not set(parameters).issubset(allowed_parameters):
            self._reject(
                "INVALID_MODEL_CONFIGURATION_SCHEMA",
                "model configuration contains unsupported parameters",
            )
        for parameter, value in parameters.items():
            if parameter in _MODEL_PARAMETER_RANGES:
                minimum, maximum, required_type = _MODEL_PARAMETER_RANGES[parameter]
                if required_type is int:
                    valid_type = isinstance(value, int) and not isinstance(value, bool)
                else:
                    valid_type = (
                        isinstance(value, (int, float))
                        and not isinstance(value, bool)
                        and math.isfinite(value)
                    )
                if not valid_type or not minimum <= value <= maximum:
                    self._reject(
                        "INVALID_MODEL_CONFIGURATION_SCHEMA",
                        f"model parameter {parameter} is outside its bound",
                    )
            elif parameter in _MODEL_STRING_PARAMETERS:
                if not self._bounded_text(
                    value,
                    maximum_characters=_MODEL_STRING_PARAMETERS[parameter],
                    allow_line_breaks=True,
                ):
                    self._reject(
                        "INVALID_MODEL_CONFIGURATION_SCHEMA",
                        f"model parameter {parameter} is invalid",
                    )
            elif not isinstance(value, str) or value not in _MODEL_ENUM_PARAMETERS[parameter]:
                self._reject(
                    "INVALID_MODEL_CONFIGURATION_SCHEMA",
                    f"model parameter {parameter} is invalid",
                )
        assert isinstance(provider, str)
        assert isinstance(model_id, str)
        return AssetLocalValidationResult(
            detected_mime="application/json",
            format_name="MODEL_CONFIGURATION_JSON",
            facts={
                "model_id": model_id,
                "parameter_count": len(parameters),
                "provider": provider,
                "schema_version": _MODEL_CONFIGURATION_SCHEMA_VERSION,
            },
        )

    def _load_json_document(
        self,
        request: AssetLocalValidationRequest,
        stream: BinaryIO,
        *,
        expected_filename_suffix: str,
        maximum_bytes: int,
        malformed_code: str,
    ) -> dict[str, object]:
        if not request.filename.lower().endswith(expected_filename_suffix):
            self._reject(
                "UNSAFE_DOCUMENT_FORMAT",
                f"document filename must end with {expected_filename_suffix}",
            )
        if request.declared_mime.lower() != "application/json":
            self._reject(
                "DECLARED_MIME_MISMATCH",
                "structured Asset declared MIME must be application/json",
            )
        if request.byte_size > maximum_bytes:
            self._reject("ASSET_TOO_LARGE", "structured Asset exceeds its byte limit")
        stream.seek(0)
        encoded = stream.read(maximum_bytes + 1)
        if len(encoded) != request.byte_size or not encoded.startswith(b"{"):
            self._reject(malformed_code, "structured Asset is not canonical UTF-8 JSON")
        try:
            document = json.loads(
                encoded.decode("utf-8"),
                object_pairs_hook=_strict_json_object,
                parse_int=_bounded_json_integer,
                parse_float=_bounded_json_float,
                parse_constant=_reject_json_number,
            )
            _enforce_json_complexity(
                document,
                maximum_depth=self._maximum_json_depth,
                maximum_nodes=self._maximum_json_nodes,
            )
        except (RecursionError, UnicodeDecodeError, ValueError) as exc:
            raise AssetLocalValidationError(
                malformed_code,
                "structured Asset is not canonical UTF-8 JSON",
            ) from exc
        if not isinstance(document, dict):
            self._reject(malformed_code, "structured Asset root must be an object")
        return document

    @staticmethod
    def _bounded_text(
        value: object,
        *,
        maximum_characters: int,
        allow_line_breaks: bool,
    ) -> bool:
        if not isinstance(value, str) or not value or len(value) > maximum_characters:
            return False
        allowed_controls = {"\n", "\r", "\t"} if allow_line_breaks else set()
        return not any(
            (ord(character) < 32 and character not in allowed_controls) or ord(character) == 127
            for character in value
        )

    @staticmethod
    def _stream_size(stream: BinaryIO) -> int:
        try:
            stream.seek(0, io.SEEK_END)
            size = stream.tell()
            stream.seek(0)
        except (OSError, ValueError) as exc:
            raise AssetLocalValidationError(
                "UNSUPPORTED_STREAM",
                "Asset validation requires a bounded seekable stream",
            ) from exc
        return size

    @staticmethod
    def _reject(code: str, message: str) -> None:
        raise AssetLocalValidationError(code, message)
