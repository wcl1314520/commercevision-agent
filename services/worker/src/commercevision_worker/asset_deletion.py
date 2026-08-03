"""Durable executor for generation-fenced Asset deletion convergence."""

from __future__ import annotations

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


class AssetDeletionCoordinator(Protocol):
    def converge(
        self,
        request: OperationExecutionRequest,
    ) -> AssetDeletionConvergenceResult: ...


class AssetDeletionExecutor:
    """Validate immutable command identity and converge every cleanup component."""

    def __init__(self, *, coordinator: AssetDeletionCoordinator) -> None:
        self._coordinator = coordinator

    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        self._validate(request)
        result = self._converge(request)
        return OperationExecutionResult(
            operation_id=request.operation_id,
            output_ref=result.output_ref,
        )

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        try:
            self._validate(request)
            result = self._converge(request)
        except OperationExecutionFailure as exc:
            if exc.error.retryable:
                raise
            return OperationReconciliationResult(
                operation_id=request.operation_id,
                outcome=ReconciliationOutcome.CONFIRMED_FAILURE,
                error=exc.error,
            )
        return OperationReconciliationResult(
            operation_id=request.operation_id,
            outcome=ReconciliationOutcome.CONFIRMED_SUCCESS,
            output_ref=result.output_ref,
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
