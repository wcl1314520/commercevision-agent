"""ProductBrief commands, projections, and durable Vision execution."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from commercevision_contracts import Settings, validate_canonical_endpoint_host
from commercevision_contracts.events import (
    EventType,
    ProductBriefAwaitingConfirmationPayload,
    ProductBriefConfirmedPayload,
    ProductBriefRequestedPayload,
    WorkflowResumeRequestedPayload,
    WorkflowRunRequestedPayload,
)
from commercevision_contracts.object_storage import (
    ObjectReference,
    ObjectStorage,
    TemporaryReadRequest,
)
from commercevision_contracts.product_briefs import (
    ProductBriefAnalysisAcceptedV1,
    ProductBriefAnalysisRequestV1,
    ProductBriefConfirmationRequestV1,
    ProductBriefConfirmationResponseV1,
    ProductBriefEvidenceOutput,
    ProductBriefEvidenceResponseV1,
    ProductBriefFieldResponseV1,
    ProductBriefProviderCallResponseV1,
    ProductBriefResponseV1,
    ProductBriefRevisionRequestV1,
    ProductBriefVersionListResponseV1,
    ProductBriefVersionResponseV1,
    ProductBriefVersionSummaryResponseV1,
    ProviderArtifactIntegrityError,
    ProviderArtifactKind,
    ProviderArtifactReference,
    ProviderArtifactState,
    ProviderArtifactUnavailableError,
    ProviderArtifactWrite,
    ProviderArtifactWriteOutcomeUnknownError,
    VisionAnalysisRequest,
    VisionAnalyzer,
    VisionCallLifecycle,
    VisionImageInput,
    VisionProviderCall,
    VisionProviderOutcome,
    VisionProviderStatus,
)
from commercevision_contracts.vision_configuration import (
    alibaba_vision_configuration_snapshot_sha256,
    deterministic_vision_configuration_snapshot_sha256,
)
from commercevision_domain import (
    Approval,
    ApprovalDecision,
    ApprovalType,
    AssetKind,
    AssetObjectState,
    ConcurrencyError,
    DurableOperation,
    InvalidTransitionError,
    NormalizedOperationError,
    NotFoundError,
    OperationKind,
    OperationState,
    ProductBrief,
    ProductBriefCategory,
    ProductBriefEvidence,
    ProductBriefEvidenceKind,
    ProductBriefField,
    ProductBriefFieldSource,
    ProductBriefRetentionExpiredError,
    ProductBriefReviewPolicy,
    ProductBriefState,
    ProductBriefVersion,
    ProductBriefVersionSource,
    ProviderPolicyDeniedError,
    ReconciliationOutcome,
    RetentionClass,
    RightsDeniedError,
    StoragePreconditionError,
    StorageUnavailableError,
    UploadObjectMissingError,
    Workflow,
    WorkflowStatus,
    canonical_task_retention_deadline,
    evaluate_current_usability,
    new_uuid7,
    validate_workspace_id,
)
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from commercevision_domain.product_briefs.schemas import (
    CATEGORY_SCHEMA_VERSIONS,
    COMMON_SCHEMA_VERSION,
)
from commercevision_domain.workflow.errors import (
    ApprovalConflictError,
    IdempotencyConflictError,
)

from .asset_idempotency import canonical_hash, key_hash
from .asset_registry_facts import canonicalize_resource_id
from .operations import (
    OperationExecutionFailure,
    OperationExecutionRequest,
    OperationExecutionResult,
    OperationHumanWaitRequired,
    OperationReconciliationResult,
    UnknownOperationOutcome,
)
from .product_brief_artifacts import (
    ProductBriefProviderArtifactReconciler,
    ProductBriefProviderArtifactService,
    ProviderArtifactOwner,
)
from .product_brief_authority import (
    ProductBriefWorkflowAuthorityState,
    assert_product_brief_workflow_authority,
    assert_product_brief_workflow_retention_active,
    evaluate_product_brief_workflow_authority,
)
from .product_brief_observability import (
    NullProductBriefObserver,
    ProductBriefObserver,
)
from .product_brief_ports import (
    ProductBriefAnalysisRecord,
    ProductBriefConfirmation,
    ProductBriefSourceAsset,
    ProductBriefUnitOfWorkFactory,
    ProductBriefUnitOfWorkPort,
    StoredProductBriefProviderSummary,
    StoredProductBriefVersion,
    StoredProductBriefVersionSummary,
    StoredProviderArtifact,
    StoredProviderAttempt,
    StoredProviderCall,
)
from .product_brief_transfer import (
    VISION_ANALYSIS_PURPOSE,
    VisionDataTransferDenied,
    VisionDataTransferPolicy,
)


@dataclass(frozen=True, slots=True)
class ProductBriefPolicy:
    provider: str
    endpoint_region: str
    endpoint_host: str
    requested_model: str
    submitted_model_snapshot: str
    configuration_snapshot_sha256: str
    prompt_version: str
    review_policy_version: str
    confidence_threshold: Decimal
    mandatory_review_paths: frozenset[str]
    sensitive_claim_paths: frozenset[str]
    operation_max_attempts: int
    operation_max_reconciliation_attempts: int
    temporary_reference_lifetime: timedelta

    def __post_init__(self) -> None:
        if any(
            not value
            for value in (
                self.provider,
                self.endpoint_region,
                self.endpoint_host,
                self.requested_model,
                self.submitted_model_snapshot,
                self.configuration_snapshot_sha256,
                self.prompt_version,
                self.review_policy_version,
            )
        ):
            raise ValueError("ProductBrief provider policy identity is incomplete")
        if len(self.configuration_snapshot_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.configuration_snapshot_sha256
        ):
            raise ValueError("ProductBrief provider configuration snapshot is invalid")
        if self.operation_max_attempts < 1:
            raise ValueError("ProductBrief operation attempts must be positive")
        if self.operation_max_reconciliation_attempts < 1:
            raise ValueError("ProductBrief reconciliation attempts must be positive")
        validate_canonical_endpoint_host(self.endpoint_host)
        _ = self.review_policy
        if not timedelta(seconds=10) <= self.temporary_reference_lifetime <= timedelta(minutes=5):
            raise ValueError("ProductBrief temporary reference lifetime is invalid")

    @property
    def review_policy(self) -> ProductBriefReviewPolicy:
        return ProductBriefReviewPolicy(
            policy_version=self.review_policy_version,
            confidence_threshold=self.confidence_threshold,
            mandatory_review_paths=self.mandatory_review_paths,
            sensitive_claim_paths=self.sensitive_claim_paths,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> ProductBriefPolicy:
        if settings.vision_adapter == "deterministic":
            provider = "deterministic-vision"
            endpoint_region = "local"
            endpoint_host = "deterministic.invalid"
            requested_model = "deterministic-vision-v1"
            submitted_model_snapshot = "deterministic-vision-v1"
            configuration_snapshot_sha256 = deterministic_vision_configuration_snapshot_sha256(
                maximum_output_tokens=settings.alibaba_vision_maximum_output_tokens,
                product_facts_maximum_bytes=(settings.vision_product_facts_maximum_bytes),
                product_facts_maximum_depth=(settings.vision_product_facts_maximum_depth),
                product_facts_maximum_nodes=(settings.vision_product_facts_maximum_nodes),
                product_facts_maximum_string_bytes=(
                    settings.vision_product_facts_maximum_string_bytes
                ),
                prompt_version=settings.vision_prompt_version,
            )
        else:
            provider = "alibaba-model-studio"
            endpoint_region = settings.alibaba_vision_endpoint_region
            endpoint_host = settings.alibaba_vision_endpoint_host
            requested_model = settings.alibaba_vision_model
            submitted_model_snapshot = settings.alibaba_vision_model_snapshot
            configuration_snapshot_sha256 = alibaba_vision_configuration_snapshot_sha256(
                adapter_version=settings.alibaba_vision_adapter_version,
                configured_snapshot=settings.alibaba_vision_model_snapshot,
                connect_timeout_seconds=(settings.alibaba_vision_connect_timeout_seconds),
                end_to_end_timeout_seconds=(settings.alibaba_vision_end_to_end_timeout_seconds),
                endpoint=settings.alibaba_vision_endpoint,
                endpoint_region=settings.alibaba_vision_endpoint_region,
                maximum_concurrency=settings.alibaba_vision_maximum_concurrency,
                maximum_output_tokens=settings.alibaba_vision_maximum_output_tokens,
                maximum_repair_attempts=(settings.alibaba_vision_maximum_repair_attempts),
                maximum_response_bytes=settings.alibaba_vision_maximum_response_bytes,
                product_facts_maximum_bytes=(settings.vision_product_facts_maximum_bytes),
                product_facts_maximum_depth=(settings.vision_product_facts_maximum_depth),
                product_facts_maximum_nodes=(settings.vision_product_facts_maximum_nodes),
                product_facts_maximum_string_bytes=(
                    settings.vision_product_facts_maximum_string_bytes
                ),
                prompt_version=settings.vision_prompt_version,
                read_timeout_seconds=settings.alibaba_vision_read_timeout_seconds,
                requested_model=settings.alibaba_vision_model,
            )
        return cls(
            provider=provider,
            endpoint_region=endpoint_region,
            endpoint_host=endpoint_host,
            requested_model=requested_model,
            submitted_model_snapshot=submitted_model_snapshot,
            configuration_snapshot_sha256=configuration_snapshot_sha256,
            prompt_version=settings.vision_prompt_version,
            review_policy_version=settings.product_brief_review_policy_version,
            confidence_threshold=settings.product_brief_confidence_threshold,
            mandatory_review_paths=frozenset(settings.product_brief_mandatory_review_paths),
            sensitive_claim_paths=frozenset(settings.product_brief_sensitive_claim_paths),
            operation_max_attempts=settings.product_brief_analysis_max_attempts,
            operation_max_reconciliation_attempts=(
                settings.product_brief_analysis_max_reconciliation_attempts
            ),
            temporary_reference_lifetime=timedelta(
                seconds=settings.vision_temporary_reference_lifetime_seconds
            ),
        )


def _category_for_product(category_code: str) -> ProductBriefCategory:
    namespace = re.split(r"[\s._/-]+", category_code.strip(), maxsplit=1)[0].upper()
    if namespace == "BEAUTY":
        return ProductBriefCategory.BEAUTY
    if namespace in {"AUTOMOTIVE", "AUTO"}:
        return ProductBriefCategory.AUTOMOTIVE
    raise ValueError("ProductBrief supports BEAUTY and AUTOMOTIVE product categories")


def _operation_input_hash(
    *,
    request: ProductBriefAnalysisRequestV1,
    category: ProductBriefCategory,
    product_version: int,
    source_hashes: tuple[tuple[str, str, str], ...],
    policy: ProductBriefPolicy,
    transfer_policy: VisionDataTransferPolicy,
) -> str:
    return canonical_hash(
        {
            "asset_versions": [
                {
                    "asset_version_id": asset_version_id,
                    "content_sha256": content_sha256,
                    "provider_version_id_sha256": hashlib.sha256(
                        provider_version_id.encode()
                    ).hexdigest(),
                }
                for asset_version_id, content_sha256, provider_version_id in source_hashes
            ],
            "category": category.value,
            "endpoint_host": policy.endpoint_host,
            "endpoint_region": policy.endpoint_region,
            "expected_workflow_version": request.expected_workflow_version,
            "product_id": request.product_id,
            "product_version": product_version,
            "prompt_version": policy.prompt_version,
            "provider": policy.provider,
            "provider_configuration_snapshot_sha256": (policy.configuration_snapshot_sha256),
            "requested_model": policy.requested_model,
            "submitted_model_snapshot": policy.submitted_model_snapshot,
            "review_policy_snapshot_sha256": policy.review_policy.snapshot_sha256,
            "review_policy_version": policy.review_policy.policy_version,
            "transfer_policy_snapshot_sha256": transfer_policy.snapshot_sha256,
            "transfer_policy_version": transfer_policy.version,
            "workflow_id": request.workflow_id,
        }
    )


def _frozen_review_policy(
    analysis: ProductBriefAnalysisRecord,
) -> ProductBriefReviewPolicy:
    policy = ProductBriefReviewPolicy(
        policy_version=analysis.review_policy_version,
        confidence_threshold=analysis.review_confidence_threshold,
        mandatory_review_paths=frozenset(analysis.review_mandatory_paths),
        sensitive_claim_paths=frozenset(analysis.review_sensitive_claim_paths),
    )
    if policy.snapshot_sha256 != analysis.review_policy_snapshot_sha256:
        raise ConcurrencyError("ProductBrief review policy snapshot is inconsistent")
    return policy


class ProductBriefApplicationService:
    def __init__(
        self,
        *,
        uow_factory: ProductBriefUnitOfWorkFactory,
        policy: ProductBriefPolicy,
        transfer_policy: VisionDataTransferPolicy,
        observer: ProductBriefObserver | None = None,
        clock: Any | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._policy = policy
        self._transfer_policy = transfer_policy
        self._observer = observer or NullProductBriefObserver()
        self._clock = clock or (lambda: datetime.now(UTC))

    def request_analysis(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        request: ProductBriefAnalysisRequestV1,
        idempotency_key: str,
        trace_id: str,
    ) -> ProductBriefAnalysisAcceptedV1:
        validate_workspace_id(workspace_id)
        scope = "product-brief:analysis:" + canonical_hash(
            {
                "product_id": request.product_id,
                "workflow_id": request.workflow_id,
                "workspace_id": workspace_id,
            }
        )
        key_digest = key_hash(idempotency_key)
        request_digest = canonical_hash(
            {
                "actor_id": actor_id,
                **request.model_dump(mode="json"),
            }
        )
        with self._uow_factory() as uow:
            workflow = uow.workflows.get(
                request.workflow_id,
                workspace_id=workspace_id,
                for_update=True,
            )
            if workflow is None:
                raise NotFoundError(f"workflow {request.workflow_id} was not found")
            if workflow.workflow_type != "COMMERCE_IMAGE_GENERATION":
                raise InvalidTransitionError(
                    "ProductBrief analysis requires a COMMERCE_IMAGE_GENERATION workflow"
                )
            if workflow.input_data.get("product_id") != request.product_id:
                raise InvalidTransitionError("ProductBrief product must match the workflow product")
            now = uow.database_now()
            assert_product_brief_workflow_retention_active(
                workflow=workflow,
                now=now,
            )
            workflow_retention_deadline = canonical_task_retention_deadline(
                created_at=workflow.created_at,
                expires_at=workflow.expires_at,
            )
            idempotency = uow.idempotency.claim(
                scope=scope,
                key_hash=key_digest,
                request_hash=request_digest,
                expires_at=workflow_retention_deadline,
            )
            if idempotency.request_hash != request_digest:
                raise IdempotencyConflictError(
                    "idempotency key was already used with a different request"
                )
            if idempotency.status == "COMPLETED":
                if idempotency.resource_type != "product-brief-analysis" or not isinstance(
                    idempotency.response_data, dict
                ):
                    raise ConcurrencyError("ProductBrief idempotency response is incomplete")
                product_brief = uow.product_briefs.get(
                    workspace_id=workspace_id,
                    product_brief_id=idempotency.resource_id,
                    for_update=True,
                )
                if product_brief is None:
                    raise ConcurrencyError("ProductBrief idempotency resource is unavailable")
                assert_product_brief_workflow_authority(
                    workflow=workflow,
                    product_brief=product_brief,
                    now=now,
                )
                response = ProductBriefAnalysisAcceptedV1.model_validate(idempotency.response_data)
                if response.product_brief.id != idempotency.resource_id:
                    raise ConcurrencyError("ProductBrief idempotency response is inconsistent")
                return response
            if idempotency.status != "PENDING":
                raise ConcurrencyError("ProductBrief idempotency record has an unsupported status")
            workflow.assert_version(request.expected_workflow_version)
            product = uow.products.get(
                workspace_id=workspace_id,
                product_id=request.product_id,
            )
            if product is None:
                raise NotFoundError(f"product {request.product_id} was not found")
            category = _category_for_product(product.category_code)
            existing_product_brief = uow.product_briefs.get_by_workflow_product(
                workspace_id=workspace_id,
                workflow_id=workflow.id,
                product_id=product.id,
                for_update=True,
            )
            if existing_product_brief is None:
                if workflow.status != WorkflowStatus.UNDERSTANDING:
                    raise InvalidTransitionError(
                        "initial ProductBrief analysis requires an UNDERSTANDING workflow"
                    )
            else:
                assert_product_brief_workflow_authority(
                    workflow=workflow,
                    product_brief=existing_product_brief,
                    now=now,
                )
                if (
                    workflow.status != WorkflowStatus.RETRIEVING
                    or existing_product_brief.state != ProductBriefState.CONFIRMED
                ):
                    raise InvalidTransitionError(
                        "ProductBrief reanalysis requires an exact confirmed brief "
                        "and a RETRIEVING workflow"
                    )
                existing_product_brief.reopen_for_analysis(
                    expected_version=existing_product_brief.version,
                    now=now,
                )
                workflow.transition(
                    WorkflowStatus.UNDERSTANDING,
                    current_node="understand_product",
                    now=now,
                )

            sources: list[ProductBriefSourceAsset] = []
            source_hashes: list[tuple[str, str, str]] = []
            for ordinal, asset_version_id in enumerate(request.asset_version_ids):
                asset_version = uow.assets.get_version(
                    workspace_id=workspace_id,
                    asset_version_id=asset_version_id,
                )
                if asset_version is None:
                    raise NotFoundError(f"Asset Version {asset_version_id} was not found")
                asset = uow.assets.get(
                    workspace_id=workspace_id,
                    asset_id=asset_version.asset_id,
                )
                if asset is None:
                    raise NotFoundError(f"Asset {asset_version.asset_id} was not found")
                if asset.kind != AssetKind.IMAGE or asset.product_id != product.id:
                    raise RightsDeniedError(
                        "ProductBrief sources must be product-bound image Assets"
                    )
                if (
                    asset.retention_class == RetentionClass.TASK
                    and asset.workflow_id != workflow.id
                ):
                    raise RightsDeniedError(
                        "Task ProductBrief sources must belong to the same workflow"
                    )
                self._authorize_current_use(
                    uow=uow,
                    workspace_id=workspace_id,
                    asset_id=asset.id,
                    asset_version_id=asset_version.id,
                )
                self._authorize_transfer(
                    workspace_id=workspace_id,
                    asset_version_id=asset_version.id,
                    retention_class=asset.retention_class,
                    persisted_version=self._transfer_policy.version,
                    persisted_snapshot=self._transfer_policy.snapshot_sha256,
                )
                object_fact = uow.assets.get_object(
                    workspace_id=workspace_id,
                    asset_version_id=asset_version.id,
                )
                if (
                    object_fact is None
                    or object_fact.state != AssetObjectState.CONTROLLED
                    or object_fact.provider_version_id is None
                ):
                    raise RightsDeniedError(
                        "ProductBrief source has no controlled immutable object"
                    )
                sources.append(
                    ProductBriefSourceAsset(
                        asset_id=asset.id,
                        asset_version_id=asset_version.id,
                        asset_object_id=object_fact.id,
                        ordinal=ordinal,
                    )
                )
                source_hashes.append(
                    (
                        asset_version.id,
                        asset_version.sha256,
                        object_fact.provider_version_id,
                    )
                )

            product_brief = (
                existing_product_brief
                if existing_product_brief is not None
                else ProductBrief.create(
                    workspace_id=workspace_id,
                    workflow_id=workflow.id,
                    product_id=product.id,
                    created_by=actor_id,
                    retention_class=RetentionClass.TASK,
                    retention_deadline=workflow_retention_deadline,
                    now=now,
                )
            )
            retention_deadline = product_brief.retention_deadline
            if retention_deadline is None:
                raise ConcurrencyError("Task ProductBrief retention boundary is unavailable")
            analysis_id = new_uuid7()
            input_hash = _operation_input_hash(
                request=request,
                category=category,
                product_version=product.version,
                source_hashes=tuple(source_hashes),
                policy=self._policy,
                transfer_policy=self._transfer_policy,
            )
            operation = DurableOperation.create(
                workspace_id=workspace_id,
                kind=OperationKind.PRODUCT_BRIEF_ANALYSIS,
                target_type="product_brief",
                target_id=product_brief.id,
                target_version=product_brief.version,
                input_hash=input_hash,
                input_ref=f"mysql://product-brief-analysis/{analysis_id}",
                max_attempts=self._policy.operation_max_attempts,
                max_reconciliation_attempts=(self._policy.operation_max_reconciliation_attempts),
                execution_max_elapsed=retention_deadline - now,
                now=now,
            )
            analysis = ProductBriefAnalysisRecord(
                id=analysis_id,
                workspace_id=workspace_id,
                product_brief_id=product_brief.id,
                operation_id=operation.id,
                category=category,
                expected_workflow_version=workflow.version,
                product_catalog_version=product.version,
                provider=self._policy.provider,
                endpoint_region=self._policy.endpoint_region,
                endpoint_host=self._policy.endpoint_host,
                requested_model=self._policy.requested_model,
                submitted_model_snapshot=self._policy.submitted_model_snapshot,
                provider_configuration_snapshot_sha256=(self._policy.configuration_snapshot_sha256),
                prompt_version=self._policy.prompt_version,
                review_policy_version=self._policy.review_policy_version,
                review_confidence_threshold=self._policy.confidence_threshold,
                review_mandatory_paths=tuple(sorted(self._policy.mandatory_review_paths)),
                review_sensitive_claim_paths=tuple(sorted(self._policy.sensitive_claim_paths)),
                review_policy_snapshot_sha256=(self._policy.review_policy.snapshot_sha256),
                transfer_policy_version=self._transfer_policy.version,
                transfer_policy_snapshot_sha256=self._transfer_policy.snapshot_sha256,
                created_by=actor_id,
                trace_id=trace_id,
                retention_class=product_brief.retention_class,
                retention_deadline=product_brief.retention_deadline,
                created_at=now,
                sources=tuple(sources),
            )
            uow.operations.add(operation)
            if existing_product_brief is None:
                uow.product_briefs.add(product_brief, operation_id=operation.id)
            else:
                uow.product_briefs.save(
                    product_brief,
                    operation_id=operation.id,
                )
                uow.workflows.save(workflow)
            uow.product_brief_analyses.add_analysis(analysis)
            uow.outbox.add(
                _product_brief_event(
                    event_type=EventType.PRODUCT_BRIEF_REQUESTED,
                    workspace_id=workspace_id,
                    product_brief=product_brief,
                    trace_id=trace_id,
                    payload=ProductBriefRequestedPayload(
                        workspace_id=workspace_id,
                        product_brief_id=product_brief.id,
                        product_brief_version=product_brief.version,
                        workflow_id=workflow.id,
                        product_id=product.id,
                        operation_id=operation.id,
                    ).model_dump(mode="json"),
                    now=now,
                )
            )
            response = ProductBriefAnalysisAcceptedV1(
                product_brief=self._response(
                    uow=uow,
                    product_brief=product_brief,
                    operation_id=operation.id,
                ),
                operation_id=operation.id,
                operation_state=operation.state.value,
            )
            uow.idempotency.complete(
                scope=scope,
                key_hash=key_digest,
                request_hash=request_digest,
                resource_type="product-brief-analysis",
                resource_id=product_brief.id,
                response_data=response.model_dump(mode="json"),
            )
            _audit(
                uow=uow,
                workspace_id=workspace_id,
                actor_id=actor_id,
                trace_id=trace_id,
                action="product_brief.analysis.requested",
                product_brief_id=product_brief.id,
                metadata={
                    "operation_id": operation.id,
                    "reanalysis": existing_product_brief is not None,
                    "source_asset_version_ids": list(request.asset_version_ids),
                    "transfer_policy_version": self._transfer_policy.version,
                },
                now=now,
                expires_at=retention_deadline,
            )
            uow.commit_before_retention_deadline(
                workspace_id=workspace_id,
                product_brief_id=product_brief.id,
                retention_deadline=retention_deadline,
                clock=self._clock,
            )
            return response

    def get(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
    ) -> ProductBriefResponseV1:
        product_brief_id = canonicalize_resource_id(
            product_brief_id,
            resource="ProductBrief",
        )
        with self._uow_factory() as uow:
            product_brief = uow.product_briefs.get(
                workspace_id=workspace_id,
                product_brief_id=product_brief_id,
            )
            if product_brief is None:
                raise NotFoundError(f"ProductBrief {product_brief_id} was not found")
            self._assert_retention_active(
                product_brief,
                now=uow.database_now(),
            )
            analysis = uow.product_brief_analyses.get_analysis_by_operation(
                workspace_id=workspace_id,
                operation_id=self._operation_id(uow, product_brief),
            )
            assert analysis is not None
            return self._response(
                uow=uow,
                product_brief=product_brief,
                operation_id=analysis.operation_id,
            )

    def list_versions(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
        limit: int,
        cursor: int | None,
    ) -> ProductBriefVersionListResponseV1:
        product_brief_id = canonicalize_resource_id(
            product_brief_id,
            resource="ProductBrief",
        )
        with self._uow_factory() as uow:
            product_brief = uow.product_briefs.get(
                workspace_id=workspace_id,
                product_brief_id=product_brief_id,
            )
            if product_brief is None:
                raise NotFoundError(f"ProductBrief {product_brief_id} was not found")
            self._assert_retention_active(
                product_brief,
                now=uow.database_now(),
            )
            page = uow.product_briefs.list_versions(
                workspace_id=workspace_id,
                product_brief_id=product_brief_id,
                limit=limit,
                cursor=cursor,
            )
            return ProductBriefVersionListResponseV1(
                items=tuple(
                    self._version_summary_response(
                        product_brief=product_brief,
                        stored=value,
                        provider_summary=(
                            page.provider_summaries_by_call_id.get(value.provider_call_id)
                            if value.provider_call_id is not None
                            else None
                        ),
                    )
                    for value in page.items
                ),
                next_cursor=page.next_cursor,
            )

    def revise(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
        actor_id: str,
        request: ProductBriefRevisionRequestV1,
        idempotency_key: str,
        trace_id: str,
    ) -> ProductBriefResponseV1:
        product_brief_id = canonicalize_resource_id(
            product_brief_id,
            resource="ProductBrief",
        )
        scope = f"product-brief:revision:{product_brief_id}"
        key_digest = key_hash(idempotency_key)
        request_digest = canonical_hash({"actor_id": actor_id, **request.model_dump(mode="json")})
        with self._uow_factory() as uow:
            identity = uow.product_briefs.get(
                workspace_id=workspace_id,
                product_brief_id=product_brief_id,
            )
            if identity is None:
                raise NotFoundError(f"ProductBrief {product_brief_id} was not found")
            workflow = uow.workflows.get(
                identity.workflow_id,
                workspace_id=workspace_id,
                for_update=True,
            )
            product_brief = uow.product_briefs.get(
                workspace_id=workspace_id,
                product_brief_id=product_brief_id,
                for_update=True,
            )
            if (
                workflow is None
                or product_brief is None
                or product_brief.workflow_id != workflow.id
            ):
                raise ConcurrencyError("ProductBrief durable workflow state is incomplete")
            now = uow.database_now()
            assert_product_brief_workflow_authority(
                workflow=workflow,
                product_brief=product_brief,
                now=now,
            )
            self._assert_retention_active(product_brief, now=now)
            assert product_brief.retention_deadline is not None
            idempotency = uow.idempotency.claim(
                scope=scope,
                key_hash=key_digest,
                request_hash=request_digest,
                expires_at=product_brief.retention_deadline,
            )
            if idempotency.request_hash != request_digest:
                raise IdempotencyConflictError(
                    "idempotency key was already used with a different request"
                )
            if idempotency.status == "COMPLETED":
                if idempotency.resource_type != "product-brief-revision" or not isinstance(
                    idempotency.response_data, dict
                ):
                    raise ConcurrencyError(
                        "ProductBrief revision idempotency response is incomplete"
                    )
                return ProductBriefResponseV1.model_validate(idempotency.response_data)
            if idempotency.status != "PENDING":
                raise ConcurrencyError("ProductBrief idempotency record has an unsupported status")
            product_brief.assert_version(request.expected_product_brief_version)
            reopening_confirmed = product_brief.state == ProductBriefState.CONFIRMED
            if (
                product_brief.current_version_id != request.base_version_id
                or product_brief.state
                not in {
                    ProductBriefState.AWAITING_CONFIRMATION,
                    ProductBriefState.CONFIRMED,
                }
            ):
                raise ConcurrencyError(
                    "revision must target the exact current awaiting or confirmed "
                    "ProductBrief version"
                )
            expected_workflow_state = (
                WorkflowStatus.RETRIEVING
                if reopening_confirmed
                else WorkflowStatus.AWAITING_PRODUCT_CONFIRMATION
            )
            if workflow.status != expected_workflow_state:
                raise InvalidTransitionError(
                    "ProductBrief revision is inconsistent with the durable workflow state"
                )
            previous_operation_id = self._operation_id(uow, product_brief)
            base = uow.product_briefs.get_version(
                workspace_id=workspace_id,
                product_brief_version_id=request.base_version_id,
            )
            if base is None:
                raise NotFoundError(f"ProductBrief Version {request.base_version_id} was not found")
            analysis = uow.product_brief_analyses.get_analysis_by_operation(
                workspace_id=workspace_id,
                operation_id=previous_operation_id,
            )
            if analysis is None:
                raise ConcurrencyError("ProductBrief analysis identity is missing")
            review_policy = _frozen_review_policy(analysis)
            source_ids = {source.asset_version_id for source in analysis.sources}
            base_fields = {field.path: field for field in base.version.fields}
            requested_fields = tuple(
                ProductBriefField.create(
                    path=requested_field.path,
                    value=requested_field.value.model_dump(mode="json"),
                    confidence=requested_field.confidence,
                    source=ProductBriefFieldSource.HUMAN,
                    conflict=requested_field.conflict,
                    review_required=requested_field.review_required,
                    sensitive=requested_field.sensitive,
                    evidence=tuple(
                        ProductBriefEvidence.create(
                            source_asset_version_id=evidence.source_asset_version_id,
                            kind=evidence.kind,
                            reference=evidence.reference,
                            region=evidence.region,
                            excerpt_sha256=evidence.excerpt_sha256,
                        )
                        for evidence in requested_field.evidence
                    ),
                )
                for requested_field in request.fields
            )
            risk_checked_fields = review_policy.enforce_risk_floor(requested_fields)
            revised_fields: list[ProductBriefField] = []
            changed_field_paths: list[str] = []
            for candidate in risk_checked_fields:
                base_field = base_fields.get(candidate.path)
                if base_field is not None and _field_revision_signature(
                    candidate
                ) == _field_revision_signature(base_field):
                    candidate = replace(candidate, source=base_field.source)
                else:
                    changed_field_paths.append(candidate.path)
                revised_fields.append(candidate)
            fields = tuple(revised_fields)
            if not changed_field_paths:
                raise InvalidTransitionError("ProductBrief revision must change at least one field")
            if any(
                evidence.source_asset_version_id not in source_ids
                for field in fields
                for evidence in field.evidence
            ):
                raise RightsDeniedError(
                    "ProductBrief revision evidence must reference an authorized source"
                )
            decision = review_policy.evaluate(fields)
            version = ProductBriefVersion.create(
                workspace_id=workspace_id,
                product_brief_id=product_brief.id,
                version_number=uow.product_briefs.next_version_number(
                    workspace_id=workspace_id,
                    product_brief_id=product_brief.id,
                ),
                supersedes_version_id=base.version.id,
                category=base.version.category,
                common_schema_version=base.version.common_schema_version,
                category_schema_version=base.version.category_schema_version,
                fields=fields,
                changed_field_paths=tuple(changed_field_paths),
                review_decision=decision,
                source=ProductBriefVersionSource.HUMAN,
                prompt_version=None,
                provider_call_id=None,
                actor_id=actor_id,
                revision_reason=request.reason,
                retention_class=product_brief.retention_class,
                retention_deadline=product_brief.retention_deadline,
                now=now,
            )
            uow.product_briefs.add_version(
                StoredProductBriefVersion(
                    version=version,
                    review_reasons_by_path=decision.reasons_by_path,
                )
            )
            product_brief.publish_version(
                version,
                expected_version=request.expected_product_brief_version,
                now=now,
            )
            current_analysis = analysis
            if reopening_confirmed:
                workflow.transition(
                    WorkflowStatus.AWAITING_PRODUCT_CONFIRMATION,
                    current_node="confirm_product_brief",
                    now=now,
                )
                revision_operation = DurableOperation.create(
                    workspace_id=workspace_id,
                    kind=OperationKind.PRODUCT_BRIEF_ANALYSIS,
                    target_type="product_brief",
                    target_id=product_brief.id,
                    target_version=product_brief.version,
                    input_hash=request_digest,
                    input_ref=f"mysql://product-brief-versions/{version.id}",
                    max_attempts=1,
                    max_reconciliation_attempts=1,
                    execution_max_elapsed=product_brief.retention_deadline - now,
                    now=now,
                )
                lease_token = revision_operation.claim(
                    owner="api:product-brief-revision",
                    lease_duration=timedelta(minutes=5),
                    now=now,
                )
                revision_operation.start(lease_token=lease_token, now=now)
                revision_operation.wait_for_human(
                    lease_token=lease_token,
                    output_ref=f"mysql://product-brief-versions/{version.id}",
                    now=now,
                )
                current_analysis = replace(
                    analysis,
                    id=new_uuid7(),
                    operation_id=revision_operation.id,
                    expected_workflow_version=workflow.version,
                    created_by=actor_id,
                    created_at=now,
                )
                uow.operations.add(revision_operation)
                uow.product_brief_analyses.add_analysis(current_analysis)
                uow.product_briefs.save(
                    product_brief,
                    operation_id=revision_operation.id,
                )
                uow.workflows.save(workflow)
            else:
                uow.product_briefs.save(product_brief)
            uow.outbox.add(
                _product_brief_event(
                    event_type=EventType.PRODUCT_BRIEF_AWAITING_CONFIRMATION,
                    workspace_id=workspace_id,
                    product_brief=product_brief,
                    trace_id=trace_id,
                    payload=ProductBriefAwaitingConfirmationPayload(
                        workspace_id=workspace_id,
                        product_brief_id=product_brief.id,
                        product_brief_version=product_brief.version,
                        product_brief_version_id=version.id,
                        product_brief_version_number=version.version_number,
                        workflow_id=workflow.id,
                        operation_id=current_analysis.operation_id,
                        unresolved_field_count=version.unresolved_field_count,
                        review_policy_version=version.review_policy_version,
                    ).model_dump(mode="json"),
                    now=now,
                )
            )
            response = self._response(
                uow=uow,
                product_brief=product_brief,
                operation_id=current_analysis.operation_id,
            )
            uow.idempotency.complete(
                scope=scope,
                key_hash=key_digest,
                request_hash=request_digest,
                resource_type="product-brief-revision",
                resource_id=product_brief.id,
                response_data=response.model_dump(mode="json"),
            )
            _audit(
                uow=uow,
                workspace_id=workspace_id,
                actor_id=actor_id,
                trace_id=trace_id,
                action="product_brief.version.revised",
                product_brief_id=product_brief.id,
                metadata={
                    "base_version_id": base.version.id,
                    "product_brief_version_id": version.id,
                    "version_number": version.version_number,
                    "changed_field_paths": list(version.changed_field_paths),
                    "operation_id": current_analysis.operation_id,
                    "reopened_confirmed_product_brief": reopening_confirmed,
                },
                now=now,
                expires_at=product_brief.retention_deadline,
            )
            uow.commit_before_retention_deadline(
                workspace_id=workspace_id,
                product_brief_id=product_brief.id,
                retention_deadline=product_brief.retention_deadline,
                clock=self._clock,
            )
            return response

    def confirm(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
        actor_id: str,
        request: ProductBriefConfirmationRequestV1,
        idempotency_key: str,
        trace_id: str,
    ) -> ProductBriefConfirmationResponseV1:
        product_brief_id = canonicalize_resource_id(
            product_brief_id,
            resource="ProductBrief",
        )
        with self._observer.confirmation(
            trace_id=trace_id,
            workspace_id=workspace_id,
            product_brief_id=product_brief_id,
            product_brief_version_id=request.product_brief_version_id,
        ):
            try:
                response = self._confirm(
                    workspace_id=workspace_id,
                    product_brief_id=product_brief_id,
                    actor_id=actor_id,
                    request=request,
                    idempotency_key=idempotency_key,
                    trace_id=trace_id,
                )
            except Exception:
                self._observer.confirmation_result(
                    workspace_id=workspace_id,
                    product_brief_id=product_brief_id,
                    product_brief_version_id=request.product_brief_version_id,
                    result="failed",
                )
                raise
            self._observer.confirmation_result(
                workspace_id=workspace_id,
                product_brief_id=product_brief_id,
                product_brief_version_id=request.product_brief_version_id,
                result="confirmed",
            )
            return response

    def _confirm(
        self,
        *,
        workspace_id: str,
        product_brief_id: str,
        actor_id: str,
        request: ProductBriefConfirmationRequestV1,
        idempotency_key: str,
        trace_id: str,
    ) -> ProductBriefConfirmationResponseV1:
        scope = f"product-brief:confirmation:{product_brief_id}"
        key_digest = key_hash(idempotency_key)
        request_digest = canonical_hash({"actor_id": actor_id, **request.model_dump(mode="json")})
        with self._uow_factory() as uow:
            identity = uow.product_briefs.get(
                workspace_id=workspace_id,
                product_brief_id=product_brief_id,
            )
            if identity is None:
                raise NotFoundError(f"ProductBrief {product_brief_id} was not found")
            workflow = uow.workflows.get(
                identity.workflow_id,
                workspace_id=workspace_id,
                for_update=True,
            )
            product_brief = uow.product_briefs.get(
                workspace_id=workspace_id,
                product_brief_id=product_brief_id,
                for_update=True,
            )
            if (
                workflow is None
                or product_brief is None
                or product_brief.workflow_id != workflow.id
            ):
                raise ConcurrencyError("ProductBrief durable workflow state is incomplete")
            now = uow.database_now()
            assert_product_brief_workflow_authority(
                workflow=workflow,
                product_brief=product_brief,
                now=now,
            )
            self._assert_retention_active(product_brief, now=now)
            assert product_brief.retention_deadline is not None
            idempotency = uow.idempotency.claim(
                scope=scope,
                key_hash=key_digest,
                request_hash=request_digest,
                expires_at=product_brief.retention_deadline,
            )
            if idempotency.request_hash != request_digest:
                raise IdempotencyConflictError(
                    "idempotency key was already used with a different request"
                )
            if idempotency.status == "COMPLETED":
                if idempotency.resource_type != "product-brief-confirmation" or not isinstance(
                    idempotency.response_data, dict
                ):
                    raise ConcurrencyError(
                        "ProductBrief confirmation idempotency response is incomplete"
                    )
                return ProductBriefConfirmationResponseV1.model_validate(idempotency.response_data)
            if idempotency.status != "PENDING":
                raise ConcurrencyError("ProductBrief idempotency record has an unsupported status")
            existing_confirmation = uow.product_brief_confirmations.get_confirmation(
                workspace_id=workspace_id,
                product_brief_id=product_brief_id,
                product_brief_version_id=request.product_brief_version_id,
            )
            if existing_confirmation is not None:
                raise ApprovalConflictError(
                    "ProductBrief version already has an append-only confirmation"
                )
            version = uow.product_briefs.get_version(
                workspace_id=workspace_id,
                product_brief_version_id=request.product_brief_version_id,
            )
            if version is None or version.version.product_brief_id != product_brief.id:
                raise NotFoundError(
                    f"ProductBrief Version {request.product_brief_version_id} was not found"
                )
            operation_id = self._operation_id(uow, product_brief)
            operation = uow.operations.get(
                operation_id,
                workspace_id=workspace_id,
                for_update=True,
            )
            if operation is None:
                raise ConcurrencyError("ProductBrief durable workflow state is incomplete")
            analysis = uow.product_brief_analyses.get_analysis_by_operation(
                workspace_id=workspace_id,
                operation_id=operation_id,
            )
            if analysis is None or analysis.product_brief_id != product_brief.id:
                raise ConcurrencyError("ProductBrief analysis trace lineage is incomplete")
            continuation_trace_id = analysis.trace_id
            if workflow.status != WorkflowStatus.AWAITING_PRODUCT_CONFIRMATION:
                raise ApprovalConflictError(
                    "ProductBrief confirmation requires an awaiting workflow"
                )
            workflow.assert_version(request.expected_workflow_version)
            product_brief.confirm(
                product_brief_version_id=version.version.id,
                expected_version=request.expected_product_brief_version,
                now=now,
            )
            if operation.state != OperationState.WAITING_HUMAN:
                raise InvalidTransitionError(
                    "ProductBrief operation is not waiting for human confirmation"
                )
            approval = Approval.create(
                workflow_id=workflow.id,
                approval_type=ApprovalType.PRODUCT_BRIEF,
                subject_id=version.version.id,
                subject_version=version.version.version_number,
                decision=ApprovalDecision.APPROVE,
                approved_by=actor_id,
                expected_workflow_version=request.expected_workflow_version,
                reason_code=request.reason_code,
                comment_ref=request.comment_ref,
                now=now,
            )
            confirmation = ProductBriefConfirmation(
                id=new_uuid7(),
                workspace_id=workspace_id,
                product_brief_id=product_brief.id,
                product_brief_version_id=version.version.id,
                product_brief_version_number=version.version.version_number,
                workflow_id=workflow.id,
                operation_id=operation.id,
                approval_id=approval.id,
                confirmed_by=actor_id,
                reason_code=request.reason_code,
                comment_ref=request.comment_ref,
                expected_product_brief_version=(request.expected_product_brief_version),
                expected_workflow_version=request.expected_workflow_version,
                created_at=now,
            )
            workflow.transition(
                WorkflowStatus.RETRIEVING,
                current_node="retrieve_references",
                now=now,
            )
            operation.complete_human_wait(
                output_ref=f"mysql://product-brief-versions/{version.version.id}",
                now=now,
            )
            uow.product_briefs.save(product_brief)
            uow.workflows.save(workflow)
            uow.operations.save(operation)
            uow.approvals.add(approval)
            uow.product_brief_confirmations.add_confirmation(confirmation)
            uow.outbox.add(
                _workflow_event(
                    workflow_id=workflow.id,
                    workspace_id=workspace_id,
                    workflow_version=workflow.version,
                    trace_id=continuation_trace_id,
                    event_type=EventType.WORKFLOW_RESUME_REQUESTED,
                    payload=WorkflowResumeRequestedPayload(
                        workflow_id=workflow.id,
                        approval_id=approval.id,
                        approval_type=ApprovalType.PRODUCT_BRIEF,
                        decision=ApprovalDecision.APPROVE,
                        expected_workflow_version=request.expected_workflow_version,
                        resulting_workflow_version=workflow.version,
                        subject_id=version.version.id,
                        subject_version=version.version.version_number,
                    ).model_dump(mode="json"),
                    now=now,
                )
            )
            uow.outbox.add(
                _product_brief_event(
                    event_type=EventType.PRODUCT_BRIEF_CONFIRMED,
                    workspace_id=workspace_id,
                    product_brief=product_brief,
                    trace_id=continuation_trace_id,
                    payload=ProductBriefConfirmedPayload(
                        workspace_id=workspace_id,
                        product_brief_id=product_brief.id,
                        product_brief_version=product_brief.version,
                        product_brief_version_id=version.version.id,
                        product_brief_version_number=version.version.version_number,
                        workflow_id=workflow.id,
                        operation_id=operation.id,
                        confirmation_id=confirmation.id,
                        confirmation_source="HUMAN",
                    ).model_dump(mode="json"),
                    now=now,
                )
            )
            response = ProductBriefConfirmationResponseV1(
                product_brief=self._response(
                    uow=uow,
                    product_brief=product_brief,
                    operation_id=operation.id,
                ),
                workflow_id=workflow.id,
                workflow_status=workflow.status.value,
                workflow_version=workflow.version,
                confirmation_id=confirmation.id,
            )
            uow.idempotency.complete(
                scope=scope,
                key_hash=key_digest,
                request_hash=request_digest,
                resource_type="product-brief-confirmation",
                resource_id=product_brief.id,
                response_data=response.model_dump(mode="json"),
            )
            _audit(
                uow=uow,
                workspace_id=workspace_id,
                actor_id=actor_id,
                trace_id=trace_id,
                action="product_brief.version.confirmed",
                product_brief_id=product_brief.id,
                metadata={
                    "confirmation_id": confirmation.id,
                    "product_brief_version_id": version.version.id,
                    "version_number": version.version.version_number,
                },
                now=now,
                expires_at=product_brief.retention_deadline,
            )
            uow.commit_before_retention_deadline(
                workspace_id=workspace_id,
                product_brief_id=product_brief.id,
                retention_deadline=product_brief.retention_deadline,
                clock=self._clock,
            )
            return response

    def _authorize_current_use(
        self,
        *,
        uow: Any,
        workspace_id: str,
        asset_id: str,
        asset_version_id: str,
    ) -> None:
        snapshot = uow.assets.get_current_usability_snapshot(
            workspace_id=workspace_id,
            asset_id=asset_id,
        )
        if snapshot is None:
            raise RightsDeniedError("Asset has no authoritative usability state")
        decision = evaluate_current_usability(
            asset=snapshot.asset,
            rights_record=snapshot.rights_record,
            asset_version_id=asset_version_id,
            purpose=VISION_ANALYSIS_PURPOSE,
            provider=self._policy.provider,
            requires_derivative=False,
            decision_time=snapshot.database_now,
        )
        if not decision.authorized:
            raise RightsDeniedError(
                f"Asset Version is not authorized for Vision analysis: {decision.reason_code.value}"
            )

    def _authorize_transfer(
        self,
        *,
        workspace_id: str,
        asset_version_id: str,
        retention_class: RetentionClass,
        persisted_version: str,
        persisted_snapshot: str,
    ) -> None:
        try:
            self._transfer_policy.authorize(
                persisted_policy_version=persisted_version,
                persisted_policy_snapshot_sha256=persisted_snapshot,
                workspace_id=workspace_id,
                asset_version_id=asset_version_id,
                retention_class=retention_class,
                provider=self._policy.provider,
                endpoint_region=self._policy.endpoint_region,
                endpoint_host=self._policy.endpoint_host,
                purpose=VISION_ANALYSIS_PURPOSE,
            )
        except VisionDataTransferDenied as exc:
            raise ProviderPolicyDeniedError(f"{exc.code}: {exc.message}") from exc

    @staticmethod
    def _operation_id(uow: Any, product_brief: ProductBrief) -> str:
        operation_id = uow.product_briefs.operation_id(
            workspace_id=product_brief.workspace_id,
            product_brief_id=product_brief.id,
        )
        if operation_id is None:
            raise ConcurrencyError("ProductBrief operation identity is missing")
        return operation_id

    @staticmethod
    def _assert_retention_active(
        product_brief: ProductBrief,
        *,
        now: datetime,
    ) -> None:
        if product_brief.retention_deadline is not None and now >= product_brief.retention_deadline:
            raise ProductBriefRetentionExpiredError("ProductBrief retention has expired")

    def _response(
        self,
        *,
        uow: Any,
        product_brief: ProductBrief,
        operation_id: str,
    ) -> ProductBriefResponseV1:
        current = (
            uow.product_briefs.get_version(
                workspace_id=product_brief.workspace_id,
                product_brief_version_id=product_brief.current_version_id,
            )
            if product_brief.current_version_id is not None
            else None
        )
        confirmed = (
            current
            if current is not None and current.version.id == product_brief.confirmed_version_id
            else (
                uow.product_briefs.get_version(
                    workspace_id=product_brief.workspace_id,
                    product_brief_version_id=product_brief.confirmed_version_id,
                )
                if product_brief.confirmed_version_id is not None
                else None
            )
        )
        return ProductBriefResponseV1(
            id=product_brief.id,
            workspace_id=product_brief.workspace_id,
            workflow_id=product_brief.workflow_id,
            product_id=product_brief.product_id,
            operation_id=operation_id,
            state=product_brief.state,
            current_version_id=product_brief.current_version_id,
            confirmed_version_id=product_brief.confirmed_version_id,
            version=product_brief.version,
            retention_class=product_brief.retention_class,
            retention_deadline=product_brief.retention_deadline,
            created_at=product_brief.created_at,
            updated_at=product_brief.updated_at,
            current_version=(
                self._version_response(
                    uow=uow,
                    product_brief=product_brief,
                    stored=current,
                )
                if current is not None
                else None
            ),
            confirmed_version=(
                self._version_response(
                    uow=uow,
                    product_brief=product_brief,
                    stored=confirmed,
                )
                if confirmed is not None
                else None
            ),
        )

    def _version_response(
        self,
        *,
        uow: Any,
        product_brief: ProductBrief,
        stored: StoredProductBriefVersion,
        provider_summary: StoredProductBriefProviderSummary | StoredProviderCall | None = None,
        provider_summary_loaded: bool = False,
    ) -> ProductBriefVersionResponseV1:
        version = stored.version
        provider_call = (
            provider_summary
            if provider_summary_loaded
            else (
                uow.product_brief_analyses.get_provider_call(
                    workspace_id=version.workspace_id,
                    provider_call_id=version.provider_call_id,
                )
                if version.provider_call_id is not None
                else None
            )
        )
        if provider_call is not None and provider_call.product_brief_id != version.product_brief_id:
            raise ConcurrencyError(
                "ProductBrief version provider provenance belongs to another aggregate"
            )
        if product_brief.confirmed_version_id == version.id:
            effective_state = ProductBriefState.CONFIRMED
        elif product_brief.current_version_id == version.id:
            effective_state = product_brief.state
        else:
            effective_state = ProductBriefState.ARCHIVED
        return ProductBriefVersionResponseV1(
            id=version.id,
            product_brief_id=version.product_brief_id,
            version_number=version.version_number,
            supersedes_version_id=version.supersedes_version_id,
            effective_state=effective_state,
            category=version.category,
            common_schema_version=version.common_schema_version,
            category_schema_version=version.category_schema_version,
            payload_sha256=version.payload_sha256,
            changed_field_paths=version.changed_field_paths,
            confirmation_required=version.confirmation_required,
            unresolved_field_count=version.unresolved_field_count,
            review_policy_version=version.review_policy_version,
            source=version.source,
            prompt_version=version.prompt_version,
            provider_call=(
                ProductBriefProviderCallResponseV1(
                    provider=provider_call.provider,
                    requested_model=provider_call.requested_model,
                    resolved_model=provider_call.resolved_model,
                    latency_ms=provider_call.latency_ms,
                )
                if provider_call is not None
                else None
            ),
            actor_id=version.actor_id,
            revision_reason=version.revision_reason,
            retention_class=version.retention_class,
            retention_deadline=version.retention_deadline,
            created_at=version.created_at,
            fields=tuple(
                ProductBriefFieldResponseV1(
                    id=field.id,
                    path=field.path,
                    value=field.value,
                    confidence=field.confidence,
                    source=field.source,
                    conflict=field.conflict,
                    review_required=field.review_required,
                    sensitive=field.sensitive,
                    review_reasons=stored.review_reasons_by_path.get(field.path, ()),
                    evidence=tuple(
                        ProductBriefEvidenceResponseV1(
                            id=evidence.id,
                            source_asset_version_id=(evidence.source_asset_version_id),
                            kind=evidence.kind,
                            reference=evidence.reference,
                            region=evidence.region,
                            excerpt_sha256=evidence.excerpt_sha256,
                        )
                        for evidence in field.evidence
                    ),
                )
                for field in version.fields
            ),
        )

    @staticmethod
    def _version_summary_response(
        *,
        product_brief: ProductBrief,
        stored: StoredProductBriefVersionSummary,
        provider_summary: StoredProductBriefProviderSummary | None,
    ) -> ProductBriefVersionSummaryResponseV1:
        if (
            provider_summary is not None
            and provider_summary.product_brief_id != stored.product_brief_id
        ):
            raise ConcurrencyError(
                "ProductBrief version provider provenance belongs to another aggregate"
            )
        if product_brief.confirmed_version_id == stored.id:
            effective_state = ProductBriefState.CONFIRMED
        elif product_brief.current_version_id == stored.id:
            effective_state = product_brief.state
        else:
            effective_state = ProductBriefState.ARCHIVED
        return ProductBriefVersionSummaryResponseV1(
            id=stored.id,
            product_brief_id=stored.product_brief_id,
            version_number=stored.version_number,
            supersedes_version_id=stored.supersedes_version_id,
            effective_state=effective_state,
            category=stored.category,
            common_schema_version=stored.common_schema_version,
            category_schema_version=stored.category_schema_version,
            payload_sha256=stored.payload_sha256,
            changed_field_paths=stored.changed_field_paths,
            confirmation_required=stored.confirmation_required,
            unresolved_field_count=stored.unresolved_field_count,
            review_policy_version=stored.review_policy_version,
            source=stored.source,
            prompt_version=stored.prompt_version,
            provider_call=(
                ProductBriefProviderCallResponseV1(
                    provider=provider_summary.provider,
                    requested_model=provider_summary.requested_model,
                    resolved_model=provider_summary.resolved_model,
                    latency_ms=provider_summary.latency_ms,
                )
                if provider_summary is not None
                else None
            ),
            actor_id=stored.actor_id,
            revision_reason=stored.revision_reason,
            retention_class=stored.retention_class,
            retention_deadline=stored.retention_deadline,
            created_at=stored.created_at,
        )


@dataclass(frozen=True, slots=True)
class _AuthorizedImage:
    asset_version_id: str
    content_sha256: str
    url: str
    required_headers: dict[str, str]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class _DurableVisionCallLifecycle(VisionCallLifecycle):
    executor: ProductBriefAnalysisExecutor
    request: OperationExecutionRequest
    analysis: ProductBriefAnalysisRecord
    product_brief: ProductBrief

    def store_artifact(
        self,
        artifact: ProviderArtifactWrite,
    ) -> ProviderArtifactReference:
        service = self.executor._artifact_service
        self.executor._validate_provider_artifact_identity(
            request=self.request,
            analysis=self.analysis,
            product_brief=self.product_brief,
            artifact=artifact,
        )
        try:
            return service.store_artifact(
                artifact,
                owner=ProviderArtifactOwner(
                    workspace_id=self.request.workspace_id,
                    product_brief_id=self.product_brief.id,
                ),
                authorize_intent=lambda uow: self.executor._authorize_provider_artifact_intent(
                    uow=uow,
                    request=self.request,
                    analysis=self.analysis,
                    product_brief=self.product_brief,
                ),
            )
        except StoragePreconditionError as exc:
            raise ProviderArtifactIntegrityError(str(exc)) from exc
        except ProviderArtifactWriteOutcomeUnknownError as exc:
            if artifact.kind == ProviderArtifactKind.RESPONSE:
                raise
            raise UnknownOperationOutcome(_provider_artifact_outcome_unknown_error()) from exc
        except StorageUnavailableError as exc:
            raise ProviderArtifactUnavailableError(str(exc)) from exc

    def before_submission(self, call_index: int) -> None:
        self._record_submission_intent(call_index)

    def _record_submission_intent(self, call_index: int) -> None:
        _, created = self.executor._record_provider_attempt(
            request=self.request,
            analysis=self.analysis,
            product_brief=self.product_brief,
            call_index=call_index,
        )
        if not created:
            raise UnknownOperationOutcome(
                NormalizedOperationError(
                    code="VISION_SUBMISSION_ALREADY_FENCED",
                    category="worker_interruption",
                    message=(
                        "provider submission intent already exists for this call "
                        "and cannot be submitted again safely"
                    ),
                    retryable=False,
                )
            )

    def persist_completed_call(self, call: VisionProviderCall) -> None:
        self.executor._persist_intermediate_provider_call(
            request=self.request,
            analysis=self.analysis,
            product_brief=self.product_brief,
            call=call,
        )


class ProductBriefAnalysisExecutor:
    def __init__(
        self,
        *,
        uow_factory: ProductBriefUnitOfWorkFactory,
        object_storage: ObjectStorage,
        analyzer: VisionAnalyzer,
        policy: ProductBriefPolicy,
        transfer_policy: VisionDataTransferPolicy,
        artifact_service: ProductBriefProviderArtifactService,
        artifact_reconciler: ProductBriefProviderArtifactReconciler | None = None,
        observer: ProductBriefObserver | None = None,
        submission_reserve: timedelta = timedelta(0),
        clock: Any | None = None,
    ) -> None:
        if submission_reserve < timedelta(0):
            raise ValueError("ProductBrief submission reserve cannot be negative")
        self._uow_factory = uow_factory
        self._object_storage = object_storage
        self._analyzer = analyzer
        self._policy = policy
        self._transfer_policy = transfer_policy
        self._artifact_service = artifact_service
        self._artifact_reconciler = artifact_reconciler
        self._observer = observer or NullProductBriefObserver()
        self._submission_reserve = submission_reserve
        self._clock = clock or (lambda: datetime.now(UTC))

    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        self._validate_operation(request)
        with self._uow_factory() as uow:
            analysis = uow.product_brief_analyses.get_analysis_by_operation(
                workspace_id=request.workspace_id,
                operation_id=request.operation_id,
            )
            product_brief = (
                uow.product_briefs.get(
                    workspace_id=request.workspace_id,
                    product_brief_id=analysis.product_brief_id,
                )
                if analysis is not None
                else None
            )
            if analysis is None or product_brief is None:
                raise OperationExecutionFailure(
                    NormalizedOperationError(
                        code="PRODUCT_BRIEF_INPUT_NOT_FOUND",
                        category="input",
                        message="ProductBrief analysis input was not found",
                        retryable=False,
                    )
                )
            self._assert_current_execution_identity(
                uow=uow,
                request=request,
                analysis=analysis,
                product_brief=product_brief,
            )
            if (
                product_brief.current_version_id is not None
                and product_brief.state != ProductBriefState.DRAFT
            ):
                if product_brief.state == ProductBriefState.AWAITING_CONFIRMATION:
                    raise OperationHumanWaitRequired(
                        output_ref=f"mysql://product-briefs/{product_brief.id}",
                    )
                return OperationExecutionResult(
                    operation_id=request.operation_id,
                    output_ref=(
                        f"mysql://product-brief-versions/{product_brief.current_version_id}"
                    ),
                )
            if uow.product_brief_analyses.list_provider_attempts(
                workspace_id=request.workspace_id,
                operation_id=request.operation_id,
                operation_attempt=request.attempt_count,
            ):
                raise UnknownOperationOutcome(
                    NormalizedOperationError(
                        code="VISION_SUBMISSION_ALREADY_FENCED",
                        category="worker_interruption",
                        message=(
                            "provider submission intent already exists for this "
                            "operation attempt and cannot be submitted again safely"
                        ),
                        retryable=False,
                    )
                )
            product = uow.products.get(
                workspace_id=request.workspace_id,
                product_id=product_brief.product_id,
            )
            if product is None or product.version != analysis.product_catalog_version:
                raise OperationExecutionFailure(
                    NormalizedOperationError(
                        code="PRODUCT_CATALOG_VERSION_CHANGED",
                        category="input",
                        message="product facts changed after ProductBrief request",
                        retryable=False,
                    )
                )
            product_facts = {
                "attributes": product.attributes,
                "brand": product.brand,
                "category_code": product.category_code,
                "external_id": product.external_id,
                "title": product.title,
            }

        try:
            review_policy = _frozen_review_policy(analysis)
        except (ConcurrencyError, ValueError) as exc:
            raise OperationExecutionFailure(
                NormalizedOperationError(
                    code="PRODUCT_BRIEF_REVIEW_POLICY_MISMATCH",
                    category="configuration",
                    message="ProductBrief review policy snapshot is invalid",
                    retryable=False,
                )
            ) from exc
        self._validate_provider_identity(analysis)
        images = tuple(
            self._authorized_image(
                workspace_id=request.workspace_id,
                analysis=analysis,
                source=source,
            )
            for source in analysis.sources
        )
        provider_request = VisionAnalysisRequest(
            operation_id=request.operation_id,
            operation_attempt=request.attempt_count,
            product_brief_id=product_brief.id,
            category=analysis.category,
            product_facts=product_facts,
            images=tuple(
                VisionImageInput(
                    asset_version_id=image.asset_version_id,
                    content_sha256=image.content_sha256,
                    url=image.url,
                    required_headers=image.required_headers,
                    expires_at=image.expires_at,
                )
                for image in images
            ),
            common_schema_version=COMMON_SCHEMA_VERSION,
            category_schema_version=CATEGORY_SCHEMA_VERSIONS[analysis.category],
            prompt_version=analysis.prompt_version,
            policy_version=analysis.review_policy_version,
            retention_class=analysis.retention_class,
            retention_deadline=analysis.retention_deadline,
        )
        with self._uow_factory() as uow:
            current = uow.product_briefs.get(
                workspace_id=request.workspace_id,
                product_brief_id=analysis.product_brief_id,
            )
            if current is None:
                raise OperationExecutionFailure(
                    NormalizedOperationError(
                        code="PRODUCT_BRIEF_INPUT_NOT_FOUND",
                        category="input",
                        message="ProductBrief analysis input was not found",
                        retryable=False,
                    )
                )
            self._assert_current_execution_identity(
                uow=uow,
                request=request,
                analysis=analysis,
                product_brief=current,
            )
        self._reauthorize_provider_transfer(
            workspace_id=request.workspace_id,
            analysis=analysis,
        )
        lifecycle = _DurableVisionCallLifecycle(
            executor=self,
            request=request,
            analysis=analysis,
            product_brief=product_brief,
        )
        with self._observer.vision_request(
            operation_id=request.operation_id,
            operation_attempt=request.attempt_count,
            workspace_id=request.workspace_id,
            product_brief_id=product_brief.id,
            provider=analysis.provider,
            endpoint_region=analysis.endpoint_region,
            requested_model=analysis.requested_model,
        ):
            try:
                outcome = self._analyzer.analyze(
                    provider_request,
                    lifecycle=lifecycle,
                )
                self._validate_provider_outcome_identity(
                    analysis=analysis,
                    outcome=outcome,
                )
            except ProductBriefRetentionExpiredError as exc:
                raise OperationExecutionFailure(
                    NormalizedOperationError(
                        code="PRODUCT_BRIEF_RETENTION_EXPIRED",
                        category="retention",
                        message="ProductBrief retention expired during provider submission",
                        retryable=False,
                    )
                ) from exc
            except (ProviderArtifactIntegrityError, StoragePreconditionError) as exc:
                raise OperationExecutionFailure(
                    NormalizedOperationError(
                        code="VISION_ARTIFACT_INTEGRITY_CONFLICT",
                        category="storage_integrity",
                        message="Vision provider artifact integrity validation failed",
                        retryable=False,
                    )
                ) from exc
            except (ProviderArtifactUnavailableError, StorageUnavailableError) as exc:
                raise OperationExecutionFailure(
                    NormalizedOperationError(
                        code="VISION_ARTIFACT_STORAGE_UNAVAILABLE",
                        category="storage",
                        message="Vision provider artifact storage is temporarily unavailable",
                        retryable=True,
                    )
                ) from exc
            except ValueError as exc:
                raise OperationExecutionFailure(
                    NormalizedOperationError(
                        code="VISION_REQUEST_INVALID",
                        category="input",
                        message="Vision provider request failed local validation",
                        retryable=False,
                    )
                ) from exc
            for call in outcome.calls:
                self._observer.provider_result(
                    operation_id=request.operation_id,
                    operation_attempt=request.attempt_count,
                    workspace_id=request.workspace_id,
                    product_brief_id=product_brief.id,
                    provider=call.provider,
                    requested_model=call.requested_model,
                    status=call.status.value,
                    latency_ms=call.latency_ms,
                    error_category=(call.error.category if call.error is not None else None),
                    retryable=(call.error.retryable if call.error is not None else None),
                    provider_request_id=call.request_id,
                )
        human_wait: OperationHumanWaitRequired | None = None
        phase = (
            "model_result"
            if outcome.status == VisionProviderStatus.SUCCEEDED and outcome.output is not None
            else "provider_failure"
        )
        with self._observer.persistence(
            operation_id=request.operation_id,
            workspace_id=request.workspace_id,
            product_brief_id=product_brief.id,
            phase=phase,
        ):
            try:
                return self._persist_outcome(
                    request=request,
                    analysis=analysis,
                    product_brief=product_brief,
                    outcome=outcome,
                    review_policy=review_policy,
                )
            except OperationHumanWaitRequired as exc:
                human_wait = exc
            except ProductBriefRetentionExpiredError as exc:
                raise OperationExecutionFailure(
                    NormalizedOperationError(
                        code="PRODUCT_BRIEF_RETENTION_EXPIRED",
                        category="retention",
                        message="ProductBrief retention expired before persistence",
                        retryable=False,
                        provider_request_id=outcome.request_id,
                    )
                ) from exc
        assert human_wait is not None
        raise human_wait

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        self._validate_operation(request)
        artifact_result = self._reconcile_provider_artifacts(request)
        if artifact_result is not None:
            return artifact_result
        with self._uow_factory() as uow:
            analysis = uow.product_brief_analyses.get_analysis_by_operation(
                workspace_id=request.workspace_id,
                operation_id=request.operation_id,
            )
            product_brief = (
                uow.product_briefs.get(
                    workspace_id=request.workspace_id,
                    product_brief_id=analysis.product_brief_id,
                )
                if analysis is not None
                else None
            )
            if analysis is None or product_brief is None:
                return OperationReconciliationResult(
                    operation_id=request.operation_id,
                    outcome=ReconciliationOutcome.CONFIRMED_FAILURE,
                    error=NormalizedOperationError(
                        code="PRODUCT_BRIEF_INPUT_NOT_FOUND",
                        category="input",
                        message="ProductBrief analysis input was not found",
                        retryable=False,
                    ),
                )
            output_version = uow.product_briefs.get_model_version_by_operation(
                workspace_id=request.workspace_id,
                operation_id=request.operation_id,
            )
            if output_version is not None:
                if output_version.version.product_brief_id != analysis.product_brief_id:
                    return OperationReconciliationResult(
                        operation_id=request.operation_id,
                        outcome=ReconciliationOutcome.CONFIRMED_FAILURE,
                        error=NormalizedOperationError(
                            code="PRODUCT_BRIEF_OUTPUT_IDENTITY_MISMATCH",
                            category="persistence",
                            message="persisted ProductBrief output belongs to another analysis",
                            retryable=False,
                        ),
                    )
                provider_call = (
                    uow.product_brief_analyses.get_provider_call(
                        workspace_id=request.workspace_id,
                        provider_call_id=output_version.version.provider_call_id,
                    )
                    if output_version.version.provider_call_id is not None
                    else None
                )
                if output_version.version.confirmation_required:
                    raise OperationHumanWaitRequired(
                        output_ref=f"mysql://product-briefs/{analysis.product_brief_id}",
                        provider_request_id=(
                            provider_call.request_id if provider_call is not None else None
                        ),
                    )
                return OperationReconciliationResult(
                    operation_id=request.operation_id,
                    outcome=ReconciliationOutcome.CONFIRMED_SUCCESS,
                    output_ref=f"mysql://product-brief-versions/{output_version.version.id}",
                    provider_request_id=(
                        provider_call.request_id if provider_call is not None else None
                    ),
                )
            calls = uow.product_brief_analyses.list_provider_calls(
                workspace_id=request.workspace_id,
                operation_id=request.operation_id,
                operation_attempt=request.attempt_count,
            )
            attempts = uow.product_brief_analyses.list_provider_attempts(
                workspace_id=request.workspace_id,
                operation_id=request.operation_id,
                operation_attempt=request.attempt_count,
            )
        completed_call_indexes = {call.call_index for call in calls}
        unresolved_attempt = next(
            (attempt for attempt in attempts if attempt.call_index not in completed_call_indexes),
            None,
        )
        if unresolved_attempt is not None:
            return OperationReconciliationResult(
                operation_id=request.operation_id,
                outcome=ReconciliationOutcome.CONFIRMED_FAILURE,
                error=_provider_submission_outcome_unknown_error(),
            )
        if calls:
            final = calls[-1]
            error = NormalizedOperationError(
                code=final.error_code or "VISION_ANALYSIS_FAILED",
                category=final.error_category or "provider",
                message="persisted Vision analysis attempt did not produce a ProductBrief",
                retryable=bool(
                    _normalized_provider_retryable(
                        status=final.status,
                        retryable=final.error_retryable,
                    )
                ),
                provider_request_id=final.request_id,
            )
            return OperationReconciliationResult(
                operation_id=request.operation_id,
                outcome=ReconciliationOutcome.CONFIRMED_FAILURE,
                provider_request_id=final.request_id,
                error=error,
            )
        return OperationReconciliationResult(
            operation_id=request.operation_id,
            outcome=ReconciliationOutcome.CONFIRMED_FAILURE,
            error=NormalizedOperationError(
                code="VISION_SUBMISSION_NOT_RECORDED",
                category="worker_interruption",
                message="provider submission did not start before worker interruption",
                retryable=True,
            ),
        )

    def _reconcile_provider_artifacts(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult | None:
        if self._artifact_reconciler is None:
            return None
        artifacts = self._artifact_reconciler.reconcile_operation(
            workspace_id=request.workspace_id,
            operation_id=request.operation_id,
            operation_attempt=request.attempt_count,
        )
        unresolved = tuple(
            artifact for artifact in artifacts if artifact.state != ProviderArtifactState.STORED
        )
        if not unresolved:
            return None
        with self._uow_factory() as uow:
            attempts = uow.product_brief_analyses.list_provider_attempts(
                workspace_id=request.workspace_id,
                operation_id=request.operation_id,
                operation_attempt=request.attempt_count,
            )
            calls = uow.product_brief_analyses.list_provider_calls(
                workspace_id=request.workspace_id,
                operation_id=request.operation_id,
                operation_attempt=request.attempt_count,
            )
        completed_call_indexes = {call.call_index for call in calls}
        submission_without_call = any(
            attempt.call_index not in completed_call_indexes for attempt in attempts
        )
        response_write_started = any(
            artifact.kind == ProviderArtifactKind.RESPONSE for artifact in unresolved
        )
        if response_write_started or submission_without_call:
            error = _provider_submission_outcome_unknown_error()
        elif any(artifact.state == ProviderArtifactState.UNKNOWN for artifact in unresolved):
            error = _provider_artifact_outcome_unknown_error()
        else:
            error = NormalizedOperationError(
                code="VISION_ARTIFACT_WRITE_NOT_CONFIRMED",
                category="worker_interruption",
                message=("provider artifact intent exists without a canonical stored object"),
                retryable=True,
            )
        return OperationReconciliationResult(
            operation_id=request.operation_id,
            outcome=ReconciliationOutcome.CONFIRMED_FAILURE,
            error=error,
        )

    def _authorize_provider_artifact_intent(
        self,
        *,
        uow: ProductBriefUnitOfWorkPort,
        request: OperationExecutionRequest,
        analysis: ProductBriefAnalysisRecord,
        product_brief: ProductBrief,
    ) -> datetime:
        _, _, operation, database_now = self._lock_current_execution(
            uow=uow,
            request=request,
            analysis=analysis,
            product_brief=product_brief,
        )
        self._assert_submission_lease(
            request=request,
            operation=operation,
            database_now=database_now,
        )
        return database_now

    @staticmethod
    def _validate_provider_artifact_identity(
        *,
        request: OperationExecutionRequest,
        analysis: ProductBriefAnalysisRecord,
        product_brief: ProductBrief,
        artifact: ProviderArtifactWrite,
    ) -> None:
        expected = (
            request.operation_id,
            request.attempt_count,
            request.workspace_id,
            request.target_id,
            analysis.workspace_id,
            analysis.product_brief_id,
            analysis.retention_class,
            analysis.retention_deadline,
            "application/json",
        )
        actual = (
            artifact.operation_id,
            artifact.operation_attempt,
            product_brief.workspace_id,
            product_brief.id,
            analysis.workspace_id,
            analysis.product_brief_id,
            artifact.retention_class,
            artifact.retention_deadline,
            artifact.content_type,
        )
        if actual != expected:
            raise StoragePreconditionError(
                "provider artifact identity does not match its ProductBrief execution"
            )

    def _record_provider_attempt(
        self,
        *,
        request: OperationExecutionRequest,
        analysis: ProductBriefAnalysisRecord,
        product_brief: ProductBrief,
        call_index: int,
    ) -> tuple[StoredProviderAttempt, bool]:
        submission_key_sha256 = hashlib.sha256(request.idempotency_key.encode()).hexdigest()
        with self._uow_factory() as uow:
            current, _, operation, database_now = self._lock_current_execution(
                uow=uow,
                request=request,
                analysis=analysis,
                product_brief=product_brief,
            )
            self._assert_submission_lease(
                request=request,
                operation=operation,
                database_now=database_now,
            )
            existing = uow.product_brief_analyses.get_provider_attempt(
                workspace_id=request.workspace_id,
                operation_id=request.operation_id,
                operation_attempt=request.attempt_count,
                call_index=call_index,
            )
            expected_signature = (
                request.workspace_id,
                product_brief.id,
                request.operation_id,
                request.attempt_count,
                call_index,
                submission_key_sha256,
                request.input_hash,
                analysis.provider,
                analysis.endpoint_region,
                analysis.endpoint_host,
                analysis.requested_model,
                analysis.submitted_model_snapshot,
                analysis.prompt_version,
                analysis.provider_configuration_snapshot_sha256,
                analysis.retention_class,
                analysis.retention_deadline,
            )
            if existing is not None:
                actual_signature = (
                    existing.workspace_id,
                    existing.product_brief_id,
                    existing.operation_id,
                    existing.operation_attempt,
                    existing.call_index,
                    existing.submission_key_sha256,
                    existing.input_sha256,
                    existing.provider,
                    existing.endpoint_region,
                    existing.endpoint_host,
                    existing.requested_model,
                    existing.submitted_model_snapshot,
                    existing.prompt_version,
                    existing.config_snapshot_sha256,
                    existing.retention_class,
                    existing.retention_deadline,
                )
                if actual_signature != expected_signature:
                    raise OperationExecutionFailure(
                        NormalizedOperationError(
                            code="VISION_PROVIDER_ATTEMPT_REPLAY_MISMATCH",
                            category="persistence",
                            message="persisted provider submission intent changed on replay",
                            retryable=False,
                        )
                    )
                return existing, False
            attempt = StoredProviderAttempt(
                id=new_uuid7(),
                workspace_id=request.workspace_id,
                product_brief_id=current.id,
                operation_id=request.operation_id,
                operation_attempt=request.attempt_count,
                call_index=call_index,
                submission_key_sha256=submission_key_sha256,
                input_sha256=request.input_hash,
                provider=analysis.provider,
                endpoint_region=analysis.endpoint_region,
                endpoint_host=analysis.endpoint_host,
                requested_model=analysis.requested_model,
                submitted_model_snapshot=analysis.submitted_model_snapshot,
                prompt_version=analysis.prompt_version,
                config_snapshot_sha256=(analysis.provider_configuration_snapshot_sha256),
                retention_class=analysis.retention_class,
                retention_deadline=analysis.retention_deadline,
                created_at=database_now,
            )
            uow.product_brief_analyses.add_provider_attempt(attempt)
            assert analysis.retention_deadline is not None
            uow.commit_before_retention_deadline(
                workspace_id=request.workspace_id,
                product_brief_id=product_brief.id,
                retention_deadline=analysis.retention_deadline,
                clock=self._clock,
            )
            return attempt, True

    def _persist_intermediate_provider_call(
        self,
        *,
        request: OperationExecutionRequest,
        analysis: ProductBriefAnalysisRecord,
        product_brief: ProductBrief,
        call: VisionProviderCall,
    ) -> None:
        self._validate_provider_call_identity(analysis=analysis, call=call)
        with self._uow_factory() as uow:
            current, _, operation, now = self._lock_current_execution(
                uow=uow,
                request=request,
                analysis=analysis,
                product_brief=product_brief,
            )
            self._assert_submission_lease(
                request=request,
                operation=operation,
                database_now=now,
            )
            candidate = _stored_provider_call(
                request=request,
                analysis=analysis,
                call=call,
                now=now,
            )
            _, inserted = self._store_provider_calls_once(
                uow=uow,
                request=request,
                candidates=(candidate,),
            )
            if inserted:
                assert analysis.retention_deadline is not None
                uow.commit_before_retention_deadline(
                    workspace_id=request.workspace_id,
                    product_brief_id=current.id,
                    retention_deadline=analysis.retention_deadline,
                    clock=self._clock,
                )

    def _lock_current_execution(
        self,
        *,
        uow: ProductBriefUnitOfWorkPort,
        request: OperationExecutionRequest,
        analysis: ProductBriefAnalysisRecord,
        product_brief: ProductBrief,
    ) -> tuple[ProductBrief, Workflow, DurableOperation, datetime]:
        workflow = uow.workflows.get(
            product_brief.workflow_id,
            workspace_id=request.workspace_id,
            for_update=True,
        )
        current = uow.product_briefs.get(
            workspace_id=request.workspace_id,
            product_brief_id=product_brief.id,
            for_update=True,
        )
        if current is None:
            raise OperationExecutionFailure(
                NormalizedOperationError(
                    code="PRODUCT_BRIEF_NOT_FOUND",
                    category="persistence",
                    message="ProductBrief disappeared while locking execution authority",
                    retryable=False,
                )
            )
        operation = uow.operations.get(
            request.operation_id,
            workspace_id=request.workspace_id,
            for_update=True,
        )
        database_now = uow.database_now()
        self._assert_execution_identity(
            request=request,
            analysis=analysis,
            product_brief=current,
            current_operation_id=uow.product_briefs.operation_id(
                workspace_id=request.workspace_id,
                product_brief_id=current.id,
            ),
            operation=operation,
            workflow=workflow,
            database_now=database_now,
        )
        assert operation is not None
        assert workflow is not None
        return current, workflow, operation, database_now

    @classmethod
    def _assert_current_execution_identity(
        cls,
        *,
        uow: ProductBriefUnitOfWorkPort,
        request: OperationExecutionRequest,
        analysis: ProductBriefAnalysisRecord,
        product_brief: ProductBrief,
    ) -> None:
        operation = uow.operations.get(
            request.operation_id,
            workspace_id=request.workspace_id,
        )
        current_operation_id = uow.product_briefs.operation_id(
            workspace_id=request.workspace_id,
            product_brief_id=product_brief.id,
        )
        workflow = uow.workflows.get(
            product_brief.workflow_id,
            workspace_id=request.workspace_id,
        )
        cls._assert_execution_identity(
            request=request,
            analysis=analysis,
            product_brief=product_brief,
            current_operation_id=current_operation_id,
            operation=operation,
            workflow=workflow,
            database_now=uow.database_now(),
        )

    @staticmethod
    def _assert_execution_identity(
        *,
        request: OperationExecutionRequest,
        analysis: ProductBriefAnalysisRecord,
        product_brief: ProductBrief,
        current_operation_id: str | None,
        operation: DurableOperation | None,
        workflow: Workflow | None,
        database_now: datetime,
    ) -> None:
        operation_identity = (
            request.workspace_id,
            request.operation_id,
            request.kind,
            request.target_type,
            request.target_id,
            request.target_version,
            request.input_hash,
            request.input_ref,
            request.attempt_count,
            request.lease_expires_at,
        )
        persisted_operation_identity = (
            (
                operation.workspace_id,
                operation.id,
                operation.kind,
                operation.target_type,
                operation.target_id,
                operation.target_version,
                operation.input_hash,
                operation.input_ref,
                operation.attempt_count,
                operation.lease_expires_at,
            )
            if operation is not None
            else None
        )
        analysis_identity = (
            analysis.workspace_id,
            analysis.operation_id,
            analysis.product_brief_id,
            f"mysql://product-brief-analysis/{analysis.id}",
        )
        if (
            operation is None
            or operation_identity != persisted_operation_identity
            or operation.state != OperationState.RUNNING
            or operation.lease_owner is None
            or operation.lease_token is None
            or operation.lease_expires_at is None
            or operation.lease_expires_at <= database_now
            or operation.recovery_generation != operation.recovery_consumed_generation
            or analysis_identity
            != (
                request.workspace_id,
                request.operation_id,
                request.target_id,
                request.input_ref,
            )
            or product_brief.workspace_id != request.workspace_id
            or product_brief.id != request.target_id
            or product_brief.version != request.target_version
            or product_brief.state != ProductBriefState.DRAFT
            or current_operation_id != request.operation_id
        ):
            raise OperationExecutionFailure(
                NormalizedOperationError(
                    code="PRODUCT_BRIEF_OPERATION_MISMATCH",
                    category="input",
                    message="operation is not the current ProductBrief analysis",
                    retryable=False,
                )
            )
        workflow_authority = (
            None
            if workflow is None
            else evaluate_product_brief_workflow_authority(
                workflow=workflow,
                product_brief=product_brief,
                now=database_now,
            )
        )
        if (
            workflow is None
            or workflow_authority is None
            or workflow_authority.state != ProductBriefWorkflowAuthorityState.ACTIVE
            or workflow.status != WorkflowStatus.UNDERSTANDING
            or workflow.version != analysis.expected_workflow_version
            or workflow.cancellation_requested_at is not None
            or analysis.retention_deadline
            != canonical_task_retention_deadline(
                created_at=workflow.created_at,
                expires_at=workflow.expires_at,
            )
        ):
            raise OperationExecutionFailure(
                NormalizedOperationError(
                    code="PRODUCT_BRIEF_WORKFLOW_NOT_EXECUTABLE",
                    category="workflow",
                    message="ProductBrief workflow is no longer executable",
                    retryable=False,
                )
            )

    def _assert_submission_lease(
        self,
        *,
        request: OperationExecutionRequest,
        operation: DurableOperation,
        database_now: datetime,
    ) -> None:
        if (
            request.lease_expires_at is None
            or operation.lease_expires_at != request.lease_expires_at
            or request.lease_expires_at - database_now < self._submission_reserve
        ):
            raise OperationExecutionFailure(
                NormalizedOperationError(
                    code="VISION_OPERATION_LEASE_RESERVE_INSUFFICIENT",
                    category="lease",
                    message="operation lease reserve is insufficient for provider submission",
                    retryable=True,
                )
            )

    def _validate_provider_identity(
        self,
        analysis: ProductBriefAnalysisRecord,
    ) -> None:
        identity = self._analyzer.configured_identity
        expected = (
            analysis.provider,
            analysis.endpoint_region,
            analysis.endpoint_host,
            analysis.requested_model,
            analysis.submitted_model_snapshot,
            analysis.prompt_version,
            analysis.provider_configuration_snapshot_sha256,
        )
        actual = (
            identity.provider,
            identity.endpoint_region,
            identity.endpoint_host,
            identity.requested_model,
            identity.submitted_model_snapshot,
            identity.prompt_version,
            identity.configuration_snapshot_sha256,
        )
        if actual != expected:
            raise OperationExecutionFailure(
                NormalizedOperationError(
                    code="VISION_PROVIDER_IDENTITY_MISMATCH",
                    category="authorization",
                    message="configured Vision provider identity does not match the request",
                    retryable=False,
                )
            )

    @staticmethod
    def _validate_provider_outcome_identity(
        *,
        analysis: ProductBriefAnalysisRecord,
        outcome: VisionProviderOutcome,
    ) -> None:
        expected = (
            analysis.provider,
            analysis.endpoint_region,
            analysis.endpoint_host,
            analysis.requested_model,
            analysis.submitted_model_snapshot,
            analysis.prompt_version,
            analysis.provider_configuration_snapshot_sha256,
        )
        identities = (
            (
                outcome.provider,
                outcome.endpoint_region,
                outcome.endpoint_host,
                outcome.requested_model,
                outcome.submitted_model_snapshot,
                outcome.prompt_version,
                outcome.config_snapshot_sha256,
            ),
            *(
                (
                    call.provider,
                    call.endpoint_region,
                    call.endpoint_host,
                    call.requested_model,
                    call.submitted_model_snapshot,
                    call.prompt_version,
                    call.config_snapshot_sha256,
                )
                for call in outcome.calls
            ),
        )
        if any(identity != expected for identity in identities):
            raise OperationExecutionFailure(
                NormalizedOperationError(
                    code="VISION_PROVIDER_PROVENANCE_MISMATCH",
                    category="provider_schema",
                    message="Vision provider returned inconsistent provenance",
                    retryable=False,
                )
            )

    @staticmethod
    def _validate_provider_call_identity(
        *,
        analysis: ProductBriefAnalysisRecord,
        call: VisionProviderCall,
    ) -> None:
        expected = (
            analysis.provider,
            analysis.endpoint_region,
            analysis.endpoint_host,
            analysis.requested_model,
            analysis.submitted_model_snapshot,
            analysis.prompt_version,
            analysis.provider_configuration_snapshot_sha256,
        )
        actual = (
            call.provider,
            call.endpoint_region,
            call.endpoint_host,
            call.requested_model,
            call.submitted_model_snapshot,
            call.prompt_version,
            call.config_snapshot_sha256,
        )
        if actual != expected:
            raise OperationExecutionFailure(
                NormalizedOperationError(
                    code="VISION_PROVIDER_PROVENANCE_MISMATCH",
                    category="provider_schema",
                    message="Vision provider returned inconsistent provenance",
                    retryable=False,
                )
            )

    def _authorized_image(
        self,
        *,
        workspace_id: str,
        analysis: ProductBriefAnalysisRecord,
        source: ProductBriefSourceAsset,
    ) -> _AuthorizedImage:
        with self._uow_factory() as uow:
            asset_version = uow.assets.get_version(
                workspace_id=workspace_id,
                asset_version_id=source.asset_version_id,
            )
            asset = (
                uow.assets.get(
                    workspace_id=workspace_id,
                    asset_id=source.asset_id,
                )
                if asset_version is not None
                else None
            )
            object_fact = uow.assets.get_object(
                workspace_id=workspace_id,
                asset_version_id=source.asset_version_id,
            )
            snapshot = uow.assets.get_current_usability_snapshot(
                workspace_id=workspace_id,
                asset_id=source.asset_id,
            )
            if (
                asset_version is None
                or asset is None
                or object_fact is None
                or object_fact.id != source.asset_object_id
                or object_fact.state != AssetObjectState.CONTROLLED
                or snapshot is None
            ):
                raise OperationExecutionFailure(
                    NormalizedOperationError(
                        code="VISION_SOURCE_NOT_CONTROLLED",
                        category="authorization",
                        message="Vision source is not a current controlled Asset Version",
                        retryable=False,
                    )
                )
            decision = evaluate_current_usability(
                asset=snapshot.asset,
                rights_record=snapshot.rights_record,
                asset_version_id=source.asset_version_id,
                purpose=VISION_ANALYSIS_PURPOSE,
                provider=analysis.provider,
                requires_derivative=False,
                decision_time=snapshot.database_now,
            )
            if not decision.authorized:
                raise OperationExecutionFailure(
                    NormalizedOperationError(
                        code=f"RIGHTS_{decision.reason_code.value}",
                        category="authorization",
                        message="Vision source rights are not currently authorized",
                        retryable=False,
                    )
                )
            try:
                self._transfer_policy.authorize(
                    persisted_policy_version=analysis.transfer_policy_version,
                    persisted_policy_snapshot_sha256=(analysis.transfer_policy_snapshot_sha256),
                    workspace_id=workspace_id,
                    asset_version_id=source.asset_version_id,
                    retention_class=asset.retention_class,
                    provider=analysis.provider,
                    endpoint_region=analysis.endpoint_region,
                    endpoint_host=analysis.endpoint_host,
                    purpose=VISION_ANALYSIS_PURPOSE,
                )
            except VisionDataTransferDenied as exc:
                raise OperationExecutionFailure(
                    NormalizedOperationError(
                        code=exc.code,
                        category="authorization",
                        message=exc.message,
                        retryable=False,
                    )
                ) from exc
            expires_at = snapshot.database_now + self._policy.temporary_reference_lifetime
            if analysis.retention_deadline is not None:
                expires_at = min(expires_at, analysis.retention_deadline)
            if (
                snapshot.rights_record is not None
                and snapshot.rights_record.valid_until is not None
            ):
                expires_at = min(expires_at, snapshot.rights_record.valid_until)
            if expires_at <= snapshot.database_now:
                raise OperationExecutionFailure(
                    NormalizedOperationError(
                        code="VISION_REFERENCE_WINDOW_EXPIRED",
                        category="authorization",
                        message="Vision source authorization window has expired",
                        retryable=False,
                    )
                )
            reference = ObjectReference(
                location=object_fact.location,
                key=object_fact.key,
                version_id=object_fact.provider_version_id,
            )
            expected_etag = object_fact.etag
            content_sha256 = asset_version.sha256
            expected_backend = object_fact.backend

        if self._object_storage.backend != expected_backend:
            raise OperationExecutionFailure(
                NormalizedOperationError(
                    code="VISION_STORAGE_BACKEND_MISMATCH",
                    category="configuration",
                    message="Vision source storage backend is not configured",
                    retryable=False,
                )
            )
        try:
            temporary = self._object_storage.temporary_read(
                TemporaryReadRequest(
                    reference=reference,
                    expires_at=expires_at,
                    expected_etag=expected_etag,
                    expected_sha256=content_sha256,
                )
            )
        except (UploadObjectMissingError, StoragePreconditionError) as exc:
            raise OperationExecutionFailure(
                NormalizedOperationError(
                    code="VISION_STORAGE_INTEGRITY",
                    category="storage_integrity",
                    message="Vision source exact object version failed integrity validation",
                    retryable=False,
                )
            ) from exc
        except StorageUnavailableError as exc:
            raise OperationExecutionFailure(
                NormalizedOperationError(
                    code="VISION_STORAGE_UNAVAILABLE",
                    category="storage",
                    message="Vision source storage is temporarily unavailable",
                    retryable=True,
                )
            ) from exc
        return _AuthorizedImage(
            asset_version_id=source.asset_version_id,
            content_sha256=content_sha256,
            url=temporary.url,
            required_headers=temporary.required_headers,
            expires_at=temporary.expires_at,
        )

    def _reauthorize_provider_transfer(
        self,
        *,
        workspace_id: str,
        analysis: ProductBriefAnalysisRecord,
    ) -> None:
        with self._uow_factory() as uow:
            for source in analysis.sources:
                object_fact = uow.assets.get_object(
                    workspace_id=workspace_id,
                    asset_version_id=source.asset_version_id,
                )
                snapshot = uow.assets.get_current_usability_snapshot(
                    workspace_id=workspace_id,
                    asset_id=source.asset_id,
                )
                if (
                    object_fact is None
                    or object_fact.id != source.asset_object_id
                    or object_fact.state != AssetObjectState.CONTROLLED
                    or snapshot is None
                ):
                    raise OperationExecutionFailure(
                        NormalizedOperationError(
                            code="VISION_SOURCE_NOT_CONTROLLED",
                            category="authorization",
                            message=("Vision source is not a current controlled Asset Version"),
                            retryable=False,
                        )
                    )
                decision = evaluate_current_usability(
                    asset=snapshot.asset,
                    rights_record=snapshot.rights_record,
                    asset_version_id=source.asset_version_id,
                    purpose=VISION_ANALYSIS_PURPOSE,
                    provider=analysis.provider,
                    requires_derivative=False,
                    decision_time=snapshot.database_now,
                )
                if not decision.authorized:
                    raise OperationExecutionFailure(
                        NormalizedOperationError(
                            code=f"RIGHTS_{decision.reason_code.value}",
                            category="authorization",
                            message=("Vision source rights are not currently authorized"),
                            retryable=False,
                        )
                    )
                try:
                    self._transfer_policy.authorize(
                        persisted_policy_version=analysis.transfer_policy_version,
                        persisted_policy_snapshot_sha256=(analysis.transfer_policy_snapshot_sha256),
                        workspace_id=workspace_id,
                        asset_version_id=source.asset_version_id,
                        retention_class=snapshot.asset.retention_class,
                        provider=analysis.provider,
                        endpoint_region=analysis.endpoint_region,
                        endpoint_host=analysis.endpoint_host,
                        purpose=VISION_ANALYSIS_PURPOSE,
                    )
                except VisionDataTransferDenied as exc:
                    raise OperationExecutionFailure(
                        NormalizedOperationError(
                            code=exc.code,
                            category="authorization",
                            message=exc.message,
                            retryable=False,
                        )
                    ) from exc

    def _persist_outcome(
        self,
        *,
        request: OperationExecutionRequest,
        analysis: ProductBriefAnalysisRecord,
        product_brief: ProductBrief,
        outcome: VisionProviderOutcome,
        review_policy: ProductBriefReviewPolicy,
    ) -> OperationExecutionResult:
        if outcome.status != VisionProviderStatus.SUCCEEDED or outcome.output is None:
            with self._uow_factory() as uow:
                current = uow.product_briefs.get(
                    workspace_id=request.workspace_id,
                    product_brief_id=product_brief.id,
                    for_update=True,
                )
                if current is None:
                    raise OperationExecutionFailure(
                        NormalizedOperationError(
                            code="PRODUCT_BRIEF_NOT_FOUND",
                            category="persistence",
                            message="ProductBrief disappeared before Vision failure persistence",
                            retryable=False,
                        )
                    )
                now = uow.database_now()
                provider_calls = tuple(
                    _stored_provider_call(
                        request=request,
                        analysis=analysis,
                        call=call,
                        now=now,
                    )
                    for call in outcome.calls
                )
                _, inserted = self._store_provider_calls_once(
                    uow=uow,
                    request=request,
                    candidates=provider_calls,
                )
                if inserted:
                    assert analysis.retention_deadline is not None
                    uow.commit_before_retention_deadline(
                        workspace_id=request.workspace_id,
                        product_brief_id=product_brief.id,
                        retention_deadline=analysis.retention_deadline,
                        clock=self._clock,
                    )
                retry_base = now
            assert outcome.error is not None
            if outcome.status == VisionProviderStatus.UNKNOWN:
                raise UnknownOperationOutcome(
                    NormalizedOperationError(
                        code=outcome.error.code,
                        category=outcome.error.category,
                        message=outcome.error.message,
                        retryable=False,
                        provider_request_id=outcome.request_id,
                    )
                )
            retry_at = (
                retry_base + timedelta(seconds=outcome.error.retry_after_seconds)
                if outcome.error.retry_after_seconds is not None
                else None
            )
            raise OperationExecutionFailure(
                NormalizedOperationError(
                    code=outcome.error.code,
                    category=outcome.error.category,
                    message=outcome.error.message,
                    retryable=outcome.error.retryable,
                    provider_request_id=outcome.request_id,
                ),
                retry_at=retry_at,
            )

        source_ids = {source.asset_version_id for source in analysis.sources}
        provider_fields = tuple(
            ProductBriefField.create(
                path=field.path,
                value=field.value.model_dump(mode="json"),
                confidence=field.confidence,
                source=ProductBriefFieldSource.MODEL,
                conflict=field.conflict,
                review_required=field.review_required,
                sensitive=field.sensitive,
                evidence=tuple(
                    ProductBriefEvidence.create(
                        source_asset_version_id=evidence.source_asset_version_id,
                        kind=evidence.kind,
                        reference=_controlled_provider_evidence_reference(
                            field_path=field.path,
                            evidence=evidence,
                        ),
                        region=evidence.region,
                        excerpt_sha256=evidence.excerpt_sha256,
                    )
                    for evidence in field.evidence
                ),
            )
            for field in outcome.output.fields
        )
        fields = review_policy.enforce_risk_floor(provider_fields)
        if any(
            evidence.source_asset_version_id not in source_ids
            for field in fields
            for evidence in field.evidence
        ):
            raise OperationExecutionFailure(
                NormalizedOperationError(
                    code="VISION_EVIDENCE_SOURCE_INVALID",
                    category="provider_schema",
                    message="Vision evidence references an unsupplied Asset Version",
                    retryable=False,
                    provider_request_id=outcome.request_id,
                )
            )
        review = review_policy.evaluate(fields)
        with self._uow_factory() as uow:
            current, workflow, _, now = self._lock_current_execution(
                uow=uow,
                request=request,
                analysis=analysis,
                product_brief=product_brief,
            )
            if current.workflow_id != workflow.id:
                raise OperationExecutionFailure(
                    NormalizedOperationError(
                        code="PRODUCT_BRIEF_WORKFLOW_MISMATCH",
                        category="persistence",
                        message="ProductBrief workflow identity changed before completion",
                        retryable=False,
                    )
                )
            if current.current_version_id is not None and current.state != ProductBriefState.DRAFT:
                if current.state == ProductBriefState.AWAITING_CONFIRMATION:
                    raise OperationHumanWaitRequired(
                        output_ref=f"mysql://product-briefs/{current.id}",
                        provider_request_id=outcome.request_id,
                    )
                return OperationExecutionResult(
                    operation_id=request.operation_id,
                    output_ref=(f"mysql://product-brief-versions/{current.current_version_id}"),
                    provider_request_id=outcome.request_id,
                )
            provider_calls = tuple(
                _stored_provider_call(
                    request=request,
                    analysis=analysis,
                    call=call,
                    now=now,
                )
                for call in outcome.calls
            )
            effective_calls, _ = self._store_provider_calls_once(
                uow=uow,
                request=request,
                candidates=provider_calls,
            )
            successful_call = next(
                call
                for call in reversed(effective_calls)
                if call.status == VisionProviderStatus.SUCCEEDED
            )
            if successful_call.product_brief_id != current.id:
                raise OperationExecutionFailure(
                    NormalizedOperationError(
                        code="VISION_PROVIDER_CALL_REPLAY_MISMATCH",
                        category="persistence",
                        message="persisted Vision call belongs to a different ProductBrief",
                        retryable=False,
                    )
                )
            if successful_call.response_artifact_id is None:
                self._raise_provider_artifact_ledger_mismatch()
            if workflow.version != analysis.expected_workflow_version:
                raise OperationExecutionFailure(
                    NormalizedOperationError(
                        code="WORKFLOW_VERSION_CHANGED",
                        category="conflict",
                        message="workflow changed before ProductBrief completion",
                        retryable=False,
                    )
                )
            version = ProductBriefVersion.create(
                workspace_id=request.workspace_id,
                product_brief_id=current.id,
                version_number=uow.product_briefs.next_version_number(
                    workspace_id=request.workspace_id,
                    product_brief_id=current.id,
                ),
                supersedes_version_id=current.current_version_id,
                category=outcome.output.category,
                common_schema_version=outcome.output.common_schema_version,
                category_schema_version=outcome.output.category_schema_version,
                fields=fields,
                review_decision=review,
                source=ProductBriefVersionSource.MODEL,
                prompt_version=analysis.prompt_version,
                provider_call_id=successful_call.id,
                actor_id="system:vision-analyzer",
                revision_reason=None,
                retention_class=current.retention_class,
                retention_deadline=current.retention_deadline,
                now=now,
            )
            uow.product_briefs.add_version(
                StoredProductBriefVersion(
                    version=version,
                    review_reasons_by_path=review.reasons_by_path,
                )
            )
            current.publish_version(
                version,
                expected_version=current.version,
                now=now,
            )
            target = (
                WorkflowStatus.AWAITING_PRODUCT_CONFIRMATION
                if version.confirmation_required
                else WorkflowStatus.RETRIEVING
            )
            workflow.transition(
                target,
                current_node=(
                    "confirm_product_brief"
                    if version.confirmation_required
                    else "retrieve_references"
                ),
                now=now,
            )
            uow.product_briefs.save(current)
            uow.workflows.save(workflow)
            if version.confirmation_required:
                uow.outbox.add(
                    _product_brief_event(
                        event_type=EventType.PRODUCT_BRIEF_AWAITING_CONFIRMATION,
                        workspace_id=request.workspace_id,
                        product_brief=current,
                        trace_id=analysis.trace_id,
                        payload=ProductBriefAwaitingConfirmationPayload(
                            workspace_id=request.workspace_id,
                            product_brief_id=current.id,
                            product_brief_version=current.version,
                            product_brief_version_id=version.id,
                            product_brief_version_number=version.version_number,
                            workflow_id=workflow.id,
                            operation_id=request.operation_id,
                            unresolved_field_count=version.unresolved_field_count,
                            review_policy_version=version.review_policy_version,
                        ).model_dump(mode="json"),
                        now=now,
                    )
                )
            else:
                uow.outbox.add(
                    _product_brief_event(
                        event_type=EventType.PRODUCT_BRIEF_CONFIRMED,
                        workspace_id=request.workspace_id,
                        product_brief=current,
                        trace_id=analysis.trace_id,
                        payload=ProductBriefConfirmedPayload(
                            workspace_id=request.workspace_id,
                            product_brief_id=current.id,
                            product_brief_version=current.version,
                            product_brief_version_id=version.id,
                            product_brief_version_number=version.version_number,
                            workflow_id=workflow.id,
                            operation_id=request.operation_id,
                            confirmation_id=None,
                            confirmation_source="POLICY",
                        ).model_dump(mode="json"),
                        now=now,
                    )
                )
                uow.outbox.add(
                    _workflow_event(
                        workflow_id=workflow.id,
                        workspace_id=request.workspace_id,
                        workflow_version=workflow.version,
                        trace_id=analysis.trace_id,
                        event_type=EventType.WORKFLOW_RUN_REQUESTED,
                        payload=WorkflowRunRequestedPayload(
                            workflow_id=workflow.id,
                            action="recover",
                            reason="product-brief-policy-confirmed",
                            product_brief_version_id=version.id,
                            product_brief_version_number=version.version_number,
                        ).model_dump(mode="json", exclude_none=True),
                        now=now,
                    )
                )
            _audit(
                uow=uow,
                workspace_id=request.workspace_id,
                actor_id="system:vision-analyzer",
                trace_id=analysis.trace_id,
                action="product_brief.version.generated",
                product_brief_id=current.id,
                metadata={
                    "confirmation_required": version.confirmation_required,
                    "product_brief_version_id": version.id,
                    "provider_call_id": successful_call.id,
                    "unresolved_field_count": version.unresolved_field_count,
                },
                now=now,
                expires_at=current.retention_deadline,
            )
            assert current.retention_deadline is not None
            uow.commit_before_retention_deadline(
                workspace_id=request.workspace_id,
                product_brief_id=current.id,
                retention_deadline=current.retention_deadline,
                clock=self._clock,
            )
        if version.confirmation_required:
            raise OperationHumanWaitRequired(
                output_ref=f"mysql://product-briefs/{current.id}",
                provider_request_id=outcome.request_id,
            )
        return OperationExecutionResult(
            operation_id=request.operation_id,
            output_ref=f"mysql://product-brief-versions/{version.id}",
            provider_request_id=outcome.request_id,
        )

    def _store_provider_calls_once(
        self,
        *,
        uow: ProductBriefUnitOfWorkPort,
        request: OperationExecutionRequest,
        candidates: tuple[StoredProviderCall, ...],
    ) -> tuple[tuple[StoredProviderCall, ...], bool]:
        candidates = tuple(
            self._bind_provider_artifact_ledger_rows(
                uow=uow,
                candidate=candidate,
            )
            for candidate in candidates
        )
        persisted = uow.product_brief_analyses.list_provider_calls(
            workspace_id=request.workspace_id,
            operation_id=request.operation_id,
            operation_attempt=request.attempt_count,
        )
        persisted_signatures = tuple(map(_stored_provider_call_signature, persisted))
        candidate_signatures = tuple(map(_stored_provider_call_signature, candidates))
        if (
            len(persisted_signatures) > len(candidate_signatures)
            or persisted_signatures != candidate_signatures[: len(persisted_signatures)]
        ):
            raise OperationExecutionFailure(
                NormalizedOperationError(
                    code="VISION_PROVIDER_CALL_REPLAY_MISMATCH",
                    category="persistence",
                    message=(
                        "Vision provider calls changed while replaying the same operation attempt"
                    ),
                    retryable=False,
                )
            )
        missing = candidates[len(persisted) :]
        if not missing:
            return persisted, False
        uow.product_brief_analyses.add_provider_calls(missing)
        return (*persisted, *missing), True

    def _bind_provider_artifact_ledger_rows(
        self,
        *,
        uow: ProductBriefUnitOfWorkPort,
        candidate: StoredProviderCall,
    ) -> StoredProviderCall:
        request_artifact = self._require_stored_provider_artifact(
            uow=uow,
            candidate=candidate,
            kind=ProviderArtifactKind.REQUEST,
            reference=candidate.request_artifact,
        )
        response_artifact = uow.product_brief_artifacts.get_provider_artifact(
            workspace_id=candidate.workspace_id,
            operation_id=candidate.operation_id,
            operation_attempt=candidate.operation_attempt,
            call_index=candidate.call_index,
            kind=ProviderArtifactKind.RESPONSE,
        )
        if candidate.response_artifact is None:
            if candidate.status == VisionProviderStatus.SUCCEEDED:
                self._raise_provider_artifact_ledger_mismatch()
            if response_artifact is not None and not (
                candidate.status == VisionProviderStatus.UNKNOWN
                and candidate.error_code == "PROVIDER_RESPONSE_ARTIFACT_OUTCOME_UNKNOWN"
                and candidate.error_category == "unknown_outcome"
                and candidate.error_retryable is False
                and response_artifact.product_brief_id == candidate.product_brief_id
                and response_artifact.retention_class == candidate.retention_class
                and response_artifact.retention_deadline == candidate.retention_deadline
                and response_artifact.state
                in {
                    ProviderArtifactState.INTENDED,
                    ProviderArtifactState.UNKNOWN,
                }
            ):
                self._raise_provider_artifact_ledger_mismatch()
            response_artifact_id = None
        else:
            response_artifact = self._require_stored_provider_artifact(
                uow=uow,
                candidate=candidate,
                kind=ProviderArtifactKind.RESPONSE,
                reference=candidate.response_artifact,
            )
            response_artifact_id = response_artifact.id
        return replace(
            candidate,
            request_artifact_id=request_artifact.id,
            response_artifact_id=response_artifact_id,
        )

    def _require_stored_provider_artifact(
        self,
        *,
        uow: ProductBriefUnitOfWorkPort,
        candidate: StoredProviderCall,
        kind: ProviderArtifactKind,
        reference: ProviderArtifactReference,
    ) -> StoredProviderArtifact:
        artifact = uow.product_brief_artifacts.get_provider_artifact(
            workspace_id=candidate.workspace_id,
            operation_id=candidate.operation_id,
            operation_attempt=candidate.operation_attempt,
            call_index=candidate.call_index,
            kind=kind,
        )
        if (
            artifact is None
            or artifact.product_brief_id != candidate.product_brief_id
            or artifact.state != ProviderArtifactState.STORED
            or _stored_provider_artifact_reference(artifact) != reference
        ):
            self._raise_provider_artifact_ledger_mismatch()
        return artifact

    @staticmethod
    def _raise_provider_artifact_ledger_mismatch() -> None:
        raise OperationExecutionFailure(
            NormalizedOperationError(
                code="VISION_PROVIDER_ARTIFACT_LEDGER_MISMATCH",
                category="storage_integrity",
                message=(
                    "Vision provider call does not match its durable provider artifact ledger rows"
                ),
                retryable=False,
            )
        )

    @staticmethod
    def _validate_operation(request: OperationExecutionRequest) -> None:
        if (
            request.kind != OperationKind.PRODUCT_BRIEF_ANALYSIS
            or request.target_type != "product_brief"
        ):
            raise OperationExecutionFailure(
                NormalizedOperationError(
                    code="PRODUCT_BRIEF_OPERATION_MISMATCH",
                    category="input",
                    message="operation is not a ProductBrief analysis",
                    retryable=False,
                )
            )


def _field_revision_signature(field: ProductBriefField) -> tuple[object, ...]:
    return (
        canonical_hash(field.value),
        str(field.confidence),
        field.conflict.value,
        field.review_required,
        field.sensitive,
        tuple(
            sorted(
                (
                    evidence.source_asset_version_id,
                    evidence.kind.value,
                    evidence.reference,
                    evidence.region or (),
                    evidence.excerpt_sha256 or "",
                )
                for evidence in field.evidence
            )
        ),
    )


def _stored_provider_call(
    *,
    request: OperationExecutionRequest,
    analysis: ProductBriefAnalysisRecord,
    call: Any,
    now: datetime,
) -> StoredProviderCall:
    return StoredProviderCall(
        id=new_uuid7(),
        workspace_id=request.workspace_id,
        product_brief_id=analysis.product_brief_id,
        operation_id=request.operation_id,
        operation_attempt=request.attempt_count,
        call_index=call.call_index,
        status=call.status,
        provider=call.provider,
        endpoint_region=call.endpoint_region,
        endpoint_host=call.endpoint_host,
        requested_model=call.requested_model,
        submitted_model_snapshot=call.submitted_model_snapshot,
        resolved_model=call.resolved_model,
        prompt_version=call.prompt_version,
        config_snapshot_sha256=call.config_snapshot_sha256,
        request_id=call.request_id,
        input_tokens=call.usage.input_tokens,
        output_tokens=call.usage.output_tokens,
        total_tokens=call.usage.total_tokens,
        latency_ms=call.latency_ms,
        request_artifact=call.request_artifact,
        response_artifact=call.response_artifact,
        error_code=call.error.code if call.error is not None else None,
        error_category=call.error.category if call.error is not None else None,
        error_retryable=_normalized_provider_retryable(
            status=call.status,
            retryable=(call.error.retryable if call.error is not None else None),
        ),
        retention_class=analysis.retention_class,
        retention_deadline=analysis.retention_deadline,
        created_at=now,
    )


def _normalized_provider_retryable(
    *,
    status: VisionProviderStatus,
    retryable: bool | None,
) -> bool | None:
    if status == VisionProviderStatus.UNKNOWN:
        return False
    return retryable


def _provider_submission_outcome_unknown_error() -> NormalizedOperationError:
    return NormalizedOperationError(
        code="VISION_SUBMISSION_OUTCOME_UNKNOWN",
        category="worker_interruption",
        message=(
            "provider submission was recorded but no durable outcome "
            "or provider lookup is available"
        ),
        retryable=False,
    )


def _provider_artifact_outcome_unknown_error() -> NormalizedOperationError:
    return NormalizedOperationError(
        code="VISION_ARTIFACT_OUTCOME_UNKNOWN",
        category="storage_integrity",
        message=(
            "provider artifact storage outcome remains ambiguous after "
            "bounded exact-key reconciliation"
        ),
        retryable=False,
    )


_EVIDENCE_REFERENCE_PREFIXES = {
    ProductBriefEvidenceKind.IMAGE_REGION: "asset-region://",
    ProductBriefEvidenceKind.VISIBLE_TEXT: "asset-text://",
    ProductBriefEvidenceKind.PRODUCT_DATA: "product-data://",
    ProductBriefEvidenceKind.HUMAN_NOTE: "human-note://",
}


def _controlled_provider_evidence_reference(
    *,
    field_path: str,
    evidence: ProductBriefEvidenceOutput,
) -> str:
    digest = canonical_hash(
        {
            "excerpt_sha256": evidence.excerpt_sha256,
            "field_path": field_path,
            "kind": evidence.kind.value,
            "region": list(evidence.region) if evidence.region is not None else None,
            "source_asset_version_id": evidence.source_asset_version_id,
        }
    )
    return f"{_EVIDENCE_REFERENCE_PREFIXES[evidence.kind]}{digest}"


def _stored_provider_call_signature(call: StoredProviderCall) -> tuple[object, ...]:
    return (
        call.workspace_id,
        call.product_brief_id,
        call.operation_id,
        call.operation_attempt,
        call.call_index,
        call.status,
        call.provider,
        call.endpoint_region,
        call.endpoint_host,
        call.requested_model,
        call.submitted_model_snapshot,
        call.resolved_model,
        call.prompt_version,
        call.config_snapshot_sha256,
        call.request_id,
        call.input_tokens,
        call.output_tokens,
        call.total_tokens,
        call.latency_ms,
        _provider_artifact_signature(call.request_artifact),
        (
            _provider_artifact_signature(call.response_artifact)
            if call.response_artifact is not None
            else None
        ),
        call.error_code,
        call.error_category,
        call.error_retryable,
        call.retention_class,
        call.retention_deadline,
    )


def _provider_artifact_signature(
    artifact: ProviderArtifactReference,
) -> tuple[object, ...]:
    return (
        artifact.storage_backend,
        artifact.location,
        artifact.bucket,
        artifact.key,
        artifact.provider_version_id,
        artifact.etag,
        artifact.sha256,
        artifact.byte_size,
        artifact.retention_class,
        artifact.retention_deadline,
    )


def _stored_provider_artifact_reference(
    artifact: StoredProviderArtifact,
) -> ProviderArtifactReference:
    if (
        artifact.state != ProviderArtifactState.STORED
        or artifact.provider_version_id is None
        or artifact.etag is None
    ):
        raise StoragePreconditionError("provider artifact ledger row has no exact stored reference")
    return ProviderArtifactReference(
        storage_backend=artifact.storage_backend,
        location=artifact.location,
        bucket=artifact.bucket,
        key=artifact.key,
        provider_version_id=artifact.provider_version_id,
        etag=artifact.etag,
        sha256=artifact.expected_sha256,
        byte_size=artifact.expected_byte_size,
        retention_class=artifact.retention_class,
        retention_deadline=artifact.retention_deadline,
    )


def _product_brief_event(
    *,
    event_type: EventType,
    workspace_id: str,
    product_brief: ProductBrief,
    trace_id: str,
    payload: dict[str, Any],
    now: datetime,
) -> OutboxEvent:
    return OutboxEvent(
        envelope=EventEnvelope.create(
            event_type=event_type.value,
            aggregate_type="ProductBrief",
            aggregate_id=product_brief.id,
            aggregate_version=product_brief.version,
            trace_id=trace_id,
            payload=payload,
            now=now,
        ),
        available_at=now,
        workspace_id=workspace_id,
    )


def _workflow_event(
    *,
    workflow_id: str,
    workspace_id: str,
    workflow_version: int,
    trace_id: str,
    event_type: EventType,
    payload: dict[str, Any],
    now: datetime,
) -> OutboxEvent:
    return OutboxEvent(
        envelope=EventEnvelope.create(
            event_type=event_type.value,
            aggregate_type="workflow",
            aggregate_id=workflow_id,
            aggregate_version=workflow_version,
            trace_id=trace_id,
            payload=payload,
            now=now,
        ),
        available_at=now,
        workspace_id=workspace_id,
    )


def _audit(
    *,
    uow: Any,
    workspace_id: str,
    actor_id: str,
    trace_id: str,
    action: str,
    product_brief_id: str,
    metadata: dict[str, object],
    now: datetime,
    expires_at: datetime | None,
) -> None:
    uow.audit.add(
        workspace_id=workspace_id,
        actor_type="SYSTEM" if actor_id.startswith("system:") else "USER",
        actor_id=actor_id,
        action=action,
        resource_type="product_brief",
        resource_id=product_brief_id,
        trace_id=trace_id,
        metadata=metadata,
        created_at=now,
        expires_at=expires_at or now + timedelta(days=180),
    )
