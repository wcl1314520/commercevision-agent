"""Stable identities for durable vector indexing operations."""

from __future__ import annotations

import hashlib


def compute_image_index_operation_hash(
    *,
    embedding_input_hash: str,
    rights_record_id: str,
    rights_record_version: int,
    operation_epoch: int,
) -> str:
    """Preserve the Ticket 09 IMAGE operation identity."""
    return compute_index_operation_hash(
        vector_kind="IMAGE",
        embedding_input_hash=embedding_input_hash,
        rights_record_id=rights_record_id,
        rights_record_version=rights_record_version,
        operation_epoch=operation_epoch,
    )


def compute_index_operation_hash(
    *,
    vector_kind: str,
    embedding_input_hash: str,
    rights_record_id: str,
    rights_record_version: int,
    operation_epoch: int,
) -> str:
    """Separate scheduling identity from immutable embedding content identity."""
    if vector_kind == "IMAGE":
        domain = "commercevision:image-index-operation:v1"
    elif vector_kind == "PRODUCT_FUSED":
        domain = "commercevision:product-fused-index-operation:v1"
    else:
        raise ValueError("unsupported vector kind for index operation identity")
    canonical = "\0".join(
        (
            domain,
            embedding_input_hash,
            rights_record_id,
            str(rights_record_version),
            str(operation_epoch),
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
