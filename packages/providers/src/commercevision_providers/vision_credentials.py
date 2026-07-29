"""Fail-closed API-key sources for Model Studio Vision submissions."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Protocol


class VisionApiKeyUnavailableError(RuntimeError):
    """A credential could not be resolved before provider submission."""


class VisionApiKeyProvider(Protocol):
    def resolve(self) -> str: ...


def _validated_api_key(value: str) -> str:
    normalized = value.strip()
    if not normalized or any(
        ord(character) < 0x21 or ord(character) > 0x7E for character in normalized
    ):
        raise VisionApiKeyUnavailableError("Vision API key is unavailable")
    return normalized


class StaticVisionApiKeyProvider:
    """Compatibility source for local deployments without in-process rotation."""

    __slots__ = ("__api_key",)

    def __init__(self, api_key: str) -> None:
        self.__api_key = _validated_api_key(api_key)

    def resolve(self) -> str:
        return self.__api_key

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"


class MountedFileVisionApiKeyProvider:
    """Read one coherent, bounded key file for every provider submission."""

    __slots__ = ("_maximum_bytes", "_path")

    def __init__(
        self,
        *,
        path: str | os.PathLike[str],
        maximum_bytes: int,
    ) -> None:
        normalized_path = Path(path)
        if not normalized_path.is_absolute():
            raise ValueError("Vision API key file path must be absolute")
        if not 1 <= maximum_bytes <= 64 * 1024:
            raise ValueError("Vision API key file bound must be between 1 and 65536 bytes")
        self._path = normalized_path
        self._maximum_bytes = maximum_bytes

    def resolve(self) -> str:
        for read_attempt in range(2):
            try:
                payload = self._read_stable_file()
            except FileNotFoundError:
                if read_attempt == 0:
                    continue
                raise VisionApiKeyUnavailableError("Vision API key is unavailable") from None
            if payload is not None:
                try:
                    return _validated_api_key(payload.decode("utf-8"))
                except (UnicodeDecodeError, VisionApiKeyUnavailableError) as exc:
                    raise VisionApiKeyUnavailableError("Vision API key is unavailable") from exc
        raise VisionApiKeyUnavailableError("Vision API key changed while it was being read")

    def _read_stable_file(self) -> bytes | None:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(self._path, flags)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise VisionApiKeyUnavailableError("Vision API key is unavailable") from exc
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise VisionApiKeyUnavailableError("Vision API key source is not a regular file")
            payload = bytearray()
            while len(payload) <= self._maximum_bytes:
                chunk = os.read(
                    descriptor,
                    min(4096, self._maximum_bytes + 1 - len(payload)),
                )
                if not chunk:
                    break
                payload.extend(chunk)
            if len(payload) > self._maximum_bytes:
                raise VisionApiKeyUnavailableError(
                    "Vision API key exceeds the configured byte bound"
                )
            after = os.fstat(descriptor)
        except OSError as exc:
            raise VisionApiKeyUnavailableError("Vision API key is unavailable") from exc
        finally:
            os.close(descriptor)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        if before_identity != after_identity or after.st_size != len(payload):
            return None
        return bytes(payload)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(path={str(self._path)!r}, maximum_bytes={self._maximum_bytes})"
        )
