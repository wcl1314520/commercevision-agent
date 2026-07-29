"""Canonical ProductBrief-to-Workflow authority evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from commercevision_domain import (
    ConcurrencyError,
    ProductBrief,
    ProductBriefRetentionExpiredError,
    RetentionStatus,
    Workflow,
    canonical_task_retention_deadline,
)


class ProductBriefWorkflowAuthorityState(StrEnum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    BINDING_MISMATCH = "BINDING_MISMATCH"


class ProductBriefWorkflowBindingIssue(StrEnum):
    WORKSPACE = "WORKSPACE"
    WORKFLOW = "WORKFLOW"
    WORKFLOW_TYPE = "WORKFLOW_TYPE"
    PRODUCT = "PRODUCT"
    RETENTION_DEADLINE = "RETENTION_DEADLINE"


@dataclass(frozen=True, slots=True)
class ProductBriefWorkflowAuthority:
    state: ProductBriefWorkflowAuthorityState
    binding_issue: ProductBriefWorkflowBindingIssue | None = None


def has_active_product_brief_workflow_retention(
    *,
    workflow: Workflow,
    now: datetime,
) -> bool:
    retention_deadline = canonical_task_retention_deadline(
        created_at=workflow.created_at,
        expires_at=workflow.expires_at,
    )
    return workflow.retention_status == RetentionStatus.ACTIVE and now < retention_deadline


def evaluate_product_brief_workflow_authority(
    *,
    workflow: Workflow,
    product_brief: ProductBrief,
    now: datetime,
) -> ProductBriefWorkflowAuthority:
    """Evaluate immutable binding before the shared retention boundary."""

    if workflow.workspace_id != product_brief.workspace_id:
        return ProductBriefWorkflowAuthority(
            ProductBriefWorkflowAuthorityState.BINDING_MISMATCH,
            ProductBriefWorkflowBindingIssue.WORKSPACE,
        )
    if workflow.id != product_brief.workflow_id:
        return ProductBriefWorkflowAuthority(
            ProductBriefWorkflowAuthorityState.BINDING_MISMATCH,
            ProductBriefWorkflowBindingIssue.WORKFLOW,
        )
    if workflow.workflow_type != "COMMERCE_IMAGE_GENERATION":
        return ProductBriefWorkflowAuthority(
            ProductBriefWorkflowAuthorityState.BINDING_MISMATCH,
            ProductBriefWorkflowBindingIssue.WORKFLOW_TYPE,
        )
    if workflow.input_data.get("product_id") != product_brief.product_id:
        return ProductBriefWorkflowAuthority(
            ProductBriefWorkflowAuthorityState.BINDING_MISMATCH,
            ProductBriefWorkflowBindingIssue.PRODUCT,
        )
    retention_deadline = canonical_task_retention_deadline(
        created_at=workflow.created_at,
        expires_at=workflow.expires_at,
    )
    if product_brief.retention_deadline != retention_deadline:
        return ProductBriefWorkflowAuthority(
            ProductBriefWorkflowAuthorityState.BINDING_MISMATCH,
            ProductBriefWorkflowBindingIssue.RETENTION_DEADLINE,
        )
    if not has_active_product_brief_workflow_retention(
        workflow=workflow,
        now=now,
    ) or (product_brief.retention_deadline is None or now >= product_brief.retention_deadline):
        return ProductBriefWorkflowAuthority(ProductBriefWorkflowAuthorityState.EXPIRED)
    return ProductBriefWorkflowAuthority(ProductBriefWorkflowAuthorityState.ACTIVE)


def assert_product_brief_workflow_authority(
    *,
    workflow: Workflow,
    product_brief: ProductBrief,
    now: datetime,
) -> None:
    authority = evaluate_product_brief_workflow_authority(
        workflow=workflow,
        product_brief=product_brief,
        now=now,
    )
    if authority.state == ProductBriefWorkflowAuthorityState.BINDING_MISMATCH:
        raise ConcurrencyError("ProductBrief Workflow binding is inconsistent")
    if authority.state == ProductBriefWorkflowAuthorityState.EXPIRED:
        raise ProductBriefRetentionExpiredError("ProductBrief retention has expired")


def assert_product_brief_workflow_retention_active(
    *,
    workflow: Workflow,
    now: datetime,
) -> None:
    if not has_active_product_brief_workflow_retention(
        workflow=workflow,
        now=now,
    ):
        raise ProductBriefRetentionExpiredError("ProductBrief retention has expired")
