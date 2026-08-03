"""Worker dependency composition and Outbox event processing."""

from __future__ import annotations

import logging
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from commercevision_agent_core import (
    FixtureAgentRuntime,
    FixtureAgentState,
    build_fixture_graph,
)
from commercevision_application import (
    BrandProfileDeletionLineageError,
    BrandProfileInvalidationApplicationService,
    BrandProfileInvalidationPort,
    DurableNodeLifecycle,
    DurableOperationWorker,
    EventRoutingError,
    EventRoutingRegistry,
    InboxCoordinator,
    OperationApplicationService,
    OperationExecutionBoundary,
    OperationExecutor,
    OperationExecutorRegistry,
    OperationReconciliationPolicy,
    OperationRetryPolicy,
    ProductBriefContinuation,
    ProductBriefContinuationAuthorityError,
    ProductBriefContinuationClaim,
    ProductBriefRecoveryClaim,
    StaleProductBriefContinuation,
    UploadObjectCleaner,
    build_event_routing_registry,
)
from commercevision_contracts import Settings
from commercevision_contracts.events import (
    ASSET_DELETE_COMPLETED_V1,
    ASSET_DELETE_REQUESTED_V1,
    ASSET_INDEX_COMPLETED_V1,
    ASSET_INDEX_DELETE_REQUESTED_V1,
    ASSET_INDEX_REQUESTED_V1,
    ASSET_RIGHTS_CHANGED_V1,
    ASSET_RIGHTS_EXPIRED_V1,
    ASSET_UPLOAD_FINALIZED_V1,
    ASSET_VALIDATION_COMPLETED_V1,
    ASSET_VALIDATION_FAILED_V1,
    ASSET_VALIDATION_REQUESTED_V1,
    BRAND_PROFILE_PUBLISHED_V1,
    DEAD_LETTER_REPLAY_RECORDED_V1,
    OPERATION_RECOVERY_REQUESTED_V1,
    PRODUCT_BRIEF_AWAITING_CONFIRMATION_V1,
    PRODUCT_BRIEF_CONFIRMED_V1,
    PRODUCT_BRIEF_REQUESTED_V1,
    WORKFLOW_CANCELLED_V1,
    WORKFLOW_FAILED_V1,
    WORKFLOW_HUMAN_INPUT_RECEIVED_V1,
    WORKFLOW_HUMAN_INPUT_REQUIRED_V1,
    WORKFLOW_NODE_COMPLETED_V1,
    WORKFLOW_NODE_STARTED_V1,
    WORKFLOW_RESUME_REQUESTED_V1,
    WORKFLOW_RUN_REQUESTED_V1,
    AssetDeleteCompletedPayload,
    AssetDeleteRequestedPayload,
    AssetIndexCompletedPayload,
    AssetIndexDeleteRequestedPayload,
    AssetIndexRequestedPayload,
    AssetRightsChangedPayload,
    AssetUploadFinalizedPayload,
    AssetValidationCompletedPayload,
    AssetValidationFailedPayload,
    AssetValidationRequestedPayload,
    BrandProfilePublishedPayload,
    EventQueue,
    EventType,
    ProductBriefAwaitingConfirmationPayload,
    ProductBriefConfirmedPayload,
    ProductBriefRequestedPayload,
    WorkflowResumeRequestedPayload,
    WorkflowRunRequestedPayload,
)
from commercevision_contracts.object_storage import ObjectStorage
from commercevision_domain import (
    ApprovalType,
    LeaseConflictError,
    NotFoundError,
    OperationKind,
    StorageLocationClass,
    canonicalize_uuid,
)
from commercevision_domain.messaging import OutboxEvent
from commercevision_object_storage import (
    build_object_storage,
    close_object_storage,
)
from commercevision_persistence import (
    Database,
    ImageIndexNotApplicable,
    MySQLCheckpointSaver,
    MySqlImageIndexRequestService,
    MySqlIndexingAuthority,
    SqlAlchemyAssetUnitOfWork,
    SqlAlchemyBrandProfileUnitOfWork,
    SqlAlchemyOperationUnitOfWork,
    SqlAlchemyUnitOfWork,
    create_database,
    is_unit_of_work_active,
)
from commercevision_tool_runtime import (
    FixtureImageTool,
    ToolDefinition,
    ToolExecutionGateway,
    ToolRegistry,
)
from commercevision_tool_runtime.policy import ToolPolicy

from . import product_brief
from .asset_cleanup import UploadSessionCleanupExecutor
from .asset_validation import build_asset_validation_executor
from .executors import available_builtin_operation_kinds
from .image_indexing import build_image_index_request_service, build_image_indexing

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class WorkerRuntime:
    database: Database
    settings: Settings
    worker_id: str
    inbox: InboxCoordinator
    agent: FixtureAgentRuntime
    event_router: EventRoutingRegistry
    operation_worker: DurableOperationWorker
    operation_executors: OperationExecutorRegistry
    object_storage: ObjectStorage | None
    resources: tuple[object, ...]
    image_index_requests: MySqlImageIndexRequestService | None = None
    image_index_authority: MySqlIndexingAuthority | None = None
    image_vector_index: object | None = None
    brand_profile_invalidation: BrandProfileInvalidationPort | None = None
    lifecycle: DurableNodeLifecycle | None = None

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        operation_executors: Mapping[OperationKind, OperationExecutor] | None = None,
        brand_profile_invalidation: BrandProfileInvalidationPort | None = None,
    ) -> WorkerRuntime:
        configured_executors = dict(operation_executors or {})
        missing_executors = set(settings.worker_required_operation_kinds).difference(
            set(configured_executors).union(available_builtin_operation_kinds(settings))
        )
        if missing_executors:
            missing = ", ".join(sorted(kind.value for kind in missing_executors))
            raise RuntimeError(f"required operation executors are unavailable: {missing}")
        database = create_database(settings)
        if brand_profile_invalidation is None:
            brand_profile_invalidation = BrandProfileInvalidationApplicationService(
                lambda: SqlAlchemyBrandProfileUnitOfWork(database.session_factory)
            )
        object_storage = (
            build_object_storage(settings) if settings.worker_requires_object_storage else None
        )
        resources: list[object] = []
        image_index_requests: MySqlImageIndexRequestService | None = None
        image_index_authority: MySqlIndexingAuthority | None = None
        image_vector_index: object | None = None
        if (
            object_storage is not None
            and settings.worker_requires_asset_validation
            and OperationKind.ASSET_VALIDATION not in configured_executors
        ):
            built_validation = build_asset_validation_executor(
                settings=settings,
                database=database,
                storage=object_storage,
            )
            configured_executors[OperationKind.ASSET_VALIDATION] = built_validation.executor
            resources.extend(built_validation.closeables)
        if (
            object_storage is not None
            and settings.asset_queue_name in settings.configured_worker_queues
            and OperationKind.PRODUCT_BRIEF_ANALYSIS not in configured_executors
        ):
            built_product_brief = product_brief.build_product_brief_executor(
                settings=settings,
                database=database,
                storage=object_storage,
            )
            configured_executors[OperationKind.PRODUCT_BRIEF_ANALYSIS] = (
                built_product_brief.executor
            )
            resources.extend(built_product_brief.closeables)
        if (
            object_storage is not None
            and settings.maintenance_queue_name in settings.configured_worker_queues
            and OperationKind.ASSET_DELETION not in configured_executors
        ):
            configured_executors[OperationKind.ASSET_DELETION] = UploadSessionCleanupExecutor(
                uow_factory=lambda: SqlAlchemyAssetUnitOfWork(database.session_factory),
                cleaner=UploadObjectCleaner(object_storage),
                reconciliation_interval=timedelta(
                    seconds=settings.upload_cleanup_reconcile_interval_seconds
                ),
                final_cleanup_budget=timedelta(
                    seconds=settings.operation_retry_max_elapsed_seconds
                ),
            )
        if (
            object_storage is not None
            and settings.index_queue_name in settings.configured_worker_queues
            and OperationKind.ASSET_INDEXING not in configured_executors
        ):
            built_indexing = build_image_indexing(
                settings=settings,
                database=database,
                storage=object_storage,
            )
            configured_executors[OperationKind.ASSET_INDEXING] = built_indexing.executor
            image_index_requests = built_indexing.request_service
            image_index_authority = built_indexing.authority
            image_vector_index = built_indexing.vector_index
            resources.extend(built_indexing.closeables)
        if {
            settings.asset_queue_name,
            settings.index_queue_name,
        }.intersection(settings.configured_worker_queues) and image_index_requests is None:
            image_index_requests = build_image_index_request_service(
                settings=settings,
                database=database,
            )
        if {
            settings.asset_queue_name,
            settings.index_queue_name,
        }.intersection(settings.configured_worker_queues) and image_index_authority is None:
            image_index_authority = MySqlIndexingAuthority(database.session_factory)

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(database.session_factory)

        def operation_uow_factory() -> SqlAlchemyOperationUnitOfWork:
            return SqlAlchemyOperationUnitOfWork(database.session_factory)

        worker_id = f"{socket.gethostname()}:{settings.service_name}"
        lifecycle = DurableNodeLifecycle(
            uow_factory=uow_factory,
            lease_duration=timedelta(seconds=settings.workflow_step_lease_seconds),
        )
        fixture_tool = FixtureImageTool()
        registry = ToolRegistry(
            [
                ToolDefinition(
                    name=fixture_tool.name,
                    version=fixture_tool.version,
                    description="Deterministic Phase 1 fixture image generation",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "count": {"type": "integer", "minimum": 1, "maximum": 10},
                            "delay_seconds": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 120,
                            },
                        },
                    },
                    output_schema={
                        "type": "object",
                        "required": ["candidates"],
                    },
                    implementation=fixture_tool,
                )
            ]
        )
        gateway = ToolExecutionGateway(
            registry=registry,
            policy=ToolPolicy(
                version="tool-policy-v1",
                allowed_tools=frozenset({fixture_tool.name}),
                transaction_active=is_unit_of_work_active,
            ),
        )
        checkpointer = MySQLCheckpointSaver(
            database.session_factory,
            retention=timedelta(hours=settings.workflow_retention_hours),
        )
        graph = build_fixture_graph(
            lifecycle=lifecycle,
            tool_gateway=gateway,
            checkpointer=checkpointer,
            worker_id=worker_id,
        )
        executor_registry = OperationExecutorRegistry()
        for kind, executor in configured_executors.items():
            executor_registry.register(kind=kind, executor=executor)
        operation_worker = DurableOperationWorker(
            operations=OperationApplicationService(
                uow_factory=operation_uow_factory,
                execution_max_elapsed=timedelta(
                    seconds=settings.operation_retry_max_elapsed_seconds
                ),
            ),
            execution=OperationExecutionBoundary(
                executor=executor_registry,
                transaction_active=is_unit_of_work_active,
            ),
            owner=worker_id,
            lease_duration=timedelta(seconds=settings.workflow_step_lease_seconds),
            retry_policy=OperationRetryPolicy(
                initial_delay=timedelta(seconds=settings.operation_retry_initial_seconds),
                maximum_delay=timedelta(seconds=settings.operation_retry_max_seconds),
                maximum_elapsed=timedelta(seconds=settings.operation_retry_max_elapsed_seconds),
            ),
            reconciliation_policy=OperationReconciliationPolicy(
                initial_delay=timedelta(seconds=settings.operation_reconciliation_initial_seconds),
                maximum_delay=timedelta(seconds=settings.operation_reconciliation_max_seconds),
                maximum_elapsed=timedelta(
                    seconds=settings.operation_reconciliation_max_elapsed_seconds
                ),
            ),
        )
        runtime = cls(
            database=database,
            settings=settings,
            worker_id=worker_id,
            inbox=InboxCoordinator(
                uow_factory=uow_factory,
                consumer=settings.worker_consumer_name,
                owner=worker_id,
                lease_duration=timedelta(seconds=settings.workflow_step_lease_seconds),
                max_attempts=settings.workflow_message_max_attempts,
                retry_initial=timedelta(seconds=settings.worker_message_retry_initial_seconds),
                retry_max=timedelta(seconds=settings.worker_message_retry_max_seconds),
            ),
            agent=FixtureAgentRuntime(graph, checkpointer),
            operation_worker=operation_worker,
            operation_executors=executor_registry,
            brand_profile_invalidation=brand_profile_invalidation,
            object_storage=object_storage,
            resources=tuple(resources),
            image_index_requests=image_index_requests,
            image_index_authority=image_index_authority,
            image_vector_index=image_vector_index,
            lifecycle=lifecycle,
            event_router=build_event_routing_registry(
                {
                    EventQueue.WORKFLOW: settings.workflow_queue_name,
                    EventQueue.ASSET: settings.asset_queue_name,
                    EventQueue.INDEX: settings.index_queue_name,
                    EventQueue.MAINTENANCE: settings.maintenance_queue_name,
                }
            ),
        )
        runtime.event_router.register_handler(
            contract=WORKFLOW_RUN_REQUESTED_V1,
            handler=runtime._handle_workflow_event,
        )
        runtime.event_router.register_handler(
            contract=WORKFLOW_RESUME_REQUESTED_V1,
            handler=runtime._handle_workflow_event,
        )
        for contract in (
            WORKFLOW_NODE_STARTED_V1,
            WORKFLOW_NODE_COMPLETED_V1,
            WORKFLOW_HUMAN_INPUT_REQUIRED_V1,
            WORKFLOW_HUMAN_INPUT_RECEIVED_V1,
            WORKFLOW_FAILED_V1,
            WORKFLOW_CANCELLED_V1,
        ):
            runtime.event_router.register_handler(
                contract=contract,
                handler=runtime._observe_workflow_event,
            )
        runtime.event_router.register_handler(
            contract=OPERATION_RECOVERY_REQUESTED_V1,
            handler=runtime._handle_operation_recovery,
        )
        runtime.event_router.register_handler(
            contract=DEAD_LETTER_REPLAY_RECORDED_V1,
            handler=runtime._observe_replay_event,
        )
        runtime.event_router.register_handler(
            contract=ASSET_UPLOAD_FINALIZED_V1,
            handler=runtime._observe_asset_upload_finalized,
        )
        runtime.event_router.register_handler(
            contract=ASSET_VALIDATION_REQUESTED_V1,
            handler=runtime._handle_asset_validation,
        )
        runtime.event_router.register_handler(
            contract=ASSET_VALIDATION_COMPLETED_V1,
            handler=runtime._observe_asset_validation_terminal,
        )
        runtime.event_router.register_handler(
            contract=ASSET_VALIDATION_FAILED_V1,
            handler=runtime._observe_asset_validation_terminal,
        )
        runtime.event_router.register_handler(
            contract=ASSET_INDEX_REQUESTED_V1,
            handler=runtime._handle_asset_index,
        )
        runtime.event_router.register_handler(
            contract=ASSET_INDEX_COMPLETED_V1,
            handler=runtime._observe_asset_index_completed,
        )
        runtime.event_router.register_handler(
            contract=ASSET_INDEX_DELETE_REQUESTED_V1,
            handler=runtime._handle_asset_index_delete,
        )
        runtime.event_router.register_handler(
            contract=ASSET_DELETE_REQUESTED_V1,
            handler=runtime._handle_asset_delete,
        )
        runtime.event_router.register_handler(
            contract=ASSET_DELETE_COMPLETED_V1,
            handler=runtime._handle_foundation_asset_deleted,
        )
        runtime.event_router.register_handler(
            contract=ASSET_RIGHTS_CHANGED_V1,
            handler=runtime._handle_asset_rights_changed,
        )
        runtime.event_router.register_handler(
            contract=ASSET_RIGHTS_EXPIRED_V1,
            handler=runtime._handle_asset_rights_changed,
        )
        runtime.event_router.register_handler(
            contract=BRAND_PROFILE_PUBLISHED_V1,
            handler=runtime._observe_brand_profile_published,
        )
        runtime.event_router.register_handler(
            contract=PRODUCT_BRIEF_REQUESTED_V1,
            handler=runtime._handle_product_brief_analysis,
        )
        runtime.event_router.register_handler(
            contract=PRODUCT_BRIEF_AWAITING_CONFIRMATION_V1,
            handler=runtime._observe_product_brief_state,
        )
        runtime.event_router.register_handler(
            contract=PRODUCT_BRIEF_CONFIRMED_V1,
            handler=runtime._observe_product_brief_state,
        )
        return runtime

    def operation_executor_readiness(self) -> dict[str, object]:
        self.assert_local_resources_ready()
        required = frozenset(self.settings.worker_required_operation_kinds)
        missing = self.operation_executors.missing(required)
        vision_credential = product_brief.validate_product_brief_vision_credential(self.settings)
        provider_result_storage = "not_required"
        if self.settings.asset_queue_name in self.settings.configured_worker_queues:
            if self.object_storage is None:
                raise RuntimeError("ProductBrief Worker requires provider-result object storage")
            self.object_storage.assert_ready((StorageLocationClass.PROVIDER_RESULT,))
            provider_result_storage = "ok"
        return {
            "ready": not missing,
            "required_kinds": sorted(kind.value for kind in required),
            "registered_kinds": sorted(
                kind.value for kind in self.operation_executors.registered_kinds
            ),
            "missing_kinds": sorted(kind.value for kind in missing),
            "vision_credential": vision_credential,
            "provider_result_storage": provider_result_storage,
        }

    def assert_local_resources_ready(self) -> None:
        """Fail closed when a process-owned adapter can no longer accept work."""

        for resource in self.resources:
            assert_ready = getattr(resource, "assert_ready", None)
            if callable(assert_ready):
                assert_ready()

    def process_event(self, event_id: str) -> str:
        claim, event = self.inbox.claim(event_id)
        if claim.already_processed:
            return "duplicate"
        if claim.dead:
            return "dead-lettered"
        if claim.retry_not_ready:
            return "retry-not-ready"
        if not claim.should_process or claim.lease_token is None:
            raise LeaseConflictError(f"message {event_id} is being processed")

        try:
            self.event_router.resolve(event.envelope)(event)
        except EventRoutingError as exc:
            self.inbox.mark_permanent_failed(
                event_id,
                claim.lease_token,
                exc,
                delivery_attempt=claim.delivery_attempt,
            )
            return "dead-lettered"
        except Exception as exc:
            self.inbox.schedule_retry(
                event_id,
                claim.lease_token,
                exc,
                delivery_attempt=claim.delivery_attempt,
            )
            return "retry-scheduled"
        self.inbox.mark_processed(event_id, claim.lease_token)
        return "processed"

    def close(self) -> None:
        failures: list[Exception] = []
        shared_dependencies_drained = True
        for resource in reversed(self.resources):
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    failures.append(exc)
            if getattr(resource, "shutdown_drained", True) is False:
                shared_dependencies_drained = False
        if not shared_dependencies_drained:
            failures.append(
                RuntimeError(
                    "Worker shared dependencies remain open until resource lifecycles drain"
                )
            )
        elif self.object_storage is not None:
            try:
                close_object_storage(self.object_storage)
            except Exception as exc:
                failures.append(exc)
        if shared_dependencies_drained:
            try:
                self.database.dispose()
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise ExceptionGroup("Worker runtime shutdown failed", failures)

    def _load_initial_state(
        self,
        workflow_id: str,
        *,
        trace_id: str,
    ) -> FixtureAgentState:
        with SqlAlchemyUnitOfWork(self.database.session_factory) as uow:
            workflow = uow.workflows.get(workflow_id)
            if workflow is None:
                raise NotFoundError(f"workflow {workflow_id} was not found")
            input_data: dict[str, Any] = workflow.input_data
        fixture_config = input_data.get("fixture_config", input_data)
        return FixtureAgentState(
            workflow_id=workflow.id,
            workflow_version=workflow.version,
            workspace_id=workflow.workspace_id,
            actor_id=workflow.created_by,
            trace_id=trace_id,
            input_ref=f"mysql://workflows/{workflow.id}/input",
            fixture_config=fixture_config,
            current_node=workflow.current_node or "validate_input",
        )

    @staticmethod
    def _product_brief_initial_state(
        *,
        claim: ProductBriefContinuationClaim,
        continuation: ProductBriefContinuation,
        trace_id: str,
    ) -> FixtureAgentState:
        node_claim = claim.node_claim
        authority = claim.generation_authority
        if (
            node_claim is None
            or node_claim.lease_token is None
            or authority is None
            or authority.initial_step_id != node_claim.step_id
        ):
            raise RuntimeError("current ProductBrief continuation has no durable node claim")
        fixture_config = claim.input_data.get("fixture_config", claim.input_data)
        return FixtureAgentState(
            workflow_id=claim.workflow_id,
            workflow_version=claim.workflow_version,
            workspace_id=claim.workspace_id,
            actor_id=claim.actor_id,
            trace_id=trace_id,
            input_ref=f"mysql://workflows/{claim.workflow_id}/input",
            fixture_config=fixture_config,
            product_brief_ref=(
                f"mysql://product-brief-versions/{continuation.product_brief_version_id}"
            ),
            product_brief_version_id=continuation.product_brief_version_id,
            product_brief_version_number=continuation.product_brief_version_number,
            product_brief_approval_id=continuation.approval_id,
            product_brief_checkpoint_generation=authority.checkpoint_generation,
            current_node="retrieve_references",
            initial_entry_reason="PRODUCT_BRIEF_CONFIRMED",
            initial_step_id=node_claim.step_id,
        )

    @staticmethod
    def _product_brief_recovery_initial_state(
        *,
        claim: ProductBriefRecoveryClaim,
        trace_id: str,
    ) -> FixtureAgentState:
        continuation = claim.continuation
        authority = claim.generation_authority
        if continuation is None or authority is None:
            raise RuntimeError("current ProductBrief recovery has no generation authority")
        node_claim = claim.node_claim
        if claim.current_node == "retrieve_references" and (
            node_claim is None
            or node_claim.lease_token is None
            or node_claim.step_id != authority.initial_step_id
        ):
            raise RuntimeError("ProductBrief retrieval recovery has no live lease authority")
        fixture_config = claim.input_data.get("fixture_config", claim.input_data)
        return FixtureAgentState(
            workflow_id=claim.workflow_id,
            workflow_version=claim.workflow_version,
            workspace_id=claim.workspace_id,
            actor_id=claim.actor_id,
            trace_id=trace_id,
            input_ref=f"mysql://workflows/{claim.workflow_id}/input",
            fixture_config=fixture_config,
            product_brief_ref=(
                f"mysql://product-brief-versions/{continuation.product_brief_version_id}"
            ),
            product_brief_version_id=continuation.product_brief_version_id,
            product_brief_version_number=continuation.product_brief_version_number,
            product_brief_approval_id=continuation.approval_id,
            product_brief_checkpoint_generation=authority.checkpoint_generation,
            current_node=claim.current_node,
            initial_entry_reason="PRODUCT_BRIEF_CONFIRMED",
            initial_step_id=authority.initial_step_id,
        )

    def _handle_workflow_event(self, event: OutboxEvent) -> None:
        resume_payload: dict[str, Any] | None = None
        continuation: ProductBriefContinuation | None = None
        product_brief_recovery: tuple[str, int] | None = None
        preclaimed_step_id: str | None = None
        preclaimed_lease_token: str | None = None
        payload_workflow_id: str
        if event.envelope.event_type == EventType.WORKFLOW_RESUME_REQUESTED:
            validated = WORKFLOW_RESUME_REQUESTED_V1.validate_payload(event.envelope.payload)
            if not isinstance(validated, WorkflowResumeRequestedPayload):
                raise TypeError("workflow resume contract returned an unexpected payload")
            payload_workflow_id = validated.workflow_id
            if validated.approval_type == ApprovalType.PRODUCT_BRIEF:
                if validated.decision.value != "APPROVE":
                    raise EventRoutingError(
                        "ProductBrief continuation requires an APPROVE decision",
                        reason="product_brief_approval_mismatch",
                    )
                if validated.resulting_workflow_version != event.envelope.aggregate_version:
                    raise EventRoutingError(
                        "ProductBrief approval version does not match its Outbox envelope",
                        reason="product_brief_resume_mismatch",
                    )
                continuation = ProductBriefContinuation(
                    workspace_id=event.workspace_id or "",
                    product_brief_version_id=validated.subject_id,
                    product_brief_version_number=validated.subject_version,
                    approval_id=validated.approval_id,
                )
            else:
                resume_payload = validated.model_dump(mode="json")
        else:
            validated_run = WORKFLOW_RUN_REQUESTED_V1.validate_payload(event.envelope.payload)
            if not isinstance(validated_run, WorkflowRunRequestedPayload):
                raise TypeError("workflow run contract returned an unexpected payload")
            payload_workflow_id = validated_run.workflow_id
            has_version_id = validated_run.product_brief_version_id is not None
            has_version_number = validated_run.product_brief_version_number is not None
            if has_version_id != has_version_number:
                raise EventRoutingError(
                    "ProductBrief continuation identity is incomplete",
                    reason="product_brief_version_mismatch",
                )
            if has_version_id:
                is_policy_continuation = (
                    validated_run.action == "recover"
                    and validated_run.reason == "product-brief-policy-confirmed"
                )
                is_generation_recovery = (
                    validated_run.action == "recover"
                    and validated_run.reason in {"expired_step_lease", "stale_workflow"}
                ) or (
                    validated_run.action == "retry"
                    and validated_run.reason == "product-brief-generation-retry"
                )
                if not is_policy_continuation and not is_generation_recovery:
                    raise EventRoutingError(
                        "ProductBrief continuation provenance is invalid",
                        reason="product_brief_resume_mismatch",
                    )
                if is_generation_recovery:
                    product_brief_recovery = (
                        validated_run.product_brief_version_id or "",
                        validated_run.product_brief_version_number or 0,
                    )
                elif (
                    validated_run.action != "recover"
                    or validated_run.reason != "product-brief-policy-confirmed"
                ):
                    raise EventRoutingError(
                        "ProductBrief policy continuation provenance is invalid",
                        reason="product_brief_resume_mismatch",
                    )
                else:
                    continuation = ProductBriefContinuation(
                        workspace_id=event.workspace_id or "",
                        product_brief_version_id=validated_run.product_brief_version_id or "",
                        product_brief_version_number=(
                            validated_run.product_brief_version_number or 0
                        ),
                        approval_id=None,
                    )
        if (
            event.envelope.aggregate_type != "workflow"
            or event.envelope.aggregate_id != payload_workflow_id
        ):
            raise EventRoutingError(
                "Workflow continuation does not match its Outbox aggregate",
                reason="aggregate_mismatch",
            )
        if product_brief_recovery is not None:
            if self.lifecycle is None:
                raise RuntimeError("Worker ProductBrief recovery lifecycle is unavailable")
            try:
                recovery_claim = self.lifecycle.recover_product_brief_continuation(
                    workflow_id=event.envelope.aggregate_id,
                    expected_workflow_version=event.envelope.aggregate_version,
                    workspace_id=event.workspace_id or "",
                    product_brief_version_id=product_brief_recovery[0],
                    product_brief_version_number=product_brief_recovery[1],
                    lease_owner=self.worker_id,
                    trace_id=event.envelope.trace_id,
                )
            except ProductBriefContinuationAuthorityError as exc:
                raise EventRoutingError(str(exc), reason=exc.reason) from exc
            if recovery_claim.stale_reason is not None:
                logger.info(
                    "product_brief_recovery_stale",
                    extra={
                        "workflow_id": event.envelope.aggregate_id,
                        "event_id": event.envelope.event_id,
                        "stale_reason": recovery_claim.stale_reason,
                    },
                )
                return
            initial_state = self._product_brief_recovery_initial_state(
                claim=recovery_claim,
                trace_id=event.envelope.trace_id,
            )
            assert recovery_claim.generation_authority is not None
            preclaimed_step_id = recovery_claim.generation_authority.initial_step_id
            preclaimed_lease_token = (
                recovery_claim.node_claim.lease_token
                if recovery_claim.node_claim is not None
                else None
            )
        elif continuation is None:
            initial_state = self._load_initial_state(
                event.envelope.aggregate_id,
                trace_id=event.envelope.trace_id,
            )
        else:
            if self.lifecycle is None:
                raise RuntimeError("Worker ProductBrief continuation lifecycle is unavailable")
            try:
                claim = self.lifecycle.claim_product_brief_continuation(
                    workflow_id=event.envelope.aggregate_id,
                    expected_workflow_version=event.envelope.aggregate_version,
                    continuation=continuation,
                    lease_owner=self.worker_id,
                    trace_id=event.envelope.trace_id,
                )
            except ProductBriefContinuationAuthorityError as exc:
                raise EventRoutingError(str(exc), reason=exc.reason) from exc
            if claim.stale_reason is not None:
                logger.info(
                    "product_brief_continuation_stale",
                    extra={
                        "workflow_id": event.envelope.aggregate_id,
                        "event_id": event.envelope.event_id,
                        "stale_reason": claim.stale_reason,
                    },
                )
                return
            initial_state = self._product_brief_initial_state(
                claim=claim,
                continuation=continuation,
                trace_id=event.envelope.trace_id,
            )
            assert claim.generation_authority is not None
            preclaimed_step_id = claim.generation_authority.initial_step_id
            preclaimed_lease_token = (
                claim.node_claim.lease_token if claim.node_claim is not None else None
            )
        try:
            self.agent.run(
                initial_state=initial_state,
                resume_payload=resume_payload,
                preclaimed_step_id=preclaimed_step_id,
                preclaimed_lease_token=preclaimed_lease_token,
            )
        except StaleProductBriefContinuation as exc:
            logger.info(
                "product_brief_continuation_stale",
                extra={
                    "workflow_id": event.envelope.aggregate_id,
                    "event_id": event.envelope.event_id,
                    "stale_reason": exc.reason,
                },
            )
            return
        except ProductBriefContinuationAuthorityError as exc:
            raise EventRoutingError(str(exc), reason=exc.reason) from exc
        except Exception:
            if self._workflow_outcome_was_durably_recorded(event):
                return
            raise

    def _workflow_outcome_was_durably_recorded(self, event: OutboxEvent) -> bool:
        with SqlAlchemyUnitOfWork(self.database.session_factory) as uow:
            workflow = uow.workflows.get(event.envelope.aggregate_id)
            if workflow is None:
                return False
            if workflow.status.terminal:
                return True
            return uow.outbox.has_unpublished(
                aggregate_id=workflow.id,
                event_type=EventType.WORKFLOW_RUN_REQUESTED.value,
                exclude_event_id=event.envelope.event_id,
            )

    def _handle_operation_recovery(self, event: OutboxEvent) -> None:
        self.operation_worker.handle_recovery_event(event)

    @staticmethod
    def _observe_asset_upload_finalized(event: OutboxEvent) -> None:
        """Acknowledge one bound upload audit observation through the Inbox."""

        payload = ASSET_UPLOAD_FINALIZED_V1.validate_payload(event.envelope.payload)
        if not isinstance(payload, AssetUploadFinalizedPayload):
            raise TypeError("Asset upload contract returned an unexpected payload")
        if event.workspace_id != payload.workspace_id:
            raise EventRoutingError(
                "Asset upload workspace does not match its Outbox envelope",
                reason="workspace_mismatch",
            )
        if (
            event.envelope.aggregate_type != "Asset"
            or event.envelope.aggregate_id != payload.asset_id
        ):
            raise EventRoutingError(
                "Asset upload identity does not match its Outbox aggregate",
                reason="aggregate_mismatch",
            )

    def _handle_asset_validation(self, event: OutboxEvent) -> None:
        payload = ASSET_VALIDATION_REQUESTED_V1.validate_payload(event.envelope.payload)
        if not isinstance(payload, AssetValidationRequestedPayload):
            raise TypeError("Asset validation contract returned an unexpected payload")
        if event.workspace_id != payload.workspace_id:
            raise EventRoutingError(
                "Asset validation workspace does not match its Outbox envelope",
                reason="workspace_mismatch",
            )
        if event.envelope.aggregate_id != payload.operation_id:
            raise EventRoutingError(
                "Asset validation operation does not match its Outbox aggregate",
                reason="aggregate_mismatch",
            )
        self.operation_worker.execute(
            workspace_id=payload.workspace_id,
            operation_id=payload.operation_id,
        )

    @staticmethod
    def _observe_asset_validation_terminal(event: OutboxEvent) -> None:
        """Acknowledge one strictly bound validation outcome through the Inbox."""

        contract = (
            ASSET_VALIDATION_COMPLETED_V1
            if event.envelope.event_type == EventType.ASSET_VALIDATION_COMPLETED.value
            else ASSET_VALIDATION_FAILED_V1
        )
        payload = contract.validate_payload(event.envelope.payload)
        if not isinstance(
            payload,
            AssetValidationCompletedPayload | AssetValidationFailedPayload,
        ):
            raise TypeError("Asset validation terminal contract returned an unexpected payload")
        if event.workspace_id != payload.workspace_id:
            raise EventRoutingError(
                "Asset validation terminal workspace does not match its Outbox envelope",
                reason="workspace_mismatch",
            )
        if (
            event.envelope.aggregate_type != "Asset"
            or event.envelope.aggregate_id != payload.asset_id
        ):
            raise EventRoutingError(
                "Asset validation terminal identity does not match its Outbox aggregate",
                reason="aggregate_mismatch",
            )

    def _handle_asset_delete(self, event: OutboxEvent) -> None:
        payload = ASSET_DELETE_REQUESTED_V1.validate_payload(event.envelope.payload)
        if not isinstance(payload, AssetDeleteRequestedPayload):
            raise TypeError("Asset deletion contract returned an unexpected payload")
        if event.workspace_id != payload.workspace_id:
            raise EventRoutingError(
                "Asset deletion workspace does not match its Outbox envelope",
                reason="workspace_mismatch",
            )
        if event.envelope.aggregate_id != payload.operation_id:
            raise EventRoutingError(
                "Asset deletion operation does not match its Outbox aggregate",
                reason="aggregate_mismatch",
            )
        self.operation_worker.execute(
            workspace_id=payload.workspace_id,
            operation_id=payload.operation_id,
        )

    def _handle_foundation_asset_deleted(self, event: OutboxEvent) -> None:
        payload = ASSET_DELETE_COMPLETED_V1.validate_payload(event.envelope.payload)
        if not isinstance(payload, AssetDeleteCompletedPayload):
            raise TypeError("Asset deletion completion contract returned an unexpected payload")
        if event.workspace_id != payload.workspace_id:
            raise EventRoutingError(
                "Asset deletion workspace does not match its Outbox envelope",
                reason="workspace_mismatch",
            )
        if (
            event.envelope.aggregate_type != "Asset"
            or event.envelope.aggregate_id != payload.asset_id
        ):
            raise EventRoutingError(
                "Asset deletion identity does not match its Outbox aggregate",
                reason="aggregate_mismatch",
            )
        if event.envelope.aggregate_version != payload.deletion_generation:
            raise EventRoutingError(
                "Asset deletion generation does not match its Outbox aggregate version",
                reason="deletion_generation_mismatch",
            )
        if self.brand_profile_invalidation is None:
            raise RuntimeError("Brand Profile invalidation is not configured")
        try:
            self.brand_profile_invalidation.invalidate_foundation_asset_deletion(
                workspace_id=payload.workspace_id,
                asset_id=payload.asset_id,
                asset_version_id=payload.asset_version_id,
                deletion_generation=payload.deletion_generation,
                occurred_at=event.envelope.occurred_at,
            )
        except BrandProfileDeletionLineageError as exc:
            raise EventRoutingError(str(exc), reason=exc.reason) from exc

    def _handle_product_brief_analysis(self, event: OutboxEvent) -> None:
        payload = PRODUCT_BRIEF_REQUESTED_V1.validate_payload(event.envelope.payload)
        if not isinstance(payload, ProductBriefRequestedPayload):
            raise TypeError("ProductBrief request contract returned an unexpected payload")
        self._validate_product_brief_event_identity(
            event=event,
            workspace_id=payload.workspace_id,
            product_brief_id=payload.product_brief_id,
            product_brief_version=payload.product_brief_version,
        )
        with SqlAlchemyOperationUnitOfWork(self.database.session_factory) as uow:
            operation = uow.operations.get(
                payload.operation_id,
                workspace_id=payload.workspace_id,
            )
        if (
            operation is None
            or operation.kind is not OperationKind.PRODUCT_BRIEF_ANALYSIS
            or operation.target_type != "product_brief"
            or operation.target_id != payload.product_brief_id
            or operation.target_version != payload.product_brief_version
        ):
            raise EventRoutingError(
                "ProductBrief request does not match its Durable Operation target",
                reason="aggregate_mismatch",
            )
        self.operation_worker.execute(
            workspace_id=payload.workspace_id,
            operation_id=payload.operation_id,
        )

    def _handle_asset_index(self, event: OutboxEvent) -> None:
        payload = ASSET_INDEX_REQUESTED_V1.validate_payload(event.envelope.payload)
        if not isinstance(payload, AssetIndexRequestedPayload):
            raise TypeError("IMAGE index request contract returned an unexpected payload")
        if event.workspace_id != payload.workspace_id:
            raise EventRoutingError(
                "IMAGE index workspace does not match its Outbox envelope",
                reason="workspace_mismatch",
            )
        if (
            event.envelope.aggregate_type != "embedding_record"
            or event.envelope.aggregate_id != payload.embedding_record_id
        ):
            raise EventRoutingError(
                "IMAGE index identity does not match its Outbox aggregate",
                reason="aggregate_mismatch",
            )
        with SqlAlchemyOperationUnitOfWork(self.database.session_factory) as uow:
            operation = uow.operations.get(
                payload.operation_id,
                workspace_id=payload.workspace_id,
            )
        if (
            operation is None
            or operation.kind is not OperationKind.ASSET_INDEXING
            or operation.target_type != "embedding_record"
            or operation.target_id != payload.embedding_record_id
            or operation.target_version != payload.operation_epoch
            or operation.input_hash != payload.operation_input_hash
        ):
            raise EventRoutingError(
                "IMAGE index request does not match its Durable Operation target",
                reason="aggregate_mismatch",
            )
        if (
            self.image_index_authority is None
            or not self.image_index_authority.validate_request_event(payload)
        ):
            raise EventRoutingError(
                "IMAGE index request does not match its Embedding or AssetVersion identity",
                reason="aggregate_mismatch",
            )
        self.operation_worker.execute(
            workspace_id=payload.workspace_id,
            operation_id=payload.operation_id,
        )

    def _observe_asset_index_completed(self, event: OutboxEvent) -> None:
        payload = ASSET_INDEX_COMPLETED_V1.validate_payload(event.envelope.payload)
        if not isinstance(payload, AssetIndexCompletedPayload):
            raise TypeError("IMAGE index completion contract returned an unexpected payload")
        if event.workspace_id != payload.workspace_id:
            raise EventRoutingError(
                "IMAGE index completion workspace does not match its Outbox envelope",
                reason="workspace_mismatch",
            )
        if (
            event.envelope.aggregate_type != "embedding_record"
            or event.envelope.aggregate_id != payload.embedding_record_id
            or event.envelope.trace_id != payload.operation_id
        ):
            raise EventRoutingError(
                "IMAGE index completion does not match its Outbox aggregate",
                reason="aggregate_mismatch",
            )

    def _handle_asset_index_delete(self, event: OutboxEvent) -> None:
        payload = ASSET_INDEX_DELETE_REQUESTED_V1.validate_payload(event.envelope.payload)
        if not isinstance(payload, AssetIndexDeleteRequestedPayload):
            raise TypeError("IMAGE index delete contract returned an unexpected payload")
        if event.workspace_id != payload.workspace_id:
            raise EventRoutingError(
                "IMAGE index delete workspace does not match its Outbox envelope",
                reason="workspace_mismatch",
            )
        if (
            event.envelope.aggregate_type != "embedding_record"
            or event.envelope.aggregate_id != payload.embedding_record_id
        ):
            raise EventRoutingError(
                "IMAGE index delete identity does not match its Outbox aggregate",
                reason="aggregate_mismatch",
            )
        if self.image_index_authority is None or self.image_vector_index is None:
            raise RuntimeError("IMAGE index deletion is not configured")
        identity = self.image_index_authority.load_delete_target(payload)
        delete = getattr(self.image_vector_index, "delete_if_generation", None)
        if not callable(delete):
            raise RuntimeError("IMAGE vector index cannot delete an exact generation")
        delete(identity)
        self.image_index_authority.complete_delete(payload)

    @staticmethod
    def _observe_product_brief_state(event: OutboxEvent) -> None:
        contract = (
            PRODUCT_BRIEF_AWAITING_CONFIRMATION_V1
            if event.envelope.event_type == EventType.PRODUCT_BRIEF_AWAITING_CONFIRMATION.value
            else PRODUCT_BRIEF_CONFIRMED_V1
        )
        payload = contract.validate_payload(event.envelope.payload)
        if not isinstance(
            payload,
            ProductBriefAwaitingConfirmationPayload | ProductBriefConfirmedPayload,
        ):
            raise TypeError("ProductBrief state contract returned an unexpected payload")
        WorkerRuntime._validate_product_brief_event_identity(
            event=event,
            workspace_id=payload.workspace_id,
            product_brief_id=payload.product_brief_id,
            product_brief_version=payload.product_brief_version,
        )

    @staticmethod
    def _validate_product_brief_event_identity(
        *,
        event: OutboxEvent,
        workspace_id: str,
        product_brief_id: str,
        product_brief_version: int,
    ) -> None:
        if event.workspace_id != workspace_id:
            raise EventRoutingError(
                "ProductBrief workspace does not match its Outbox envelope",
                reason="workspace_mismatch",
            )
        if (
            event.envelope.aggregate_type != "ProductBrief"
            or event.envelope.aggregate_id != product_brief_id
            or event.envelope.aggregate_version != product_brief_version
        ):
            raise EventRoutingError(
                "ProductBrief identity does not match its Outbox aggregate",
                reason="aggregate_mismatch",
            )

    def _handle_asset_rights_changed(self, event: OutboxEvent) -> None:
        contract = (
            ASSET_RIGHTS_EXPIRED_V1
            if event.envelope.event_type == EventType.ASSET_RIGHTS_EXPIRED.value
            else ASSET_RIGHTS_CHANGED_V1
        )
        payload = contract.validate_payload(event.envelope.payload)
        if not isinstance(payload, AssetRightsChangedPayload):
            raise TypeError("Asset rights contract returned an unexpected payload")
        if event.workspace_id != payload.workspace_id:
            raise EventRoutingError(
                "Asset rights workspace does not match its Outbox envelope",
                reason="workspace_mismatch",
            )
        if (
            event.envelope.aggregate_type != "Asset"
            or event.envelope.aggregate_id != payload.asset_id
        ):
            raise EventRoutingError(
                "Asset rights identity does not match its Outbox aggregate",
                reason="aggregate_mismatch",
            )
        try:
            canonical_asset_id = canonicalize_uuid(payload.asset_id)
        except (TypeError, ValueError):
            canonical_asset_id = None
        if canonical_asset_id != payload.asset_id:
            raise EventRoutingError(
                "Asset rights event requires a canonical Asset identity",
                reason="malformed_asset_identity",
            )
        if self.brand_profile_invalidation is None:
            raise RuntimeError("Brand Profile invalidation is not configured")
        self.brand_profile_invalidation.invalidate_asset(
            workspace_id=payload.workspace_id,
            asset_id=payload.asset_id,
            occurred_at=event.envelope.occurred_at,
        )
        if (
            payload.required_convergence == "REINDEX"
            and self.settings.asset_queue_name in self.settings.configured_worker_queues
        ):
            if self.image_index_requests is None:
                raise RuntimeError("IMAGE index request service is not configured")
            try:
                self.image_index_requests.request_current_image(
                    workspace_id=payload.workspace_id,
                    asset_id=payload.asset_id,
                )
            except ImageIndexNotApplicable:
                return
        elif (
            payload.required_convergence == "REMOVE_EXTERNAL_DERIVATIVES"
            and self.settings.asset_queue_name in self.settings.configured_worker_queues
        ):
            if self.image_index_authority is None:
                raise RuntimeError("IMAGE index authority is not configured")
            self.image_index_authority.mark_current_asset_stale(
                workspace_id=payload.workspace_id,
                asset_id=payload.asset_id,
                asset_version_id=payload.asset_version_id,
                reason=(
                    "RIGHTS_INVALID"
                    if payload.change in {"REVOKED", "EXPIRED"}
                    else "ASSET_BLOCKED"
                ),
            )

    @staticmethod
    def _observe_brand_profile_published(event: OutboxEvent) -> None:
        payload = BRAND_PROFILE_PUBLISHED_V1.validate_payload(event.envelope.payload)
        if not isinstance(payload, BrandProfilePublishedPayload):
            raise TypeError("Brand Profile publication contract returned an unexpected payload")
        if event.workspace_id != payload.workspace_id:
            raise EventRoutingError(
                "Brand Profile workspace does not match its Outbox envelope",
                reason="workspace_mismatch",
            )
        if (
            event.envelope.aggregate_type != "BrandProfile"
            or event.envelope.aggregate_id != payload.profile_id
        ):
            raise EventRoutingError(
                "Brand Profile identity does not match its Outbox aggregate",
                reason="aggregate_mismatch",
            )

    @staticmethod
    def _observe_replay_event(_event: OutboxEvent) -> None:
        """Acknowledge immutable replay audit observations through the Inbox."""

    @staticmethod
    def _observe_workflow_event(_event: OutboxEvent) -> None:
        """Acknowledge a durable Phase 1 notification through the Inbox audit trail."""
