from __future__ import annotations

import io
import json
import os
import time
from pathlib import Path

import pytest
from billiard import Pipe, Process, current_process
from commercevision_contracts.validation import (
    ProvenanceConfiguredIdentity,
    ProvenanceEvidenceStatus,
    ProvenanceVerificationOutcome,
)
from commercevision_providers.provenance import (
    C2paProvenanceAdapter,
    DeterministicProvenanceAdapter,
    KillableC2paReaderBoundary,
)


def _verify_c2pa_from_daemonized_billiard_child(
    fake_module_dir: str,
    result_sender,
) -> None:
    try:
        os.environ["PYTHONPATH"] = os.pathsep.join(
            filter(
                None,
                (fake_module_dir, os.environ.get("PYTHONPATH", "")),
            )
        )
        boundary = KillableC2paReaderBoundary(
            maximum_report_bytes=64 * 1024,
            memory_limit_bytes=128 * 1024 * 1024,
            file_descriptor_limit=32,
        )
        adapter = C2paProvenanceAdapter(
            reader_boundary=boundary,
            sdk_version="fixture-c2pa",
            trust_config_version="trust-v1",
            trust_anchors_pem="anchor",
            trust_eku_policy="1.3.6.1.5.5.7.3.4",
            timeout_seconds=1.5,
            maximum_concurrency=1,
            maximum_asset_bytes=1024,
            maximum_report_bytes=1024,
            maximum_report_depth=8,
            maximum_report_nodes=100,
            maximum_manifests=4,
            maximum_status_codes=8,
        )
        started = time.monotonic()
        timed_out = adapter.verify(
            mime_type="image/jpeg",
            stream=io.BytesIO(b"hang"),
            byte_length=4,
        )
        timeout_elapsed = time.monotonic() - started
        recovered_attempts = []
        for _ in range(3):
            recovered = adapter.verify(
                mime_type="image/jpeg",
                stream=io.BytesIO(b"clean"),
                byte_length=5,
            )
            recovered_attempts.append(recovered)
            if recovered.outcome == ProvenanceVerificationOutcome.EVIDENCE:
                break
        malformed = adapter.verify(
            mime_type="image/jpeg",
            stream=io.BytesIO(b"malformed"),
            byte_length=9,
        )
        unavailable = adapter.verify(
            mime_type="image/jpeg",
            stream=io.BytesIO(b"unavailable"),
            byte_length=11,
        )
        adapter.close()
        result_sender.send(
            {
                "daemon": current_process().daemon,
                "malformed_failure_codes": malformed.failure_codes,
                "malformed_status": malformed.status.value if malformed.status else None,
                "recovered_failure_code": recovered.failure_code,
                "recovered_failure_codes": recovered.failure_codes,
                "recovered_outcome": recovered.outcome.value,
                "recovered_status": recovered.status.value if recovered.status else None,
                "recovered_transient_codes": tuple(
                    result.failure_code for result in recovered_attempts[:-1]
                ),
                "timed_out_code": timed_out.failure_code,
                "timeout_elapsed": timeout_elapsed,
                "unavailable_failure_code": unavailable.failure_code,
                "unavailable_outcome": unavailable.outcome.value,
            }
        )
    except BaseException as exc:
        result_sender.send({"error": f"{type(exc).__name__}: {exc}"})
    finally:
        result_sender.close()


_FAKE_C2PA_MODULE = """
import io
import json
import time


def sdk_version():
    return "fixture-c2pa"


class Settings:
    @staticmethod
    def from_dict(settings):
        assert settings["core"] == {"allowed_network_hosts": []}
        assert settings["verify"] == {
            "ocsp_fetch": False,
            "remote_manifest_fetch": False,
            "verify_after_reading": True,
            "verify_timestamp_trust": True,
            "verify_trust": True,
        }
        return settings


class Context:
    def __init__(self, settings):
        self.settings = settings

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class Reader:
    @classmethod
    def try_create(cls, mime_type, stream, *, context):
        assert mime_type == "image/jpeg"
        payload = stream.read()
        if payload == b"hang":
            time.sleep(60)
        if payload == b"unavailable":
            raise OSError("native reader unavailable detail must not escape")
        return cls(payload)

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def json(self):
        if self.payload == b"malformed":
            return "{native payload must not escape"
        return json.dumps(
            {
                "active_manifest": "urn:uuid:manifest-1",
                "manifests": {"urn:uuid:manifest-1": {}},
            },
            separators=(",", ":"),
        )

    def get_validation_state(self):
        return "Trusted"

    def get_validation_results(self):
        return {
            "activeManifest": {
                "success": [{"code": "claimSignature.validated"}],
                "informational": [],
                "failure": [],
            },
            "ingredientDeltas": [],
        }
"""


def test_installed_c2pa_sdk_matches_the_production_reader_boundary() -> None:
    import c2pa

    assert callable(c2pa.Context)
    assert callable(c2pa.Settings.from_dict)
    assert callable(c2pa.Reader.try_create)
    assert callable(c2pa.Reader.get_validation_state)
    assert callable(c2pa.Reader.get_validation_results)
    assert c2pa.sdk_version()

    adapter = C2paProvenanceAdapter.from_runtime(
        trust_config_version="c2pa-trust-v1",
        trust_anchors_pem="contract-trust-anchor",
        trust_eku_policy="1.3.6.1.5.5.7.3.3",
        timeout_seconds=1,
        maximum_concurrency=1,
        maximum_asset_bytes=1024,
        maximum_report_bytes=1024,
        maximum_report_depth=4,
        maximum_report_nodes=32,
        maximum_manifests=4,
        maximum_status_codes=4,
    )
    adapter.close()


class CapturingContext:
    def __init__(self, settings: dict[str, object]) -> None:
        self.settings = settings

    def __enter__(self) -> CapturingContext:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class CapturingContextFactory:
    def __init__(self) -> None:
        self.settings: list[dict[str, object]] = []

    def __call__(self, settings: dict[str, object]) -> CapturingContext:
        self.settings.append(settings)
        return CapturingContext(settings)


class FakeReader:
    def __init__(
        self,
        report: str,
        *,
        validation_state: str | None,
        validation_results: dict[str, object] | None,
        delay_seconds: float = 0,
    ) -> None:
        self._report = report
        self._validation_state = validation_state
        self._validation_results = validation_results
        self._delay_seconds = delay_seconds

    def __enter__(self) -> FakeReader:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def json(self) -> str:
        if self._delay_seconds:
            time.sleep(self._delay_seconds)
        return self._report

    def get_validation_state(self) -> str | None:
        return self._validation_state

    def get_validation_results(self) -> dict[str, object] | None:
        return self._validation_results


class FakeReaderFactory:
    def __init__(
        self,
        report: dict[str, object] | str | None,
        *,
        validation_state: str | None = "Trusted",
        validation_results: dict[str, object] | None = None,
        delay_seconds: float = 0,
    ) -> None:
        self.report = report
        self.validation_state = validation_state
        self.validation_results = (
            _validation_results() if validation_results is None else validation_results
        )
        self.delay_seconds = delay_seconds
        self.calls: list[tuple[str, bytes, object]] = []

    def __call__(
        self,
        mime_type: str,
        stream: io.BytesIO,
        *,
        context: object,
    ) -> FakeReader | None:
        self.calls.append((mime_type, stream.read(), context))
        if self.report is None:
            return None
        encoded = (
            json.dumps(self.report, separators=(",", ":"))
            if isinstance(self.report, dict)
            else self.report
        )
        return FakeReader(
            encoded,
            validation_state=self.validation_state,
            validation_results=self.validation_results,
            delay_seconds=self.delay_seconds,
        )


def _report() -> dict[str, object]:
    return {
        "active_manifest": "urn:uuid:manifest-1",
        "manifests": {"urn:uuid:manifest-1": {"title": "must-not-be-persisted"}},
    }


def _validation_results(
    *,
    failure_codes: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "activeManifest": {
            "success": [{"code": "claimSignature.validated"}],
            "informational": [],
            "failure": [
                {"code": code, "explanation": "must-not-be-persisted"} for code in failure_codes
            ],
        },
        "ingredientDeltas": [],
    }


def _adapter(
    report: dict[str, object] | str | None,
    *,
    validation_state: str | None = "Trusted",
    validation_results: dict[str, object] | None = None,
    delay_seconds: float = 0,
    timeout_seconds: float = 0.2,
    maximum_concurrency: int = 2,
    maximum_asset_bytes: int = 10 * 1024 * 1024,
) -> tuple[C2paProvenanceAdapter, FakeReaderFactory, CapturingContextFactory]:
    reader_factory = FakeReaderFactory(
        report,
        validation_state=validation_state,
        validation_results=validation_results,
        delay_seconds=delay_seconds,
    )
    context_factory = CapturingContextFactory()
    adapter = C2paProvenanceAdapter(
        reader_factory=reader_factory,
        context_factory=context_factory,
        sdk_version="0.32.6",
        trust_config_version="commercevision-c2pa-trust-v2",
        trust_anchors_pem="-----BEGIN CERTIFICATE-----\nfixture\n-----END CERTIFICATE-----",
        trust_eku_policy="1.3.6.1.5.5.7.3.4\n1.3.6.1.5.5.7.3.36",
        timeout_seconds=timeout_seconds,
        maximum_concurrency=maximum_concurrency,
        maximum_asset_bytes=maximum_asset_bytes,
        maximum_report_bytes=64 * 1024,
        maximum_report_depth=16,
        maximum_report_nodes=2_000,
        maximum_manifests=32,
        maximum_status_codes=128,
    )
    return adapter, reader_factory, context_factory


def test_c2pa_adapter_verifies_stream_with_trust_and_network_fetch_disabled() -> None:
    adapter, reader_factory, context_factory = _adapter(_report())

    result = adapter.verify(
        mime_type="image/jpeg",
        stream=io.BytesIO(b"literal-image"),
        byte_length=13,
    )
    adapter.close()

    assert adapter.configured_identity == ProvenanceConfiguredIdentity(
        validator="c2pa",
        sdk_version=result.sdk_version,
        trust_config_version=result.trust_config_version,
        trust_config_sha256=result.trust_config_sha256,
    )
    assert result.outcome == ProvenanceVerificationOutcome.EVIDENCE
    assert result.status == ProvenanceEvidenceStatus.VERIFIED
    assert result.sdk_version == "0.32.6"
    assert result.trust_config_version == "commercevision-c2pa-trust-v2"
    assert len(result.trust_config_sha256) == 64
    assert result.validation_state == "TRUSTED"
    assert result.manifest_count == 1
    assert result.failure_codes == ()
    assert result.remote_manifest_fetch is False
    assert reader_factory.calls[0][:2] == ("image/jpeg", b"literal-image")
    assert context_factory.settings == [
        {
            "version": 1,
            "core": {"allowed_network_hosts": []},
            "trust": {
                "trust_anchors": (
                    "-----BEGIN CERTIFICATE-----\nfixture\n-----END CERTIFICATE-----"
                ),
                "trust_config": "1.3.6.1.5.5.7.3.4\n1.3.6.1.5.5.7.3.36",
            },
            "verify": {
                "ocsp_fetch": False,
                "remote_manifest_fetch": False,
                "verify_after_reading": True,
                "verify_timestamp_trust": True,
                "verify_trust": True,
            },
        }
    ]
    assert "must-not-be-persisted" not in repr(result)


@pytest.mark.parametrize(
    ("report", "state", "results", "expected"),
    [
        (
            None,
            "Trusted",
            _validation_results(),
            ProvenanceEvidenceStatus.NOT_PRESENT,
        ),
        (
            _report(),
            "Valid",
            _validation_results(),
            ProvenanceEvidenceStatus.UNVERIFIED,
        ),
        (
            _report(),
            "Invalid",
            _validation_results(),
            ProvenanceEvidenceStatus.CONFLICTING,
        ),
        (
            _report(),
            "Trusted",
            _validation_results(failure_codes=("claimSignature.mismatch",)),
            ProvenanceEvidenceStatus.CONFLICTING,
        ),
    ],
)
def test_c2pa_adapter_uses_conservative_evidence_status_mapping(
    report: dict[str, object] | None,
    state: str,
    results: dict[str, object],
    expected: ProvenanceEvidenceStatus,
) -> None:
    adapter, _, _ = _adapter(
        report,
        validation_state=state,
        validation_results=results,
    )

    result = adapter.verify(
        mime_type="image/png",
        stream=io.BytesIO(b"image"),
        byte_length=5,
    )
    adapter.close()

    assert result.status == expected
    assert result.outcome == ProvenanceVerificationOutcome.EVIDENCE


@pytest.mark.parametrize(
    "report",
    [
        "{",
        ('{"active_manifest":"one","manifests":{"one":{}},"adversarial_number":1e99999}'),
        '{"active_manifest":"one","active_manifest":"two","manifests":{}}',
        '{"active_manifest":"one","manifests":[]}',
        json.dumps({"active_manifest": "one", "manifests": {"x": {}}}) + (" " * 70_000),
        json.dumps({"nested": [[[[[[[[[[[[[[[[[[]]]]]]]]]]]]]]]]]]}),
        json.dumps(
            {
                "active_manifest": "one",
                "manifests": {f"manifest-{index}": {} for index in range(33)},
            }
        ),
        json.dumps(
            {
                "active_manifest": "one",
                "manifests": {"one": {}},
                "validation_status": [{"code": f"status-{index}"} for index in range(129)],
            }
        ),
    ],
    ids=[
        "invalid-json",
        "non-finite-float",
        "duplicate-key",
        "wrong-manifest-shape",
        "oversized-report",
        "excessive-depth",
        "excessive-manifests",
        "excessive-statuses",
    ],
)
def test_c2pa_adapter_bounds_malicious_manifest_reports(report: str) -> None:
    adapter, _, _ = _adapter(report)

    result = adapter.verify(
        mime_type="image/webp",
        stream=io.BytesIO(b"image"),
        byte_length=5,
    )
    adapter.close()

    assert result.status == ProvenanceEvidenceStatus.CONFLICTING
    assert result.failure_codes == ("MALFORMED_CREDENTIAL",)
    assert report not in repr(result)


def test_c2pa_adapter_bounds_input_and_rejects_length_mismatch() -> None:
    adapter, reader_factory, _ = _adapter(_report(), maximum_asset_bytes=10)

    oversized = adapter.verify(
        mime_type="image/jpeg",
        stream=io.BytesIO(b"x" * 11),
        byte_length=11,
    )
    mismatched = adapter.verify(
        mime_type="image/jpeg",
        stream=io.BytesIO(b"short"),
        byte_length=6,
    )
    adapter.close()

    assert oversized.status == ProvenanceEvidenceStatus.CONFLICTING
    assert mismatched.status == ProvenanceEvidenceStatus.CONFLICTING
    assert reader_factory.calls == []


def test_c2pa_subprocess_works_in_daemonized_prefork_child_and_reclaims_capacity(
    tmp_path: Path,
) -> None:
    fake_module = tmp_path / "c2pa.py"
    fake_module.write_text(_FAKE_C2PA_MODULE, encoding="utf-8")
    result_receiver, result_sender = Pipe(duplex=False)
    process = Process(
        target=_verify_c2pa_from_daemonized_billiard_child,
        args=(str(tmp_path), result_sender),
        daemon=True,
    )

    process.start()
    result_sender.close()
    process.join(timeout=25)
    if process.is_alive():
        process.terminate()
        process.join(timeout=2)
        pytest.fail("daemonized C2PA boundary regression did not terminate")
    assert result_receiver.poll(2), "daemonized child returned no result"
    result = result_receiver.recv()
    result_receiver.close()

    assert process.exitcode == 0
    assert "error" not in result, result
    assert result["daemon"] is True
    assert result["timed_out_code"] == "PROVENANCE_TIMEOUT"
    assert result["timeout_elapsed"] < 3
    assert result["recovered_outcome"] == ProvenanceVerificationOutcome.EVIDENCE.value
    assert result["recovered_status"] == ProvenanceEvidenceStatus.VERIFIED.value, result
    assert set(result["recovered_transient_codes"]) <= {
        "PROVENANCE_READER_UNAVAILABLE",
        "PROVENANCE_TIMEOUT",
    }
    assert result["malformed_status"] == ProvenanceEvidenceStatus.CONFLICTING.value
    assert result["malformed_failure_codes"] == ("MALFORMED_CREDENTIAL",)
    assert result["unavailable_outcome"] == ProvenanceVerificationOutcome.RETRYABLE_FAILURE.value
    assert result["unavailable_failure_code"] == "PROVENANCE_READER_UNAVAILABLE"
    assert "native payload" not in repr(result)
    assert "native reader unavailable detail" not in repr(result)


@pytest.mark.parametrize("status", list(ProvenanceEvidenceStatus))
def test_deterministic_provenance_adapter_uses_normalized_contract(
    status: ProvenanceEvidenceStatus,
) -> None:
    adapter = DeterministicProvenanceAdapter(
        status=status,
        trust_config_version="deterministic-trust-v1",
    )

    result = adapter.verify(
        mime_type="image/jpeg",
        stream=io.BytesIO(b"image"),
        byte_length=5,
    )

    assert adapter.configured_identity == ProvenanceConfiguredIdentity(
        validator="deterministic-c2pa",
        sdk_version="deterministic-v1",
        trust_config_version="deterministic-trust-v1",
        trust_config_sha256=result.trust_config_sha256,
    )
    assert result.status == status
    assert result.validator == "deterministic-c2pa"
    assert result.remote_manifest_fetch is False
