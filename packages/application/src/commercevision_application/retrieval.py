"""Rights-first hybrid retrieval orchestration."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from commercevision_contracts import (
    EmbeddingImageInputV1,
    EmbeddingProviderFailure,
    EmbeddingProviderRequestV1,
    EmbeddingProviderResultV1,
    MilvusAnnSearchHitV1,
    RetrievalCitationV1,
    RetrievalDegradationV1,
    RetrievalQueryV1,
    RetrievalResponseV1,
    RetrievalScoreBreakdownV1,
)
from commercevision_domain import (
    ReciprocalRankedCandidate,
    RetentionClass,
    RetrievalCandidate,
    RetrievalChannel,
    RetrievalPolicy,
    VectorKind,
    apply_bounded_rerank,
    bound_retrieval_candidates,
    canonicalize_uuid,
    deduplicate_retrieval_candidates,
    reciprocal_rank_fuse,
)

from .indexing_transfer import ImageIndexDataTransferDenied


@dataclass(frozen=True, slots=True)
class EligibleRetrievalAsset:
    asset_id: str
    asset_version_id: str
    content_sha256: str
    product_id: str
    category: str
    brand: str
    role: str
    rights_record_id: str
    rights_record_version: int
    retention_class: RetentionClass

    def __post_init__(self) -> None:
        for value in (
            self.asset_id,
            self.asset_version_id,
            self.product_id,
            self.rights_record_id,
        ):
            canonicalize_uuid(value)
        if len(self.content_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.content_sha256
        ):
            raise ValueError("eligible retrieval content hash is invalid")
        if type(self.rights_record_version) is not int or self.rights_record_version < 1:
            raise ValueError("eligible retrieval Rights Record version is invalid")
        try:
            retention_class = RetentionClass(self.retention_class)
        except (TypeError, ValueError):
            raise ValueError("eligible retrieval retention class is invalid") from None
        object.__setattr__(self, "retention_class", retention_class)


@dataclass(frozen=True, slots=True)
class RetrievalEligibility:
    decided_at: datetime
    items: tuple[EligibleRetrievalAsset, ...]

    def __post_init__(self) -> None:
        if self.decided_at.tzinfo is None or self.decided_at.utcoffset() is None:
            raise ValueError("retrieval eligibility decision time must be timezone-aware")
        if len({item.asset_version_id for item in self.items}) != len(self.items):
            raise ValueError("retrieval eligibility must contain unique Asset Versions")
        object.__setattr__(self, "decided_at", self.decided_at.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class RetrievalRecallHit:
    asset_version_id: str
    raw_score: float | None = None

    def __post_init__(self) -> None:
        canonicalize_uuid(self.asset_version_id)
        if self.raw_score is not None and (
            not isinstance(self.raw_score, int | float)
            or isinstance(self.raw_score, bool)
            or not math.isfinite(self.raw_score)
        ):
            raise ValueError("retrieval recall raw score must be finite")


@dataclass(frozen=True, slots=True)
class RetrievalRecallBatch:
    channel: RetrievalChannel
    hits: tuple[RetrievalRecallHit, ...]

    def __post_init__(self) -> None:
        if len({hit.asset_version_id for hit in self.hits}) != len(self.hits):
            raise ValueError("retrieval recall channel returned duplicate candidates")


@dataclass(frozen=True, slots=True)
class DenseEmbeddingCandidate:
    embedding_record_id: str
    asset_version_id: str

    def __post_init__(self) -> None:
        canonicalize_uuid(self.embedding_record_id)
        canonicalize_uuid(self.asset_version_id)


@dataclass(frozen=True, slots=True)
class DenseRetrievalTarget:
    collection_name: str
    vector_kind: VectorKind
    dimension: int
    provider: str
    model_id: str
    pinned_revision: str
    model_configuration_version: str
    preprocessing_version: str
    candidates: tuple[DenseEmbeddingCandidate, ...]

    def __post_init__(self) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_]{0,254}", self.collection_name) is None:
            raise ValueError("dense retrieval collection name is invalid")
        if not isinstance(self.vector_kind, VectorKind):
            raise ValueError("dense retrieval target vector kind is invalid")
        if type(self.dimension) is not int or not 1 <= self.dimension <= 32_768:
            raise ValueError("dense retrieval dimension is invalid")
        if any(
            not value or value != value.strip()
            for value in (
                self.provider,
                self.model_id,
                self.pinned_revision,
                self.model_configuration_version,
                self.preprocessing_version,
            )
        ):
            raise ValueError("dense retrieval model identity is incomplete")
        if len({item.embedding_record_id for item in self.candidates}) != len(self.candidates):
            raise ValueError("dense retrieval embedding identities must be unique")
        if len({item.asset_version_id for item in self.candidates}) != len(self.candidates):
            raise ValueError("dense retrieval Asset Versions must be unique")


class DenseRetrievalCatalog(Protocol):
    def load_target(
        self,
        query: RetrievalQueryV1,
        *,
        vector_kind: VectorKind,
        eligible_asset_version_ids: tuple[str, ...],
    ) -> DenseRetrievalTarget | None: ...


class DenseQueryVectorProvider(Protocol):
    def embed_query(
        self,
        query: RetrievalQueryV1,
        *,
        target: DenseRetrievalTarget,
    ) -> tuple[float, ...]: ...


class RetrievalQueryImageReference(Protocol):
    def temporary_input(
        self,
        query: RetrievalQueryV1,
        *,
        provider: str,
    ) -> EmbeddingImageInputV1: ...


class RetrievalEmbeddingProvider(Protocol):
    def embed(self, request: EmbeddingProviderRequestV1) -> EmbeddingProviderResultV1: ...


class ProviderDenseQueryVectorService:
    """Create one provider query vector matching the active collection identity."""

    def __init__(
        self,
        *,
        embedding: RetrievalEmbeddingProvider,
        image_references: RetrievalQueryImageReference | None = None,
    ) -> None:
        self._embedding = embedding
        self._image_references = image_references

    def embed_query(
        self,
        query: RetrievalQueryV1,
        *,
        target: DenseRetrievalTarget,
    ) -> tuple[float, ...]:
        images: list[EmbeddingImageInputV1] = []
        if query.query_image_asset_version_id is not None:
            if self._image_references is None:
                raise ValueError("retrieval query image reference service is not configured")
            image = self._image_references.temporary_input(query, provider=target.provider)
            if image.asset_version_id != query.query_image_asset_version_id:
                raise ValueError("retrieval query image reference identity changed")
            images.append(image)
        controlled_text = (
            query.query_text if target.vector_kind is VectorKind.PRODUCT_FUSED else None
        )
        if target.vector_kind is VectorKind.IMAGE and not images:
            raise ValueError("IMAGE retrieval requires a controlled query image")
        hash_payload = {
            "image": (
                {
                    "asset_version_id": images[0].asset_version_id,
                    "content_sha256": images[0].content_sha256,
                }
                if images
                else None
            ),
            "model_configuration_version": target.model_configuration_version,
            "preprocessing_version": target.preprocessing_version,
            "query_text": controlled_text,
            "vector_kind": target.vector_kind.value,
        }
        input_hash = hashlib.sha256(
            json.dumps(
                hash_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        request = EmbeddingProviderRequestV1(
            provider=target.provider,
            model_id=target.model_id,
            pinned_revision=target.pinned_revision,
            model_configuration_version=target.model_configuration_version,
            preprocessing_version=target.preprocessing_version,
            vector_kind=target.vector_kind,
            expected_dimension=target.dimension,
            input_hash=input_hash,
            images=images,
            controlled_text=controlled_text,
        )
        result = self._embedding.embed(request)
        result.validate_for(request)
        return tuple(result.vectors[0].values)


class DenseAnnSearch(Protocol):
    def search(
        self,
        *,
        collection_name: str,
        workspace_id: str,
        vector_kind: VectorKind,
        eligible_embedding_record_ids: tuple[str, ...],
        query_vector: tuple[float, ...],
        limit: int,
    ) -> tuple[MilvusAnnSearchHitV1, ...]: ...


class DenseRetrievalIndexUnavailable(RuntimeError):
    """Expected active-index gap that must make hybrid completeness explicit."""

    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


class RetrievalQueryImageUnavailable(RuntimeError):
    """Expected query-image authority or controlled-object loss."""

    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


class DenseRetrievalSource:
    """Bind active MySQL routing, query embedding and fenced Milvus recall."""

    def __init__(
        self,
        *,
        vector_kind: VectorKind,
        catalog: DenseRetrievalCatalog,
        query_vectors: DenseQueryVectorProvider,
        search: DenseAnnSearch,
    ) -> None:
        if vector_kind not in {VectorKind.IMAGE, VectorKind.PRODUCT_FUSED}:
            raise ValueError("dense retrieval vector kind is unsupported")
        self._vector_kind = vector_kind
        self._catalog = catalog
        self._query_vectors = query_vectors
        self._search = search
        self.channel = (
            RetrievalChannel.IMAGE_DENSE
            if vector_kind is VectorKind.IMAGE
            else RetrievalChannel.PRODUCT_FUSED_DENSE
        )

    def recall(
        self,
        query: RetrievalQueryV1,
        *,
        eligible_asset_version_ids: tuple[str, ...],
        limit: int,
    ) -> RetrievalRecallBatch:
        if self._vector_kind not in query.vector_kinds:
            return RetrievalRecallBatch(channel=self.channel, hits=())
        try:
            target = self._catalog.load_target(
                query,
                vector_kind=self._vector_kind,
                eligible_asset_version_ids=eligible_asset_version_ids,
            )
        except DenseRetrievalIndexUnavailable as exc:
            raise RetrievalSourceUnavailable(
                channel=self.channel,
                code=exc.code,
                message=exc.safe_message,
            ) from exc
        if target is None or not target.candidates:
            return RetrievalRecallBatch(channel=self.channel, hits=())
        if target.vector_kind is not self._vector_kind:
            raise ValueError("dense retrieval catalog returned the wrong vector kind")
        candidate_by_embedding = {
            candidate.embedding_record_id: candidate for candidate in target.candidates
        }
        try:
            query_vector = tuple(self._query_vectors.embed_query(query, target=target))
            if len(query_vector) != target.dimension:
                raise ValueError("query vector dimension does not match the active collection")
            hits = self._search.search(
                collection_name=target.collection_name,
                workspace_id=query.workspace_id,
                vector_kind=self._vector_kind,
                eligible_embedding_record_ids=tuple(candidate_by_embedding),
                query_vector=query_vector,
                limit=min(limit, 100, len(candidate_by_embedding)),
            )
        except RetrievalQueryImageUnavailable as exc:
            raise RetrievalSourceUnavailable(
                channel=self.channel,
                code=exc.code,
                message=exc.safe_message,
            ) from exc
        except ImageIndexDataTransferDenied as exc:
            raise RetrievalSourceUnavailable(
                channel=self.channel,
                code=exc.code,
                message=exc.message,
            ) from exc
        except (EmbeddingProviderFailure, ConnectionError, TimeoutError) as exc:
            raise RetrievalSourceUnavailable(
                channel=self.channel,
                code="DENSE_RECALL_UNAVAILABLE",
                message="dense retrieval is temporarily unavailable",
            ) from exc
        except ValueError as exc:
            raise RuntimeError("dense retrieval returned invalid evidence") from exc
        recalled: list[RetrievalRecallHit] = []
        for hit in hits:
            candidate = candidate_by_embedding.get(hit.embedding_record_id)
            if candidate is None or candidate.asset_version_id != hit.asset_version_id:
                raise RuntimeError("dense retrieval escaped its MySQL embedding fence")
            recalled.append(
                RetrievalRecallHit(
                    asset_version_id=candidate.asset_version_id,
                    raw_score=hit.score,
                )
            )
        return RetrievalRecallBatch(channel=self.channel, hits=tuple(recalled))


class RetrievalSourceUnavailable(RuntimeError):
    """Explicit optional-channel degradation safe to expose to callers."""

    def __init__(self, *, channel: RetrievalChannel, code: str, message: str) -> None:
        super().__init__(message)
        self.channel = channel
        self.code = code
        self.safe_message = message


class RetrievalRerankerUnavailable(RuntimeError):
    """Expected reranker outage that permits an explicit fusion-only response."""


class ExplicitReferenceRetrievalSource:
    """Turn caller-supplied references into a bounded, authorized candidate channel."""

    channel = RetrievalChannel.EXPLICIT

    @staticmethod
    def recall(
        query: RetrievalQueryV1,
        *,
        eligible_asset_version_ids: tuple[str, ...],
        limit: int,
    ) -> RetrievalRecallBatch:
        eligible = set(eligible_asset_version_ids)
        return RetrievalRecallBatch(
            channel=RetrievalChannel.EXPLICIT,
            hits=tuple(
                RetrievalRecallHit(asset_version_id=asset_version_id)
                for asset_version_id in query.explicit_reference_asset_version_ids
                if asset_version_id in eligible
            )[:limit],
        )


class RetrievalAuthority(Protocol):
    def eligible_asset_versions(self, query: RetrievalQueryV1) -> RetrievalEligibility: ...

    def revalidate_asset_versions(
        self,
        query: RetrievalQueryV1,
        *,
        asset_version_ids: tuple[str, ...],
    ) -> RetrievalEligibility: ...


class RetrievalSource(Protocol):
    channel: RetrievalChannel

    def recall(
        self,
        query: RetrievalQueryV1,
        *,
        eligible_asset_version_ids: tuple[str, ...],
        limit: int,
    ) -> RetrievalRecallBatch: ...


class RetrievalReranker(Protocol):
    def rerank(
        self,
        query: RetrievalQueryV1,
        *,
        candidate_ids: tuple[str, ...],
    ) -> tuple[str, ...]: ...


class RetrievalApplicationService:
    """Keep every recall, fusion, rerank and final-return step inside rights fences."""

    def __init__(
        self,
        *,
        authority: RetrievalAuthority,
        sources: Sequence[RetrievalSource],
        policy: RetrievalPolicy,
        reranker: RetrievalReranker | None = None,
    ) -> None:
        source_channels = [source.channel for source in sources]
        if len(set(source_channels)) != len(source_channels):
            raise ValueError("retrieval candidate source channels must be unique")
        self._authority = authority
        self._sources = tuple(sources)
        self._policy = policy
        self._reranker = reranker

    def execute(self, query: RetrievalQueryV1) -> RetrievalResponseV1:
        started_ns = time.perf_counter_ns()
        if query.retrieval_policy_version != self._policy.version:
            raise ValueError("requested Retrieval Policy version is not active")
        initial = self._authority.eligible_asset_versions(query)
        eligible_by_id = {item.asset_version_id: item for item in initial.items}
        if not eligible_by_id:
            return RetrievalResponseV1(
                retrieval_policy_version=self._policy.version,
                complete_hybrid=True,
                degradations=[],
                eligible_asset_version_count=0,
                fused_candidate_count=0,
                final_authorized_candidate_count=0,
                latency_ms=self._elapsed_ms(started_ns),
                citations=[],
            )
        eligible_ids = tuple(eligible_by_id)
        rankings: dict[RetrievalChannel, tuple[str, ...]] = {}
        raw_scores: dict[tuple[RetrievalChannel, str], float] = {}
        attempted_channels: set[RetrievalChannel] = set()
        degradations: list[RetrievalDegradationV1] = []
        for source in self._sources:
            attempted_channels.add(source.channel)
            try:
                batch = source.recall(
                    query,
                    eligible_asset_version_ids=eligible_ids,
                    limit=min(query.candidate_limit, len(eligible_ids)),
                )
            except RetrievalSourceUnavailable as exc:
                if exc.channel is not source.channel:
                    raise RuntimeError(
                        "retrieval degradation channel identity is inconsistent"
                    ) from exc
                degradations.append(
                    RetrievalDegradationV1(
                        component=source.channel.value,
                        code=exc.code,
                        message=exc.safe_message,
                    )
                )
                continue
            except ValueError as exc:
                raise RuntimeError("retrieval source returned invalid evidence") from exc
            if batch.channel is not source.channel:
                raise RuntimeError("retrieval source returned the wrong channel")
            escaped = {hit.asset_version_id for hit in batch.hits} - set(eligible_by_id)
            if escaped:
                raise RuntimeError("retrieval candidate source escaped the MySQL eligible set")
            rankings[batch.channel] = tuple(hit.asset_version_id for hit in batch.hits)
            raw_scores.update(
                {
                    (batch.channel, hit.asset_version_id): hit.raw_score
                    for hit in batch.hits
                    if hit.raw_score is not None
                }
            )
        expected_dense = {
            RetrievalChannel.IMAGE_DENSE
            if vector_kind is VectorKind.IMAGE
            else RetrievalChannel.PRODUCT_FUSED_DENSE
            for vector_kind in query.vector_kinds
        }
        if expected_dense - attempted_channels:
            raise RuntimeError("requested dense retrieval channel is not configured")
        fused = reciprocal_rank_fuse(rankings=rankings, policy=self._policy)
        fused_by_id = {candidate.asset_version_id: candidate for candidate in fused}
        deduplicated = deduplicate_retrieval_candidates(
            tuple(
                RetrievalCandidate(
                    asset_id=eligible_by_id[candidate.asset_version_id].asset_id,
                    asset_version_id=candidate.asset_version_id,
                    content_sha256=eligible_by_id[candidate.asset_version_id].content_sha256,
                    required_brand_member=(
                        RetrievalChannel.BRAND_PROFILE in candidate.channel_ranks
                    ),
                )
                for candidate in fused
            )
        )
        bounded = bound_retrieval_candidates(
            deduplicated,
            limit=query.candidate_limit,
        )
        candidate_ids = tuple(candidate.asset_version_id for candidate in bounded)
        rerank_positions: dict[str, int] = {}
        if self._reranker is not None and candidate_ids:
            try:
                candidate_ids = apply_bounded_rerank(
                    candidate_ids,
                    self._reranker.rerank(query, candidate_ids=candidate_ids),
                )
                rerank_positions = {
                    candidate_id: position
                    for position, candidate_id in enumerate(candidate_ids, start=1)
                }
            except (RetrievalRerankerUnavailable, ValueError):
                degradations.append(
                    RetrievalDegradationV1(
                        component="RERANKER",
                        code="RERANKER_UNAVAILABLE",
                        message="reranker unavailable; preserving versioned fusion order",
                    )
                )
        try:
            final = self._authority.revalidate_asset_versions(
                query,
                asset_version_ids=candidate_ids,
            )
        except ValueError as exc:
            raise RuntimeError("final retrieval authority rejected its candidate set") from exc
        final_by_id = {item.asset_version_id: item for item in final.items}
        if set(final_by_id) - set(candidate_ids):
            raise RuntimeError("final retrieval authority introduced a new candidate")
        selected_ids = tuple(
            candidate_id for candidate_id in candidate_ids if candidate_id in final_by_id
        )[: query.result_limit]
        try:
            citations = [
                self._citation(
                    query=query,
                    item=final_by_id[candidate_id],
                    fused=fused_by_id[candidate_id],
                    raw_scores=raw_scores,
                    rank=rank,
                    rerank_position=rerank_positions.get(candidate_id),
                    decided_at=final.decided_at,
                )
                for rank, candidate_id in enumerate(selected_ids, start=1)
            ]
            return RetrievalResponseV1(
                retrieval_policy_version=self._policy.version,
                complete_hybrid=not degradations,
                degradations=degradations,
                eligible_asset_version_count=len(eligible_by_id),
                fused_candidate_count=len(candidate_ids),
                final_authorized_candidate_count=len(final_by_id),
                latency_ms=self._elapsed_ms(started_ns),
                citations=citations,
            )
        except ValueError as exc:
            raise RuntimeError("retrieval response evidence is internally inconsistent") from exc

    @staticmethod
    def _elapsed_ms(started_ns: int) -> int:
        return max(0, (time.perf_counter_ns() - started_ns) // 1_000_000)

    def _citation(
        self,
        *,
        query: RetrievalQueryV1,
        item: EligibleRetrievalAsset,
        fused: ReciprocalRankedCandidate,
        raw_scores: dict[tuple[RetrievalChannel, str], float],
        rank: int,
        rerank_position: int | None,
        decided_at: datetime,
    ) -> RetrievalCitationV1:
        channels = sorted(
            fused.channel_ranks,
            key=lambda channel: (fused.channel_ranks[channel], channel.value),
        )
        if RetrievalChannel.BRAND_PROFILE in channels:
            reason = "required published brand member"
        elif RetrievalChannel.EXPLICIT in channels:
            reason = "explicit authorized reference"
        else:
            reason = "authorized hybrid retrieval candidate"
        return RetrievalCitationV1(
            asset_id=item.asset_id,
            asset_version_id=item.asset_version_id,
            rights_record_id=item.rights_record_id,
            rights_record_version=item.rights_record_version,
            retrieval_policy_version=self._policy.version,
            brand_profile_version=(
                query.brand_profile_version if RetrievalChannel.BRAND_PROFILE in channels else None
            ),
            channels=channels,
            score=RetrievalScoreBreakdownV1(
                channel_ranks=dict(fused.channel_ranks),
                channel_raw_scores={
                    channel: raw_scores[(channel, item.asset_version_id)]
                    for channel in channels
                    if (channel, item.asset_version_id) in raw_scores
                },
                reciprocal_rank_fusion=fused.rrf_score,
                business_adjustment=fused.business_adjustment,
                final_score=fused.final_score,
                rerank_position=rerank_position,
            ),
            rank=rank,
            reason=reason,
            decided_at=decided_at,
        )
