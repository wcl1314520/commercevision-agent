"""Shared composition for rights-first hybrid retrieval."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from datetime import timedelta

from commercevision_application import (
    DenseRetrievalSource,
    ExplicitReferenceRetrievalSource,
    ImageIndexDataTransferPolicy,
    ProviderDenseQueryVectorService,
    RetrievalApplicationService,
)
from commercevision_contracts import Settings
from commercevision_contracts.object_storage import ObjectStorage
from commercevision_domain import RetrievalChannel, RetrievalPolicy, VectorKind
from commercevision_persistence import (
    Database,
    MySqlBrandProfileRetrievalSource,
    MySqlDenseRetrievalCatalog,
    MySqlLexicalRetrievalSource,
    MySqlProductLexicalSearch,
    MySqlRetrievalAuthority,
    MySqlRetrievalPreviewService,
    MySqlRetrievalQueryImageReference,
    MySqlRetrievalRunStore,
)
from commercevision_providers import (
    AlibabaEmbeddingProvider,
    DeterministicEmbeddingProvider,
    DeterministicEmbeddingScenario,
    MountedFileVisionApiKeyProvider,
    StaticVisionApiKeyProvider,
)
from commercevision_retrieval import ChunkedMilvusAnnSearch, MilvusVectorIndexAdapter


@dataclass(frozen=True, slots=True)
class BuiltRetrieval:
    service: RetrievalApplicationService
    runs: MySqlRetrievalRunStore
    previews: MySqlRetrievalPreviewService
    closeables: tuple[object, ...]


def _close_resource(resource: object) -> None:
    close = getattr(resource, "close", None)
    if callable(close):
        close()


def _embedding_provider(settings: Settings) -> object:
    if settings.embedding_adapter == "deterministic":
        return DeterministicEmbeddingProvider(
            provider=settings.embedding_provider,
            model_id=settings.embedding_model_id,
            pinned_revision=settings.embedding_pinned_revision,
            model_configuration_version=settings.embedding_model_configuration_version,
            preprocessing_version=settings.embedding_preprocessing_version,
            additional_preprocessing_versions=frozenset(
                {settings.product_fused_preprocessing_version}
            ),
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
        additional_preprocessing_versions=frozenset({settings.product_fused_preprocessing_version}),
        connect_timeout_seconds=settings.alibaba_embedding_connect_timeout_seconds,
        read_timeout_seconds=settings.alibaba_embedding_read_timeout_seconds,
        end_to_end_timeout_seconds=settings.alibaba_embedding_end_to_end_timeout_seconds,
        maximum_concurrency=settings.alibaba_embedding_maximum_concurrency,
        maximum_response_bytes=settings.alibaba_embedding_maximum_response_bytes,
        allowed_image_origins=frozenset(settings.alibaba_embedding_allowed_image_origins),
    )


def build_retrieval(
    *,
    settings: Settings,
    database: Database,
    storage: ObjectStorage,
) -> BuiltRetrieval:
    with ExitStack() as startup_cleanup:
        embedding = _embedding_provider(settings)
        startup_cleanup.callback(_close_resource, embedding)
        vector_index = MilvusVectorIndexAdapter(
            uri=settings.milvus_uri,
            token=settings.milvus_token,
            db_name=settings.milvus_database,
            timeout_seconds=settings.milvus_timeout_seconds,
            readiness_timeout_seconds=settings.milvus_readiness_timeout_seconds,
        )
        startup_cleanup.callback(_close_resource, vector_index)
        search = ChunkedMilvusAnnSearch(
            index=vector_index,
            maximum_filter_ids=settings.retrieval_milvus_maximum_filter_ids,
        )
        catalog = MySqlDenseRetrievalCatalog(database.session_factory)
        image_references = MySqlRetrievalQueryImageReference(
            session_factory=database.session_factory,
            storage=storage,
            lifetime=timedelta(seconds=settings.image_index_temporary_reference_lifetime_seconds),
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
        query_vectors = ProviderDenseQueryVectorService(
            embedding=embedding,
            image_references=image_references,
        )
        sources = (
            DenseRetrievalSource(
                vector_kind=VectorKind.IMAGE,
                catalog=catalog,
                query_vectors=query_vectors,
                search=search,
            ),
            DenseRetrievalSource(
                vector_kind=VectorKind.PRODUCT_FUSED,
                catalog=catalog,
                query_vectors=query_vectors,
                search=search,
            ),
            MySqlLexicalRetrievalSource(MySqlProductLexicalSearch(database.session_factory)),
            MySqlBrandProfileRetrievalSource(database.session_factory),
            ExplicitReferenceRetrievalSource(),
        )
        policy = RetrievalPolicy(
            version=settings.retrieval_policy_version,
            rrf_k=settings.retrieval_rrf_k,
            channel_weights={channel: 1.0 for channel in RetrievalChannel},
            maximum_business_adjustment=settings.retrieval_maximum_business_adjustment,
        )
        built = BuiltRetrieval(
            service=RetrievalApplicationService(
                authority=MySqlRetrievalAuthority(database.session_factory),
                sources=sources,
                policy=policy,
            ),
            runs=MySqlRetrievalRunStore(
                database.session_factory,
                run_retention=timedelta(seconds=settings.retrieval_run_retention_seconds),
                preview_token_lifetime=timedelta(
                    seconds=settings.retrieval_preview_token_lifetime_seconds
                ),
            ),
            previews=MySqlRetrievalPreviewService(
                session_factory=database.session_factory,
                storage=storage,
                reference_lifetime=timedelta(
                    seconds=settings.retrieval_preview_reference_lifetime_seconds
                ),
            ),
            closeables=(embedding, vector_index),
        )
        startup_cleanup.pop_all()
        return built
