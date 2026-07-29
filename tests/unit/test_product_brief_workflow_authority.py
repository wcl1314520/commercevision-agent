from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from commercevision_application.product_brief_authority import (
    ProductBriefWorkflowAuthorityState,
    ProductBriefWorkflowBindingIssue,
    assert_product_brief_workflow_authority,
    assert_product_brief_workflow_retention_active,
    evaluate_product_brief_workflow_authority,
)
from commercevision_domain import (
    ConcurrencyError,
    ProductBrief,
    ProductBriefRetentionExpiredError,
    RetentionClass,
    RetentionStatus,
    Workflow,
)

NOW = datetime(2026, 7, 29, 8, 0, tzinfo=UTC)
DEADLINE = NOW + timedelta(hours=72)
WORKSPACE_ID = "product-brief-authority"
PRODUCT_ID = "019b0000-0000-7000-8000-000000000002"


def _authority_pair() -> tuple[Workflow, ProductBrief]:
    workflow = Workflow.create(
        workspace_id=WORKSPACE_ID,
        created_by="brief-reviewer",
        workflow_type="COMMERCE_IMAGE_GENERATION",
        input_data={"schema_version": "1.0", "product_id": PRODUCT_ID},
        retention=DEADLINE - NOW,
        now=NOW,
    )
    product_brief = ProductBrief.create(
        workspace_id=WORKSPACE_ID,
        workflow_id=workflow.id,
        product_id=PRODUCT_ID,
        created_by="brief-reviewer",
        retention_class=RetentionClass.TASK,
        retention_deadline=workflow.expires_at,
        now=NOW,
    )
    return workflow, product_brief


def test_product_brief_workflow_authority_accepts_one_exact_active_boundary() -> None:
    workflow, product_brief = _authority_pair()

    authority = evaluate_product_brief_workflow_authority(
        workflow=workflow,
        product_brief=product_brief,
        now=NOW,
    )

    assert authority.state == ProductBriefWorkflowAuthorityState.ACTIVE
    assert authority.binding_issue is None


@pytest.mark.parametrize(
    ("workflow_mutation", "brief_mutation", "expected_issue"),
    [
        ({"workspace_id": "other-workspace"}, {}, ProductBriefWorkflowBindingIssue.WORKSPACE),
        (
            {"id": "019b0000-0000-7000-8000-000000000099"},
            {},
            ProductBriefWorkflowBindingIssue.WORKFLOW,
        ),
        (
            {"workflow_type": "FIXTURE_IMAGE_GENERATION"},
            {},
            ProductBriefWorkflowBindingIssue.WORKFLOW_TYPE,
        ),
        (
            {"input_data": {"schema_version": "1.0", "product_id": "other-product"}},
            {},
            ProductBriefWorkflowBindingIssue.PRODUCT,
        ),
        (
            {},
            {"retention_deadline": DEADLINE - timedelta(seconds=1)},
            ProductBriefWorkflowBindingIssue.RETENTION_DEADLINE,
        ),
    ],
)
def test_product_brief_workflow_authority_classifies_permanent_binding_mismatch(
    workflow_mutation: dict[str, object],
    brief_mutation: dict[str, object],
    expected_issue: ProductBriefWorkflowBindingIssue,
) -> None:
    workflow, product_brief = _authority_pair()

    authority = evaluate_product_brief_workflow_authority(
        workflow=replace(workflow, **workflow_mutation),
        product_brief=replace(product_brief, **brief_mutation),
        now=NOW,
    )

    assert authority.state == ProductBriefWorkflowAuthorityState.BINDING_MISMATCH
    assert authority.binding_issue == expected_issue
    with pytest.raises(ConcurrencyError, match="binding is inconsistent"):
        assert_product_brief_workflow_authority(
            workflow=replace(workflow, **workflow_mutation),
            product_brief=replace(product_brief, **brief_mutation),
            now=NOW,
        )


@pytest.mark.parametrize(
    ("retention_status", "observed_at"),
    [
        (RetentionStatus.EXPIRING, NOW),
        (RetentionStatus.DELETING, NOW),
        (RetentionStatus.EXPIRED, NOW),
        (RetentionStatus.ACTIVE, DEADLINE),
    ],
)
def test_product_brief_workflow_authority_classifies_revoked_or_elapsed_retention(
    retention_status: RetentionStatus,
    observed_at: datetime,
) -> None:
    workflow, product_brief = _authority_pair()
    workflow = replace(workflow, retention_status=retention_status)

    authority = evaluate_product_brief_workflow_authority(
        workflow=workflow,
        product_brief=product_brief,
        now=observed_at,
    )

    assert authority.state == ProductBriefWorkflowAuthorityState.EXPIRED
    assert authority.binding_issue is None
    with pytest.raises(ProductBriefRetentionExpiredError):
        assert_product_brief_workflow_authority(
            workflow=workflow,
            product_brief=product_brief,
            now=observed_at,
        )


def test_product_brief_workflow_authority_caps_legacy_overlong_workflow() -> None:
    workflow = Workflow.create(
        workspace_id=WORKSPACE_ID,
        created_by="brief-reviewer",
        workflow_type="COMMERCE_IMAGE_GENERATION",
        input_data={"schema_version": "1.0", "product_id": PRODUCT_ID},
        retention=timedelta(hours=168),
        now=NOW,
    )
    product_brief = ProductBrief.create(
        workspace_id=WORKSPACE_ID,
        workflow_id=workflow.id,
        product_id=PRODUCT_ID,
        created_by="brief-reviewer",
        retention_class=RetentionClass.TASK,
        retention_deadline=DEADLINE,
        now=NOW,
    )

    before_boundary = evaluate_product_brief_workflow_authority(
        workflow=workflow,
        product_brief=product_brief,
        now=DEADLINE - timedelta(microseconds=1),
    )
    at_boundary = evaluate_product_brief_workflow_authority(
        workflow=workflow,
        product_brief=product_brief,
        now=DEADLINE,
    )

    assert workflow.expires_at == NOW + timedelta(hours=168)
    assert before_boundary.state == ProductBriefWorkflowAuthorityState.ACTIVE
    assert at_boundary.state == ProductBriefWorkflowAuthorityState.EXPIRED
    assert at_boundary.binding_issue is None


def test_product_brief_command_rejects_legacy_workflow_after_task_boundary() -> None:
    workflow = Workflow.create(
        workspace_id=WORKSPACE_ID,
        created_by="brief-reviewer",
        workflow_type="COMMERCE_IMAGE_GENERATION",
        input_data={"schema_version": "1.0", "product_id": PRODUCT_ID},
        retention=timedelta(hours=168),
        now=NOW,
    )

    with pytest.raises(ProductBriefRetentionExpiredError):
        assert_product_brief_workflow_retention_active(
            workflow=workflow,
            now=DEADLINE,
        )
