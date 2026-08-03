"""IMAGE indexing dependency composition owned by the index Worker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from commercevision_application import ImageIndexDataTransferPolicy, ImageIndexingExecutor
from commercevision_contracts import Settings
from commercevision_contracts.object_storage import ObjectStorage
from commercevision_domain import CollectionSpec, VectorKind
from commercevision_persistence import (
    Database,
    MySqlExactImageReference,
    MySqlImageIndexRequestService,
    MySqlIndexingAuthority,
)
from commercevision_providers import (
    AlibabaEmbeddingProvider,
    DeterministicEmbeddingProvider,
    DeterministicEmbeddingScenario,
    MountedFileVisionApiKeyProvider,
    StaticVisionApiKeyProvider,
    VisionApiKeyUnavailableError,
)
from commercevision_retrieval import MilvusVectorIndexAdapter


@dataclass(frozen=True, slots=True)
class BuiltImageIndexing:
    executor: ImageIndexingExecutor
    request_service: MySqlImageIndexRequestService
    authority: MySqlIndexingAuthority
    embedding_provider: object
    vector_index: MilvusVectorIndexAdapter
    closeables: tuple[object, ...]


def collection_spec_from_settings(settings: Settings) -> CollectionSpec:
    return CollectionSpec.create(
        model_family=settings.embedding_model_family,
        pinned_revision=settings.embedding_pinned_revision,
        dimension=settings.embedding_dimension,
        vector_kind=VectorKind.IMAGE,
        schema_version=settings.embedding_collection_schema_version,
        index_spec_version=settings.embedding_collection_index_spec_version,
    )


def _build_embedding_provider(settings: Settings) -> object:
    if settings.embedding_adapter == "deterministic":
        return DeterministicEmbeddingProvider(
            provider=settings.embedding_provider,
            model_id=settings.embedding_model_id,
            pinned_revision=settings.embedding_pinned_revision,
            model_configuration_version=settings.embedding_model_configuration_version,
            scenario=DeterministicEmbeddingScenario(settings.deterministic_embedding_scenario),
        )
    api_key_file = settings.alibaba_embedding_api_key_file
    if api_key_file is not None:
        credential_provider = MountedFileVisionApiKeyProvider(
            path=api_key_file,
            maximum_bytes=settings.alibaba_embedding_api_key_file_max_bytes,
        )
    else:
        secret = settings.alibaba_embedding_api_key
        if secret is None:
            raise RuntimeError("Alibaba embedding API key is not configured")
        credential_provider = StaticVisionApiKeyProvider(secret.get_secret_value())
    return AlibabaEmbeddingProvider(
        credential_provider=credential_provider,
        endpoint=settings.alibaba_embedding_endpoint,
        endpoint_region=settings.alibaba_embedding_endpoint_region,
        model_id=settings.embedding_model_id,
        pinned_revision=settings.embedding_pinned_revision,
        model_configuration_version=settings.embedding_model_configuration_version,
        preprocessing_version=settings.embedding_preprocessing_version,
        connect_timeout_seconds=settings.alibaba_embedding_connect_timeout_seconds,
        read_timeout_seconds=settings.alibaba_embedding_read_timeout_seconds,
        end_to_end_timeout_seconds=settings.alibaba_embedding_end_to_end_timeout_seconds,
        maximum_concurrency=settings.alibaba_embedding_maximum_concurrency,
        maximum_response_bytes=settings.alibaba_embedding_maximum_response_bytes,
        allowed_image_origins=frozenset(settings.alibaba_embedding_allowed_image_origins),
    )


def _build_vector_index(settings: Settings) -> MilvusVectorIndexAdapter:
    return MilvusVectorIndexAdapter(
        uri=settings.milvus_uri,
        token=settings.milvus_token,
        db_name=settings.milvus_database,
        timeout_seconds=settings.milvus_timeout_seconds,
        readiness_timeout_seconds=settings.milvus_readiness_timeout_seconds,
    )


def _assert_embedding_credential_ready(settings: Settings) -> None:
    if settings.embedding_adapter != "alibaba":
        return
    try:
        api_key_file = settings.alibaba_embedding_api_key_file
        if api_key_file is not None:
            credential = MountedFileVisionApiKeyProvider(
                path=api_key_file,
                maximum_bytes=settings.alibaba_embedding_api_key_file_max_bytes,
            ).resolve()
        else:
            secret = settings.alibaba_embedding_api_key
            credential = None if secret is None else secret.get_secret_value()
    except (OSError, UnicodeError, ValueError, VisionApiKeyUnavailableError):
        credential = None
    if not credential:
        raise RuntimeError("Alibaba embedding API key is unavailable") from None
    del credential


def build_image_index_request_service(
    *,
    settings: Settings,
    database: Database,
) -> MySqlImageIndexRequestService:
    return MySqlImageIndexRequestService(
        session_factory=database.session_factory,
        collection_spec=collection_spec_from_settings(settings),
        provider=settings.embedding_provider,
        model_id=settings.embedding_model_id,
        model_configuration_version=settings.embedding_model_configuration_version,
        preprocessing_version=settings.embedding_preprocessing_version,
        max_attempts=settings.image_index_max_attempts,
        max_reconciliation_attempts=settings.image_index_max_reconciliation_attempts,
    )


def build_image_indexing(
    *,
    settings: Settings,
    database: Database,
    storage: ObjectStorage,
) -> BuiltImageIndexing:
    embedding = _build_embedding_provider(settings)
    vectors = _build_vector_index(settings)
    authority = MySqlIndexingAuthority(database.session_factory)
    requests = build_image_index_request_service(
        settings=settings,
        database=database,
    )
    executor = ImageIndexingExecutor(
        authority=authority,
        references=MySqlExactImageReference(
            session_factory=database.session_factory,
            storage=storage,
            lifetime=timedelta(seconds=settings.image_index_temporary_reference_lifetime_seconds),
        ),
        embedding=embedding,
        vectors=vectors,
        transfer_policy=(
            ImageIndexDataTransferPolicy.from_settings(settings)
            if settings.embedding_adapter == "alibaba"
            else None
        ),
        external_endpoint_region=(
            settings.alibaba_embedding_endpoint_region
            if settings.embedding_adapter == "alibaba"
            else None
        ),
        external_endpoint_host=(
            settings.alibaba_embedding_endpoint_host
            if settings.embedding_adapter == "alibaba"
            else None
        ),
    )
    closeables = (embedding, vectors) if callable(getattr(embedding, "close", None)) else (vectors,)
    return BuiltImageIndexing(
        executor=executor,
        request_service=requests,
        authority=authority,
        embedding_provider=embedding,
        vector_index=vectors,
        closeables=closeables,
    )


def probe_image_indexing_dependencies(settings: Settings) -> dict[str, str]:
    """Construct, probe, and close the exact index-only external adapters."""

    embedding = _build_embedding_provider(settings)
    vectors = _build_vector_index(settings)
    failures: list[Exception] = []
    try:
        vectors.assert_ready()
        _assert_embedding_credential_ready(settings)
        assert_embedding_ready = getattr(embedding, "assert_ready", None)
        if callable(assert_embedding_ready):
            assert_embedding_ready()
        return {
            "milvus": "ok",
            "embedding_provider": (
                "ok" if settings.embedding_adapter == "alibaba" else "not_required"
            ),
        }
    finally:
        for resource in (embedding, vectors):
            close = getattr(resource, "close", None)
            if not callable(close):
                continue
            try:
                close()
            except Exception:
                failures.append(ConnectionError("IMAGE indexing adapter cleanup failed"))
        if failures:
            raise ExceptionGroup("IMAGE indexing readiness cleanup failed", failures)
