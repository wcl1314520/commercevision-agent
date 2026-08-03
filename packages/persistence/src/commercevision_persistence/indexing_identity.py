"""Stable identities for IMAGE indexing durable operations."""

from __future__ import annotations

import hashlib


def compute_image_index_operation_hash(
    *,
    embedding_input_hash: str,
    rights_record_id: str,
    rights_record_version: int,
    operation_epoch: int,
) -> str:
    """Separate scheduling identity from the immutable embedding content identity."""
    canonical = "\0".join(
        (
            "commercevision:image-index-operation:v1",
            embedding_input_hash,
            rights_record_id,
            str(rights_record_version),
            str(operation_epoch),
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
