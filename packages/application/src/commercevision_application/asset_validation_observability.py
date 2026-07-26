"""Sanitized observability boundary for Asset validation."""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import Literal, Protocol

from commercevision_domain import AssetValidationResult, ValidationStage

from .asset_validation_target import AssetValidationTarget
from .operations import OperationExecutionRequest

AssetValidationMode = Literal["execute", "reconcile"]
AssetValidationCompletion = Literal["PENDING_REVIEW", "PENDING_RIGHTS"]


class AssetValidationObserver(Protocol):
    """Observe normalized lifecycle facts without receiving bytes or raw evidence."""

    def operation(
        self,
        *,
        request: OperationExecutionRequest,
        mode: AssetValidationMode,
    ) -> AbstractContextManager[None]: ...

    def stage(
        self,
        *,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
        stage: ValidationStage,
        reused: bool,
    ) -> AbstractContextManager[None]: ...

    def target_bound(
        self,
        *,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
    ) -> None: ...

    def result(
        self,
        *,
        result: AssetValidationResult,
        reused: bool,
    ) -> None: ...

    def completed(
        self,
        *,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
        outcome: AssetValidationCompletion,
    ) -> None: ...


class NullAssetValidationObserver:
    """Default observer for pure application tests and non-instrumented callers."""

    def operation(
        self,
        *,
        request: OperationExecutionRequest,
        mode: AssetValidationMode,
    ) -> AbstractContextManager[None]:
        del request, mode
        return nullcontext()

    def stage(
        self,
        *,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
        stage: ValidationStage,
        reused: bool,
    ) -> AbstractContextManager[None]:
        del request, target, stage, reused
        return nullcontext()

    def target_bound(
        self,
        *,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
    ) -> None:
        del request, target

    def result(
        self,
        *,
        result: AssetValidationResult,
        reused: bool,
    ) -> None:
        del result, reused

    def completed(
        self,
        *,
        request: OperationExecutionRequest,
        target: AssetValidationTarget,
        outcome: AssetValidationCompletion,
    ) -> None:
        del request, target, outcome
