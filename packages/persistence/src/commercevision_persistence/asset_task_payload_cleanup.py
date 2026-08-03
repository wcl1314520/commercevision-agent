"""MySQL cleanup of Task payloads owned by an Asset's Workflow."""

from __future__ import annotations

from dataclasses import dataclass

from commercevision_domain import RetentionClass
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .models import AgentCheckpointModel, AgentCheckpointWriteModel
from .product_brief_models import (
    ProductBriefEvidenceModel,
    ProductBriefFieldModel,
    ProductBriefModel,
)
from .retrieval_models import RetrievalResultModel, RetrievalRunModel


@dataclass(frozen=True, slots=True)
class AssetTaskPayloadScope:
    workspace_id: str
    asset_id: str
    retention_class: RetentionClass
    workflow_id: str | None


@dataclass(frozen=True, slots=True)
class AssetTaskPayloadCleanupCounts:
    product_brief_payloads: int
    retrieval_runs: int
    temporary_references: int
    checkpoints: int


def converge_task_payloads(
    session: Session,
    *,
    scope: AssetTaskPayloadScope,
) -> AssetTaskPayloadCleanupCounts:
    product_brief_payloads = _delete_product_brief_payloads(session, scope=scope)
    retrieval_runs, temporary_references = _delete_retrieval_runs(session, scope=scope)
    checkpoints = _delete_checkpoints(session, scope=scope)
    return AssetTaskPayloadCleanupCounts(
        product_brief_payloads=product_brief_payloads,
        retrieval_runs=retrieval_runs,
        temporary_references=temporary_references,
        checkpoints=checkpoints,
    )


def task_payloads_are_converged(
    session: Session,
    *,
    scope: AssetTaskPayloadScope,
) -> bool:
    remaining_retrieval_results = session.scalar(
        select(func.count())
        .select_from(RetrievalResultModel)
        .where(
            RetrievalResultModel.workspace_id == scope.workspace_id,
            RetrievalResultModel.asset_id == scope.asset_id,
        )
    )
    return not any(
        (
            remaining_retrieval_results,
            _remaining_product_brief_payloads(session, scope=scope),
            _remaining_checkpoints(session, scope=scope),
        )
    )


def _brief_ids(scope: AssetTaskPayloadScope):
    return select(ProductBriefModel.id).where(
        ProductBriefModel.workspace_id == scope.workspace_id,
        ProductBriefModel.workflow_id == scope.workflow_id,
        ProductBriefModel.retention_class == RetentionClass.TASK.value,
    )


def _delete_product_brief_payloads(
    session: Session,
    *,
    scope: AssetTaskPayloadScope,
) -> int:
    if scope.retention_class != RetentionClass.TASK or scope.workflow_id is None:
        return 0
    brief_ids = _brief_ids(scope)
    evidence_count = session.scalar(
        select(func.count())
        .select_from(ProductBriefEvidenceModel)
        .where(
            ProductBriefEvidenceModel.workspace_id == scope.workspace_id,
            ProductBriefEvidenceModel.product_brief_id.in_(brief_ids),
        )
    )
    field_count = session.scalar(
        select(func.count())
        .select_from(ProductBriefFieldModel)
        .where(
            ProductBriefFieldModel.workspace_id == scope.workspace_id,
            ProductBriefFieldModel.product_brief_id.in_(brief_ids),
        )
    )
    session.execute(
        delete(ProductBriefEvidenceModel).where(
            ProductBriefEvidenceModel.workspace_id == scope.workspace_id,
            ProductBriefEvidenceModel.product_brief_id.in_(brief_ids),
        )
    )
    session.execute(
        delete(ProductBriefFieldModel).where(
            ProductBriefFieldModel.workspace_id == scope.workspace_id,
            ProductBriefFieldModel.product_brief_id.in_(brief_ids),
        )
    )
    return int(evidence_count or 0) + int(field_count or 0)


def _remaining_product_brief_payloads(
    session: Session,
    *,
    scope: AssetTaskPayloadScope,
) -> int:
    if scope.retention_class != RetentionClass.TASK or scope.workflow_id is None:
        return 0
    brief_ids = _brief_ids(scope)
    fields = session.scalar(
        select(func.count())
        .select_from(ProductBriefFieldModel)
        .where(
            ProductBriefFieldModel.workspace_id == scope.workspace_id,
            ProductBriefFieldModel.product_brief_id.in_(brief_ids),
        )
    )
    evidence = session.scalar(
        select(func.count())
        .select_from(ProductBriefEvidenceModel)
        .where(
            ProductBriefEvidenceModel.workspace_id == scope.workspace_id,
            ProductBriefEvidenceModel.product_brief_id.in_(brief_ids),
        )
    )
    return int(fields or 0) + int(evidence or 0)


def _delete_retrieval_runs(
    session: Session,
    *,
    scope: AssetTaskPayloadScope,
) -> tuple[int, int]:
    run_ids = select(RetrievalResultModel.retrieval_run_id).where(
        RetrievalResultModel.workspace_id == scope.workspace_id,
        RetrievalResultModel.asset_id == scope.asset_id,
    )
    temporary_references = session.scalar(
        select(func.count())
        .select_from(RetrievalResultModel)
        .where(
            RetrievalResultModel.workspace_id == scope.workspace_id,
            RetrievalResultModel.asset_id == scope.asset_id,
            RetrievalResultModel.preview_token_sha256.is_not(None),
        )
    )
    result = session.execute(
        delete(RetrievalRunModel).where(
            RetrievalRunModel.workspace_id == scope.workspace_id,
            RetrievalRunModel.id.in_(run_ids),
        )
    )
    return int(result.rowcount or 0), int(temporary_references or 0)


def _delete_checkpoints(session: Session, *, scope: AssetTaskPayloadScope) -> int:
    if scope.workflow_id is None:
        return 0
    thread_ids = select(AgentCheckpointModel.thread_id).where(
        AgentCheckpointModel.workflow_id == scope.workflow_id
    )
    writes = session.execute(
        delete(AgentCheckpointWriteModel).where(AgentCheckpointWriteModel.thread_id.in_(thread_ids))
    )
    checkpoints = session.execute(
        delete(AgentCheckpointModel).where(AgentCheckpointModel.workflow_id == scope.workflow_id)
    )
    return int(writes.rowcount or 0) + int(checkpoints.rowcount or 0)


def _remaining_checkpoints(session: Session, *, scope: AssetTaskPayloadScope) -> int:
    if scope.workflow_id is None:
        return 0
    checkpoints = session.scalar(
        select(func.count())
        .select_from(AgentCheckpointModel)
        .where(AgentCheckpointModel.workflow_id == scope.workflow_id)
    )
    writes = session.scalar(
        select(func.count())
        .select_from(AgentCheckpointWriteModel)
        .where(
            AgentCheckpointWriteModel.thread_id.in_(
                select(AgentCheckpointModel.thread_id).where(
                    AgentCheckpointModel.workflow_id == scope.workflow_id
                )
            )
        )
    )
    return int(checkpoints or 0) + int(writes or 0)
