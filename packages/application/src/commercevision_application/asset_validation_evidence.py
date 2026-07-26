"""Append-only validation evidence persistence and immutable reuse checks."""

from __future__ import annotations

from dataclasses import dataclass

from commercevision_domain import (
    AssetValidationResult,
    UniqueConstraintError,
    ValidationStage,
    ValidationVerdict,
)

from .asset_ports import AssetUnitOfWorkFactory, AssetUnitOfWorkPort
from .asset_validation_target import AssetValidationTarget
from .operations import OperationExecutionRequest

_CROSS_ATTEMPT_REUSABLE_VERDICTS = frozenset(
    {
        ValidationVerdict.PASS,
        ValidationVerdict.NOT_APPLICABLE,
    }
)


@dataclass(frozen=True, slots=True)
class AssetValidationEvidenceError(Exception):
    code: str
    message: str
    retryable: bool = False

    def __str__(self) -> str:
        return self.message


def assert_source_evidence_identity(
    *,
    target: AssetValidationTarget,
    request: OperationExecutionRequest,
    result: AssetValidationResult,
    expected_stage: ValidationStage,
) -> None:
    """Prove reused evidence addresses the current exact quarantine object."""

    source = target.source_object
    attempt_is_reusable = result.attempt_number == request.attempt_count or (
        result.attempt_number < request.attempt_count
        and result.verdict in _CROSS_ATTEMPT_REUSABLE_VERDICTS
    )
    if not all(
        (
            result.workspace_id == request.workspace_id,
            result.operation_id == request.operation_id,
            result.asset_version_id == target.asset_version.id,
            result.asset_object_id == source.id,
            attempt_is_reusable,
            result.stage == expected_stage,
            result.policy_version == target.asset_version.validation_policy_version,
            result.object_provider_version_id == source.provider_version_id,
            result.object_etag == source.etag,
            result.content_sha256 == source.sha256,
            result.retention_deadline == target.asset.retention_deadline,
        )
    ):
        raise AssetValidationEvidenceError(
            code="VALIDATION_EVIDENCE_IDENTITY_MISMATCH",
            message=(
                "append-only validation evidence does not match the current "
                "Asset Version, policy, and object identity"
            ),
        )


class AssetValidationEvidenceStore:
    """Persist one immutable stage fact and converge concurrent insert races."""

    def __init__(self, *, uow_factory: AssetUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def attempt_results(
        self,
        request: OperationExecutionRequest,
    ) -> dict[ValidationStage, AssetValidationResult]:
        with self._uow_factory() as uow:
            results = uow.assets.list_validation_results(
                workspace_id=request.workspace_id,
                asset_version_id=request.target_id,
            )
        by_attempt_stage: dict[
            tuple[int, ValidationStage],
            AssetValidationResult,
        ] = {}
        for result in results:
            if (
                result.operation_id != request.operation_id
                or result.attempt_number > request.attempt_count
            ):
                continue
            identity = (result.attempt_number, result.stage)
            if identity in by_attempt_stage:
                raise AssetValidationEvidenceError(
                    code="AMBIGUOUS_VALIDATION_EVIDENCE",
                    message="validation attempt contains duplicate stage evidence",
                )
            by_attempt_stage[identity] = result

        selected: dict[ValidationStage, AssetValidationResult] = {}
        for stage in ValidationStage:
            current = by_attempt_stage.get((request.attempt_count, stage))
            if current is not None:
                selected[stage] = current
                continue
            prior = (
                result
                for (attempt_number, result_stage), result in by_attempt_stage.items()
                if result_stage == stage
                and attempt_number < request.attempt_count
                and result.verdict in _CROSS_ATTEMPT_REUSABLE_VERDICTS
            )
            latest = max(prior, key=lambda result: result.attempt_number, default=None)
            if latest is not None:
                selected[stage] = latest
        return selected

    def append(
        self,
        result: AssetValidationResult,
    ) -> AssetValidationResult:
        try:
            with self._uow_factory() as uow:
                existing = self._get(uow, result)
                if existing is not None:
                    return existing
                uow.assets.add_validation_result(result)
                uow.commit()
        except UniqueConstraintError as exc:
            with self._uow_factory() as uow:
                existing = self._get(uow, result)
            if existing is None:
                raise AssetValidationEvidenceError(
                    code="VALIDATION_EVIDENCE_WRITE_CONFLICT",
                    message=("a concurrent validation evidence insert has not become visible"),
                    retryable=True,
                ) from exc
            return existing
        return result

    @staticmethod
    def _get(
        uow: AssetUnitOfWorkPort,
        result: AssetValidationResult,
    ) -> AssetValidationResult | None:
        return uow.assets.get_validation_result(
            workspace_id=result.workspace_id,
            asset_version_id=result.asset_version_id,
            operation_id=result.operation_id,
            attempt_number=result.attempt_number,
            stage=result.stage,
            validator_name=result.validator_name,
            validator_version=result.validator_version,
            policy_version=result.policy_version,
        )
