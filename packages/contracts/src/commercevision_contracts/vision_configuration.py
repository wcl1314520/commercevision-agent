"""Canonical Vision provider configuration snapshots shared across processes."""

from __future__ import annotations

import hashlib
import json

VISION_CONFIGURATION_SNAPSHOT_SCHEMA_VERSION = 2


def _snapshot_sha256(document: dict[str, object]) -> str:
    canonical = json.dumps(
        document,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def deterministic_vision_configuration_snapshot_sha256(
    *,
    maximum_output_tokens: int,
    product_facts_maximum_bytes: int,
    product_facts_maximum_depth: int,
    product_facts_maximum_nodes: int,
    product_facts_maximum_string_bytes: int,
    prompt_version: str,
) -> str:
    """Freeze the deterministic adapter's provider-facing configuration.

    The selected deterministic scenario is fault/output injection for tests and
    local development. It is deliberately not part of the provider identity.
    """

    return _snapshot_sha256(
        {
            "adapter": "deterministic-vision-v1",
            "schema_version": VISION_CONFIGURATION_SNAPSHOT_SCHEMA_VERSION,
            "maximum_output_tokens": maximum_output_tokens,
            "product_facts_maximum_bytes": product_facts_maximum_bytes,
            "product_facts_maximum_depth": product_facts_maximum_depth,
            "product_facts_maximum_nodes": product_facts_maximum_nodes,
            "product_facts_maximum_string_bytes": product_facts_maximum_string_bytes,
            "prompt_version": prompt_version,
        }
    )


def alibaba_vision_configuration_snapshot_sha256(
    *,
    adapter_version: str,
    configured_snapshot: str,
    connect_timeout_seconds: float,
    end_to_end_timeout_seconds: float,
    endpoint: str,
    endpoint_region: str,
    maximum_concurrency: int,
    maximum_output_tokens: int,
    maximum_repair_attempts: int,
    maximum_response_bytes: int,
    product_facts_maximum_bytes: int,
    product_facts_maximum_depth: int,
    product_facts_maximum_nodes: int,
    product_facts_maximum_string_bytes: int,
    prompt_version: str,
    read_timeout_seconds: float,
    requested_model: str,
) -> str:
    """Freeze every provider-facing Alibaba Vision behavior control."""

    return _snapshot_sha256(
        {
            "adapter_version": adapter_version,
            "configured_snapshot": configured_snapshot,
            "connect_timeout_seconds": connect_timeout_seconds,
            "end_to_end_timeout_seconds": end_to_end_timeout_seconds,
            "endpoint": endpoint.rstrip("/"),
            "endpoint_region": endpoint_region,
            "maximum_concurrency": maximum_concurrency,
            "maximum_output_tokens": maximum_output_tokens,
            "maximum_repair_attempts": maximum_repair_attempts,
            "maximum_response_bytes": maximum_response_bytes,
            "product_facts_maximum_bytes": product_facts_maximum_bytes,
            "product_facts_maximum_depth": product_facts_maximum_depth,
            "product_facts_maximum_nodes": product_facts_maximum_nodes,
            "product_facts_maximum_string_bytes": product_facts_maximum_string_bytes,
            "prompt_version": prompt_version,
            "read_timeout_seconds": read_timeout_seconds,
            "requested_model": requested_model,
            "schema_version": VISION_CONFIGURATION_SNAPSHOT_SCHEMA_VERSION,
        }
    )
