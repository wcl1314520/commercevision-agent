"""MySQL ngram lexical candidate adapter for controlled product documents."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from commercevision_domain import validate_workspace_id
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session, sessionmaker

_MAX_QUERY_BYTES = 1024
_MYSQL_IN_CHUNK_SIZE = 1_000


@dataclass(frozen=True, slots=True)
class ProductLexicalHit:
    search_document_id: str
    asset_version_id: str
    embedding_record_id: str
    score: float


class MySqlProductLexicalSearch:
    """Return bounded FULLTEXT candidates; Ticket 11 owns retrieval fusion."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def search(
        self,
        *,
        workspace_id: str,
        query: str,
        limit: int,
    ) -> tuple[ProductLexicalHit, ...]:
        validate_workspace_id(workspace_id)
        if type(limit) is not int or limit < 1 or limit > 100:
            raise ValueError("lexical search limit must be an integer between 1 and 100")
        normalized_query = self._normalize_query(query)
        with self._session_factory() as session:
            rows = session.execute(
                text(
                    "SELECT id, asset_version_id, embedding_record_id, "
                    "MATCH(title, labels, ocr_summary, product_brief_summary, approved_notes) "
                    "AGAINST (:query IN NATURAL LANGUAGE MODE) AS score "
                    "FROM product_search_documents FORCE INDEX (ft_product_search_cjk) "
                    "WHERE workspace_id = :workspace_id AND state = 'INDEXED' AND "
                    "(retention_class = 'FOUNDATION' OR "
                    "(retention_class = 'TASK' AND retention_deadline > UTC_TIMESTAMP(6))) AND "
                    "MATCH(title, labels, ocr_summary, product_brief_summary, approved_notes) "
                    "AGAINST (:query IN NATURAL LANGUAGE MODE) > 0 "
                    "ORDER BY score DESC, id ASC LIMIT :limit"
                ),
                {
                    "workspace_id": workspace_id,
                    "query": normalized_query,
                    "limit": limit,
                },
            ).mappings()
            return tuple(
                ProductLexicalHit(
                    search_document_id=row["id"],
                    asset_version_id=row["asset_version_id"],
                    embedding_record_id=row["embedding_record_id"],
                    score=float(row["score"]),
                )
                for row in rows
            )

    def search_eligible(
        self,
        *,
        workspace_id: str,
        query: str,
        eligible_asset_version_ids: tuple[str, ...],
        limit: int,
    ) -> tuple[ProductLexicalHit, ...]:
        """Search only the caller's complete, current MySQL-authorized set."""

        validate_workspace_id(workspace_id)
        if type(limit) is not int or limit < 1 or limit > 1_000:
            raise ValueError("lexical search limit must be an integer between 1 and 1000")
        if not eligible_asset_version_ids:
            return ()
        if len(set(eligible_asset_version_ids)) != len(eligible_asset_version_ids):
            raise ValueError("eligible lexical identities must be unique")
        normalized_query = self._normalize_query(query)
        statement = text(
            "SELECT id, asset_version_id, embedding_record_id, "
            "MATCH(title, labels, ocr_summary, product_brief_summary, approved_notes) "
            "AGAINST (:query IN NATURAL LANGUAGE MODE) AS score "
            "FROM product_search_documents FORCE INDEX (ft_product_search_cjk) "
            "WHERE workspace_id = :workspace_id AND asset_version_id IN :eligible_ids AND "
            "state = 'INDEXED' AND (retention_class = 'FOUNDATION' OR "
            "(retention_class = 'TASK' AND retention_deadline > UTC_TIMESTAMP(6))) AND "
            "MATCH(title, labels, ocr_summary, product_brief_summary, approved_notes) "
            "AGAINST (:query IN NATURAL LANGUAGE MODE) > 0 "
            "ORDER BY score DESC, id ASC LIMIT :limit"
        ).bindparams(bindparam("eligible_ids", expanding=True))
        hits: list[ProductLexicalHit] = []
        with self._session_factory() as session:
            for offset in range(0, len(eligible_asset_version_ids), _MYSQL_IN_CHUNK_SIZE):
                eligible_chunk = eligible_asset_version_ids[offset : offset + _MYSQL_IN_CHUNK_SIZE]
                rows = session.execute(
                    statement,
                    {
                        "workspace_id": workspace_id,
                        "eligible_ids": eligible_chunk,
                        "query": normalized_query,
                        "limit": min(limit, len(eligible_chunk)),
                    },
                ).mappings()
                hits.extend(
                    ProductLexicalHit(
                        search_document_id=row["id"],
                        asset_version_id=row["asset_version_id"],
                        embedding_record_id=row["embedding_record_id"],
                        score=float(row["score"]),
                    )
                    for row in rows
                )
        if len({hit.asset_version_id for hit in hits}) != len(hits):
            raise RuntimeError("eligible lexical chunks returned duplicate Asset Versions")
        hits.sort(key=lambda hit: (-hit.score, hit.search_document_id))
        return tuple(hits[:limit])

    @staticmethod
    def _normalize_query(query: str) -> str:
        if not isinstance(query, str):
            raise ValueError("lexical search query must be text")
        normalized = unicodedata.normalize("NFKC", query)
        safe = "".join(
            " " if unicodedata.category(character).startswith("C") else character
            for character in normalized
        )
        collapsed = " ".join(safe.split()).casefold()
        if not collapsed:
            raise ValueError("lexical search query must not be empty")
        if len(collapsed.encode("utf-8")) > _MAX_QUERY_BYTES:
            raise ValueError("lexical search query exceeds the byte limit")
        return collapsed
