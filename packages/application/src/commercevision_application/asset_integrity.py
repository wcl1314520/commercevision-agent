"""Transaction-free integrity proof for objects awaiting Asset registration."""

from __future__ import annotations

import hashlib
import tempfile
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePath

from commercevision_contracts.object_storage import (
    BoundedReadRequest,
    ObjectReference,
    ObjectStat,
    ObjectStorage,
)
from commercevision_domain import AssetKind, ObjectMismatchError, UploadSession
from PIL import Image, UnidentifiedImageError

from .asset_image_metadata import (
    ImageMetadataLimitExceededError,
    ImageMetadataPolicy,
    MalformedImageMetadataError,
)

_FORMAT_FACTS = {
    "JPEG": ("image/jpeg", frozenset({".jpg", ".jpeg"})),
    "PNG": ("image/png", frozenset({".png"})),
    "WEBP": ("image/webp", frozenset({".webp"})),
}


@dataclass(frozen=True, slots=True)
class VerifiedUpload:
    stat: ObjectStat
    sha256: str
    byte_size: int
    detected_mime: str | None
    image_format: str | None
    width: int | None
    height: int | None
    frame_count: int | None


class UploadIntegrityVerifier:
    """Prove object identity without making non-image content eligible for use."""

    def __init__(
        self,
        *,
        storage: ObjectStorage,
        transaction_active: Callable[[], bool],
        maximum_bytes: int,
        maximum_dimension: int,
        maximum_pixels: int,
        maximum_frames: int,
        maximum_metadata_bytes: int,
        maximum_lora_bytes: int = 100 * 1024 * 1024,
        maximum_prompt_template_bytes: int = 256 * 1024,
        maximum_model_configuration_bytes: int = 64 * 1024,
    ) -> None:
        self._storage = storage
        self._transaction_active = transaction_active
        self._maximum_bytes_by_kind = {
            AssetKind.IMAGE: maximum_bytes,
            AssetKind.LORA: maximum_lora_bytes,
            AssetKind.PROMPT_TEMPLATE: maximum_prompt_template_bytes,
            AssetKind.MODEL_CONFIGURATION: maximum_model_configuration_bytes,
        }
        self._maximum_dimension = maximum_dimension
        self._maximum_pixels = maximum_pixels
        self._maximum_frames = maximum_frames
        self._metadata_policy = ImageMetadataPolicy(
            maximum_bytes=maximum_metadata_bytes,
        )

    def verify(
        self,
        upload_session: UploadSession,
        *,
        reference: ObjectReference | None = None,
        expected_bucket: str | None = None,
    ) -> VerifiedUpload:
        if self._transaction_active():
            raise RuntimeError("object verification is forbidden inside a database transaction")
        if reference is None:
            reference = ObjectReference(
                location=upload_session.storage_location,
                key=upload_session.storage_key,
            )
        expected_bucket = expected_bucket or upload_session.storage_bucket
        stat = self._storage.stat(reference)
        self._verify_head(
            upload_session,
            stat,
            reference=reference,
            expected_bucket=expected_bucket,
        )

        digest = hashlib.sha256()
        byte_size = 0
        maximum_bytes = self._maximum_bytes_by_kind[upload_session.asset_kind]
        with tempfile.SpooledTemporaryFile(max_size=min(maximum_bytes, 1024 * 1024)) as spool:
            with self._storage.open_bounded_read(
                BoundedReadRequest(
                    reference=stat.reference,
                    maximum_bytes=upload_session.expected_byte_length,
                    expected_etag=stat.etag,
                )
            ) as chunks:
                for chunk in chunks:
                    byte_size += len(chunk)
                    if byte_size > upload_session.expected_byte_length:
                        raise ObjectMismatchError("uploaded object is longer than declared")
                    digest.update(chunk)
                    spool.write(chunk)
            if byte_size != upload_session.expected_byte_length:
                raise ObjectMismatchError("uploaded object length does not match the declaration")
            sha256 = digest.hexdigest()
            if sha256 != upload_session.expected_sha256:
                raise ObjectMismatchError("uploaded object SHA-256 does not match the declaration")
            if upload_session.asset_kind == AssetKind.IMAGE:
                spool.seek(0)
                detected_mime, image_format, width, height, frame_count = self._decode_image(
                    spool,
                    upload_session.filename,
                )
                if detected_mime != upload_session.declared_mime.lower():
                    raise ObjectMismatchError("declared MIME does not match the uploaded image")
            else:
                detected_mime = None
                image_format = None
                width = None
                height = None
                frame_count = None
        return VerifiedUpload(
            stat=stat,
            sha256=sha256,
            byte_size=byte_size,
            detected_mime=detected_mime,
            image_format=image_format,
            width=width,
            height=height,
            frame_count=frame_count,
        )

    def _verify_head(
        self,
        upload_session: UploadSession,
        stat: ObjectStat,
        *,
        reference: ObjectReference,
        expected_bucket: str,
    ) -> None:
        if stat.backend != upload_session.storage_backend:
            raise ObjectMismatchError("uploaded object storage backend does not match the session")
        if stat.bucket != expected_bucket:
            raise ObjectMismatchError("uploaded object storage location does not match the session")
        if stat.reference.location != reference.location or stat.reference.key != reference.key:
            raise ObjectMismatchError(
                "uploaded object reference does not match the requested object"
            )
        if stat.content_length != upload_session.expected_byte_length:
            raise ObjectMismatchError("uploaded object length does not match the declaration")
        if stat.content_length > self._maximum_bytes_by_kind[upload_session.asset_kind]:
            raise ObjectMismatchError("uploaded object exceeds the configured byte limit")
        if not stat.etag:
            raise ObjectMismatchError("uploaded object has no stable object identity")
        if stat.content_type is None or (
            stat.content_type.partition(";")[0].strip().lower()
            != upload_session.declared_mime.lower()
        ):
            raise ObjectMismatchError("uploaded object Content-Type does not match the declaration")
        metadata_session = stat.metadata.get("upload-session-id")
        if metadata_session != upload_session.id:
            raise ObjectMismatchError("uploaded object does not belong to this upload session")

    def _decode_image(
        self,
        spool: tempfile.SpooledTemporaryFile[bytes],
        filename: str,
    ) -> tuple[str, str, int, int, int]:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(spool) as probe:
                    image_format = str(probe.format or "").upper()
                    facts = _FORMAT_FACTS.get(image_format)
                    if facts is None:
                        raise ObjectMismatchError("uploaded image format is not supported")
                    detected_mime, extensions = facts
                    extension = PurePath(filename).suffix.lower()
                    if extension not in extensions:
                        raise ObjectMismatchError(
                            "filename extension does not match the uploaded image format"
                        )
                    probe.verify()

                spool.seek(0)
                with Image.open(spool) as image:
                    if str(image.format or "").upper() != image_format:
                        raise ObjectMismatchError(
                            "uploaded image format changed during complete decode"
                        )
                    width, height = image.size
                    frame_count = int(getattr(image, "n_frames", 1))
                    self._verify_image_limits(
                        image=image,
                        width=width,
                        height=height,
                        frame_count=frame_count,
                    )
                    for frame_number in range(frame_count):
                        image.seek(frame_number)
                        image.load()
                    return detected_mime, image_format, width, height, frame_count
        except ObjectMismatchError:
            raise
        except (
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            UnidentifiedImageError,
            OSError,
            SyntaxError,
            ValueError,
        ) as exc:
            raise ObjectMismatchError("uploaded image cannot be decoded completely") from exc

    def _verify_image_limits(
        self,
        *,
        image: Image.Image,
        width: int,
        height: int,
        frame_count: int,
    ) -> None:
        if width < 1 or height < 1:
            raise ObjectMismatchError("uploaded image dimensions must be positive")
        if width > self._maximum_dimension or height > self._maximum_dimension:
            raise ObjectMismatchError("uploaded image dimensions exceed the configured limit")
        if frame_count < 1 or frame_count > self._maximum_frames:
            raise ObjectMismatchError("uploaded image frame count exceeds the configured limit")
        if width * height * frame_count > self._maximum_pixels:
            raise ObjectMismatchError("uploaded image decoded pixels exceed the configured limit")

        try:
            self._metadata_policy.validate(image)
        except MalformedImageMetadataError as exc:
            raise ObjectMismatchError("uploaded image metadata is malformed") from exc
        except ImageMetadataLimitExceededError as exc:
            raise ObjectMismatchError(
                "uploaded image metadata exceeds the configured limit"
            ) from exc


# Preserve the Ticket 04 import while callers migrate to the kind-aware interface.
ImageUploadIntegrityVerifier = UploadIntegrityVerifier
