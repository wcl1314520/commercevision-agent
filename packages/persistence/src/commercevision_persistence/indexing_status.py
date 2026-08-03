"""Sanitized MySQL-backed IMAGE index status projection."""

from __future__ import annotations

from commercevision_contracts import AssetIndexStatusResponseV1
from commercevision_domain import NotFoundError, VectorKind, canonicalize_uuid
from commercevision_domain.workspace_identity import validate_workspace_id
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .indexing_models import EmbeddingRecordModel
from .models import AssetModel, DurableOperationModel


class SqlAlchemyImageIndexStatusQueries:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_current(
        self,
        *,
        workspace_id: str,
        asset_id: str,
    ) -> AssetIndexStatusResponseV1:
        validate_workspace_id(workspace_id)
        asset_id = canonicalize_uuid(asset_id)
        with self._session_factory() as session:
            asset = session.scalar(
                select(AssetModel).where(
                    AssetModel.workspace_id == workspace_id,
                    AssetModel.id == asset_id,
                )
            )
            if asset is None:
                raise NotFoundError("Asset was not found")
            if asset.current_version_id is None:
                return self._not_requested(asset)
            row = session.execute(
                select(EmbeddingRecordModel, DurableOperationModel)
                .join(
                    DurableOperationModel,
                    (DurableOperationModel.workspace_id == EmbeddingRecordModel.workspace_id)
                    & (DurableOperationModel.id == EmbeddingRecordModel.operation_id),
                )
                .where(
                    EmbeddingRecordModel.workspace_id == workspace_id,
                    EmbeddingRecordModel.asset_id == asset_id,
                    EmbeddingRecordModel.asset_version_id == asset.current_version_id,
                    EmbeddingRecordModel.vector_kind == VectorKind.IMAGE.value,
                )
                .order_by(
                    EmbeddingRecordModel.updated_at.desc(),
                    EmbeddingRecordModel.id.desc(),
                )
                .limit(1)
            ).one_or_none()
            if row is None:
                return self._not_requested(asset)
            embedding, operation = row
            if embedding.rights_record_id != asset.current_rights_record_id:
                return AssetIndexStatusResponseV1(
                    asset_id=asset.id,
                    asset_version_id=embedding.asset_version_id,
                    state="STALE",
                    retryable=False,
                    failure_reason="RIGHTS_CHANGED",
                    indexed_at=embedding.indexed_at,
                    updated_at=asset.updated_at,
                )
            return AssetIndexStatusResponseV1(
                asset_id=asset.id,
                asset_version_id=embedding.asset_version_id,
                state=embedding.state,
                retryable=bool(operation.error_retryable),
                failure_reason=embedding.stale_reason or operation.error_code,
                indexed_at=embedding.indexed_at,
                updated_at=embedding.updated_at,
            )

    @staticmethod
    def _not_requested(asset: AssetModel) -> AssetIndexStatusResponseV1:
        return AssetIndexStatusResponseV1(
            asset_id=asset.id,
            asset_version_id=asset.current_version_id,
            state="NOT_REQUESTED",
            retryable=False,
            failure_reason=None,
            indexed_at=None,
            updated_at=asset.updated_at,
        )
