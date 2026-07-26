from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from commercevision_application import OperationExecutionRequest
from commercevision_application.asset_validation_evidence import (
    AssetValidationEvidenceError,
    AssetValidationEvidenceStore,
)
from commercevision_domain import (
    AssetValidationResult,
    OperationKind,
    ValidationStage,
    ValidationVerdict,
)

NOW = datetime(2026, 7, 26, 13, 0, tzinfo=UTC)
OPERATION_ID = "019f8a00-0000-7000-8000-000000000801"
ASSET_VERSION_ID = "019f8a00-0000-7000-8000-000000000802"
ASSET_OBJECT_ID = "019f8a00-0000-7000-8000-000000000803"


class _AssetRepository:
    def __init__(self, results: tuple[AssetValidationResult, ...]) -> None:
        self._results = results

    def list_validation_results(
        self,
        **_kwargs: object,
    ) -> list[AssetValidationResult]:
        return list(self._results)


class _UnitOfWork:
    def __init__(self, results: tuple[AssetValidationResult, ...]) -> None:
        self.assets = _AssetRepository(results)

    def __enter__(self) -> _UnitOfWork:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _request(attempt_count: int) -> OperationExecutionRequest:
    return OperationExecutionRequest(
        operation_id=OPERATION_ID,
        workspace_id="validation-evidence-workspace",
        kind=OperationKind.ASSET_VALIDATION,
        target_type="ASSET_VERSION",
        target_id=ASSET_VERSION_ID,
        target_version=1,
        input_hash="a" * 64,
        input_ref=f"mysql://asset-versions/{ASSET_VERSION_ID}",
        provider_request_id=None,
        attempt_count=attempt_count,
        idempotency_key=f"durable-operation:{OPERATION_ID}",
    )


def _result(
    *,
    attempt_number: int,
    verdict: ValidationVerdict,
    validator_version: str = "asset-local-validator-v1",
) -> AssetValidationResult:
    return AssetValidationResult.create(
        workspace_id="validation-evidence-workspace",
        operation_id=OPERATION_ID,
        asset_version_id=ASSET_VERSION_ID,
        asset_object_id=ASSET_OBJECT_ID,
        attempt_number=attempt_number,
        stage=ValidationStage.LOCAL_FORMAT,
        validator_name="commercevision-local",
        validator_version=validator_version,
        policy_version="asset-validation-v1",
        verdict=verdict,
        reason_code=(
            "LOCAL_FORMAT_RETRYABLE" if verdict == ValidationVerdict.RETRYABLE_FAILURE else None
        ),
        object_provider_version_id="source-version-1",
        object_etag='"source-etag"',
        content_sha256="a" * 64,
        evidence={
            "asset_kind": "IMAGE",
            "byte_size": 68,
            "detected_mime": "image/png",
            "format_name": "PNG",
        },
        retention_deadline=None,
        now=NOW + timedelta(seconds=attempt_number),
    )


def _store(
    *results: AssetValidationResult,
) -> AssetValidationEvidenceStore:
    return AssetValidationEvidenceStore(uow_factory=lambda: _UnitOfWork(tuple(results)))


def test_evidence_store_selects_latest_prior_pass() -> None:
    first = _result(attempt_number=1, verdict=ValidationVerdict.PASS)
    latest = _result(attempt_number=2, verdict=ValidationVerdict.PASS)

    selected = _store(first, latest).attempt_results(_request(3))

    assert selected == {ValidationStage.LOCAL_FORMAT: latest}


def test_evidence_store_does_not_select_prior_retryable_failure() -> None:
    retryable = _result(
        attempt_number=1,
        verdict=ValidationVerdict.RETRYABLE_FAILURE,
    )

    assert _store(retryable).attempt_results(_request(2)) == {}


def test_evidence_store_prefers_current_retryable_failure_over_older_pass() -> None:
    passed = _result(attempt_number=1, verdict=ValidationVerdict.PASS)
    retryable = _result(
        attempt_number=2,
        verdict=ValidationVerdict.RETRYABLE_FAILURE,
    )

    selected = _store(passed, retryable).attempt_results(_request(2))

    assert selected == {ValidationStage.LOCAL_FORMAT: retryable}


def test_evidence_store_ignores_future_attempt_evidence() -> None:
    prior = _result(attempt_number=1, verdict=ValidationVerdict.PASS)
    future = _result(attempt_number=3, verdict=ValidationVerdict.PASS)

    selected = _store(prior, future).attempt_results(_request(2))

    assert selected == {ValidationStage.LOCAL_FORMAT: prior}


def test_evidence_store_keeps_duplicate_same_attempt_stage_ambiguous() -> None:
    first = _result(attempt_number=1, verdict=ValidationVerdict.PASS)
    duplicate = _result(
        attempt_number=1,
        verdict=ValidationVerdict.PASS,
        validator_version="asset-local-validator-v2",
    )

    with pytest.raises(AssetValidationEvidenceError) as failed:
        _store(first, duplicate).attempt_results(_request(2))

    assert failed.value.code == "AMBIGUOUS_VALIDATION_EVIDENCE"


def test_evidence_store_can_reuse_prior_not_applicable_result() -> None:
    not_applicable = _result(
        attempt_number=1,
        verdict=ValidationVerdict.NOT_APPLICABLE,
    )

    selected = _store(not_applicable).attempt_results(_request(2))

    assert selected == {ValidationStage.LOCAL_FORMAT: not_applicable}
