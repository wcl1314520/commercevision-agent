from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from commercevision_application import (
    OperationExecutionFailure,
    OperationExecutionRequest,
)
from commercevision_domain import OperationKind
from commercevision_worker.generation import (
    GenerationDispatchAuthorityDenied,
    GenerationOperationExecutor,
)


class _DeniedAuthority:
    def __init__(self) -> None:
        self.requests: list[OperationExecutionRequest] = []

    def prepare_dispatch(self, request: OperationExecutionRequest) -> object:
        self.requests.append(request)
        raise GenerationDispatchAuthorityDenied


class _NeverDispatcher:
    def __init__(self) -> None:
        self.calls = 0

    def submit(self, _dispatch: object) -> object:
        self.calls += 1
        raise AssertionError("authority denial must happen before Provider dispatch")


def _request() -> OperationExecutionRequest:
    now = datetime.now(UTC)
    return OperationExecutionRequest(
        operation_id="018f5f4d-7c11-7d11-8a11-111111111111",
        workspace_id="catalog-workspace",
        kind=OperationKind.IMAGE_GENERATION,
        target_type="generation-candidate-slot",
        target_id="018f5f4d-7c11-7d11-8a11-222222222222",
        target_version=1,
        input_hash="a" * 64,
        input_ref=None,
        provider_request_id=None,
        attempt_count=1,
        idempotency_key="durable-operation:018f5f4d-7c11-7d11-8a11-111111111111",
        lease_expires_at=now + timedelta(minutes=2),
    )


def test_generation_executor_denies_stale_authority_before_dispatch() -> None:
    authority = _DeniedAuthority()
    dispatcher = _NeverDispatcher()
    executor = GenerationOperationExecutor(
        authority=authority,
        dispatcher=dispatcher,
    )
    request = _request()

    with pytest.raises(OperationExecutionFailure) as captured:
        executor.execute(request)

    assert authority.requests == [request]
    assert dispatcher.calls == 0
    assert captured.value.error.code == "GENERATION_AUTHORITY_DENIED"
    assert captured.value.error.category == "authorization"
    assert captured.value.error.retryable is False
    assert captured.value.error.provider_request_id is None
    assert "catalog-workspace" not in str(captured.value)
