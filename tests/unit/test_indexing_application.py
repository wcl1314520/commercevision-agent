from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from commercevision_application import (
    ImageIndexDataTransferPolicy,
    ImageIndexingExecutor,
    ImageIndexingTarget,
    IndexCommitDecision,
    IndexingTarget,
    OperationExecutionFailure,
    OperationExecutionRequest,
    UnknownOperationOutcome,
    VectorIndexingExecutor,
)
from commercevision_contracts import (
    EmbeddingImageInputV1,
    EmbeddingProviderErrorV1,
    EmbeddingProviderFailure,
    EmbeddingProviderRequestV1,
    EmbeddingProviderResultV1,
    EmbeddingVectorV1,
    MilvusCollectionCreateRequestV1,
    MilvusUpsertRequestV1,
    MilvusVectorIdentityV1,
    MilvusVectorProofV1,
)
from commercevision_domain import (
    CollectionSpec,
    OperationKind,
    ReconciliationOutcome,
    RetentionClass,
    VectorKind,
    new_uuid7,
)
from pydantic import SecretStr


def _request(*, target_version: int = 7) -> OperationExecutionRequest:
    return OperationExecutionRequest(
        operation_id=new_uuid7(),
        workspace_id="workspace-index",
        kind=OperationKind.ASSET_INDEXING,
        target_type="embedding_record",
        target_id=new_uuid7(),
        target_version=target_version,
        input_hash="a" * 64,
        input_ref=None,
        provider_request_id=None,
        attempt_count=1,
        idempotency_key="operation:index",
    )


def _target(request: OperationExecutionRequest) -> ImageIndexingTarget:
    return ImageIndexingTarget(
        operation_id=request.operation_id,
        embedding_record_id=request.target_id,
        workspace_id=request.workspace_id,
        asset_id=new_uuid7(),
        asset_version_id=new_uuid7(),
        asset_version_number=request.target_version,
        rights_record_id=new_uuid7(),
        rights_record_version=3,
        collection_id=new_uuid7(),
        collection_spec=CollectionSpec.create(
            model_family="qwen3-vl-embedding",
            pinned_revision="2026-06-30",
            dimension=4,
            vector_kind=VectorKind.IMAGE,
            schema_version=1,
            index_spec_version="hnsw-cosine-v1",
        ),
        provider="alibaba-model-studio",
        model_id="qwen3-vl-embedding",
        model_configuration_version="embedding-config-v1",
        preprocessing_version="image-preprocess-v1",
        input_hash=request.input_hash,
        embedding_spec_sha256="b" * 64,
        write_generation=2,
        category="APPAREL",
        brand="Example",
        asset_role="HERO",
        content_sha256="c" * 64,
        provider_request_id="provider-request-1",
        actual_model="qwen3-vl-embedding-2026-06-30",
        indexed_at=datetime(2026, 7, 31, tzinfo=UTC),
        retention_class=RetentionClass.FOUNDATION,
    )


class _Authority:
    def __init__(
        self,
        target: ImageIndexingTarget,
        *,
        commit_error: Exception | None = None,
        committed_outcome: IndexCommitDecision | None = None,
    ) -> None:
        self.target = target
        self.commit_error = commit_error
        self.committed_outcome = committed_outcome
        self.commits = 0
        self.failures: list[bool] = []
        self.terminal_failures = 0
        self.calls: list[str] = []

    def load_for_provisioning(
        self,
        request: OperationExecutionRequest,
    ) -> ImageIndexingTarget:
        self.calls.append("load_for_provisioning")
        return replace(self.target, provider_request_id=None, actual_model=None)

    def activate_collection(self, target: ImageIndexingTarget) -> None:
        self.calls.append("activate_collection")

    def claim_for_submission(self, request: OperationExecutionRequest) -> ImageIndexingTarget:
        self.calls.append("claim_for_submission")
        return replace(self.target, provider_request_id=None, actual_model=None)

    def record_provider_result(
        self,
        target: ImageIndexingTarget,
        result: EmbeddingProviderResultV1,
    ) -> ImageIndexingTarget:
        self.target = replace(
            target,
            provider_request_id=result.provider_request_id,
            actual_model=result.actual_model,
        )
        return self.target

    def load_for_reconciliation(self, request: OperationExecutionRequest) -> ImageIndexingTarget:
        return self.target

    def load_committed_outcome(
        self,
        request: OperationExecutionRequest,
    ) -> IndexCommitDecision | None:
        return self.committed_outcome

    def commit_after_upsert(self, target: ImageIndexingTarget) -> IndexCommitDecision:
        self.commits += 1
        assert target.provider_request_id == "provider-request-1"
        assert target.actual_model == "qwen3-vl-embedding-2026-06-30"
        if self.commit_error is not None:
            raise self.commit_error
        return IndexCommitDecision(indexed=True)

    def record_failure(
        self,
        target: ImageIndexingTarget,
        *,
        retryable: bool,
    ) -> None:
        assert target.operation_id == self.target.operation_id
        self.failures.append(retryable)

    def mark_terminal_failure(self, request: OperationExecutionRequest) -> bool:
        assert request.operation_id == self.target.operation_id
        self.terminal_failures += 1
        return self.terminal_failures == 1


class _References:
    def temporary_input(self, target: ImageIndexingTarget) -> EmbeddingImageInputV1:
        return EmbeddingImageInputV1(
            asset_version_id=target.asset_version_id,
            content_sha256=target.content_sha256,
            byte_size=1024,
            url=SecretStr("https://controlled.invalid/read-token"),
            required_headers={"x-required": SecretStr("secret-value")},
            expires_at=datetime(2026, 7, 31, 1, tzinfo=UTC),
        )


class _Embedding:
    def __init__(self, failure: EmbeddingProviderFailure | None = None) -> None:
        self.failure = failure

    def embed(self, request: EmbeddingProviderRequestV1) -> EmbeddingProviderResultV1:
        if self.failure is not None:
            raise self.failure
        assert request.provider == "alibaba-model-studio"
        assert request.images[0].required_headers["x-required"].get_secret_value() == "secret-value"
        return EmbeddingProviderResultV1(
            vectors=[EmbeddingVectorV1(values=[0.1, 0.2, 0.3, 0.4])],
            provider="alibaba-model-studio",
            provider_request_id="provider-request-1",
            actual_model="qwen3-vl-embedding-2026-06-30",
            latency_ms=1,
        )


class _Vectors:
    def __init__(
        self,
        *,
        ensure_error: Exception | None = None,
        upsert_error: Exception | None = None,
        proof_error: Exception | None = None,
        proof_exists: bool = True,
        calls: list[str] | None = None,
    ) -> None:
        self.ensure_error = ensure_error
        self.upsert_error = upsert_error
        self.proof_error = proof_error
        self.proof_exists = proof_exists
        self.last_upsert: MilvusUpsertRequestV1 | None = None
        self.calls = calls

    def ensure_collection(self, request: MilvusCollectionCreateRequestV1) -> None:
        if self.calls is not None:
            self.calls.append("ensure_collection")
        if self.ensure_error is not None:
            raise self.ensure_error

    def upsert(self, request: MilvusUpsertRequestV1) -> None:
        self.last_upsert = request
        if self.upsert_error is not None:
            raise self.upsert_error

    def prove(self, identity: MilvusVectorIdentityV1) -> MilvusVectorProofV1:
        if self.proof_error is not None:
            raise self.proof_error
        if not self.proof_exists:
            return MilvusVectorProofV1(exists=False)
        return MilvusVectorProofV1(
            exists=True,
            milvus_primary_key=identity.milvus_primary_key,
            input_hash=identity.input_hash,
            embedding_spec_sha256=identity.embedding_spec_sha256,
            write_generation=identity.write_generation,
        )

    def delete_if_generation(self, identity: MilvusVectorIdentityV1) -> bool:
        raise AssertionError("stale deletion must be dispatched durably by MySQL")


def _executor(
    authority: _Authority,
    vectors: _Vectors,
    embedding: _Embedding | None = None,
    observer=None,
) -> ImageIndexingExecutor:
    return ImageIndexingExecutor(
        authority=authority,
        references=_References(),
        embedding=embedding or _Embedding(),
        vectors=vectors,
        observer=observer,
    )


class _IndexObserver:
    def __init__(self) -> None:
        self.steps: list[str] = []
        self.provider_results: list[dict[str, object]] = []
        self.completions: list[dict[str, object]] = []

    @contextmanager
    def span(self, *, step, request, target=None):
        assert request.kind is OperationKind.ASSET_INDEXING
        self.steps.append(step)
        yield

    def provider_result(self, **values):
        self.provider_results.append(values)

    def completed(self, **values):
        self.completions.append(values)


def test_image_index_observer_covers_provider_milvus_and_commit_boundaries() -> None:
    request = _request()
    authority = _Authority(_target(request))
    observer = _IndexObserver()

    result = _executor(authority, _Vectors(), observer=observer).execute(request)

    assert result.operation_id == request.operation_id
    assert observer.steps == [
        "collection",
        "rights",
        "temporary_reference",
        "embedding",
        "milvus_upsert",
        "commit",
    ]
    assert observer.provider_results == [
        {
            "request": request,
            "target": authority.target,
            "outcome": "succeeded",
            "latency_ms": 1,
            "provider_request_id": "provider-request-1",
        }
    ]
    assert observer.completions == [
        {"request": request, "target": authority.target, "outcome": "indexed"}
    ]


def test_milvus_upsert_boundary_timeout_enters_reconciliation_without_resubmission() -> None:
    request = _request()
    authority = _Authority(_target(request))

    with pytest.raises(UnknownOperationOutcome):
        _executor(authority, _Vectors(upsert_error=TimeoutError("write outcome unknown"))).execute(
            request
        )

    assert authority.commits == 0


def test_product_fused_execution_submits_controlled_text_and_fused_vector_metadata() -> None:
    class CapturingEmbedding(_Embedding):
        request: EmbeddingProviderRequestV1 | None = None

        def embed(self, request: EmbeddingProviderRequestV1) -> EmbeddingProviderResultV1:
            self.request = request
            return super().embed(request)

    request = _request()
    target: IndexingTarget = replace(
        _target(request),
        collection_spec=CollectionSpec.create(
            model_family="qwen3-vl-embedding",
            pinned_revision="2026-06-30",
            dimension=4,
            vector_kind=VectorKind.PRODUCT_FUSED,
            schema_version=1,
            index_spec_version="hnsw-cosine-v1",
        ),
        vector_kind=VectorKind.PRODUCT_FUSED,
        product_brief_version_id=new_uuid7(),
        controlled_text_sha256="d" * 64,
        controlled_text='{"title":"鎏金口红 summer"}',
    )
    authority = _Authority(target)
    embedding = CapturingEmbedding()
    vectors = _Vectors()

    VectorIndexingExecutor(
        authority=authority,
        references=_References(),
        embedding=embedding,
        vectors=vectors,
    ).execute(request)

    assert embedding.request is not None
    assert embedding.request.vector_kind is VectorKind.PRODUCT_FUSED
    assert embedding.request.controlled_text == target.controlled_text
    assert vectors.last_upsert is not None
    assert vectors.last_upsert.row.vector_kind is VectorKind.PRODUCT_FUSED


def test_post_upsert_mysql_timeout_enters_same_generation_reconciliation() -> None:
    request = _request()
    authority = _Authority(
        _target(request),
        commit_error=TimeoutError("MySQL completion response was lost"),
    )

    with pytest.raises(UnknownOperationOutcome) as raised:
        _executor(authority, _Vectors()).execute(request)

    assert raised.value.error.code == "INDEX_COMMIT_OUTCOME_UNKNOWN"
    assert authority.commits == 1
    assert authority.failures == []


def test_malformed_vector_proof_converges_to_normalized_permanent_failure() -> None:
    request = _request()
    authority = _Authority(_target(request))

    result = _executor(
        authority,
        _Vectors(proof_error=ValueError("malformed SDK response")),
    ).reconcile(request)

    assert result.outcome is ReconciliationOutcome.CONFIRMED_FAILURE
    assert result.error is not None
    assert result.error.code == "VECTOR_RECONCILIATION_INVALID"
    assert result.error.retryable is False
    assert authority.failures == [False]


def test_collection_is_verified_and_activated_before_provider_claim() -> None:
    class OrderedAuthority(_Authority):
        def load_for_provisioning(
            self,
            request: OperationExecutionRequest,
        ) -> ImageIndexingTarget:
            calls.append("load_for_provisioning")
            return replace(self.target, provider_request_id=None, actual_model=None)

        def activate_collection(self, target: ImageIndexingTarget) -> None:
            calls.append("activate_collection")

        def claim_for_submission(
            self,
            request: OperationExecutionRequest,
        ) -> ImageIndexingTarget:
            calls.append("claim_for_submission")
            return replace(self.target, provider_request_id=None, actual_model=None)

    calls: list[str] = []
    request = _request()
    authority = OrderedAuthority(_target(request))

    _executor(authority, _Vectors(calls=calls)).execute(request)

    assert calls == [
        "load_for_provisioning",
        "ensure_collection",
        "activate_collection",
        "claim_for_submission",
    ]


def test_collection_verification_failure_does_not_claim_or_call_provider() -> None:
    class CountingEmbedding(_Embedding):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def embed(self, request: EmbeddingProviderRequestV1) -> EmbeddingProviderResultV1:
            self.calls += 1
            return super().embed(request)

    request = _request()
    authority = _Authority(_target(request))
    embedding = CountingEmbedding()

    with pytest.raises(OperationExecutionFailure) as raised:
        _executor(
            authority,
            _Vectors(ensure_error=ConnectionError("milvus unavailable")),
            embedding,
        ).execute(request)

    assert raised.value.error.retryable is True
    assert authority.calls == ["load_for_provisioning"]
    assert authority.failures == []
    assert embedding.calls == 0


def test_reconciliation_exact_proof_rechecks_authority_and_finalizes_mysql() -> None:
    request = _request()
    authority = _Authority(_target(request))

    result = _executor(authority, _Vectors()).reconcile(request)

    assert result.outcome is ReconciliationOutcome.CONFIRMED_SUCCESS
    assert authority.commits == 1


def test_reconciliation_accepts_an_atomic_mysql_completion_marker_without_vector_reproof() -> None:
    class Vectors(_Vectors):
        def prove(self, identity: MilvusVectorIdentityV1) -> MilvusVectorProofV1:
            raise AssertionError("a committed MySQL outcome must not re-enter vector proof")

    request = _request()
    authority = _Authority(
        _target(request),
        committed_outcome=IndexCommitDecision(
            indexed=False,
            stale_reason="SUPERSEDED",
        ),
    )

    result = _executor(authority, Vectors()).reconcile(request)

    assert result.outcome is ReconciliationOutcome.CONFIRMED_SUCCESS
    assert result.output_ref == f"mysql://embedding-records/{request.target_id}/stale"
    assert authority.commits == 0


def test_strong_absence_exits_reconciliation_and_returns_embedding_to_retryable() -> None:
    request = _request()
    authority = _Authority(_target(request))

    result = _executor(authority, _Vectors(proof_exists=False)).reconcile(request)

    assert result.outcome is ReconciliationOutcome.CONFIRMED_FAILURE
    assert result.error is not None
    assert result.error.retryable is True
    assert authority.failures == [True]


def test_identity_conflict_marks_embedding_permanent_and_terminal_callback_is_idempotent() -> None:
    class ConflictingVectors(_Vectors):
        @staticmethod
        def prove(identity: MilvusVectorIdentityV1) -> MilvusVectorProofV1:
            return MilvusVectorProofV1(
                exists=True,
                milvus_primary_key=identity.milvus_primary_key,
                input_hash="f" * 64,
                embedding_spec_sha256=identity.embedding_spec_sha256,
                write_generation=identity.write_generation,
            )

    request = _request()
    authority = _Authority(_target(request))
    executor = _executor(authority, ConflictingVectors())

    result = executor.reconcile(request)
    executor.record_terminal_failure(
        request,
        result.error or pytest.fail("identity conflict must return a normalized terminal error"),
    )
    executor.record_terminal_failure(
        request,
        result.error or pytest.fail("identity conflict must return a normalized terminal error"),
    )

    assert result.outcome is ReconciliationOutcome.CONFIRMED_FAILURE
    assert authority.failures == [False]
    assert authority.terminal_failures == 2


def test_generation_specific_physical_key_prevents_late_generation_overwrite() -> None:
    request = _request()
    target = _target(request)
    authority = _Authority(target)
    vectors = _Vectors()

    _executor(authority, vectors).execute(request)

    assert vectors.last_upsert is not None
    assert vectors.last_upsert.row.milvus_primary_key == f"{target.embedding_record_id}:g2"
    assert vectors.last_upsert.row.embedding_record_id == target.embedding_record_id


def test_external_reference_failure_never_persists_url_or_secret_details() -> None:
    class LeakingReferences:
        @staticmethod
        def temporary_input(_target: ImageIndexingTarget) -> EmbeddingImageInputV1:
            raise ValueError(
                "https://controlled.invalid/read?token=super-secret object/key/private"
            )

    request = _request()
    target = _target(request)
    executor = ImageIndexingExecutor(
        authority=_Authority(target),
        references=LeakingReferences(),
        embedding=_Embedding(),
        vectors=_Vectors(),
    )

    with pytest.raises(OperationExecutionFailure) as raised:
        executor.execute(request)

    assert raised.value.error.message == "IMAGE indexing dependency rejected the request"
    serialized = repr(raised.value.error)
    assert "super-secret" not in serialized
    assert "controlled.invalid" not in serialized
    assert "object/key/private" not in serialized
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_embedding_transfer_policy_runs_before_url_signing_and_provider_submission() -> None:
    class CountingReferences(_References):
        def __init__(self) -> None:
            self.calls = 0

        def temporary_input(self, target: ImageIndexingTarget) -> EmbeddingImageInputV1:
            self.calls += 1
            return super().temporary_input(target)

    class CountingEmbedding(_Embedding):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def embed(self, request: EmbeddingProviderRequestV1) -> EmbeddingProviderResultV1:
            self.calls += 1
            return super().embed(request)

    def policy(*, workspace: str, retention: RetentionClass) -> ImageIndexDataTransferPolicy:
        return ImageIndexDataTransferPolicy(
            enabled=True,
            version="embedding-transfer-v1",
            allowed_workspace_ids=frozenset({workspace}),
            allowed_retention_classes=frozenset({retention}),
            allowed_providers=frozenset({"alibaba-model-studio"}),
            allowed_endpoint_regions=frozenset({"cn-beijing"}),
            allowed_endpoint_hosts=frozenset({"dashscope.aliyuncs.com"}),
        )

    for denied_policy in (
        policy(workspace="other-workspace", retention=RetentionClass.FOUNDATION),
        policy(workspace="workspace-index", retention=RetentionClass.TASK),
    ):
        request = _request()
        authority = _Authority(_target(request))
        references = CountingReferences()
        embedding = CountingEmbedding()
        executor = ImageIndexingExecutor(
            authority=authority,
            references=references,
            embedding=embedding,
            vectors=_Vectors(),
            transfer_policy=denied_policy,
            external_endpoint_region="cn-beijing",
            external_endpoint_host="dashscope.aliyuncs.com",
        )

        with pytest.raises(OperationExecutionFailure, match="not authorized"):
            executor.execute(request)

        assert references.calls == 0
        assert embedding.calls == 0
        assert authority.failures == [False]

    request = _request()
    references = CountingReferences()
    embedding = CountingEmbedding()
    executor = ImageIndexingExecutor(
        authority=_Authority(_target(request)),
        references=references,
        embedding=embedding,
        vectors=_Vectors(),
        transfer_policy=policy(
            workspace="workspace-index",
            retention=RetentionClass.FOUNDATION,
        ),
        external_endpoint_region="cn-beijing",
        external_endpoint_host="dashscope.aliyuncs.com",
    )

    executor.execute(request)

    assert references.calls == embedding.calls == 1


def test_unknown_vector_outcome_clears_sdk_exception_graph() -> None:
    class LeakingVectors(_Vectors):
        @staticmethod
        def upsert(_request: MilvusUpsertRequestV1) -> None:
            raise TimeoutError("https://milvus.internal?token=super-secret required-header=private")

    request = _request()

    with pytest.raises(UnknownOperationOutcome) as raised:
        _executor(_Authority(_target(request)), LeakingVectors()).execute(request)

    assert raised.value.error.code == "MILVUS_UPSERT_OUTCOME_UNKNOWN"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    serialized = repr(raised.value)
    assert "super-secret" not in serialized
    assert "required-header" not in serialized


def test_provider_float32_overflow_fails_permanently_before_milvus() -> None:
    class OverflowEmbedding:
        @staticmethod
        def embed(_request: EmbeddingProviderRequestV1) -> EmbeddingProviderResultV1:
            return EmbeddingProviderResultV1.model_construct(
                vectors=[EmbeddingVectorV1.model_construct(values=[0.1, 0.2, 0.3, 1e100])],
                provider="alibaba-model-studio",
                provider_request_id="provider-request-overflow",
                actual_model="qwen3-vl-embedding-2026-06-30",
                latency_ms=1,
                usage={},
            )

    request = _request()
    authority = _Authority(_target(request))
    vectors = _Vectors()

    with pytest.raises(OperationExecutionFailure) as raised:
        _executor(authority, vectors, OverflowEmbedding()).execute(request)  # type: ignore[arg-type]

    assert raised.value.error.retryable is False
    assert authority.failures == [False]
    assert vectors.last_upsert is None


@pytest.mark.parametrize("outcome_unknown", [False, True])
def test_provider_failure_preserves_normalized_retry_and_unknown_outcome(
    outcome_unknown: bool,
) -> None:
    request = _request()
    authority = _Authority(_target(request))
    failure = EmbeddingProviderFailure(
        EmbeddingProviderErrorV1(
            code="EMBEDDING_TIMEOUT",
            category="TIMEOUT",
            safe_message="Embedding request timed out",
            retryable=not outcome_unknown,
            retry_after_seconds=None if outcome_unknown else 11,
            provider_request_id="provider-request-timeout",
            outcome_unknown=outcome_unknown,
        )
    )

    expected = UnknownOperationOutcome if outcome_unknown else OperationExecutionFailure
    with pytest.raises(expected) as raised:
        _executor(authority, _Vectors(), _Embedding(failure)).execute(request)

    assert raised.value.error.code == "EMBEDDING_TIMEOUT"
    assert raised.value.error.provider_request_id == "provider-request-timeout"
    if not outcome_unknown:
        assert raised.value.retry_after_seconds == 11
        assert authority.failures == [True]
    else:
        assert authority.failures == []
