"""SQL projections for the ProductBrief browser workbench."""

from __future__ import annotations

from datetime import UTC, datetime

from commercevision_application.product_brief_views import (
    ProductBriefAnalysisWorkflowProjection,
    ProductBriefOperationProjection,
    ProductBriefWorkflowProjection,
)
from commercevision_domain import (
    TASK_RETENTION_MAX_HOURS,
    OperationKind,
    OperationState,
    ProductBriefRetentionExpiredError,
    RetentionStatus,
    WorkflowStatus,
    canonical_task_retention_deadline,
)
from sqlalchemy import and_, func, literal_column, or_, select
from sqlalchemy.orm import Session, sessionmaker

from .models import DurableOperationModel, WorkflowModel
from .product_brief_models import ProductBriefModel


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class SqlAlchemyProductBriefViewQueries:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_analysis_workflow_context(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
    ) -> ProductBriefAnalysisWorkflowProjection | None:
        database_now = literal_column("UTC_TIMESTAMP(6)")
        task_retention_deadline = func.least(
            WorkflowModel.expires_at,
            func.timestampadd(
                literal_column("HOUR"),
                literal_column(str(TASK_RETENTION_MAX_HOURS)),
                WorkflowModel.created_at,
            ),
        )
        identity_filters = (
            WorkflowModel.workspace_id == workspace_id,
            WorkflowModel.id == workflow_id,
            WorkflowModel.workflow_type == "COMMERCE_IMAGE_GENERATION",
            WorkflowModel.status == WorkflowStatus.UNDERSTANDING.value,
        )
        with self._session_factory() as session:
            row = (
                session.execute(
                    select(
                        WorkflowModel.id,
                        WorkflowModel.status,
                        WorkflowModel.version,
                        WorkflowModel.created_at,
                        WorkflowModel.expires_at,
                    ).where(
                        *identity_filters,
                        WorkflowModel.retention_status == RetentionStatus.ACTIVE.value,
                        task_retention_deadline > database_now,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                expired = session.scalar(
                    select(literal_column("1"))
                    .select_from(WorkflowModel)
                    .where(
                        *identity_filters,
                        or_(
                            WorkflowModel.retention_status != RetentionStatus.ACTIVE.value,
                            task_retention_deadline <= database_now,
                        ),
                    )
                    .limit(1)
                )
                if expired is not None:
                    raise ProductBriefRetentionExpiredError(
                        "ProductBrief analysis Workflow context retention has expired"
                    )
                return None
        created_at = row["created_at"]
        expires_at = row["expires_at"]
        if not isinstance(created_at, datetime) or not isinstance(expires_at, datetime):
            raise RuntimeError(
                "Task ProductBrief analysis Workflow projection has no retention deadline"
            )
        retention_deadline = canonical_task_retention_deadline(
            created_at=_aware(created_at),
            expires_at=_aware(expires_at),
        )
        return ProductBriefAnalysisWorkflowProjection(
            id=row["id"],
            status=WorkflowStatus(row["status"]),
            version=row["version"],
            retention_deadline=retention_deadline,
        )

    def get_workflow_context(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
        workflow_id: str,
    ) -> ProductBriefWorkflowProjection | None:
        database_now = literal_column("UTC_TIMESTAMP(6)")
        brief_join = and_(
            ProductBriefModel.workspace_id == WorkflowModel.workspace_id,
            ProductBriefModel.workflow_id == WorkflowModel.id,
        )
        identity_filters = (
            WorkflowModel.workspace_id == workspace_id,
            WorkflowModel.id == workflow_id,
            WorkflowModel.workflow_type == "COMMERCE_IMAGE_GENERATION",
            WorkflowModel.input_json["product_id"].as_string() == ProductBriefModel.product_id,
            ProductBriefModel.workspace_id == workspace_id,
            ProductBriefModel.id == product_brief_id,
        )
        with self._session_factory() as session:
            row = (
                session.execute(
                    select(
                        WorkflowModel.id,
                        WorkflowModel.status,
                        WorkflowModel.version,
                        WorkflowModel.expires_at.label("workflow_retention_deadline"),
                        ProductBriefModel.retention_deadline.label(
                            "product_brief_retention_deadline"
                        ),
                    )
                    .join(ProductBriefModel, brief_join)
                    .where(
                        *identity_filters,
                        WorkflowModel.expires_at > database_now,
                        ProductBriefModel.retention_deadline > database_now,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                expired = session.scalar(
                    select(literal_column("1"))
                    .select_from(WorkflowModel)
                    .join(ProductBriefModel, brief_join)
                    .where(
                        *identity_filters,
                        or_(
                            WorkflowModel.expires_at <= database_now,
                            ProductBriefModel.retention_deadline <= database_now,
                        ),
                    )
                    .limit(1)
                )
                if expired is not None:
                    raise ProductBriefRetentionExpiredError(
                        "ProductBrief Workflow context retention has expired"
                    )
                return None
        workflow_deadline = row["workflow_retention_deadline"]
        product_brief_deadline = row["product_brief_retention_deadline"]
        if not isinstance(workflow_deadline, datetime) or not isinstance(
            product_brief_deadline, datetime
        ):
            raise RuntimeError("Task ProductBrief Workflow projection has no retention deadline")
        deadline = min(
            _aware(workflow_deadline),
            _aware(product_brief_deadline),
        )
        return ProductBriefWorkflowProjection(
            id=row["id"],
            status=WorkflowStatus(row["status"]),
            version=row["version"],
            retention_deadline=deadline,
        )

    def get_operation_status(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
        operation_id: str,
    ) -> ProductBriefOperationProjection | None:
        database_now = literal_column("UTC_TIMESTAMP(6)")
        brief_join = and_(
            ProductBriefModel.workspace_id == DurableOperationModel.workspace_id,
            ProductBriefModel.id == DurableOperationModel.target_id,
        )
        identity_filters = (
            DurableOperationModel.workspace_id == workspace_id,
            DurableOperationModel.id == operation_id,
            DurableOperationModel.kind == OperationKind.PRODUCT_BRIEF_ANALYSIS.value,
            DurableOperationModel.target_type == "product_brief",
            DurableOperationModel.target_id == product_brief_id,
            ProductBriefModel.workspace_id == workspace_id,
            ProductBriefModel.id == product_brief_id,
        )
        with self._session_factory() as session:
            row = (
                session.execute(
                    select(
                        DurableOperationModel.id,
                        DurableOperationModel.state,
                        DurableOperationModel.attempt_count,
                        DurableOperationModel.max_attempts,
                        DurableOperationModel.error_code,
                        DurableOperationModel.error_category,
                        DurableOperationModel.error_retryable,
                        DurableOperationModel.version,
                    )
                    .join(ProductBriefModel, brief_join)
                    .where(
                        *identity_filters,
                        ProductBriefModel.retention_deadline > database_now,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                expired = session.scalar(
                    select(literal_column("1"))
                    .select_from(DurableOperationModel)
                    .join(ProductBriefModel, brief_join)
                    .where(
                        *identity_filters,
                        ProductBriefModel.retention_deadline <= database_now,
                    )
                    .limit(1)
                )
                if expired is not None:
                    raise ProductBriefRetentionExpiredError(
                        "ProductBrief operation context retention has expired"
                    )
                return None
        return ProductBriefOperationProjection(
            id=row["id"],
            state=OperationState(row["state"]),
            attempt_count=row["attempt_count"],
            max_attempts=row["max_attempts"],
            error_code=row["error_code"],
            error_category=row["error_category"],
            error_retryable=row["error_retryable"],
            version=row["version"],
        )
