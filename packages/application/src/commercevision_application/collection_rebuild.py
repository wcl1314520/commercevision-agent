"""Bounded, restart-safe execution of one candidate Collection rebuild."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from commercevision_contracts import (
    CollectionRebuildValidationV1,
    MilvusAnnSearchHitV1,
    MilvusAnnSearchRequestV1,
    MilvusCollectionCreateRequestV1,
    MilvusCollectionSnapshotV1,
    MilvusUpsertRequestV1,
    MilvusVectorIdentityV1,
    MilvusVectorRowV1,
    collection_create_request,
)
from commercevision_contracts.events import (
    CollectionRebuildCommand,
    CollectionRebuildRequestedPayload,
)
from commercevision_domain import CollectionRebuildState, CollectionSpec

from .indexing import (
    EmbeddingProviderPort,
    ExactImageReferencePort,
    ImageIndexingTarget,
    build_embedding_provider_request,
    build_milvus_upsert_request,
)


@dataclass(frozen=True, slots=True)
class CollectionRebuildTarget:
    id: str
    workspace_id: str
    operation_id: str
    source_collection_id: str
    source_collection_name: str
    candidate_collection_id: str
    candidate_collection_name: str
    collection_spec: CollectionSpec
    model_id: str
    state: CollectionRebuildState
    generation: int
    processed_count: int


@dataclass(frozen=True, slots=True)
class RebuildWorkBatch:
    upserts: tuple[ImageIndexingTarget, ...]
    deletes: tuple[MilvusVectorIdentityV1, ...]
    cursor: str | None
    phase_complete: bool


@dataclass(frozen=True, slots=True)
class RebuildValidationExpected:
    embedding_record_ids: frozenset[str]


class CollectionRebuildRepositoryPort(Protocol):
    def load_command(
        self, payload: CollectionRebuildRequestedPayload
    ) -> CollectionRebuildTarget | None: ...

    def begin_provisioning(self, target: CollectionRebuildTarget) -> CollectionRebuildTarget: ...

    def complete_provisioning(self, target: CollectionRebuildTarget) -> None: ...

    def load_work_batch(
        self, target: CollectionRebuildTarget, *, limit: int
    ) -> RebuildWorkBatch: ...

    def commit_work_batch(
        self,
        target: CollectionRebuildTarget,
        batch: RebuildWorkBatch,
        rows: tuple[MilvusVectorRowV1, ...],
    ) -> None: ...

    def validation_expected(self, target: CollectionRebuildTarget) -> RebuildValidationExpected: ...

    def begin_validation(self, target: CollectionRebuildTarget) -> CollectionRebuildTarget: ...

    def complete_validation(
        self,
        target: CollectionRebuildTarget,
        report: CollectionRebuildValidationV1,
    ) -> None: ...

    def complete_retirement(self, target: CollectionRebuildTarget) -> None: ...


class CollectionRebuildVectorPort(Protocol):
    def ensure_collection(self, request: MilvusCollectionCreateRequestV1) -> None: ...

    def upsert(self, request: MilvusUpsertRequestV1) -> None: ...

    def delete_if_generation(self, identity: MilvusVectorIdentityV1) -> bool: ...

    def collection_snapshot(
        self,
        *,
        collection_name: str,
        maximum_rows: int,
        batch_size: int,
    ) -> MilvusCollectionSnapshotV1: ...

    def search(self, request: MilvusAnnSearchRequestV1) -> tuple[MilvusAnnSearchHitV1, ...]: ...

    def retire_collection(self, collection_name: str) -> bool: ...


class CollectionRebuildRunner:
    """Run exactly one externally idempotent phase or batch per command event."""

    def __init__(
        self,
        *,
        repository: CollectionRebuildRepositoryPort,
        references: ExactImageReferencePort,
        provider: EmbeddingProviderPort,
        vectors: CollectionRebuildVectorPort,
        batch_size: int,
        validation_maximum_rows: int,
        validation_sample_size: int,
        minimum_ann_recall_at_10: float,
    ) -> None:
        if not 1 <= batch_size <= 1000:
            raise ValueError("collection rebuild batch size must be between 1 and 1000")
        if not 1 <= validation_maximum_rows <= 1_000_000:
            raise ValueError("collection validation maximum rows is out of bounds")
        if not 1 <= validation_sample_size <= 1000:
            raise ValueError("collection validation sample size is out of bounds")
        if not 0 < minimum_ann_recall_at_10 <= 1:
            raise ValueError("minimum ANN recall must be in (0, 1]")
        self._repository = repository
        self._references = references
        self._provider = provider
        self._vectors = vectors
        self._batch_size = batch_size
        self._validation_maximum_rows = validation_maximum_rows
        self._validation_sample_size = validation_sample_size
        self._minimum_ann_recall_at_10 = minimum_ann_recall_at_10

    def process(self, payload: CollectionRebuildRequestedPayload) -> None:
        target = self._repository.load_command(payload)
        if target is None:
            return
        if payload.command is CollectionRebuildCommand.CONTINUE:
            self._continue(target)
        elif payload.command is CollectionRebuildCommand.VALIDATE:
            self._validate(target)
        elif payload.command is CollectionRebuildCommand.RETIRE:
            self._retire(target)

    def _continue(self, target: CollectionRebuildTarget) -> None:
        if target.state in {
            CollectionRebuildState.REQUESTED,
            CollectionRebuildState.PROVISIONING,
        }:
            provisioning = self._repository.begin_provisioning(target)
            self._vectors.ensure_collection(
                collection_create_request(
                    provisioning.collection_spec,
                    collection_name=provisioning.candidate_collection_name,
                )
            )
            self._repository.complete_provisioning(provisioning)
            return
        if target.state not in {
            CollectionRebuildState.BACKFILLING,
            CollectionRebuildState.REPLAYING,
            CollectionRebuildState.RIGHTS_RESCAN,
        }:
            return
        # Re-assert the immutable candidate on every resumed batch. This is cheap for an
        # existing Collection and makes operator deletion recoverable without touching active.
        self._vectors.ensure_collection(
            collection_create_request(
                target.collection_spec,
                collection_name=target.candidate_collection_name,
            )
        )
        batch = self._repository.load_work_batch(target, limit=self._batch_size)
        rows: list[MilvusVectorRowV1] = []
        for source in batch.upserts:
            provider_request = build_embedding_provider_request(source, references=self._references)
            result = self._provider.embed(provider_request)
            result.validate_for(provider_request)
            upsert = build_milvus_upsert_request(
                source,
                result,
                collection_name=target.candidate_collection_name,
            )
            self._vectors.upsert(upsert)
            rows.append(upsert.row)
        for identity in batch.deletes:
            if identity.collection_name != target.candidate_collection_name:
                raise ValueError("rebuild deletion escaped the candidate collection")
            self._vectors.delete_if_generation(identity)
        self._repository.commit_work_batch(target, batch, tuple(rows))

    def _validate(self, target: CollectionRebuildTarget) -> None:
        if target.state not in {
            CollectionRebuildState.AWAITING_VALIDATION,
            CollectionRebuildState.VALIDATING,
        }:
            return
        validating = self._repository.begin_validation(target)
        expected = self._repository.validation_expected(validating)
        snapshot = self._vectors.collection_snapshot(
            collection_name=validating.candidate_collection_name,
            maximum_rows=self._validation_maximum_rows,
            batch_size=min(self._batch_size, 1000),
        )
        self._repository.complete_validation(
            validating, self._validation_report(validating, expected, snapshot)
        )

    def _validation_report(
        self,
        target: CollectionRebuildTarget,
        expected: RebuildValidationExpected,
        snapshot: MilvusCollectionSnapshotV1,
    ) -> CollectionRebuildValidationV1:
        actual_by_id = {row.embedding_record_id: row for row in snapshot.rows}
        actual_ids = frozenset(actual_by_id)
        missing = expected.embedding_record_ids - actual_ids
        unexpected = actual_ids - expected.embedding_record_ids
        sample_ids = sorted(expected.embedding_record_ids)[: self._validation_sample_size]
        visible_ids = [record_id for record_id in sample_ids if record_id in actual_by_id]
        recalls: list[float] = []
        fixed_passes = 0
        for record_id in visible_ids:
            query_row = actual_by_id[record_id]
            eligible_rows = sorted(
                (
                    row
                    for row in snapshot.rows
                    if row.workspace_id == query_row.workspace_id
                    and row.embedding_record_id in expected.embedding_record_ids
                ),
                key=lambda row: row.embedding_record_id,
            )
            limit = min(10, len(eligible_rows))
            if limit == 0:
                continue
            exact_ids = {
                row.embedding_record_id
                for row in sorted(
                    eligible_rows,
                    key=lambda row: (
                        -self._cosine(query_row.vector, row.vector),
                        row.embedding_record_id,
                    ),
                )[:limit]
            }
            hits = self._vectors.search(
                MilvusAnnSearchRequestV1(
                    collection_name=target.candidate_collection_name,
                    workspace_id=query_row.workspace_id,
                    vector_kind=query_row.vector_kind,
                    eligible_embedding_record_ids=[
                        row.embedding_record_id for row in eligible_rows
                    ],
                    query_vector=query_row.vector,
                    limit=limit,
                )
            )
            hit_ids = {getattr(hit, "embedding_record_id", None) for hit in hits}
            recall = len(exact_ids.intersection(hit_ids)) / len(exact_ids)
            recalls.append(recall)
            if recall >= self._minimum_ann_recall_at_10:
                fixed_passes += 1
        ann_recall = sum(recalls) / len(recalls) if recalls else 1.0
        accepted = (
            not missing
            and not unexpected
            and len(visible_ids) == len(sample_ids)
            and ann_recall >= self._minimum_ann_recall_at_10
            and fixed_passes == len(visible_ids)
        )
        return CollectionRebuildValidationV1(
            expected_row_count=len(expected.embedding_record_ids),
            actual_row_count=snapshot.row_count,
            missing_primary_key_count=len(missing),
            unexpected_primary_key_count=len(unexpected),
            sampled_visibility_count=len(sample_ids),
            sampled_visibility_failures=len(sample_ids) - len(visible_ids),
            ann_recall_at_10=ann_recall,
            minimum_ann_recall_at_10=self._minimum_ann_recall_at_10,
            fixed_query_pass_count=fixed_passes,
            fixed_query_total_count=len(visible_ids),
            unauthorized_result_count=len(unexpected),
            queries_with_unauthorized_results=1 if unexpected else 0,
            accepted=accepted,
        )

    def _retire(self, target: CollectionRebuildTarget) -> None:
        if target.state is not CollectionRebuildState.RETIRING:
            return
        if target.source_collection_name == target.candidate_collection_name:
            raise ValueError("rebuild retirement cannot target the active candidate")
        self._vectors.retire_collection(target.source_collection_name)
        self._repository.complete_retirement(target)

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if len(left) != len(right) or not left:
            raise ValueError("validation vectors must have one matching dimension")
        dot = sum(a * b for a, b in zip(left, right, strict=True))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return dot / (left_norm * right_norm)
