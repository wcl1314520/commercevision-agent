"""Killable framed subprocess boundary for untrusted native C2PA parsing."""

from __future__ import annotations

import json
import os
import signal
import struct
import subprocess
import sys
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import BinaryIO

from commercevision_contracts.validation import ProvenanceEvidenceStatus

from .c2pa_normalization import C2paReaderEvidence

_PROTOCOL_VERSION = 1
_REQUEST_MAGIC = b"CVCPARQ1"
_RESPONSE_MAGIC = b"CVCPARS1"
_REQUEST_PREFIX = struct.Struct("!8sIQ")
_RESPONSE_PREFIX = struct.Struct("!8sI")
_MAXIMUM_REQUEST_HEADER_BYTES = 2 * 1024 * 1024
_ABSOLUTE_MAXIMUM_ASSET_BYTES = 1024 * 1024 * 1024
_CHILD_MODULE = "commercevision_providers.c2pa_reader_child"
_ENVIRONMENT_ALLOWLIST = frozenset(
    {
        "COMSPEC",
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "PATHEXT",
        "PYTHONHOME",
        "PYTHONPATH",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
        "VIRTUAL_ENV",
        "WINDIR",
    }
)


class C2paReaderTimeout(TimeoutError):
    pass


class C2paReaderUnavailable(OSError):
    pass


class C2paReaderMalformed(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class C2paChildRequest:
    mime_type: str
    asset_bytes: bytes
    settings: dict[str, object]
    maximum_report_bytes: int
    maximum_report_depth: int
    maximum_report_nodes: int
    maximum_manifests: int
    maximum_status_codes: int
    timeout_seconds: float
    memory_limit_bytes: int
    file_descriptor_limit: int

    @property
    def maximum_output_bytes(self) -> int:
        return max(4096, self.maximum_status_codes * 160 + 2048)


class KillableC2paReaderBoundary:
    """Run each native parse in an independently killable child interpreter."""

    def __init__(
        self,
        *,
        maximum_report_bytes: int,
        maximum_report_depth: int = 32,
        maximum_report_nodes: int = 100_000,
        maximum_manifests: int = 1024,
        maximum_status_codes: int = 128,
        maximum_asset_bytes: int = _ABSOLUTE_MAXIMUM_ASSET_BYTES,
        memory_limit_bytes: int = 512 * 1024 * 1024,
        file_descriptor_limit: int = 64,
    ) -> None:
        if maximum_report_bytes < 1:
            raise ValueError("C2PA subprocess report bound must be positive")
        if maximum_report_depth < 1 or maximum_report_nodes < 1:
            raise ValueError("C2PA subprocess report structure bounds are invalid")
        if not 1 <= maximum_manifests <= 1024:
            raise ValueError("C2PA subprocess manifest bound is invalid")
        if not 1 <= maximum_status_codes <= 128:
            raise ValueError("C2PA subprocess status-code bound is invalid")
        if not 1 <= maximum_asset_bytes <= _ABSOLUTE_MAXIMUM_ASSET_BYTES:
            raise ValueError("C2PA subprocess asset bound is invalid")
        if memory_limit_bytes < 64 * 1024 * 1024:
            raise ValueError("C2PA subprocess memory bound is too small")
        if not 16 <= file_descriptor_limit <= 1024:
            raise ValueError("C2PA subprocess file-descriptor bound is invalid")
        self._maximum_report_bytes = maximum_report_bytes
        self._maximum_report_depth = maximum_report_depth
        self._maximum_report_nodes = maximum_report_nodes
        self._maximum_manifests = maximum_manifests
        self._maximum_status_codes = maximum_status_codes
        self._maximum_asset_bytes = maximum_asset_bytes
        self._memory_limit_bytes = memory_limit_bytes
        self._file_descriptor_limit = file_descriptor_limit
        self._closed = False

    def read(
        self,
        *,
        mime_type: str,
        asset_bytes: bytes,
        settings: dict[str, object],
        timeout_seconds: float,
    ) -> C2paReaderEvidence | None:
        if self._closed:
            raise C2paReaderUnavailable("C2PA subprocess boundary is closed")
        if (
            not isinstance(asset_bytes, bytes)
            or not asset_bytes
            or len(asset_bytes) > self._maximum_asset_bytes
        ):
            raise C2paReaderMalformed("C2PA subprocess asset input is invalid")
        if (
            not isinstance(mime_type, str)
            or not mime_type
            or len(mime_type) > 128
            or not mime_type.isascii()
        ):
            raise C2paReaderMalformed("C2PA subprocess MIME type is invalid")
        if timeout_seconds <= 0:
            raise C2paReaderTimeout("C2PA reader exceeded its hard deadline")

        request = C2paChildRequest(
            mime_type=mime_type,
            asset_bytes=asset_bytes,
            settings=settings,
            maximum_report_bytes=self._maximum_report_bytes,
            maximum_report_depth=self._maximum_report_depth,
            maximum_report_nodes=self._maximum_report_nodes,
            maximum_manifests=self._maximum_manifests,
            maximum_status_codes=self._maximum_status_codes,
            timeout_seconds=timeout_seconds,
            memory_limit_bytes=self._memory_limit_bytes,
            file_descriptor_limit=self._file_descriptor_limit,
        )
        request_chunks = _encode_child_request(request)
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", _CHILD_MODULE],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
                bufsize=0,
                env=_child_environment(),
                start_new_session=os.name == "posix",
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
            )
        except OSError as exc:
            raise C2paReaderUnavailable("C2PA reader subprocess could not start") from exc

        output: list[bytes] = []
        writer_errors: list[BaseException] = []
        output_overflow = threading.Event()
        writer = threading.Thread(
            target=_write_request,
            args=(process.stdin, request_chunks, writer_errors),
            daemon=True,
            name="c2pa-request-writer",
        )
        reader = threading.Thread(
            target=_read_response,
            args=(
                process.stdout,
                request.maximum_output_bytes,
                output,
                output_overflow,
            ),
            daemon=True,
            name="c2pa-response-reader",
        )
        writer.start()
        reader.start()
        deadline = started + timeout_seconds
        try:
            while process.poll() is None:
                if output_overflow.is_set():
                    _terminate_process_group(process)
                    raise C2paReaderMalformed("C2PA child response exceeds its bound")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _terminate_process_group(process)
                    raise C2paReaderTimeout("C2PA reader exceeded its hard deadline")
                time.sleep(min(0.01, remaining))

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process_group(process)
                raise C2paReaderTimeout("C2PA reader exceeded its hard deadline")
            writer.join(timeout=remaining)
            reader.join(timeout=remaining)
            if writer.is_alive() or reader.is_alive():
                _terminate_process_group(process)
                raise C2paReaderTimeout("C2PA reader exceeded its hard deadline")
            if output_overflow.is_set():
                raise C2paReaderMalformed("C2PA child response exceeds its bound")
            if process.returncode != 0:
                raise C2paReaderUnavailable("C2PA reader subprocess failed")
            if writer_errors and not output:
                raise C2paReaderUnavailable("C2PA reader subprocess rejected its input")
            if len(output) != 1:
                raise C2paReaderUnavailable("C2PA reader returned no bounded response")
            return _decode_child_response(
                output[0],
                maximum_manifests=self._maximum_manifests,
                maximum_status_codes=self._maximum_status_codes,
            )
        finally:
            if process.poll() is None:
                _terminate_process_group(process)
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    with suppress(OSError, ValueError):
                        stream.close()
            writer.join(timeout=0.25)
            reader.join(timeout=0.25)

    def close(self) -> None:
        self._closed = True


def _child_environment() -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if key.upper() in _ENVIRONMENT_ALLOWLIST
    }
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUNBUFFERED"] = "1"
    return environment


def _write_request(
    stream: BinaryIO | None,
    chunks: tuple[bytes, bytes, bytes],
    errors: list[BaseException],
) -> None:
    if stream is None:
        errors.append(OSError("C2PA child stdin is unavailable"))
        return
    try:
        for chunk in chunks:
            stream.write(chunk)
        stream.flush()
    except (BrokenPipeError, OSError, ValueError) as exc:
        errors.append(exc)
    finally:
        with suppress(OSError, ValueError):
            stream.close()


def _read_response(
    stream: BinaryIO | None,
    maximum_output_bytes: int,
    output: list[bytes],
    overflow: threading.Event,
) -> None:
    if stream is None:
        return
    try:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = stream.read(min(64 * 1024, maximum_output_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum_output_bytes:
                overflow.set()
                return
        output.append(b"".join(chunks))
    except (OSError, ValueError):
        return
    finally:
        with suppress(OSError, ValueError):
            stream.close()


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGTERM)
    else:
        with suppress(OSError):
            process.terminate()
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=0.5)
    if os.name == "posix":
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
    else:
        with suppress(OSError):
            process.kill()
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=0.5)


def _encode_child_request(
    request: C2paChildRequest,
) -> tuple[bytes, bytes, bytes]:
    header = json.dumps(
        {
            "file_descriptor_limit": request.file_descriptor_limit,
            "maximum_manifests": request.maximum_manifests,
            "maximum_report_bytes": request.maximum_report_bytes,
            "maximum_report_depth": request.maximum_report_depth,
            "maximum_report_nodes": request.maximum_report_nodes,
            "maximum_status_codes": request.maximum_status_codes,
            "memory_limit_bytes": request.memory_limit_bytes,
            "mime_type": request.mime_type,
            "protocol_version": _PROTOCOL_VERSION,
            "settings": request.settings,
            "timeout_milliseconds": max(1, round(request.timeout_seconds * 1000)),
        },
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(header) > _MAXIMUM_REQUEST_HEADER_BYTES:
        raise C2paReaderMalformed("C2PA child request header exceeds its bound")
    prefix = _REQUEST_PREFIX.pack(_REQUEST_MAGIC, len(header), len(request.asset_bytes))
    return prefix, header, request.asset_bytes


def read_child_request(stream: BinaryIO) -> C2paChildRequest:
    prefix = _read_exact(stream, _REQUEST_PREFIX.size)
    magic, header_length, asset_length = _REQUEST_PREFIX.unpack(prefix)
    if (
        magic != _REQUEST_MAGIC
        or not 1 <= header_length <= _MAXIMUM_REQUEST_HEADER_BYTES
        or not 1 <= asset_length <= _ABSOLUTE_MAXIMUM_ASSET_BYTES
    ):
        raise C2paReaderMalformed("C2PA child request frame is invalid")
    header_bytes = _read_exact(stream, header_length)
    asset_bytes = _read_exact(stream, asset_length)
    if stream.read(1) != b"":
        raise C2paReaderMalformed("C2PA child request has trailing bytes")
    try:
        decoded = json.loads(header_bytes)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise C2paReaderMalformed("C2PA child request header is malformed") from exc
    if not isinstance(decoded, dict) or set(decoded) != {
        "file_descriptor_limit",
        "maximum_manifests",
        "maximum_report_bytes",
        "maximum_report_depth",
        "maximum_report_nodes",
        "maximum_status_codes",
        "memory_limit_bytes",
        "mime_type",
        "protocol_version",
        "settings",
        "timeout_milliseconds",
    }:
        raise C2paReaderMalformed("C2PA child request fields are malformed")
    if decoded.get("protocol_version") != _PROTOCOL_VERSION:
        raise C2paReaderMalformed("C2PA child protocol version is unsupported")
    mime_type = decoded.get("mime_type")
    settings = decoded.get("settings")
    if (
        not isinstance(mime_type, str)
        or not mime_type
        or len(mime_type) > 128
        or not mime_type.isascii()
        or not isinstance(settings, dict)
    ):
        raise C2paReaderMalformed("C2PA child request identity is malformed")
    maximum_report_bytes = _bounded_integer(
        decoded,
        "maximum_report_bytes",
        minimum=1,
        maximum=16 * 1024 * 1024,
    )
    maximum_report_depth = _bounded_integer(
        decoded,
        "maximum_report_depth",
        minimum=1,
        maximum=128,
    )
    maximum_report_nodes = _bounded_integer(
        decoded,
        "maximum_report_nodes",
        minimum=1,
        maximum=2_000_000,
    )
    maximum_manifests = _bounded_integer(
        decoded,
        "maximum_manifests",
        minimum=1,
        maximum=1024,
    )
    maximum_status_codes = _bounded_integer(
        decoded,
        "maximum_status_codes",
        minimum=1,
        maximum=128,
    )
    timeout_milliseconds = _bounded_integer(
        decoded,
        "timeout_milliseconds",
        minimum=1,
        maximum=300_000,
    )
    memory_limit_bytes = _bounded_integer(
        decoded,
        "memory_limit_bytes",
        minimum=64 * 1024 * 1024,
        maximum=16 * 1024 * 1024 * 1024,
    )
    file_descriptor_limit = _bounded_integer(
        decoded,
        "file_descriptor_limit",
        minimum=16,
        maximum=1024,
    )
    return C2paChildRequest(
        mime_type=mime_type,
        asset_bytes=asset_bytes,
        settings=settings,
        maximum_report_bytes=maximum_report_bytes,
        maximum_report_depth=maximum_report_depth,
        maximum_report_nodes=maximum_report_nodes,
        maximum_manifests=maximum_manifests,
        maximum_status_codes=maximum_status_codes,
        timeout_seconds=timeout_milliseconds / 1000,
        memory_limit_bytes=memory_limit_bytes,
        file_descriptor_limit=file_descriptor_limit,
    )


def write_child_response(
    stream: BinaryIO,
    payload: dict[str, object],
    *,
    maximum_output_bytes: int,
) -> None:
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) + _RESPONSE_PREFIX.size > maximum_output_bytes:
        encoded = b'{"code":"MALFORMED_CREDENTIAL","protocol_version":1,"result":"error"}'
    stream.write(_RESPONSE_PREFIX.pack(_RESPONSE_MAGIC, len(encoded)))
    stream.write(encoded)
    stream.flush()


def _decode_child_response(
    framed: bytes,
    *,
    maximum_manifests: int,
    maximum_status_codes: int,
) -> C2paReaderEvidence | None:
    if len(framed) < _RESPONSE_PREFIX.size:
        raise C2paReaderMalformed("C2PA child response frame is truncated")
    magic, payload_length = _RESPONSE_PREFIX.unpack(framed[: _RESPONSE_PREFIX.size])
    payload = framed[_RESPONSE_PREFIX.size :]
    if magic != _RESPONSE_MAGIC or payload_length != len(payload):
        raise C2paReaderMalformed("C2PA child response frame is invalid")
    try:
        decoded = json.loads(payload)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise C2paReaderMalformed("C2PA child response is malformed") from exc
    if not isinstance(decoded, dict) or decoded.get("protocol_version") != _PROTOCOL_VERSION:
        raise C2paReaderMalformed("C2PA child response is malformed")
    result = decoded.get("result")
    if result == "error":
        if set(decoded) != {"code", "protocol_version", "result"}:
            raise C2paReaderMalformed("C2PA child error response is malformed")
        code = decoded.get("code")
        if code == "READER_UNAVAILABLE":
            raise C2paReaderUnavailable("C2PA native reader is unavailable")
        if code == "MALFORMED_CREDENTIAL":
            raise C2paReaderMalformed("C2PA native credential is malformed")
        raise C2paReaderMalformed("C2PA child error code is malformed")
    if result == "absent":
        if set(decoded) != {"protocol_version", "result"}:
            raise C2paReaderMalformed("C2PA child absent response is malformed")
        return None
    if result != "evidence" or set(decoded) != {
        "failure_codes",
        "manifest_count",
        "protocol_version",
        "result",
        "status",
        "validation_state",
    }:
        raise C2paReaderMalformed("C2PA child evidence response is malformed")
    try:
        status = ProvenanceEvidenceStatus(decoded.get("status"))
    except (TypeError, ValueError) as exc:
        raise C2paReaderMalformed("C2PA child evidence status is malformed") from exc
    validation_state = decoded.get("validation_state")
    manifest_count = decoded.get("manifest_count")
    failure_codes = decoded.get("failure_codes")
    if (
        validation_state is not None
        and validation_state not in {"TRUSTED", "VALID", "INVALID"}
        or isinstance(manifest_count, bool)
        or not isinstance(manifest_count, int)
        or not 0 <= manifest_count <= maximum_manifests
        or not isinstance(failure_codes, list)
        or len(failure_codes) > maximum_status_codes
    ):
        raise C2paReaderMalformed("C2PA child evidence values are malformed")
    normalized_codes: list[str] = []
    for code in failure_codes:
        if (
            not isinstance(code, str)
            or not code
            or len(code) > 128
            or not code.isascii()
            or any(
                not (character.isalnum() or character in {"_", "-", ".", ":"}) for character in code
            )
        ):
            raise C2paReaderMalformed("C2PA child failure code is malformed")
        normalized_codes.append(code)
    if normalized_codes != sorted(set(normalized_codes)):
        raise C2paReaderMalformed("C2PA child failure codes are not canonical")
    return C2paReaderEvidence(
        status=status,
        validation_state=validation_state,
        manifest_count=manifest_count,
        failure_codes=tuple(normalized_codes),
    )


def _read_exact(stream: BinaryIO, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise C2paReaderMalformed("C2PA child request frame is truncated")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _bounded_integer(
    values: dict[str, object],
    key: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    value = values.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise C2paReaderMalformed(f"C2PA child {key} is invalid")
    return value
