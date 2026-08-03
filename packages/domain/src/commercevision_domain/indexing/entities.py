"""Pure collection-registry and embedding identities."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid5

from commercevision_domain.ids import canonicalize_uuid
from commercevision_domain.workspace_identity import validate_workspace_id

from .enums import EmbeddingState, VectorKind

_IDENTITY_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", re.ASCII)
_PINNED_REVISION_ALIASES = frozenset({"latest", "main", "stable"})
_EMBEDDING_RECORD_NAMESPACE = UUID("72d9008e-6a96-5cab-b1f8-a68054a12f4f")


def _require_identity_token(value: str, field: str) -> None:
    if (
        not isinstance(value, str)
        or _IDENTITY_TOKEN.fullmatch(value) is None
        or value.lower() in _PINNED_REVISION_ALIASES
    ):
        raise ValueError(f"{field} must be a pinned ASCII identity token")


@dataclass(frozen=True, slots=True)
class CollectionSpec:
    """Compatibility boundary for one physical Milvus collection."""

    model_family: str
    pinned_revision: str
    dimension: int
    vector_kind: VectorKind
    schema_version: int
    index_spec_version: str

    def __post_init__(self) -> None:
        _require_identity_token(self.model_family, "model_family")
        _require_identity_token(self.pinned_revision, "pinned_revision")
        _require_identity_token(self.index_spec_version, "index_spec_version")
        if (
            isinstance(self.dimension, bool)
            or not isinstance(self.dimension, int)
            or not 1 <= self.dimension <= 32_768
        ):
            raise ValueError("dimension must be an integer between 1 and 32768")
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version < 1
        ):
            raise ValueError("schema_version must be a positive integer")
        if not isinstance(self.vector_kind, VectorKind):
            raise ValueError("vector_kind must be a VectorKind")

    @classmethod
    def create(
        cls,
        *,
        model_family: str,
        pinned_revision: str,
        dimension: int,
        vector_kind: VectorKind,
        schema_version: int,
        index_spec_version: str,
    ) -> CollectionSpec:
        return cls(
            model_family=model_family,
            pinned_revision=pinned_revision,
            dimension=dimension,
            vector_kind=vector_kind,
            schema_version=schema_version,
            index_spec_version=index_spec_version,
        )

    @property
    def logical_key(self) -> str:
        return ":".join(
            (
                self.model_family,
                self.pinned_revision,
                str(self.dimension),
                self.vector_kind.value,
                str(self.schema_version),
                self.index_spec_version,
            )
        )

    @property
    def physical_name(self) -> str:
        return f"cv_{self.vector_kind.value.lower()}_{self.spec_hash[:16]}"

    @property
    def spec_hash(self) -> str:
        return hashlib.sha256(self.logical_key.encode("utf-8")).hexdigest()


def _require_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lowercase SHA-256")


def compute_embedding_input_hash(
    *,
    content_sha256: str,
    provider: str,
    preprocessing_version: str,
    model_configuration_version: str,
    vector_kind: VectorKind,
) -> str:
    """Bind IMAGE bytes and every transformation/configuration input."""

    _require_sha256(content_sha256, "content_sha256")
    _require_identity_token(provider, "provider")
    _require_identity_token(preprocessing_version, "preprocessing_version")
    _require_identity_token(model_configuration_version, "model_configuration_version")
    if not isinstance(vector_kind, VectorKind):
        raise ValueError("vector_kind must be a VectorKind")
    canonical = "\0".join(
        (
            vector_kind.value,
            content_sha256,
            provider,
            preprocessing_version,
            model_configuration_version,
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def generation_milvus_primary_key(
    *,
    embedding_record_id: str,
    write_generation: int,
) -> str:
    canonicalize_uuid(embedding_record_id)
    if write_generation < 1:
        raise ValueError("write_generation must be positive")
    key = f"{embedding_record_id}:g{write_generation}"
    if len(key) > 64:
        raise ValueError("generation-specific Milvus primary key exceeds 64 characters")
    return key


@dataclass(slots=True)
class EmbeddingRecord:
    id: str
    workspace_id: str
    asset_id: str
    asset_version_id: str
    asset_version_number: int
    rights_record_id: str
    rights_record_version: int
    collection_id: str
    embedding_spec_hash: str
    input_hash: str
    vector_kind: VectorKind
    product_brief_version_id: str | None
    controlled_text_sha256: str | None
    milvus_primary_key: str
    state: EmbeddingState
    write_generation: int
    provider_request_id: str | None
    actual_model: str | None
    indexed_at: datetime | None
    stale_at: datetime | None
    stale_reason: str | None
    version: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)
        for value in (
            self.id,
            self.asset_id,
            self.asset_version_id,
            self.rights_record_id,
            self.collection_id,
        ):
            canonicalize_uuid(value)
        _require_sha256(self.embedding_spec_hash, "embedding_spec_hash")
        _require_sha256(self.input_hash, "input_hash")
        if self.vector_kind is VectorKind.PRODUCT_FUSED:
            if self.product_brief_version_id is None or self.controlled_text_sha256 is None:
                raise ValueError("PRODUCT_FUSED records require controlled ProductBrief identity")
            canonicalize_uuid(self.product_brief_version_id)
            _require_sha256(self.controlled_text_sha256, "controlled_text_sha256")
        elif self.product_brief_version_id is not None or self.controlled_text_sha256 is not None:
            raise ValueError("IMAGE records cannot carry controlled ProductBrief identity")
        if self.asset_version_number < 1 or self.rights_record_version < 1:
            raise ValueError("record versions must be positive")
        if self.milvus_primary_key != self.id:
            raise ValueError("Milvus primary key must equal the deterministic record id")
        if self.write_generation < 0 or self.version < 1:
            raise ValueError("embedding counters must not be negative")

    @classmethod
    def create(
        cls,
        *,
        workspace_id: str,
        asset_id: str,
        asset_version_id: str,
        asset_version_number: int,
        rights_record_id: str,
        rights_record_version: int,
        collection_id: str,
        embedding_spec_hash: str,
        input_hash: str,
        vector_kind: VectorKind,
        product_brief_version_id: str | None = None,
        controlled_text_sha256: str | None = None,
        now: datetime | None = None,
    ) -> EmbeddingRecord:
        created_at = now or datetime.now(UTC)
        deterministic_id = str(
            uuid5(
                _EMBEDDING_RECORD_NAMESPACE,
                (
                    f"{asset_version_id}:{embedding_spec_hash}:{input_hash}"
                    if vector_kind is VectorKind.PRODUCT_FUSED
                    else f"{asset_version_id}:{embedding_spec_hash}"
                ),
            )
        )
        return cls(
            id=deterministic_id,
            workspace_id=workspace_id,
            asset_id=asset_id,
            asset_version_id=asset_version_id,
            asset_version_number=asset_version_number,
            rights_record_id=rights_record_id,
            rights_record_version=rights_record_version,
            collection_id=collection_id,
            embedding_spec_hash=embedding_spec_hash,
            input_hash=input_hash,
            vector_kind=vector_kind,
            product_brief_version_id=product_brief_version_id,
            controlled_text_sha256=controlled_text_sha256,
            milvus_primary_key=deterministic_id,
            state=EmbeddingState.PENDING,
            write_generation=0,
            provider_request_id=None,
            actual_model=None,
            indexed_at=None,
            stale_at=None,
            stale_reason=None,
            version=1,
            created_at=created_at,
            updated_at=created_at,
        )

    def begin_processing(self, *, now: datetime | None = None) -> int:
        if self.state not in {
            EmbeddingState.PENDING,
            EmbeddingState.RETRYABLE_FAILED,
            EmbeddingState.STALE,
            EmbeddingState.DELETE_PENDING,
        }:
            raise ValueError(f"cannot begin processing from {self.state.value}")
        self.write_generation += 1
        self.state = EmbeddingState.PROCESSING
        self.stale_at = None
        self.stale_reason = None
        self._touch(now)
        return self.write_generation

    def mark_failed(
        self,
        *,
        write_generation: int,
        retryable: bool,
        now: datetime | None = None,
    ) -> None:
        if self.state is not EmbeddingState.PROCESSING:
            raise ValueError("only a processing embedding can fail")
        self._assert_generation(write_generation)
        self.state = (
            EmbeddingState.RETRYABLE_FAILED if retryable else EmbeddingState.PERMANENT_FAILED
        )
        self._touch(now)

    def milvus_key_for(self, write_generation: int) -> str:
        return generation_milvus_primary_key(
            embedding_record_id=self.id,
            write_generation=write_generation,
        )

    def mark_indexed(
        self,
        *,
        write_generation: int,
        provider_request_id: str,
        actual_model: str,
        now: datetime | None = None,
    ) -> None:
        if self.state is not EmbeddingState.PROCESSING:
            raise ValueError("only a processing embedding can become indexed")
        self._assert_generation(write_generation)
        if not provider_request_id or not actual_model:
            raise ValueError("indexed embeddings require provider provenance")
        changed_at = now or datetime.now(UTC)
        self.state = EmbeddingState.INDEXED
        self.provider_request_id = provider_request_id
        self.actual_model = actual_model
        self.indexed_at = changed_at
        self.stale_at = None
        self.stale_reason = None
        self._touch(changed_at)

    def mark_stale(
        self,
        *,
        write_generation: int,
        reason: str,
        now: datetime | None = None,
    ) -> None:
        self._assert_generation(write_generation)
        if not reason:
            raise ValueError("stale embeddings require a reason")
        changed_at = now or datetime.now(UTC)
        self.state = EmbeddingState.STALE
        self.stale_at = changed_at
        self.stale_reason = reason
        self._touch(changed_at)

    def begin_delete(
        self,
        *,
        write_generation: int,
        now: datetime | None = None,
    ) -> None:
        self._assert_generation(write_generation)
        if self.state is not EmbeddingState.STALE:
            raise ValueError("only stale embeddings can begin deletion")
        self.state = EmbeddingState.DELETE_PENDING
        self._touch(now)

    def complete_delete(
        self,
        *,
        write_generation: int,
        now: datetime | None = None,
    ) -> bool:
        if write_generation != self.write_generation:
            return False
        if self.state is not EmbeddingState.DELETE_PENDING:
            return False
        self.state = EmbeddingState.DELETED
        self._touch(now)
        return True

    def _assert_generation(self, write_generation: int) -> None:
        if write_generation != self.write_generation:
            raise ValueError("embedding write generation is stale")

    def _touch(self, now: datetime | None) -> None:
        changed_at = now or datetime.now(UTC)
        self.updated_at = changed_at
        self.version += 1
