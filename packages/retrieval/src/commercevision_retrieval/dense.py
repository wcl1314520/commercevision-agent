"""Eligible-set-preserving dense recall composition."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from commercevision_contracts import MilvusAnnSearchHitV1, MilvusAnnSearchRequestV1
from commercevision_domain import VectorKind


class _MilvusAnnIndex(Protocol):
    def search(
        self,
        request: MilvusAnnSearchRequestV1,
    ) -> tuple[MilvusAnnSearchHitV1, ...]: ...


class ChunkedMilvusAnnSearch:
    """Search every bounded eligible-ID chunk and merge without weakening filters."""

    def __init__(self, *, index: _MilvusAnnIndex, maximum_filter_ids: int = 1_000) -> None:
        if type(maximum_filter_ids) is not int or not 1 <= maximum_filter_ids <= 1_000:
            raise ValueError("Milvus eligible filter bound must be between 1 and 1000")
        self._index = index
        self._maximum_filter_ids = maximum_filter_ids

    def search(
        self,
        *,
        collection_name: str,
        workspace_id: str,
        vector_kind: VectorKind,
        eligible_embedding_record_ids: Sequence[str],
        query_vector: Sequence[float],
        limit: int,
    ) -> tuple[MilvusAnnSearchHitV1, ...]:
        eligible = tuple(eligible_embedding_record_ids)
        if not eligible:
            return ()
        if len(set(eligible)) != len(eligible):
            raise ValueError("dense recall eligible identities must be unique")
        if type(limit) is not int or not 1 <= limit <= min(100, len(eligible)):
            raise ValueError("dense recall limit exceeds the eligible set")
        hits: list[MilvusAnnSearchHitV1] = []
        for offset in range(0, len(eligible), self._maximum_filter_ids):
            chunk = eligible[offset : offset + self._maximum_filter_ids]
            hits.extend(
                self._index.search(
                    MilvusAnnSearchRequestV1(
                        collection_name=collection_name,
                        workspace_id=workspace_id,
                        vector_kind=vector_kind,
                        eligible_embedding_record_ids=list(chunk),
                        query_vector=list(query_vector),
                        limit=min(limit, len(chunk)),
                    )
                )
            )
        if len({hit.embedding_record_id for hit in hits}) != len(hits):
            raise ValueError("dense recall returned a candidate from multiple eligible chunks")
        hits.sort(key=lambda hit: (-hit.score, hit.embedding_record_id))
        return tuple(hits[:limit])
