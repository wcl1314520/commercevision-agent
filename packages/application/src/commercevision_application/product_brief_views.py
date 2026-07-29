"""Least-privilege ProductBrief browser projections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from commercevision_contracts.product_brief_views import (
    ProductBriefOperationErrorResponseV1,
    ProductBriefOperationStatusResponseV1,
    ProductBriefWorkflowContextResponseV1,
)
from commercevision_domain import NotFoundError, OperationState, WorkflowStatus

from .asset_registry_facts import canonicalize_resource_id


@dataclass(frozen=True, slots=True)
class ProductBriefAnalysisWorkflowProjection:
    id: str
    status: WorkflowStatus
    version: int
    retention_deadline: datetime


@dataclass(frozen=True, slots=True)
class ProductBriefWorkflowProjection:
    id: str
    status: WorkflowStatus
    version: int
    retention_deadline: datetime


@dataclass(frozen=True, slots=True)
class ProductBriefOperationProjection:
    id: str
    state: OperationState
    attempt_count: int
    max_attempts: int
    error_code: str | None
    error_category: str | None
    error_retryable: bool | None
    version: int


class ProductBriefViewQueryPort(Protocol):
    def get_analysis_workflow_context(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
    ) -> ProductBriefAnalysisWorkflowProjection | None: ...

    def get_workflow_context(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
        workflow_id: str,
    ) -> ProductBriefWorkflowProjection | None: ...

    def get_operation_status(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
        operation_id: str,
    ) -> ProductBriefOperationProjection | None: ...


class ProductBriefViewApplicationService:
    def __init__(self, *, queries: ProductBriefViewQueryPort) -> None:
        self._queries = queries

    def analysis_workflow_context(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
    ) -> ProductBriefWorkflowContextResponseV1:
        projection = self._queries.get_analysis_workflow_context(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
        )
        if projection is None:
            raise NotFoundError(f"workflow {workflow_id} was not found")
        return ProductBriefWorkflowContextResponseV1(
            id=projection.id,
            status=projection.status,
            version=projection.version,
            retention_deadline=projection.retention_deadline,
        )

    def workflow_context(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
        workflow_id: str,
    ) -> ProductBriefWorkflowContextResponseV1:
        product_brief_id = canonicalize_resource_id(
            product_brief_id,
            resource="ProductBrief",
        )
        projection = self._queries.get_workflow_context(
            workspace_id=workspace_id,
            product_brief_id=product_brief_id,
            workflow_id=workflow_id,
        )
        if projection is None:
            raise NotFoundError(f"workflow {workflow_id} was not found")
        return ProductBriefWorkflowContextResponseV1(
            id=projection.id,
            status=projection.status,
            version=projection.version,
            retention_deadline=projection.retention_deadline,
        )

    def operation_status(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
        operation_id: str,
    ) -> ProductBriefOperationStatusResponseV1:
        product_brief_id = canonicalize_resource_id(
            product_brief_id,
            resource="ProductBrief",
        )
        projection = self._queries.get_operation_status(
            workspace_id=workspace_id,
            product_brief_id=product_brief_id,
            operation_id=operation_id,
        )
        if projection is None:
            raise NotFoundError(f"operation {operation_id} was not found")
        error = None
        if projection.error_code is not None:
            if projection.error_category is None or projection.error_retryable is None:
                raise RuntimeError("ProductBrief operation error projection is incomplete")
            error = ProductBriefOperationErrorResponseV1(
                code=projection.error_code,
                category=projection.error_category,
                message=(
                    "Product analysis is temporarily unavailable and may be retried."
                    if projection.error_retryable
                    else (
                        "Product analysis could not be completed. Review the ProductBrief status."
                    )
                ),
                retryable=projection.error_retryable,
            )
        return ProductBriefOperationStatusResponseV1(
            id=projection.id,
            state=projection.state,
            attempt_count=projection.attempt_count,
            max_attempts=projection.max_attempts,
            error=error,
            version=projection.version,
        )
