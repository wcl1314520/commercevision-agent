"""Durable executor for generation-fenced Asset deletion convergence."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import NoReturn, Protocol

from commercevision_application import (
    OperationExecutionFailure,
    OperationExecutionRequest,
    OperationExecutionResult,
    OperationExecutor,
    OperationReconciliationResult,
)
from commercevision_application.asset_deletion import AssetDeletionConvergenceResult
from commercevision_domain import (
    NormalizedOperationError,
    OperationKind,
    ReconciliationOutcome,
    StoragePreconditionError,
    StorageUnavailableError,
)
from commercevision_observability import Phase2Span, Phase2Telemetry, TelemetryIdentity


class AssetDeletionCoordinator(Protocol):
    def converge(
        self,
        request: OperationExecutionRequest,
    ) -> AssetDeletionConvergenceResult: ...


class AssetDeletionTelemetry(Protocol):
    def span(
        self,
        name: Phase2Span,
        *,
        identity: TelemetryIdentity | None = None,
    ) -> AbstractContextManager[None]: ...

    def record_deletion(self, *, backlog: int, outcome: str) -> None: ...


class AssetDeletionExecutor:
    """Validate immutable command identity and converge every cleanup component."""

    def __init__(
        self,
        *,
        coordinator: AssetDeletionCoordinator,
        telemetry: AssetDeletionTelemetry | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._telemetry = telemetry or Phase2Telemetry()

    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        try:
            with self._telemetry.span(
                Phase2Span.DELETION,
                identity=self._identity(request),
            ):
                self._validate(request)
                result = self._converge(request)
        except OperationExecutionFailure as exc:
            self._telemetry.record_deletion(
                backlog=1,
                outcome="retryable_failure" if exc.error.retryable else "permanent_failure",
            )
            raise
        self._telemetry.record_deletion(backlog=0, outcome="completed")
        return OperationExecutionResult(
            operation_id=request.operation_id,
            output_ref=result.output_ref,
        )

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        with self._telemetry.span(
            Phase2Span.RECONCILIATION,
            identity=self._identity(request),
        ):
            try:
                self._validate(request)
                result = self._converge(request)
            except OperationExecutionFailure as exc:
                if exc.error.retryable:
                    self._telemetry.record_deletion(backlog=1, outcome="reconcile_retry")
                    raise
                self._telemetry.record_deletion(backlog=0, outcome="confirmed_failure")
                return OperationReconciliationResult(
                    operation_id=request.operation_id,
                    outcome=ReconciliationOutcome.CONFIRMED_FAILURE,
                    error=exc.error,
                )
            self._telemetry.record_deletion(backlog=0, outcome="confirmed_success")
            return OperationReconciliationResult(
                operation_id=request.operation_id,
                outcome=ReconciliationOutcome.CONFIRMED_SUCCESS,
                output_ref=result.output_ref,
            )

    @staticmethod
    def _identity(request: OperationExecutionRequest) -> TelemetryIdentity:
        return TelemetryIdentity(
            operation_id=request.operation_id,
            workspace_id=request.workspace_id,
            target_id=request.target_id,
            target_version=request.target_version,
            provider_request_id=request.provider_request_id,
        )

    def _converge(
        self,
        request: OperationExecutionRequest,
    ) -> AssetDeletionConvergenceResult:
        try:
            return self._coordinator.converge(request)
        except (ConnectionError, TimeoutError, StorageUnavailableError) as exc:
            self._fail(
                code="ASSET_DELETION_DEPENDENCY_UNAVAILABLE",
                category="dependency",
                message="Asset deletion dependency is temporarily unavailable",
                retryable=True,
                cause=exc,
            )
        except StoragePreconditionError as exc:
            self._fail(
                code="ASSET_DELETION_IDENTITY_CONFLICT",
                category="integrity",
                message="Asset deletion found an external identity conflict",
                retryable=False,
                cause=exc,
            )
        except ValueError as exc:
            self._fail(
                code="ASSET_DELETION_FACT_MISMATCH",
                category="integrity",
                message="Asset deletion command no longer matches MySQL authority",
                retryable=False,
                cause=exc,
            )

    @staticmethod
    def _validate(request: OperationExecutionRequest) -> None:
        expected_ref = f"mysql://assets/{request.target_id}/deletions/{request.target_version}"
        if (
            request.kind != OperationKind.ASSET_DELETION
            or request.target_type != "ASSET"
            or request.target_version < 1
            or request.input_ref != expected_ref
        ):
            AssetDeletionExecutor._fail(
                code="ASSET_DELETION_TARGET_MISMATCH",
                category="contract",
                message="Asset deletion Operation target identity is invalid",
                retryable=False,
            )

    @staticmethod
    def _fail(
        *,
        code: str,
        category: str,
        message: str,
        retryable: bool,
        cause: Exception | None = None,
    ) -> NoReturn:
        failure = OperationExecutionFailure(
            NormalizedOperationError(
                code=code,
                category=category,
                message=message,
                retryable=retryable,
            )
        )
        if cause is None:
            raise failure
        raise failure from cause


class AssetCleanupExecutor:
    """Route the shared ASSET_DELETION kind by its explicit target type."""

    def __init__(self, *, uploads: object, assets: AssetDeletionExecutor) -> None:
        self._uploads = uploads
        self._assets = assets

    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        return self._resolve(request).execute(request)

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        return self._resolve(request).reconcile(request)

    def _resolve(self, request: OperationExecutionRequest) -> OperationExecutor:
        if request.target_type == "UPLOAD_SESSION":
            return self._uploads
        if request.target_type == "ASSET":
            return self._assets
        AssetDeletionExecutor._fail(
            code="ASSET_DELETION_TARGET_MISMATCH",
            category="contract",
            message="Asset deletion target type is unsupported",
            retryable=False,
        )
