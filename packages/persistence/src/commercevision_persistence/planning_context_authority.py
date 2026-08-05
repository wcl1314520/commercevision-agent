"""MySQL authority adapter for bounded Planning Context source facts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, cast

from commercevision_application import (
    PlanningContextAuthorizedSource,
    PlanningContextExactReference,
)
from commercevision_domain import (
    PlanningContextPolicy,
    PlanningContextSource,
    PlanningContextSourceKind,
    canonicalize_uuid,
)
from sqlalchemy import and_, exists, literal_column, or_, select
from sqlalchemy.orm import Session, sessionmaker

from .brand_profile_models import (
    BrandProfileMemberModel,
    BrandProfileModel,
    BrandProfileVersionModel,
)
from .models import (
    AssetModel,
    AssetVersionModel,
    RightsRecordModel,
    RightsRecordUseModel,
    WorkflowModel,
)
from .product_brief_models import (
    ProductBriefFieldModel,
    ProductBriefModel,
    ProductBriefVersionModel,
)
from .retrieval_models import RetrievalResultModel, RetrievalRunModel

_REDACTED = "[REDACTED]"


def planning_context_citation_id(retrieval_run_id: str, *, rank: int) -> str:
    canonicalize_uuid(retrieval_run_id)
    if type(rank) is not int or not 1 <= rank <= 1_000:
        raise ValueError("Planning Context citation rank is invalid")
    return f"{retrieval_run_id}:{rank}"


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _earliest(*values: datetime | None) -> datetime:
    present = tuple(_utc(value) for value in values if value is not None)
    if not present:
        raise RuntimeError("authorized Planning Context source has no retention boundary")
    return min(present)


class MySqlPlanningContextAuthority:
    """Revalidate exact authoritative identities before exposing bounded source data."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        policies: Mapping[str, PlanningContextPolicy],
    ) -> None:
        frozen = dict(policies)
        if not frozen or any(key != policy.version for key, policy in frozen.items()):
            raise ValueError("Planning Context policy registry is invalid")
        self._session_factory = session_factory
        self._policies = MappingProxyType(frozen)

    def database_now(self) -> datetime:
        with self._session_factory() as session:
            value = session.scalar(select(literal_column("UTC_TIMESTAMP(6)")))
        if not isinstance(value, datetime):
            raise RuntimeError("MySQL Planning Context authority time is unavailable")
        return _utc(value)

    def load_policy(self, *, version: str) -> PlanningContextPolicy | None:
        return self._policies.get(version)

    def workflow_retention_deadline(
        self, *, workspace_id: str, workflow_id: str
    ) -> datetime | None:
        with self._session_factory() as session:
            value = self._workflow_deadline(
                session,
                workspace_id=workspace_id,
                workflow_id=workflow_id,
            )
        return _utc(value) if value is not None else None

    def load_authorized_source(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        purpose: str,
        reference: PlanningContextExactReference,
        at: datetime,
    ) -> PlanningContextAuthorizedSource | None:
        decision_time = _utc(at)
        with self._session_factory() as session:
            workflow_deadline = self._workflow_deadline(
                session,
                workspace_id=workspace_id,
                workflow_id=workflow_id,
            )
            if workflow_deadline is None or _utc(workflow_deadline) <= decision_time:
                return None
            if reference.kind == PlanningContextSourceKind.PRODUCT_BRIEF:
                return self._load_product_brief(
                    session,
                    workspace_id=workspace_id,
                    workflow_id=workflow_id,
                    purpose=purpose,
                    reference=reference,
                    at=decision_time,
                    workflow_deadline=_utc(workflow_deadline),
                )
            if reference.kind == PlanningContextSourceKind.BRAND_PROFILE:
                return self._load_brand_profile(
                    session,
                    workspace_id=workspace_id,
                    workflow_id=workflow_id,
                    purpose=purpose,
                    reference=reference,
                    at=decision_time,
                    workflow_deadline=_utc(workflow_deadline),
                )
            return self._load_retrieval_citation(
                session,
                workspace_id=workspace_id,
                workflow_id=workflow_id,
                purpose=purpose,
                reference=reference,
                at=decision_time,
                workflow_deadline=_utc(workflow_deadline),
            )

    @staticmethod
    def _workflow_deadline(
        session: Session,
        *,
        workspace_id: str,
        workflow_id: str,
    ) -> datetime | None:
        return session.scalar(
            select(WorkflowModel.expires_at).where(
                WorkflowModel.workspace_id == workspace_id,
                WorkflowModel.id == workflow_id,
                WorkflowModel.retention_status == "ACTIVE",
            )
        )

    @staticmethod
    def _load_product_brief(
        session: Session,
        *,
        workspace_id: str,
        workflow_id: str,
        purpose: str,
        reference: PlanningContextExactReference,
        at: datetime,
        workflow_deadline: datetime,
    ) -> PlanningContextAuthorizedSource | None:
        row = session.execute(
            select(ProductBriefModel, ProductBriefVersionModel)
            .join(
                ProductBriefVersionModel,
                and_(
                    ProductBriefVersionModel.workspace_id == ProductBriefModel.workspace_id,
                    ProductBriefVersionModel.product_brief_id == ProductBriefModel.id,
                    ProductBriefVersionModel.id == ProductBriefModel.confirmed_version_id,
                ),
            )
            .where(
                ProductBriefModel.workspace_id == workspace_id,
                ProductBriefModel.workflow_id == workflow_id,
                ProductBriefModel.id == reference.source_id,
                ProductBriefModel.state == "CONFIRMED",
                ProductBriefModel.current_version_id == ProductBriefModel.confirmed_version_id,
                ProductBriefVersionModel.version_number == reference.version_number,
                ProductBriefVersionModel.payload_sha256 == reference.content_sha256,
            )
        ).one_or_none()
        if row is None:
            return None
        brief, version = row
        if (brief.retention_deadline is not None and _utc(brief.retention_deadline) <= at) or (
            version.retention_deadline is not None and _utc(version.retention_deadline) <= at
        ):
            return None
        fields = tuple(
            session.scalars(
                select(ProductBriefFieldModel)
                .where(
                    ProductBriefFieldModel.workspace_id == workspace_id,
                    ProductBriefFieldModel.product_brief_id == brief.id,
                    ProductBriefFieldModel.product_brief_version_id == version.id,
                )
                .order_by(ProductBriefFieldModel.path)
            )
        )
        content: dict[str, object] = {
            "category": version.category,
            "common_schema_version": version.common_schema_version,
            "category_schema_version": version.category_schema_version,
            "review_policy_version": version.review_policy_version,
            "fields": [
                {
                    "path": field.path,
                    "value": _REDACTED if field.sensitive else field.value_json,
                    "redacted": field.sensitive,
                }
                for field in fields
            ],
        }
        source = PlanningContextSource.create(
            kind=PlanningContextSourceKind.PRODUCT_BRIEF,
            source_id=brief.id,
            version_number=version.version_number,
            content_sha256=version.payload_sha256,
            content=content,
        )
        return PlanningContextAuthorizedSource(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            purpose=purpose,
            source=source,
            usable_until=_earliest(
                workflow_deadline,
                brief.retention_deadline,
                version.retention_deadline,
            ),
        )

    @staticmethod
    def _load_brand_profile(
        session: Session,
        *,
        workspace_id: str,
        workflow_id: str,
        purpose: str,
        reference: PlanningContextExactReference,
        at: datetime,
        workflow_deadline: datetime,
    ) -> PlanningContextAuthorizedSource | None:
        row = session.execute(
            select(BrandProfileModel, BrandProfileVersionModel)
            .join(
                BrandProfileVersionModel,
                and_(
                    BrandProfileVersionModel.workspace_id == BrandProfileModel.workspace_id,
                    BrandProfileVersionModel.profile_id == BrandProfileModel.id,
                    BrandProfileVersionModel.id == BrandProfileModel.current_version_id,
                    BrandProfileVersionModel.version_number
                    == BrandProfileModel.current_version_number,
                ),
            )
            .where(
                BrandProfileModel.workspace_id == workspace_id,
                BrandProfileModel.id == reference.source_id,
                BrandProfileModel.state == "ACTIVE",
                BrandProfileVersionModel.version_number == reference.version_number,
                BrandProfileVersionModel.content_sha256 == reference.content_sha256,
                BrandProfileVersionModel.purpose == purpose,
            )
        ).one_or_none()
        if row is None:
            return None
        profile, version = row
        members = tuple(
            session.scalars(
                select(BrandProfileMemberModel)
                .where(
                    BrandProfileMemberModel.workspace_id == workspace_id,
                    BrandProfileMemberModel.profile_id == profile.id,
                    BrandProfileMemberModel.profile_version_id == version.id,
                    BrandProfileMemberModel.profile_version_number == version.version_number,
                )
                .order_by(BrandProfileMemberModel.ordinal)
            )
        )
        member_rows = tuple(
            session.execute(
                select(
                    BrandProfileMemberModel,
                    AssetModel,
                    AssetVersionModel,
                    RightsRecordModel,
                )
                .join(
                    AssetModel,
                    and_(
                        AssetModel.workspace_id == BrandProfileMemberModel.workspace_id,
                        AssetModel.id == BrandProfileMemberModel.asset_id,
                    ),
                )
                .join(
                    AssetVersionModel,
                    and_(
                        AssetVersionModel.workspace_id == BrandProfileMemberModel.workspace_id,
                        AssetVersionModel.id == BrandProfileMemberModel.asset_version_id,
                        AssetVersionModel.asset_id == BrandProfileMemberModel.asset_id,
                    ),
                )
                .join(
                    RightsRecordModel,
                    and_(
                        RightsRecordModel.workspace_id == BrandProfileMemberModel.workspace_id,
                        RightsRecordModel.id == BrandProfileMemberModel.rights_record_id,
                        RightsRecordModel.asset_id == BrandProfileMemberModel.asset_id,
                    ),
                )
                .where(
                    BrandProfileMemberModel.workspace_id == workspace_id,
                    BrandProfileMemberModel.profile_id == profile.id,
                    BrandProfileMemberModel.profile_version_id == version.id,
                    AssetModel.status == "AVAILABLE",
                    AssetModel.current_version_id == BrandProfileMemberModel.asset_version_id,
                    AssetModel.current_rights_record_id == BrandProfileMemberModel.rights_record_id,
                    or_(
                        AssetModel.retention_class == "FOUNDATION",
                        and_(
                            AssetModel.retention_class == "TASK",
                            AssetModel.retention_deadline.is_not(None),
                            AssetModel.retention_deadline > at,
                        ),
                    ),
                    RightsRecordModel.version_number
                    == BrandProfileMemberModel.rights_record_version,
                    RightsRecordModel.decision == "GRANT",
                    RightsRecordModel.permissions_sealed_at.is_not(None),
                    RightsRecordModel.valid_from <= at,
                    or_(
                        RightsRecordModel.perpetual.is_(True),
                        and_(
                            RightsRecordModel.valid_until.is_not(None),
                            RightsRecordModel.valid_until > at,
                        ),
                    ),
                    or_(
                        RightsRecordModel.asset_version_id.is_(None),
                        RightsRecordModel.asset_version_id
                        == BrandProfileMemberModel.asset_version_id,
                    ),
                    exists(
                        select(RightsRecordUseModel.rights_record_id).where(
                            RightsRecordUseModel.workspace_id == workspace_id,
                            RightsRecordUseModel.asset_id == BrandProfileMemberModel.asset_id,
                            RightsRecordUseModel.rights_record_id
                            == BrandProfileMemberModel.rights_record_id,
                            RightsRecordUseModel.allowed_use == purpose,
                        )
                    ),
                )
                .order_by(BrandProfileMemberModel.ordinal)
            )
        )
        if len(member_rows) != len(members):
            return None
        member_content = [
            {
                "asset_version_id": member.asset_version_id,
                "content_sha256": asset_version.sha256,
                "rights_record_id": rights.id,
                "rights_record_version": rights.version_number,
                "role": member.role,
            }
            for member, _asset, asset_version, rights in member_rows
        ]
        source = PlanningContextSource.create(
            kind=PlanningContextSourceKind.BRAND_PROFILE,
            source_id=profile.id,
            version_number=version.version_number,
            content_sha256=version.content_sha256,
            content={
                "profile": cast(dict[str, object], version.content_json),
                "members": member_content,
            },
        )
        return PlanningContextAuthorizedSource(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            purpose=purpose,
            source=source,
            usable_until=_earliest(
                workflow_deadline,
                *(
                    value
                    for _member, asset, _asset_version, rights in member_rows
                    for value in (asset.retention_deadline, rights.valid_until)
                ),
            ),
        )

    @staticmethod
    def _load_retrieval_citation(
        session: Session,
        *,
        workspace_id: str,
        workflow_id: str,
        purpose: str,
        reference: PlanningContextExactReference,
        at: datetime,
        workflow_deadline: datetime,
    ) -> PlanningContextAuthorizedSource | None:
        assert reference.retrieval_run_id is not None
        assert reference.retrieval_rank is not None
        expected_citation_id = planning_context_citation_id(
            reference.retrieval_run_id,
            rank=reference.retrieval_rank,
        )
        if reference.citation_id != expected_citation_id:
            return None
        brief_id = session.scalar(
            select(ProductBriefModel.id).where(
                ProductBriefModel.workspace_id == workspace_id,
                ProductBriefModel.workflow_id == workflow_id,
                ProductBriefModel.state == "CONFIRMED",
                ProductBriefModel.current_version_id == ProductBriefModel.confirmed_version_id,
            )
        )
        if brief_id is None:
            return None
        row = session.execute(
            select(
                RetrievalRunModel,
                RetrievalResultModel,
                AssetModel,
                AssetVersionModel,
                RightsRecordModel,
            )
            .join(
                RetrievalResultModel,
                and_(
                    RetrievalResultModel.workspace_id == RetrievalRunModel.workspace_id,
                    RetrievalResultModel.retrieval_run_id == RetrievalRunModel.id,
                ),
            )
            .join(
                AssetModel,
                and_(
                    AssetModel.workspace_id == RetrievalResultModel.workspace_id,
                    AssetModel.id == RetrievalResultModel.asset_id,
                ),
            )
            .join(
                AssetVersionModel,
                and_(
                    AssetVersionModel.workspace_id == AssetModel.workspace_id,
                    AssetVersionModel.asset_id == AssetModel.id,
                    AssetVersionModel.id == RetrievalResultModel.asset_version_id,
                ),
            )
            .join(
                RightsRecordModel,
                and_(
                    RightsRecordModel.workspace_id == AssetModel.workspace_id,
                    RightsRecordModel.asset_id == AssetModel.id,
                    RightsRecordModel.id == RetrievalResultModel.rights_record_id,
                ),
            )
            .where(
                RetrievalRunModel.workspace_id == workspace_id,
                RetrievalRunModel.id == reference.retrieval_run_id,
                RetrievalRunModel.retrieval_policy_version == reference.retrieval_policy_version,
                RetrievalRunModel.expires_at > at,
                RetrievalResultModel.rank == reference.retrieval_rank,
                RetrievalResultModel.asset_version_id == reference.source_id,
                RetrievalResultModel.rights_record_id == reference.authority_id,
                RetrievalResultModel.rights_record_version == reference.authority_version,
                AssetModel.status == "AVAILABLE",
                AssetModel.current_version_id == AssetVersionModel.id,
                AssetModel.current_rights_record_id == RightsRecordModel.id,
                or_(
                    AssetModel.retention_class == "FOUNDATION",
                    and_(
                        AssetModel.retention_class == "TASK",
                        AssetModel.retention_deadline.is_not(None),
                        AssetModel.retention_deadline > at,
                    ),
                ),
                AssetVersionModel.sha256 == reference.content_sha256,
                RightsRecordModel.version_number == reference.authority_version,
                RightsRecordModel.decision == "GRANT",
                RightsRecordModel.permissions_sealed_at.is_not(None),
                RightsRecordModel.valid_from <= at,
                or_(
                    RightsRecordModel.perpetual.is_(True),
                    and_(
                        RightsRecordModel.valid_until.is_not(None),
                        RightsRecordModel.valid_until > at,
                    ),
                ),
                or_(
                    RightsRecordModel.asset_version_id.is_(None),
                    RightsRecordModel.asset_version_id == AssetVersionModel.id,
                ),
                exists(
                    select(RightsRecordUseModel.rights_record_id).where(
                        RightsRecordUseModel.workspace_id == workspace_id,
                        RightsRecordUseModel.asset_id == AssetModel.id,
                        RightsRecordUseModel.rights_record_id == RightsRecordModel.id,
                        RightsRecordUseModel.allowed_use == purpose,
                    )
                ),
            )
        ).one_or_none()
        if row is None:
            return None
        run, result, asset, asset_version, rights = row
        query_json = cast(dict[str, Any], run.query_json)
        if query_json.get("product_brief_id") != brief_id:
            return None
        source = PlanningContextSource.create(
            kind=PlanningContextSourceKind.RETRIEVAL_CITATION,
            source_id=asset_version.id,
            version_number=None,
            content_sha256=asset_version.sha256,
            content={
                "asset_id": asset.id,
                "category": asset_version.category,
                "role": asset_version.role,
                "width": asset_version.width,
                "height": asset_version.height,
                "reason": result.reason,
                "channels": list(result.channels_json),
            },
            authority_id=rights.id,
            authority_version=rights.version_number,
            retrieval_run_id=run.id,
            retrieval_policy_version=run.retrieval_policy_version,
            retrieval_rank=result.rank,
            citation_id=expected_citation_id,
            image_count=1,
        )
        return PlanningContextAuthorizedSource(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            purpose=purpose,
            source=source,
            usable_until=_earliest(
                workflow_deadline,
                run.expires_at,
                asset.retention_deadline,
                rights.valid_until,
            ),
        )
