"""Package-owned child entrypoint for one bounded native C2PA parse."""

from __future__ import annotations

import io
import math
import os
import sys

from .c2pa_normalization import C2paEvidenceNormalizer
from .c2pa_subprocess import (
    C2paChildRequest,
    read_child_request,
    write_child_response,
)


def _apply_process_limits(request: C2paChildRequest) -> None:
    if os.name != "posix":
        return
    import resource

    try:
        cpu_seconds = max(1, math.ceil(request.timeout_seconds))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(
            resource.RLIMIT_AS,
            (request.memory_limit_bytes, request.memory_limit_bytes),
        )
        resource.setrlimit(
            resource.RLIMIT_NOFILE,
            (request.file_descriptor_limit, request.file_descriptor_limit),
        )
    except (OSError, ValueError) as exc:
        raise OSError("C2PA child resource isolation is unavailable") from exc


def _validate_offline_settings(settings: dict[str, object]) -> None:
    core = settings.get("core")
    verify = settings.get("verify")
    trust = settings.get("trust")
    if core != {"allowed_network_hosts": []}:
        raise ValueError("C2PA child network allowlist is not empty")
    if verify != {
        "ocsp_fetch": False,
        "remote_manifest_fetch": False,
        "verify_after_reading": True,
        "verify_timestamp_trust": True,
        "verify_trust": True,
    }:
        raise ValueError("C2PA child verification settings are unsafe")
    if (
        not isinstance(trust, dict)
        or not isinstance(trust.get("trust_anchors"), str)
        or not trust.get("trust_anchors")
        or not isinstance(trust.get("trust_config"), str)
        or not trust.get("trust_config")
    ):
        raise ValueError("C2PA child trust settings are malformed")


def _read_native_evidence(request: C2paChildRequest) -> dict[str, object]:
    _validate_offline_settings(request.settings)
    import c2pa

    normalizer = C2paEvidenceNormalizer(
        maximum_report_bytes=request.maximum_report_bytes,
        maximum_report_depth=request.maximum_report_depth,
        maximum_report_nodes=request.maximum_report_nodes,
        maximum_manifests=request.maximum_manifests,
        maximum_status_codes=request.maximum_status_codes,
    )
    with c2pa.Context(c2pa.Settings.from_dict(request.settings)) as context:
        reader = c2pa.Reader.try_create(
            request.mime_type,
            io.BytesIO(request.asset_bytes),
            context=context,
        )
        if reader is None:
            return {
                "protocol_version": 1,
                "result": "absent",
            }
        with reader:
            evidence = normalizer.normalize(
                manifest_json=reader.json(),
                validation_state=reader.get_validation_state(),
                validation_results=reader.get_validation_results(),
            )
    return {
        "failure_codes": list(evidence.failure_codes),
        "manifest_count": evidence.manifest_count,
        "protocol_version": 1,
        "result": "evidence",
        "status": evidence.status.value,
        "validation_state": evidence.validation_state,
    }


def main() -> int:
    request: C2paChildRequest | None = None
    try:
        request = read_child_request(sys.stdin.buffer)
        _apply_process_limits(request)
        payload = _read_native_evidence(request)
    except (ImportError, MemoryError, OSError):
        payload = {
            "code": "READER_UNAVAILABLE",
            "protocol_version": 1,
            "result": "error",
        }
    except BaseException:
        payload = {
            "code": "MALFORMED_CREDENTIAL",
            "protocol_version": 1,
            "result": "error",
        }
    write_child_response(
        sys.stdout.buffer,
        payload,
        maximum_output_bytes=request.maximum_output_bytes if request else 4096,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
