"""Retained Retrieval Runs and opaque, rights-rechecked preview exchange."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import UTC, timedelta

from commercevision_contracts import (
    RetrievalCitationV1,
    RetrievalQueryV1,
    RetrievalResponseV1,
    RetrievalScoreBreakdownV1,
    RetrievalTemporaryReferenceV1,
)
from commercevision_contracts.object_storage import (
    ObjectReference,
    ObjectStorage,
    TemporaryReadRequest,
)
from commercevision_domain import RetrievalChannel, StorageLocationClass, new_uuid7
from sqlalchemy import and_, literal_column, select
from sqlalchemy.orm import Session, sessionmaker

from .models import AssetObjectModel
from .retrieval import MySqlRetrievalAuthority
from .retrieval_models import RetrievalResultModel, RetrievalRunModel

_OPAQUE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,128}$", re.ASCII)


class MySqlRetrievalRunStore:
    """Persist exact query/citation evidence and hash-only preview grants."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        run_retention: timedelta,
        preview_token_lifetime: timedelta,
    ) -> None:
        if run_retention <= timedelta(0):
            raise ValueError("retrieval run retention must be positive")
        if not timedelta(seconds=30) <= preview_token_lifetime <= timedelta(seconds=60):
            raise ValueError("retrieval preview token lifetime must be between 30 and 60 seconds")
        if run_retention <= preview_token_lifetime:
            raise ValueError("retrieval run retention must exceed preview token lifetime")
        self._session_factory = session_factory
        self._run_retention = run_retention
        self._preview_token_lifetime = preview_token_lifetime

    def record(
        self,
        query: RetrievalQueryV1,
        response: RetrievalResponseV1,
    ) -> RetrievalResponseV1:
        if query.retrieval_policy_version != response.retrieval_policy_version:
            raise ValueError("retrieval query and response policy versions must match")
        if response.retrieval_run_id is not None or any(
            citation.preview_reference_token is not None for citation in response.citations
        ):
            raise ValueError("retrieval response has already been retained")
        run_id = new_uuid7()
        query_json = query.model_dump(mode="json")
        query_bytes = json.dumps(
            query_json,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        tokens = [secrets.token_urlsafe(32) for _ in response.citations]
        with self._session_factory.begin() as session:
            database_now = session.scalar(select(literal_column("UTC_TIMESTAMP(6)")))
            if database_now is None:
                raise RuntimeError("MySQL retrieval run time is unavailable")
            if database_now.tzinfo is None:
                database_now = database_now.replace(tzinfo=UTC)
            session.add(
                RetrievalRunModel(
                    id=run_id,
                    workspace_id=query.workspace_id,
                    requester_id=query.requester_id,
                    query_json=query_json,
                    query_sha256=hashlib.sha256(query_bytes).hexdigest(),
                    retrieval_policy_version=response.retrieval_policy_version,
                    complete_hybrid=response.complete_hybrid,
                    degradations_json=[
                        degradation.model_dump(mode="json") for degradation in response.degradations
                    ],
                    eligible_asset_version_count=(response.eligible_asset_version_count),
                    fused_candidate_count=response.fused_candidate_count,
                    final_authorized_candidate_count=(response.final_authorized_candidate_count),
                    latency_ms=response.latency_ms,
                    created_at=database_now,
                    expires_at=database_now + self._run_retention,
                )
            )
            session.flush()
            for citation, token in zip(response.citations, tokens, strict=True):
                session.add(
                    RetrievalResultModel(
                        retrieval_run_id=run_id,
                        rank=citation.rank,
                        workspace_id=query.workspace_id,
                        asset_id=citation.asset_id,
                        asset_version_id=citation.asset_version_id,
                        rights_record_id=citation.rights_record_id,
                        rights_record_version=citation.rights_record_version,
                        brand_profile_version=citation.brand_profile_version,
                        channels_json=[channel.value for channel in citation.channels],
                        score_json=citation.score.model_dump(mode="json"),
                        reason=citation.reason,
                        decided_at=citation.decided_at,
                        preview_token_sha256=self._token_hash(token),
                        preview_expires_at=database_now + self._preview_token_lifetime,
                        created_at=database_now,
                    )
                )
        return response.model_copy(
            update={
                "retrieval_run_id": run_id,
                "citations": [
                    citation.model_copy(update={"preview_reference_token": token})
                    for citation, token in zip(response.citations, tokens, strict=True)
                ],
            }
        )

    def get(self, *, workspace_id: str, run_id: str) -> RetrievalResponseV1 | None:
        with self._session_factory() as session:
            database_now = literal_column("UTC_TIMESTAMP(6)")
            run = session.scalar(
                select(RetrievalRunModel).where(
                    RetrievalRunModel.workspace_id == workspace_id,
                    RetrievalRunModel.id == run_id,
                    RetrievalRunModel.expires_at > database_now,
                )
            )
            if run is None:
                return None
            results = tuple(
                session.scalars(
                    select(RetrievalResultModel)
                    .where(
                        RetrievalResultModel.workspace_id == workspace_id,
                        RetrievalResultModel.retrieval_run_id == run_id,
                    )
                    .order_by(RetrievalResultModel.rank)
                )
            )
        return RetrievalResponseV1.model_validate(
            {
                "retrieval_run_id": run.id,
                "retrieval_policy_version": run.retrieval_policy_version,
                "complete_hybrid": run.complete_hybrid,
                "degradations": run.degradations_json,
                "eligible_asset_version_count": run.eligible_asset_version_count,
                "fused_candidate_count": run.fused_candidate_count,
                "final_authorized_candidate_count": (run.final_authorized_candidate_count),
                "latency_ms": run.latency_ms,
                "citations": [
                    self._citation_json(
                        result,
                        retrieval_policy_version=run.retrieval_policy_version,
                    )
                    for result in results
                ],
            }
        )

    @staticmethod
    def _citation_json(
        result: RetrievalResultModel,
        *,
        retrieval_policy_version: str,
    ) -> RetrievalCitationV1:
        score_json = dict(result.score_json)
        score_json["channel_ranks"] = {
            RetrievalChannel(channel): rank for channel, rank in score_json["channel_ranks"].items()
        }
        score_json["channel_raw_scores"] = {
            RetrievalChannel(channel): score
            for channel, score in score_json["channel_raw_scores"].items()
        }
        return RetrievalCitationV1(
            asset_id=result.asset_id,
            asset_version_id=result.asset_version_id,
            rights_record_id=result.rights_record_id,
            rights_record_version=result.rights_record_version,
            retrieval_policy_version=retrieval_policy_version,
            brand_profile_version=result.brand_profile_version,
            channels=[RetrievalChannel(channel) for channel in result.channels_json],
            score=RetrievalScoreBreakdownV1.model_validate(score_json),
            rank=result.rank,
            reason=result.reason,
            decided_at=result.decided_at,
            preview_reference_token=None,
        )

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("ascii")).hexdigest()


class MySqlRetrievalPreviewService:
    """Exchange one opaque grant only after another current-rights decision."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        storage: ObjectStorage,
        reference_lifetime: timedelta,
    ) -> None:
        if not timedelta(seconds=30) <= reference_lifetime <= timedelta(seconds=60):
            raise ValueError(
                "retrieval preview reference lifetime must be between 30 and 60 seconds"
            )
        self._session_factory = session_factory
        self._authority = MySqlRetrievalAuthority(session_factory)
        self._storage = storage
        self._reference_lifetime = reference_lifetime

    def exchange(
        self,
        *,
        workspace_id: str,
        requester_id: str,
        run_id: str,
        rank: int,
        token: str,
    ) -> RetrievalTemporaryReferenceV1 | None:
        if type(rank) is not int or rank < 1 or _OPAQUE_TOKEN.fullmatch(token) is None:
            return None
        with self._session_factory() as session:
            database_now = literal_column("UTC_TIMESTAMP(6)")
            row = session.execute(
                select(RetrievalRunModel, RetrievalResultModel)
                .join(
                    RetrievalResultModel,
                    and_(
                        RetrievalResultModel.workspace_id == RetrievalRunModel.workspace_id,
                        RetrievalResultModel.retrieval_run_id == RetrievalRunModel.id,
                    ),
                )
                .where(
                    RetrievalRunModel.workspace_id == workspace_id,
                    RetrievalRunModel.id == run_id,
                    RetrievalRunModel.requester_id == requester_id,
                    RetrievalRunModel.expires_at > database_now,
                    RetrievalResultModel.rank == rank,
                    RetrievalResultModel.preview_token_sha256 == self._token_hash(token),
                    RetrievalResultModel.preview_expires_at > database_now,
                )
            ).one_or_none()
            if row is None:
                return None
            run, result = row
        query = RetrievalQueryV1.model_validate_json(
            json.dumps(run.query_json, ensure_ascii=False, separators=(",", ":"))
        )
        current = self._authority.revalidate_asset_versions(
            query,
            asset_version_ids=(result.asset_version_id,),
        )
        if len(current.items) != 1:
            return None
        item = current.items[0]
        with self._session_factory() as session:
            object_fact = session.scalar(
                select(AssetObjectModel).where(
                    AssetObjectModel.workspace_id == workspace_id,
                    AssetObjectModel.asset_version_id == result.asset_version_id,
                    AssetObjectModel.role == "CONTROLLED_ORIGINAL",
                    AssetObjectModel.state == "CONTROLLED",
                    AssetObjectModel.sha256 == item.content_sha256,
                )
            )
            if object_fact is None or object_fact.provider_version_id.strip().casefold() == "null":
                return None
            reference = ObjectReference(
                location=StorageLocationClass(object_fact.location),
                key=object_fact.key,
                version_id=object_fact.provider_version_id,
            )
            etag = object_fact.etag
        temporary = self._storage.temporary_read(
            TemporaryReadRequest(
                reference=reference,
                expected_etag=etag,
                expected_sha256=item.content_sha256,
                expires_at=current.decided_at + self._reference_lifetime,
            )
        )
        if temporary.method != "GET":
            raise ValueError("retrieval preview temporary read must use GET")
        return RetrievalTemporaryReferenceV1(
            method="GET",
            url=temporary.url,
            required_headers=temporary.required_headers,
            expires_at=temporary.expires_at,
        )

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("ascii")).hexdigest()
