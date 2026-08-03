"""IMAGE indexing orchestration over durable-operation and typed external seams."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from commercevision_contracts import (
    AssetIndexStatusResponseV1,
    EmbeddingImageInputV1,
    EmbeddingProviderFailure,
    EmbeddingProviderRequestV1,
    EmbeddingProviderResultV1,
    MilvusCollectionCreateRequestV1,
    MilvusUpsertRequestV1,
    MilvusVectorIdentityV1,
    MilvusVectorProofV1,
    MilvusVectorRowV1,
    collection_create_request,
)
from commercevision_domain import (
    CollectionSpec,
    NormalizedOperationError,
    OperationKind,
    ReconciliationOutcome,
    RetentionClass,
    VectorKind,
    generation_milvus_primary_key,
)

from .indexing_transfer import ImageIndexDataTransferDenied, ImageIndexDataTransferPolicy
from .operations import (
    OperationExecutionFailure,
    OperationExecutionRequest,
    OperationExecutionResult,
    OperationReconciliationResult,
    UnknownOperationOutcome,
)


@dataclass(frozen=True, slots=True)
class ImageIndexingTarget:
    operation_id: str
    embedding_record_id: str
    workspace_id: str
    asset_id: str
    asset_version_id: str
    asset_version_number: int
    rights_record_id: str
    rights_record_version: int
    collection_id: str
    collection_spec: CollectionSpec
    provider: str
    model_id: str
    model_configuration_version: str
    preprocessing_version: str
    input_hash: str
    embedding_spec_sha256: str
    write_generation: int
    category: str
    brand: str
    asset_role: str
    content_sha256: str
    provider_request_id: str | None
    actual_model: str | None
    indexed_at: datetime
    retention_class: RetentionClass
    replay_source_dead_letter_id: str | None = None
    replay_attempt: int = 0


@dataclass(frozen=True, slots=True)
class IndexCommitDecision:
    indexed: bool
    stale_reason: str | None = None


class IndexingAuthorityPort(Protocol):
    """MySQL authority; implementations perform both checks with database time."""

    def load_for_provisioning(
        self,
        request: OperationExecutionRequest,
    ) -> ImageIndexingTarget: ...

    def activate_collection(self, target: ImageIndexingTarget) -> None: ...

    def claim_for_submission(
        self,
        request: OperationExecutionRequest,
    ) -> ImageIndexingTarget: ...

    def load_for_reconciliation(
        self,
        request: OperationExecutionRequest,
    ) -> ImageIndexingTarget: ...

    def load_committed_outcome(
        self,
        request: OperationExecutionRequest,
    ) -> IndexCommitDecision | None: ...

    def record_provider_result(
        self,
        target: ImageIndexingTarget,
        result: EmbeddingProviderResultV1,
    ) -> ImageIndexingTarget: ...

    def commit_after_upsert(
        self,
        target: ImageIndexingTarget,
    ) -> IndexCommitDecision: ...

    def record_failure(
        self,
        target: ImageIndexingTarget,
        *,
        retryable: bool,
    ) -> None: ...

    def mark_terminal_failure(self, request: OperationExecutionRequest) -> bool: ...


class ExactImageReferencePort(Protocol):
    def temporary_input(self, target: ImageIndexingTarget) -> EmbeddingImageInputV1: ...


class EmbeddingProviderPort(Protocol):
    def embed(self, request: EmbeddingProviderRequestV1) -> EmbeddingProviderResultV1: ...


class VectorIndexPort(Protocol):
    def ensure_collection(self, request: MilvusCollectionCreateRequestV1) -> None: ...

    def upsert(self, request: MilvusUpsertRequestV1) -> None: ...

    def prove(self, identity: MilvusVectorIdentityV1) -> MilvusVectorProofV1: ...

    def delete_if_generation(self, identity: MilvusVectorIdentityV1) -> bool: ...


class ImageIndexStatusQueryPort(Protocol):
    def get_current(
        self,
        *,
        workspace_id: str,
        asset_id: str,
    ) -> AssetIndexStatusResponseV1: ...


class ImageIndexStatusApplicationService:
    def __init__(self, queries: ImageIndexStatusQueryPort) -> None:
        self._queries = queries

    def get_current(
        self,
        *,
        workspace_id: str,
        asset_id: str,
    ) -> AssetIndexStatusResponseV1:
        return self._queries.get_current(workspace_id=workspace_id, asset_id=asset_id)


class ImageIndexingExecutor:
    """One transaction-free external execution behind DurableOperationWorker."""

    def __init__(
        self,
        *,
        authority: IndexingAuthorityPort,
        references: ExactImageReferencePort,
        embedding: EmbeddingProviderPort,
        vectors: VectorIndexPort,
        transfer_policy: ImageIndexDataTransferPolicy | None = None,
        external_endpoint_region: str | None = None,
        external_endpoint_host: str | None = None,
    ) -> None:
        self._authority = authority
        self._references = references
        self._embedding = embedding
        self._vectors = vectors
        self._transfer_policy = transfer_policy
        self._external_endpoint_region = external_endpoint_region
        self._external_endpoint_host = external_endpoint_host

    def execute(self, request: OperationExecutionRequest) -> OperationExecutionResult:
        self._validate_request(request)
        target: ImageIndexingTarget | None = None
        try:
            target = self._authority.load_for_provisioning(request)
        except (TimeoutError, ConnectionError):
            failure = self._external_failure(retryable=True)
        except ValueError:
            failure = self._external_failure(retryable=False)
        else:
            failure = None
        if failure is not None:
            raise failure

        collection_failure = self._prepare_collection_safely(target)
        if collection_failure is not None:
            raise OperationExecutionFailure(collection_failure)

        try:
            target = self._authority.claim_for_submission(request)
        except (TimeoutError, ConnectionError):
            failure = self._external_failure(retryable=True)
        except ValueError:
            failure = self._external_failure(retryable=False)
        else:
            failure = None
        if failure is not None:
            raise failure

        transfer_failure = self._authorize_transfer(target)
        if transfer_failure is not None:
            self._authority.record_failure(target, retryable=False)
            raise OperationExecutionFailure(transfer_failure)

        result, provider_failure, retry_after_seconds, outcome_unknown = (
            self._request_embedding_safely(target)
        )
        if provider_failure is not None:
            if outcome_unknown:
                raise UnknownOperationOutcome(provider_failure)
            self._authority.record_failure(target, retryable=provider_failure.retryable)
            raise OperationExecutionFailure(
                provider_failure,
                retry_after_seconds=retry_after_seconds,
            )
        if result is None:
            raise RuntimeError("IMAGE embedding attempt returned no result or failure")

        try:
            target = self._authority.record_provider_result(target, result)
        except (TimeoutError, ConnectionError):
            failure = self._external_failure(retryable=True)
        except ValueError:
            failure = self._external_failure(retryable=False)
        else:
            failure = None
        if failure is not None:
            self._authority.record_failure(target, retryable=failure.error.retryable)
            raise failure

        vector_failure, vector_outcome_unknown = self._write_vector_safely(target, result)
        if vector_failure is not None:
            if vector_outcome_unknown:
                raise UnknownOperationOutcome(vector_failure)
            self._authority.record_failure(target, retryable=vector_failure.retryable)
            raise OperationExecutionFailure(vector_failure)

        try:
            decision = self._authority.commit_after_upsert(target)
        except (TimeoutError, ConnectionError):
            failure = NormalizedOperationError(
                code="INDEX_COMMIT_OUTCOME_UNKNOWN",
                category="worker_interruption",
                message="MySQL index completion may have committed; reconciliation is required",
                retryable=False,
            )
            outcome_unknown = True
        except ValueError:
            failure = self._external_failure(retryable=False).error
            outcome_unknown = False
        else:
            failure = None
            outcome_unknown = False
        if failure is not None:
            if outcome_unknown:
                raise UnknownOperationOutcome(failure)
            self._authority.record_failure(target, retryable=failure.retryable)
            raise OperationExecutionFailure(failure)
        return OperationExecutionResult(
            operation_id=request.operation_id,
            output_ref=(
                f"mysql://embedding-records/{target.embedding_record_id}"
                if decision.indexed
                else f"mysql://embedding-records/{target.embedding_record_id}/stale"
            ),
            provider_request_id=result.provider_request_id,
        )

    def _authorize_transfer(
        self,
        target: ImageIndexingTarget,
    ) -> NormalizedOperationError | None:
        if self._transfer_policy is None:
            return None
        if self._external_endpoint_region is None or self._external_endpoint_host is None:
            return NormalizedOperationError(
                code="EMBEDDING_TRANSFER_CONFIGURATION_INVALID",
                category="policy",
                message="IMAGE embedding data transfer is not authorized",
                retryable=False,
            )
        try:
            self._transfer_policy.authorize(
                workspace_id=target.workspace_id,
                retention_class=target.retention_class,
                provider=target.provider,
                endpoint_region=self._external_endpoint_region,
                endpoint_host=self._external_endpoint_host,
            )
        except ImageIndexDataTransferDenied as exc:
            return NormalizedOperationError(
                code=exc.code,
                category="policy",
                message=exc.message,
                retryable=False,
            )
        return None

    def _prepare_collection_safely(
        self,
        target: ImageIndexingTarget,
    ) -> NormalizedOperationError | None:
        """Verify the physical collection before making MySQL advertise it as active."""
        try:
            self._vectors.ensure_collection(collection_create_request(target.collection_spec))
        except (TimeoutError, ConnectionError):
            return self._external_failure(retryable=True).error
        except ValueError:
            return self._external_failure(retryable=False).error
        try:
            self._authority.activate_collection(target)
        except (TimeoutError, ConnectionError):
            return self._external_failure(retryable=True).error
        except ValueError:
            return self._external_failure(retryable=False).error
        return None

    def _request_embedding_safely(
        self,
        target: ImageIndexingTarget,
    ) -> tuple[
        EmbeddingProviderResultV1 | None,
        NormalizedOperationError | None,
        int | None,
        bool,
    ]:
        """Contain provider/reference exception graphs before returning to orchestration."""
        try:
            provider_request = self._provider_request(target)
            result = self._embedding.embed(provider_request)
            result.validate_for(provider_request)
            return result, None, None, False
        except EmbeddingProviderFailure as exc:
            error = NormalizedOperationError(
                code=exc.error.code,
                category=exc.error.category,
                message=exc.error.safe_message,
                retryable=exc.error.retryable,
                provider_request_id=exc.error.provider_request_id,
            )
            return None, error, exc.error.retry_after_seconds, exc.error.outcome_unknown
        except (TimeoutError, ConnectionError):
            return None, self._external_failure(retryable=True).error, None, False
        except ValueError:
            return None, self._external_failure(retryable=False).error, None, False

    def _write_vector_safely(
        self,
        target: ImageIndexingTarget,
        result: EmbeddingProviderResultV1,
    ) -> tuple[NormalizedOperationError | None, bool]:
        """Contain SDK exception graphs and distinguish unknown upsert outcomes."""
        try:
            self._vectors.upsert(self._upsert_request(target, result))
        except (TimeoutError, ConnectionError):
            return (
                NormalizedOperationError(
                    code="MILVUS_UPSERT_OUTCOME_UNKNOWN",
                    category="worker_interruption",
                    message="Milvus upsert may have committed; reconciliation is required",
                    retryable=False,
                ),
                True,
            )
        except ValueError:
            return self._external_failure(retryable=False).error, False
        return None, False

    def reconcile(
        self,
        request: OperationExecutionRequest,
    ) -> OperationReconciliationResult:
        self._validate_request(request)
        target: ImageIndexingTarget | None = None
        try:
            committed = self._authority.load_committed_outcome(request)
            if committed is not None:
                return OperationReconciliationResult(
                    operation_id=request.operation_id,
                    outcome=ReconciliationOutcome.CONFIRMED_SUCCESS,
                    output_ref=(
                        f"mysql://embedding-records/{request.target_id}"
                        if committed.indexed
                        else f"mysql://embedding-records/{request.target_id}/stale"
                    ),
                    provider_request_id=request.provider_request_id,
                )
            target = self._authority.load_for_reconciliation(request)
            identity = self._identity(target)
            proof = self._vectors.prove(identity)
            if not proof.exists:
                self._authority.record_failure(target, retryable=True)
                return OperationReconciliationResult(
                    operation_id=request.operation_id,
                    outcome=ReconciliationOutcome.CONFIRMED_FAILURE,
                    provider_request_id=request.provider_request_id,
                    error=NormalizedOperationError(
                        code="VECTOR_NOT_FOUND_AFTER_UNKNOWN_UPSERT",
                        category="vector_index",
                        message="Exact vector is absent after strong reconciliation",
                        retryable=True,
                    ),
                )
            if not proof.matches(identity):
                self._authority.record_failure(target, retryable=False)
                return OperationReconciliationResult(
                    operation_id=request.operation_id,
                    outcome=ReconciliationOutcome.CONFIRMED_FAILURE,
                    provider_request_id=request.provider_request_id,
                    error=NormalizedOperationError(
                        code="VECTOR_IDENTITY_CONFLICT",
                        category="vector_index",
                        message="stored vector does not match the exact input/spec/generation",
                        retryable=False,
                    ),
                )
            decision = self._authority.commit_after_upsert(target)
            return OperationReconciliationResult(
                operation_id=request.operation_id,
                outcome=ReconciliationOutcome.CONFIRMED_SUCCESS,
                output_ref=(
                    f"mysql://embedding-records/{target.embedding_record_id}"
                    if decision.indexed
                    else f"mysql://embedding-records/{target.embedding_record_id}/stale"
                ),
                provider_request_id=target.provider_request_id,
            )
        except ValueError:
            if target is not None:
                try:
                    self._authority.record_failure(target, retryable=False)
                except ValueError:
                    pass
                except (TimeoutError, ConnectionError):
                    return OperationReconciliationResult(
                        operation_id=request.operation_id,
                        outcome=ReconciliationOutcome.PENDING,
                        provider_request_id=request.provider_request_id,
                        error=NormalizedOperationError(
                            code="INDEX_RECONCILIATION_AUTHORITY_UNAVAILABLE",
                            category="persistence",
                            message="index reconciliation authority is temporarily unavailable",
                            retryable=True,
                        ),
                    )
            return OperationReconciliationResult(
                operation_id=request.operation_id,
                outcome=ReconciliationOutcome.CONFIRMED_FAILURE,
                provider_request_id=request.provider_request_id,
                error=NormalizedOperationError(
                    code="VECTOR_RECONCILIATION_INVALID",
                    category="vector_index",
                    message="vector reconciliation returned invalid identity evidence",
                    retryable=False,
                ),
            )
        except (TimeoutError, ConnectionError):
            return OperationReconciliationResult(
                operation_id=request.operation_id,
                outcome=ReconciliationOutcome.PENDING,
                provider_request_id=request.provider_request_id,
                error=NormalizedOperationError(
                    code="VECTOR_RECONCILIATION_UNAVAILABLE",
                    category="vector_index",
                    message="vector proof is temporarily unavailable",
                    retryable=True,
                ),
            )

    def record_terminal_failure(
        self,
        request: OperationExecutionRequest,
        _error: NormalizedOperationError,
    ) -> None:
        self._validate_request(request)
        self._authority.mark_terminal_failure(request)

    def _provider_request(self, target: ImageIndexingTarget) -> EmbeddingProviderRequestV1:
        return EmbeddingProviderRequestV1(
            provider=target.provider,
            model_id=target.model_id,
            pinned_revision=target.collection_spec.pinned_revision,
            model_configuration_version=target.model_configuration_version,
            preprocessing_version=target.preprocessing_version,
            vector_kind=VectorKind.IMAGE,
            expected_dimension=target.collection_spec.dimension,
            input_hash=target.input_hash,
            images=[self._references.temporary_input(target)],
        )

    @staticmethod
    def _upsert_request(
        target: ImageIndexingTarget,
        result: EmbeddingProviderResultV1,
    ) -> MilvusUpsertRequestV1:
        return MilvusUpsertRequestV1(
            collection_name=target.collection_spec.physical_name,
            row=MilvusVectorRowV1(
                embedding_record_id=target.embedding_record_id,
                milvus_primary_key=generation_milvus_primary_key(
                    embedding_record_id=target.embedding_record_id,
                    write_generation=target.write_generation,
                ),
                asset_version_id=target.asset_version_id,
                workspace_id=target.workspace_id,
                rights_record_version=target.rights_record_version,
                category=target.category,
                brand=target.brand,
                asset_role=target.asset_role,
                vector_kind=VectorKind.IMAGE,
                model_configuration_version=target.model_configuration_version,
                input_hash=target.input_hash,
                embedding_spec_sha256=target.embedding_spec_sha256,
                write_generation=target.write_generation,
                indexed_at_epoch_micros=int(target.indexed_at.timestamp() * 1_000_000),
                vector=result.vectors[0].values,
            ),
        )

    @staticmethod
    def _identity(target: ImageIndexingTarget) -> MilvusVectorIdentityV1:
        return MilvusVectorIdentityV1(
            collection_name=target.collection_spec.physical_name,
            embedding_record_id=target.embedding_record_id,
            milvus_primary_key=generation_milvus_primary_key(
                embedding_record_id=target.embedding_record_id,
                write_generation=target.write_generation,
            ),
            input_hash=target.input_hash,
            embedding_spec_sha256=target.embedding_spec_sha256,
            write_generation=target.write_generation,
        )

    @staticmethod
    def _validate_request(request: OperationExecutionRequest) -> None:
        if request.kind is not OperationKind.ASSET_INDEXING:
            raise ValueError("IMAGE indexing executor requires ASSET_INDEXING")
        if request.target_type != "embedding_record":
            raise ValueError("IMAGE indexing target must be an embedding_record")

    @staticmethod
    def _external_failure(*, retryable: bool) -> OperationExecutionFailure:
        return OperationExecutionFailure(
            NormalizedOperationError(
                code=(
                    "INDEX_EXTERNAL_TEMPORARY_FAILURE"
                    if retryable
                    else "INDEX_EXTERNAL_CONTRACT_FAILURE"
                ),
                category="indexing",
                message=(
                    "IMAGE indexing dependency is temporarily unavailable"
                    if retryable
                    else "IMAGE indexing dependency rejected the request"
                ),
                retryable=retryable,
            )
        )
