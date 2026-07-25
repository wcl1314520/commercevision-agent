"""Built-in Durable Operation executor for Upload Session object cleanup."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from commercevision_application import (
    OperationExecutionFailure,
    OperationExecutionRequest,
    OperationExecutionResult,
    OperationReconciliationRequired,
    OperationReconciliationResult,
    UploadObjectCleaner,
)
from commercevision_application.asset_cleanup_dispatch import upload_cleanup_input_hash
from commercevision_application.asset_ports import AssetUnitOfWorkPort
from commercevision_domain import (
    NormalizedOperationError,
    ObjectMismatchError,
    OperationKind,
    ReconciliationOutcome,
    StoragePreconditionError,
    StorageUnavailableError,
    UploadSession,
    UploadSessionState,
)


class UploadSessionCleanupExecutor:
    """Resolve cleanup facts from MySQL, then delete exact object versions."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], AssetUnitOfWorkPort],
        cleaner: UploadObjectCleaner,
        reconciliation_interval: timedelta,
        final_cleanup_budget: timedelta,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if reconciliation_interval <= timedelta(0):
            raise ValueError("cleanup reconciliation interval must be positive")
        if final_cleanup_budget <= timedelta(0):
            raise ValueError("cleanup final budget must be positive")
        self._uow_factory = uow_factory
        self._cleaner = cleaner
        self._reconciliation_interval = reconciliation_interval
        self._final_cleanup_budget = final_cleanup_budget
        self._clock = clock or (lambda: datetime.now(UTC))

    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        upload_session = self._load_session(request)
        self._cleanup(upload_session)
        now = self._clock()
        reconcile_until = self._reconcile_until(upload_session)
        if now < reconcile_until:
            raise OperationReconciliationRequired(
                self._pending_error(),
                deadline_at=reconcile_until + self._final_cleanup_budget,
            )
        return OperationExecutionResult(
            operation_id=request.operation_id,
            output_ref=f"mysql://upload-sessions/{upload_session.id}/cleanup",
        )

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        try:
            upload_session = self._load_session(request)
            self._cleanup(upload_session)
        except OperationExecutionFailure as exc:
            if exc.error.retryable:
                raise
            return OperationReconciliationResult(
                operation_id=request.operation_id,
                outcome=ReconciliationOutcome.CONFIRMED_FAILURE,
                error=exc.error,
            )
        now = self._clock()
        reconcile_until = self._reconcile_until(upload_session)
        if now < reconcile_until:
            return OperationReconciliationResult(
                operation_id=request.operation_id,
                outcome=ReconciliationOutcome.PENDING,
                error=self._pending_error(),
                retry_at=min(now + self._reconciliation_interval, reconcile_until),
            )
        return OperationReconciliationResult(
            operation_id=request.operation_id,
            outcome=ReconciliationOutcome.CONFIRMED_SUCCESS,
            output_ref=f"mysql://upload-sessions/{upload_session.id}/cleanup",
        )

    def _load_session(self, request: OperationExecutionRequest) -> UploadSession:
        if request.kind != OperationKind.ASSET_DELETION:
            self._fail(
                code="UPLOAD_CLEANUP_KIND_MISMATCH",
                category="contract",
                message="cleanup executor received the wrong Operation kind",
                retryable=False,
            )
        if request.target_type != "UPLOAD_SESSION":
            self._fail(
                code="UPLOAD_CLEANUP_TARGET_MISMATCH",
                category="contract",
                message="cleanup executor only accepts Upload Session targets",
                retryable=False,
            )
        expected_input_ref = f"mysql://upload-sessions/{request.target_id}"
        if request.input_ref != expected_input_ref:
            self._fail(
                code="UPLOAD_CLEANUP_INPUT_REF_MISMATCH",
                category="contract",
                message="cleanup Operation input reference is invalid",
                retryable=False,
            )

        with self._uow_factory() as uow:
            upload_session = uow.upload_sessions.get(
                workspace_id=request.workspace_id,
                upload_session_id=request.target_id,
            )
        if upload_session is None:
            self._fail(
                code="UPLOAD_CLEANUP_SESSION_MISSING",
                category="integrity",
                message="cleanup target Upload Session no longer exists",
                retryable=False,
            )
        assert upload_session is not None
        if (
            upload_session.state
            not in {
                UploadSessionState.FINALIZED,
                UploadSessionState.EXPIRED,
                UploadSessionState.ABORTED,
            }
            or upload_session.cleanup_operation_id != request.operation_id
            or upload_session.version != request.target_version
            or upload_cleanup_input_hash(upload_session) != request.input_hash
        ):
            self._fail(
                code="UPLOAD_CLEANUP_FACT_MISMATCH",
                category="integrity",
                message="cleanup Operation does not match current Upload Session facts",
                retryable=False,
            )
        return upload_session

    def _cleanup(self, upload_session: UploadSession) -> None:
        try:
            self._cleaner.cleanup(upload_session)
        except StorageUnavailableError:
            self._fail(
                code="UPLOAD_CLEANUP_STORAGE_UNAVAILABLE",
                category="storage",
                message="object storage is unavailable during Upload Session cleanup",
                retryable=True,
            )
        except StoragePreconditionError:
            self._fail(
                code="UPLOAD_CLEANUP_PRECONDITION_CHANGED",
                category="storage",
                message="stored object changed while Upload Session cleanup was deleting it",
                retryable=True,
            )
        except ObjectMismatchError:
            self._fail(
                code="UPLOAD_CLEANUP_OWNERSHIP_MISMATCH",
                category="integrity",
                message="stored object ownership does not match the cleanup target",
                retryable=False,
            )

    @staticmethod
    def _reconcile_until(upload_session: UploadSession) -> datetime:
        if upload_session.cleanup_reconcile_until is None:
            raise RuntimeError("cleanup reconciliation window is missing")
        return upload_session.cleanup_reconcile_until

    @staticmethod
    def _pending_error() -> NormalizedOperationError:
        return NormalizedOperationError(
            code="UPLOAD_CLEANUP_QUIESCENCE_PENDING",
            category="reconciliation",
            message="upload cleanup remains open for late object reconciliation",
            retryable=True,
        )

    @staticmethod
    def _fail(
        *,
        code: str,
        category: str,
        message: str,
        retryable: bool,
    ) -> None:
        raise OperationExecutionFailure(
            NormalizedOperationError(
                code=code,
                category=category,
                message=message,
                retryable=retryable,
            )
        )
