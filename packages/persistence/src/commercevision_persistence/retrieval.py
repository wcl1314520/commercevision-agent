"""MySQL authority for rights-first retrieval candidate sets."""

from __future__ import annotations

from datetime import UTC

from commercevision_application import EligibleRetrievalAsset, RetrievalEligibility
from commercevision_contracts import RetrievalQueryV1
from sqlalchemy import and_, exists, literal_column, or_, select
from sqlalchemy.orm import Session, sessionmaker

from .models import (
    AssetModel,
    AssetVersionModel,
    ProductModel,
    RightsRecordModel,
    RightsRecordProviderModel,
    RightsRecordUseModel,
)
from .product_brief_models import ProductBriefModel


class MySqlRetrievalAuthority:
    """Generate and revalidate candidate sets from current MySQL authority facts."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def eligible_asset_versions(
        self,
        query: RetrievalQueryV1,
    ) -> RetrievalEligibility:
        with self._session_factory() as session:
            product_id = self._resolve_product_id(session, query)
            if product_id is None:
                return self._empty_snapshot(session)
            return self._load_eligible(
                session,
                query=query,
                product_id=product_id,
                restrict_to_ids=None,
            )

    def revalidate_asset_versions(
        self,
        query: RetrievalQueryV1,
        *,
        asset_version_ids: tuple[str, ...],
    ) -> RetrievalEligibility:
        if not asset_version_ids:
            with self._session_factory() as session:
                return self._empty_snapshot(session)
        if len(asset_version_ids) > query.candidate_limit:
            raise ValueError("final retrieval revalidation exceeds the candidate limit")
        if len(set(asset_version_ids)) != len(asset_version_ids):
            raise ValueError("final retrieval revalidation identities must be unique")
        with self._session_factory() as session:
            product_id = self._resolve_product_id(session, query)
            if product_id is None:
                return self._empty_snapshot(session)
            return self._load_eligible(
                session,
                query=query,
                product_id=product_id,
                restrict_to_ids=asset_version_ids,
            )

    @staticmethod
    def _resolve_product_id(session: Session, query: RetrievalQueryV1) -> str | None:
        if query.product_brief_id is None:
            return query.product_id
        brief = session.scalar(
            select(ProductBriefModel)
            .where(
                ProductBriefModel.workspace_id == query.workspace_id,
                ProductBriefModel.id == query.product_brief_id,
                ProductBriefModel.state == "CONFIRMED",
                ProductBriefModel.current_version_id == ProductBriefModel.confirmed_version_id,
            )
            .with_for_update(read=True)
        )
        if brief is None:
            return None
        if query.product_id is not None and query.product_id != brief.product_id:
            return None
        return brief.product_id

    @staticmethod
    def _load_eligible(
        session: Session,
        *,
        query: RetrievalQueryV1,
        product_id: str,
        restrict_to_ids: tuple[str, ...] | None,
    ) -> RetrievalEligibility:
        database_now = literal_column("UTC_TIMESTAMP(6)")
        statement = (
            select(
                AssetModel.id.label("asset_id"),
                AssetVersionModel.id.label("asset_version_id"),
                AssetVersionModel.sha256.label("content_sha256"),
                AssetModel.product_id,
                AssetVersionModel.category,
                ProductModel.brand,
                AssetVersionModel.role,
                RightsRecordModel.id.label("rights_record_id"),
                RightsRecordModel.version_number.label("rights_record_version"),
                AssetModel.retention_class,
                database_now.label("decided_at"),
            )
            .join(
                AssetVersionModel,
                and_(
                    AssetVersionModel.workspace_id == AssetModel.workspace_id,
                    AssetVersionModel.id == AssetModel.current_version_id,
                    AssetVersionModel.asset_id == AssetModel.id,
                ),
            )
            .join(
                RightsRecordModel,
                and_(
                    RightsRecordModel.workspace_id == AssetModel.workspace_id,
                    RightsRecordModel.id == AssetModel.current_rights_record_id,
                    RightsRecordModel.asset_id == AssetModel.id,
                ),
            )
            .join(
                ProductModel,
                and_(
                    ProductModel.workspace_id == AssetModel.workspace_id,
                    ProductModel.id == AssetModel.product_id,
                ),
            )
            .where(
                AssetModel.workspace_id == query.workspace_id,
                AssetModel.product_id == product_id,
                AssetModel.status == "AVAILABLE",
                or_(
                    AssetModel.retention_class == "FOUNDATION",
                    and_(
                        AssetModel.retention_class == "TASK",
                        AssetModel.retention_deadline.is_not(None),
                        AssetModel.retention_deadline > database_now,
                    ),
                ),
                RightsRecordModel.decision == "GRANT",
                RightsRecordModel.permissions_sealed_at.is_not(None),
                RightsRecordModel.valid_from <= database_now,
                or_(
                    RightsRecordModel.perpetual.is_(True),
                    and_(
                        RightsRecordModel.valid_until.is_not(None),
                        RightsRecordModel.valid_until > database_now,
                    ),
                ),
                or_(
                    RightsRecordModel.asset_version_id.is_(None),
                    RightsRecordModel.asset_version_id == AssetVersionModel.id,
                ),
                exists(
                    select(RightsRecordUseModel.rights_record_id).where(
                        RightsRecordUseModel.workspace_id == AssetModel.workspace_id,
                        RightsRecordUseModel.asset_id == AssetModel.id,
                        RightsRecordUseModel.rights_record_id == RightsRecordModel.id,
                        RightsRecordUseModel.allowed_use == query.purpose,
                    )
                ),
                exists(
                    select(RightsRecordProviderModel.rights_record_id).where(
                        RightsRecordProviderModel.workspace_id == AssetModel.workspace_id,
                        RightsRecordProviderModel.asset_id == AssetModel.id,
                        RightsRecordProviderModel.rights_record_id == RightsRecordModel.id,
                        RightsRecordProviderModel.allowed_provider == query.provider,
                    )
                ),
            )
            .order_by(AssetVersionModel.id)
        )
        if query.requires_derivative:
            statement = statement.where(RightsRecordModel.derivative_allowed.is_(True))
        if query.category is not None:
            statement = statement.where(AssetVersionModel.category == query.category)
        if query.brand is not None:
            statement = statement.where(ProductModel.brand == query.brand)
        if query.roles:
            statement = statement.where(AssetVersionModel.role.in_(query.roles))
        if restrict_to_ids is not None:
            statement = statement.where(AssetVersionModel.id.in_(restrict_to_ids))
        rows = tuple(session.execute(statement).mappings())
        decided_at = rows[0]["decided_at"] if rows else session.scalar(select(database_now))
        if decided_at is None:
            raise RuntimeError("MySQL retrieval authority time is unavailable")
        if decided_at.tzinfo is None:
            decided_at = decided_at.replace(tzinfo=UTC)
        if any(row["decided_at"] != rows[0]["decided_at"] for row in rows[1:]):
            raise RuntimeError("MySQL retrieval authority returned inconsistent decision times")
        return RetrievalEligibility(
            decided_at=decided_at,
            items=tuple(
                EligibleRetrievalAsset(
                    asset_id=row["asset_id"],
                    asset_version_id=row["asset_version_id"],
                    content_sha256=row["content_sha256"],
                    product_id=row["product_id"],
                    category=row["category"],
                    brand=row["brand"],
                    role=row["role"],
                    rights_record_id=row["rights_record_id"],
                    rights_record_version=row["rights_record_version"],
                    retention_class=row["retention_class"],
                )
                for row in rows
            ),
        )

    @staticmethod
    def _empty_snapshot(session: Session) -> RetrievalEligibility:
        database_now = session.scalar(select(literal_column("UTC_TIMESTAMP(6)")))
        if database_now is None:
            raise RuntimeError("MySQL retrieval authority time is unavailable")
        if database_now.tzinfo is None:
            database_now = database_now.replace(tzinfo=UTC)
        return RetrievalEligibility(decided_at=database_now, items=())
