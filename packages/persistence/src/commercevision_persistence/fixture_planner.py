"""MySQL authority adapter for the deterministic Fixture Planner."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid5

from commercevision_application.fixture_planner import FixturePlanningAuthority
from commercevision_application.planning_contexts import PlanningContextExactReference
from commercevision_domain import (
    ConcurrencyError,
    InvalidTransitionError,
    NotFoundError,
    PlanningContextSourceKind,
    ProductBriefCategory,
    ProductBriefState,
    RetentionStatus,
    WorkflowStatus,
)
from sqlalchemy import literal_column, select
from sqlalchemy.orm import Session, sessionmaker

from .product_brief_models import ProductBriefModel, ProductBriefVersionModel
from .repositories import WorkflowRepository
from .retrieval_models import RetrievalRunModel

_RUN_NAMESPACE = UUID("bcf31bf3-d397-4451-9cef-95b747a9edbb")
_RETRIEVAL_POLICY = "fixture-retrieval-v1"
_CATEGORIES = {
    ProductBriefCategory.BEAUTY: "beauty",
    ProductBriefCategory.AUTOMOTIVE: "automotive-parts",
}


class MySqlFixturePlanningAuthority:
    """Fence a Planner input and retain a deterministic empty Retrieval Run."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def load(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        product_brief_version_id: str,
        product_brief_version_number: int,
        expected_workflow_version: int,
    ) -> FixturePlanningAuthority:
        with self._session_factory.begin() as session:
            workflow = WorkflowRepository(session).get(
                workflow_id,
                workspace_id=workspace_id,
                for_update=True,
            )
            if workflow is None:
                raise NotFoundError("Fixture Planner Workflow was not found")
            workflow.assert_version(expected_workflow_version)
            if (
                workflow.status is not WorkflowStatus.PLANNING
                or workflow.current_node != "create_plan"
                or workflow.retention_status is not RetentionStatus.ACTIVE
            ):
                raise InvalidTransitionError("Workflow is not eligible for Fixture planning")
            now = self._database_now(session)
            if now >= workflow.expires_at:
                raise InvalidTransitionError("Workflow retention expired before Fixture planning")

            row = session.execute(
                select(ProductBriefModel, ProductBriefVersionModel)
                .join(
                    ProductBriefVersionModel,
                    (ProductBriefVersionModel.workspace_id == ProductBriefModel.workspace_id)
                    & (ProductBriefVersionModel.product_brief_id == ProductBriefModel.id),
                )
                .where(
                    ProductBriefModel.workspace_id == workspace_id,
                    ProductBriefModel.workflow_id == workflow_id,
                    ProductBriefModel.state == ProductBriefState.CONFIRMED.value,
                    ProductBriefModel.current_version_id == product_brief_version_id,
                    ProductBriefModel.confirmed_version_id == product_brief_version_id,
                    ProductBriefVersionModel.id == product_brief_version_id,
                    ProductBriefVersionModel.version_number == product_brief_version_number,
                )
                .with_for_update()
            ).one_or_none()
            if row is None:
                raise NotFoundError("Fixture Planner ProductBrief was not found")
            product_brief, version = row
            if (
                product_brief.retention_deadline is not None
                and now >= product_brief.retention_deadline
            ) or (version.retention_deadline is not None and now >= version.retention_deadline):
                raise ConcurrencyError(
                    "Fixture Planner ProductBrief is not the exact current confirmed version"
                )

            run_id = str(
                uuid5(
                    _RUN_NAMESPACE,
                    f"{workspace_id}\0{workflow_id}\0{product_brief_version_id}",
                )
            )
            self._ensure_retrieval_run(
                session,
                run_id=run_id,
                workspace_id=workspace_id,
                workflow_id=workflow_id,
                product_brief_id=product_brief.id,
                product_brief_version_id=product_brief_version_id,
                category=ProductBriefCategory(version.category),
                requester_id=workflow.created_by,
                created_at=now,
                expires_at=workflow.expires_at,
            )
            return FixturePlanningAuthority(
                product_brief=PlanningContextExactReference(
                    kind=PlanningContextSourceKind.PRODUCT_BRIEF,
                    source_id=product_brief.id,
                    version_number=version.version_number,
                    content_sha256=version.payload_sha256,
                ),
                brand_profile=None,
                retrieval_citations=(),
                retrieval_run_id=run_id,
                category=_CATEGORIES[ProductBriefCategory(version.category)],
            )

    @staticmethod
    def _database_now(session: Session) -> datetime:
        value = session.scalar(select(literal_column("UTC_TIMESTAMP(6)")))
        if not isinstance(value, datetime):
            raise RuntimeError("Fixture Planner MySQL time is unavailable")
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @staticmethod
    def _ensure_retrieval_run(
        session: Session,
        *,
        run_id: str,
        workspace_id: str,
        workflow_id: str,
        product_brief_id: str,
        product_brief_version_id: str,
        category: ProductBriefCategory,
        requester_id: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> None:
        query = {
            "schema_version": "fixture-planner-retrieval.v1",
            "workflow_id": workflow_id,
            "product_brief_id": product_brief_id,
            "product_brief_version_id": product_brief_version_id,
            "category": category.value,
        }
        query_sha256 = hashlib.sha256(
            json.dumps(query, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        existing = session.scalar(
            select(RetrievalRunModel).where(
                RetrievalRunModel.workspace_id == workspace_id,
                RetrievalRunModel.id == run_id,
            )
        )
        if existing is not None:
            if (
                existing.query_sha256 != query_sha256
                or existing.retrieval_policy_version != _RETRIEVAL_POLICY
                or existing.expires_at != expires_at
            ):
                raise ConcurrencyError("Fixture Planner Retrieval Run stores different authority")
            return
        session.add(
            RetrievalRunModel(
                id=run_id,
                workspace_id=workspace_id,
                requester_id=requester_id,
                query_json=query,
                query_sha256=query_sha256,
                retrieval_policy_version=_RETRIEVAL_POLICY,
                complete_hybrid=True,
                degradations_json=[],
                eligible_asset_version_count=0,
                fused_candidate_count=0,
                final_authorized_candidate_count=0,
                latency_ms=0,
                created_at=created_at,
                expires_at=expires_at,
            )
        )
