"""MySQL-backed controlled candidate channels for rights-first retrieval."""

from __future__ import annotations

from datetime import timedelta

from commercevision_application import (
    DenseEmbeddingCandidate,
    DenseRetrievalIndexUnavailable,
    DenseRetrievalTarget,
    ImageIndexDataTransferPolicy,
    RetrievalQueryImageUnavailable,
    RetrievalRecallBatch,
    RetrievalRecallHit,
)
from commercevision_contracts import EmbeddingImageInputV1, RetrievalQueryV1
from commercevision_contracts.object_storage import (
    ObjectReference,
    ObjectStorage,
    TemporaryReadRequest,
)
from commercevision_domain import RetrievalChannel, StorageLocationClass
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .brand_profile_models import BrandProfileMemberModel, BrandProfileVersionModel
from .indexing_models import CollectionRegistryModel, EmbeddingRecordModel
from .models import AssetObjectModel
from .product_search import MySqlProductLexicalSearch
from .retrieval import MySqlRetrievalAuthority

_MYSQL_IN_CHUNK_SIZE = 1_000


class MySqlLexicalRetrievalSource:
    """Push the full eligible-set intersection into the FULLTEXT SQL statement."""

    channel = RetrievalChannel.LEXICAL

    def __init__(self, search: MySqlProductLexicalSearch) -> None:
        self._search = search

    def recall(
        self,
        query: RetrievalQueryV1,
        *,
        eligible_asset_version_ids: tuple[str, ...],
        limit: int,
    ) -> RetrievalRecallBatch:
        if query.query_text is None:
            return RetrievalRecallBatch(channel=self.channel, hits=())
        hits = self._search.search_eligible(
            workspace_id=query.workspace_id,
            query=query.query_text,
            eligible_asset_version_ids=eligible_asset_version_ids,
            limit=limit,
        )
        return RetrievalRecallBatch(
            channel=self.channel,
            hits=tuple(
                RetrievalRecallHit(
                    asset_version_id=hit.asset_version_id,
                    raw_score=hit.score,
                )
                for hit in hits
            ),
        )


class MySqlBrandProfileRetrievalSource:
    """Recall members from one immutable publication, intersected in MySQL."""

    channel = RetrievalChannel.BRAND_PROFILE

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def recall(
        self,
        query: RetrievalQueryV1,
        *,
        eligible_asset_version_ids: tuple[str, ...],
        limit: int,
    ) -> RetrievalRecallBatch:
        if query.brand_profile_id is None or not eligible_asset_version_ids:
            return RetrievalRecallBatch(channel=self.channel, hits=())
        members: list[tuple[str, int]] = []
        with self._session_factory() as session:
            for offset in range(0, len(eligible_asset_version_ids), _MYSQL_IN_CHUNK_SIZE):
                eligible_chunk = eligible_asset_version_ids[offset : offset + _MYSQL_IN_CHUNK_SIZE]
                members.extend(
                    session.execute(
                        select(
                            BrandProfileMemberModel.asset_version_id,
                            BrandProfileMemberModel.ordinal,
                        )
                        .join(
                            BrandProfileVersionModel,
                            BrandProfileVersionModel.id
                            == BrandProfileMemberModel.profile_version_id,
                        )
                        .where(
                            BrandProfileVersionModel.workspace_id == query.workspace_id,
                            BrandProfileVersionModel.profile_id == query.brand_profile_id,
                            BrandProfileVersionModel.version_number == query.brand_profile_version,
                            BrandProfileMemberModel.workspace_id == query.workspace_id,
                            BrandProfileMemberModel.profile_id == query.brand_profile_id,
                            BrandProfileMemberModel.asset_version_id.in_(eligible_chunk),
                        )
                        .order_by(BrandProfileMemberModel.ordinal)
                        .limit(min(limit, len(eligible_chunk)))
                    ).tuples()
                )
        members.sort(key=lambda member: (member[1], member[0]))
        asset_version_ids = tuple(member[0] for member in members[:limit])
        return RetrievalRecallBatch(
            channel=self.channel,
            hits=tuple(
                RetrievalRecallHit(asset_version_id=asset_version_id)
                for asset_version_id in asset_version_ids
            ),
        )


class MySqlDenseRetrievalCatalog:
    """Resolve the sole active read collection and its eligible indexed records."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def load_target(
        self,
        query: RetrievalQueryV1,
        *,
        vector_kind,
        eligible_asset_version_ids: tuple[str, ...],
    ) -> DenseRetrievalTarget | None:
        if not eligible_asset_version_ids:
            return None
        if len(set(eligible_asset_version_ids)) != len(eligible_asset_version_ids):
            raise ValueError("dense eligible Asset Versions must be unique")
        with self._session_factory() as session:
            collections = tuple(
                session.scalars(
                    select(CollectionRegistryModel)
                    .where(
                        CollectionRegistryModel.vector_kind == vector_kind.value,
                        CollectionRegistryModel.state == "ACTIVE",
                        CollectionRegistryModel.is_read_enabled.is_(True),
                    )
                    .order_by(CollectionRegistryModel.id)
                    .limit(2)
                )
            )
            if not collections:
                raise DenseRetrievalIndexUnavailable(
                    code="DENSE_COLLECTION_UNAVAILABLE",
                    message="dense retrieval collection is unavailable",
                )
            if len(collections) != 1:
                raise RuntimeError("dense retrieval routing has multiple active collections")
            collection = collections[0]
            rows = []
            for offset in range(0, len(eligible_asset_version_ids), _MYSQL_IN_CHUNK_SIZE):
                eligible_chunk = eligible_asset_version_ids[offset : offset + _MYSQL_IN_CHUNK_SIZE]
                rows.extend(
                    session.execute(
                        select(
                            EmbeddingRecordModel.id,
                            EmbeddingRecordModel.asset_version_id,
                            EmbeddingRecordModel.provider,
                            EmbeddingRecordModel.model_id,
                            EmbeddingRecordModel.pinned_revision,
                            EmbeddingRecordModel.model_configuration_version,
                            EmbeddingRecordModel.preprocessing_version,
                        )
                        .where(
                            EmbeddingRecordModel.workspace_id == query.workspace_id,
                            EmbeddingRecordModel.collection_id == collection.id,
                            EmbeddingRecordModel.vector_kind == vector_kind.value,
                            EmbeddingRecordModel.state == "INDEXED",
                            EmbeddingRecordModel.asset_version_id.in_(eligible_chunk),
                        )
                        .order_by(EmbeddingRecordModel.id)
                    ).tuples()
                )
        rows.sort(key=lambda row: row.id)
        if not rows:
            raise DenseRetrievalIndexUnavailable(
                code="DENSE_INDEX_UNAVAILABLE",
                message="dense retrieval has no current indexed candidates",
            )
        model_identities = {
            (
                row.provider,
                row.model_id,
                row.pinned_revision,
                row.model_configuration_version,
                row.preprocessing_version,
            )
            for row in rows
        }
        if len(model_identities) != 1:
            raise RuntimeError("dense eligible records have inconsistent model identities")
        if (
            rows[0].model_id != collection.model_id
            or rows[0].pinned_revision != collection.pinned_revision
        ):
            raise RuntimeError("dense record identity does not match the active collection")
        return DenseRetrievalTarget(
            collection_name=collection.physical_name,
            vector_kind=vector_kind,
            dimension=collection.dimension,
            provider=rows[0].provider,
            model_id=rows[0].model_id,
            pinned_revision=rows[0].pinned_revision,
            model_configuration_version=rows[0].model_configuration_version,
            preprocessing_version=rows[0].preprocessing_version,
            candidates=tuple(
                DenseEmbeddingCandidate(
                    embedding_record_id=record_id,
                    asset_version_id=asset_version_id,
                )
                for record_id, asset_version_id, *_ in rows
            ),
        )


class MySqlRetrievalQueryImageReference:
    """Reauthorize an exact query image immediately before provider access."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        storage: ObjectStorage,
        lifetime: timedelta,
        transfer_policy: ImageIndexDataTransferPolicy | None = None,
        external_endpoint_region: str | None = None,
        external_endpoint_host: str | None = None,
    ) -> None:
        if lifetime <= timedelta(0):
            raise ValueError("retrieval query image lifetime must be positive")
        self._session_factory = session_factory
        self._authority = MySqlRetrievalAuthority(session_factory)
        self._storage = storage
        self._lifetime = lifetime
        self._transfer_policy = transfer_policy
        self._external_endpoint_region = external_endpoint_region
        self._external_endpoint_host = external_endpoint_host

    def temporary_input(
        self,
        query: RetrievalQueryV1,
        *,
        provider: str,
    ) -> EmbeddingImageInputV1:
        asset_version_id = query.query_image_asset_version_id
        if asset_version_id is None:
            raise ValueError("retrieval query has no image Asset Version")
        authorized = self._authority.revalidate_asset_versions(
            query,
            asset_version_ids=(asset_version_id,),
        )
        if len(authorized.items) != 1:
            raise RetrievalQueryImageUnavailable(
                code="DENSE_QUERY_IMAGE_UNAUTHORIZED",
                message="retrieval query image is not currently authorized",
            )
        item = authorized.items[0]
        if self._transfer_policy is not None:
            if self._external_endpoint_region is None or self._external_endpoint_host is None:
                raise ValueError("retrieval embedding transfer endpoint is not configured")
            self._transfer_policy.authorize(
                workspace_id=query.workspace_id,
                retention_class=item.retention_class,
                provider=provider,
                endpoint_region=self._external_endpoint_region,
                endpoint_host=self._external_endpoint_host,
            )
        with self._session_factory() as session:
            object_fact = session.scalar(
                select(AssetObjectModel).where(
                    AssetObjectModel.workspace_id == query.workspace_id,
                    AssetObjectModel.asset_version_id == asset_version_id,
                    AssetObjectModel.role == "CONTROLLED_ORIGINAL",
                    AssetObjectModel.state == "CONTROLLED",
                )
            )
            if (
                object_fact is None
                or object_fact.sha256 != item.content_sha256
                or object_fact.provider_version_id.strip().casefold() == "null"
            ):
                raise RetrievalQueryImageUnavailable(
                    code="DENSE_QUERY_IMAGE_UNAVAILABLE",
                    message="controlled retrieval query image is unavailable",
                )
            reference = ObjectReference(
                location=StorageLocationClass(object_fact.location),
                key=object_fact.key,
                version_id=object_fact.provider_version_id,
            )
            etag = object_fact.etag
            byte_size = object_fact.byte_size
        temporary = self._storage.temporary_read(
            TemporaryReadRequest(
                reference=reference,
                expected_etag=etag,
                expected_sha256=item.content_sha256,
                expires_at=authorized.decided_at + self._lifetime,
            )
        )
        if temporary.method != "GET":
            raise ValueError("retrieval query image temporary read must use GET")
        return EmbeddingImageInputV1(
            asset_version_id=asset_version_id,
            content_sha256=item.content_sha256,
            byte_size=byte_size,
            url=SecretStr(temporary.url),
            required_headers={
                name: SecretStr(value) for name, value in temporary.required_headers.items()
            },
            expires_at=temporary.expires_at,
        )
