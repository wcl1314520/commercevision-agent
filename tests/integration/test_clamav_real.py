from __future__ import annotations

import os
import socket

import pytest
from commercevision_contracts.validation import MalwareScanOutcome
from commercevision_providers import ClamdMalwareScanner

EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def _clamav_endpoint() -> tuple[str, int]:
    host = os.getenv("CV_REAL_CLAMAV_HOST", "127.0.0.1")
    port = int(os.getenv("CV_REAL_CLAMAV_PORT", "13310"))
    try:
        with socket.create_connection((host, port), timeout=0.5):
            pass
    except OSError:
        pytest.skip(
            "real ClamAV is unavailable; start the Compose clamav service "
            "or configure CV_REAL_CLAMAV_HOST/CV_REAL_CLAMAV_PORT"
        )
    return host, port


@pytest.mark.integration
def test_real_clamav_readiness_clean_scan_and_eicar_detection() -> None:
    host, port = _clamav_endpoint()
    scanner = ClamdMalwareScanner(
        host=host,
        port=port,
        timeout_seconds=15,
        maximum_concurrency=1,
        stream_max_bytes=1024 * 1024,
        chunk_bytes=4096,
        maximum_response_bytes=4096,
    )

    version = scanner.assert_ready()
    clean = b"commercevision clean asset validation fixture"
    clean_result = scanner.scan((clean,), content_length=len(clean))
    infected_result = scanner.scan((EICAR,), content_length=len(EICAR))

    assert version.startswith("ClamAV ")
    assert clean_result.outcome == MalwareScanOutcome.CLEAN
    assert infected_result.outcome == MalwareScanOutcome.INFECTED
    assert infected_result.signature is not None
    assert "Eicar" in infected_result.signature
