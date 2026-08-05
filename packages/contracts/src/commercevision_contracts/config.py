"""Validated runtime configuration shared by service entrypoints."""

from __future__ import annotations

import os
import re
from decimal import Decimal
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal
from urllib.parse import urlsplit

from commercevision_domain import (
    DEFAULT_PRODUCT_BRIEF_SENSITIVE_CLAIM_PATHS,
    AssetKind,
    OperationKind,
    ProductBriefCategory,
    RetentionClass,
    StorageLocationClass,
    product_brief_field_paths,
)
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SecretsSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from .endpoint_identity import validate_canonical_endpoint_host
from .workspace_identity import validate_workspace_id

_FINALIZE_STORAGE_REQUEST_BOUND = 3
_ALIBABA_IMMUTABLE_MODEL_SNAPSHOT = re.compile(r"^[-a-zA-Z0-9.]+-\d{4}-\d{2}-\d{2}$")
WORKER_READINESS_BROKER_CONNECT_TIMEOUT_SECONDS = 3.0
WORKER_READINESS_MYSQL_QUERY_TIMEOUT_SECONDS = 5.0
WORKER_READINESS_PROBE_INTERVAL_SECONDS = 5.0
WORKER_READINESS_PUBLICATION_MARGIN_SECONDS = 2.0
PROVIDER_ARTIFACT_RECONCILIATION_TARGET_LIMIT = 16
PROVIDER_ARTIFACT_READINESS_TARGET_LIMIT = PROVIDER_ARTIFACT_RECONCILIATION_TARGET_LIMIT + 1
_OBJECT_STORAGE_READINESS_REQUEST_BOUND = 3
_OBJECT_STORAGE_READINESS_TIMEOUT_PHASE_BOUND = 2


def _secret_directories() -> list[Path]:
    configured = os.getenv("CV_SECRETS_DIR")
    if configured:
        return [Path(value) for value in configured.split(os.pathsep) if value]
    return [path for path in (Path("/run/secrets"), Path("secrets")) if path.is_dir()]


def _origin_identity(value: str) -> tuple[str, str, int]:
    parsed = urlsplit(value)
    assert parsed.hostname is not None
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    return parsed.scheme.lower(), parsed.hostname.lower(), parsed.port or default_port


class ProviderArtifactReconciliationTargetSettings(BaseModel):
    """Explicit connection overrides for one historical provider-artifact target."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    object_store_backend: Literal["minio", "oss"]
    object_store_provider_result_bucket: str = Field(min_length=1, max_length=255)
    object_store_credential_mode: (
        Literal[
            "static",
            "ecs_ram_role",
            "oidc_role_arn",
        ]
        | None
    ) = None
    object_store_endpoint: str | None = None
    object_store_presign_endpoint: str | None = None
    object_store_region: str | None = None
    object_store_access_key: str | None = None
    object_store_secret_key: SecretStr | None = None
    object_store_session_token: SecretStr | None = None
    object_store_ram_role_name: str | None = None
    object_store_oidc_role_arn: str | None = None
    object_store_oidc_provider_arn: str | None = None
    object_store_oidc_token_file_path: str | None = None
    object_store_sts_endpoint: str | None = None
    object_store_role_session_name: str | None = None
    object_store_tls_verify: bool | None = None
    object_store_force_path_style: bool | None = None
    object_store_require_encryption: bool | None = None
    object_store_connect_timeout_seconds: float | None = Field(default=None, gt=0, le=60)
    object_store_read_timeout_seconds: float | None = Field(default=None, gt=0, le=300)
    object_store_readiness_timeout_seconds: float | None = Field(default=None, gt=0, le=10)
    object_store_credential_refresh_timeout_seconds: float | None = Field(
        default=None,
        gt=0,
        le=30,
    )

    @field_validator("object_store_provider_result_bucket")
    @classmethod
    def _trim_required_target_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("provider artifact reconciliation bucket must not be blank")
        return normalized


class Settings(BaseSettings):
    """Configuration loaded from environment variables with the CV_ prefix."""

    model_config = SettingsConfigDict(
        env_prefix="CV_",
        env_file=(".env", ".env.local"),
        extra="ignore",
        case_sensitive=False,
        yaml_file="config/base.yaml",
        yaml_file_encoding="utf-8",
    )

    service_name: str = "commercevision"
    version: str = "0.1.0"
    environment: Literal["local", "ci", "staging", "demo", "production"] = "local"
    log_level: str = "INFO"
    readiness_probe_external: bool = False

    mysql_dsn: str = "mysql+aiomysql://commercevision:commercevision@mysql:3306/commercevision"
    redis_url: str = "redis://redis:6379/0"
    rabbitmq_url: str = "amqp://commercevision:commercevision@rabbitmq:5672//"
    milvus_uri: str = "http://milvus:19530"
    milvus_health_uri: str = "http://milvus:9091/healthz"
    milvus_token: SecretStr | None = None
    milvus_database: str = "default"
    milvus_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    milvus_readiness_timeout_seconds: float = Field(default=1.0, gt=0, le=10)

    object_store_backend: Literal["minio", "oss"] = "minio"
    object_store_credential_mode: Literal[
        "static",
        "ecs_ram_role",
        "oidc_role_arn",
    ] = "static"
    object_store_endpoint: str = "http://minio:9000"
    object_store_presign_endpoint: str | None = None
    object_store_region: str = "us-east-1"
    object_store_access_key: str = "commercevision"
    object_store_secret_key: SecretStr = SecretStr("change-me")
    object_store_session_token: SecretStr | None = None
    object_store_ram_role_name: str | None = None
    object_store_oidc_role_arn: str | None = None
    object_store_oidc_provider_arn: str | None = None
    object_store_oidc_token_file_path: str | None = None
    object_store_sts_endpoint: str | None = None
    object_store_role_session_name: str = "commercevision-object-storage"
    object_store_bucket: str = "task-assets"
    object_store_quarantine_bucket: str = "quarantine-assets"
    object_store_task_bucket: str = "task-assets"
    object_store_foundation_bucket: str = "foundation-assets"
    object_store_provider_result_bucket: str = "provider-results"
    object_store_tls_verify: bool = True
    object_store_force_path_style: bool = True
    object_store_require_encryption: bool = False
    object_store_connect_timeout_seconds: float = Field(default=3.0, gt=0, le=60)
    object_store_read_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    object_store_readiness_timeout_seconds: float = Field(default=1.0, gt=0, le=10)
    object_store_credential_refresh_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        le=30,
    )
    provider_artifact_reconciliation_targets: list[ProviderArtifactReconciliationTargetSettings] = (
        Field(
            default_factory=list,
            max_length=PROVIDER_ARTIFACT_RECONCILIATION_TARGET_LIMIT,
        )
    )
    upload_session_expiry_seconds: int = Field(default=900, ge=60, le=3600)
    upload_cleanup_presign_grace_seconds: int = Field(default=30, ge=1, le=300)
    upload_cleanup_max_attempts: int = Field(default=600, ge=2, le=2000)
    upload_cleanup_reconcile_interval_seconds: int = Field(
        default=3600,
        ge=60,
        le=86400,
    )
    upload_cleanup_reconcile_horizon_seconds: int = Field(
        default=72 * 3600,
        ge=3600,
        le=7 * 24 * 3600,
    )
    upload_cleanup_reconcile_max_attempts: int = Field(default=80, ge=2, le=1000)
    upload_finalize_lease_seconds: int = Field(default=120, ge=15, le=900)
    upload_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1, le=10 * 1024 * 1024)
    upload_max_image_dimension: int = Field(default=1280, ge=1, le=1280)
    upload_max_image_pixels: int = Field(default=1280 * 1280, ge=1, le=1280 * 1280)
    upload_max_image_frames: int = Field(default=1, ge=1, le=100)
    upload_max_metadata_bytes: int = Field(default=256 * 1024, ge=1024, le=1024 * 1024)
    upload_max_lora_bytes: int = Field(
        default=100 * 1024 * 1024,
        ge=1,
        le=512 * 1024 * 1024,
    )
    upload_max_prompt_template_bytes: int = Field(
        default=256 * 1024,
        ge=1,
        le=1024 * 1024,
    )
    upload_max_model_configuration_bytes: int = Field(
        default=64 * 1024,
        ge=1,
        le=1024 * 1024,
    )
    upload_policy_version: str = "direct-put-v1"
    upload_integrity_policy_version: str = "image-integrity-v1"
    asset_validation_policy_version: str = "asset-validation-v1"
    asset_validation_max_attempts: int = Field(default=5, ge=1, le=50)
    asset_retention_cleanup_version_page_size: int = Field(
        default=100,
        ge=1,
        le=1000,
    )
    asset_retention_cleanup_max_version_pages: int = Field(
        default=50,
        ge=2,
        le=1000,
    )
    asset_retention_cleanup_max_versions: int = Field(
        default=1000,
        ge=1,
        le=100_000,
    )
    asset_retention_cleanup_stable_empty_passes: int = Field(
        default=2,
        ge=2,
        le=10,
    )
    asset_validation_image_decoded_max_bytes: int = Field(
        default=32 * 1024 * 1024,
        ge=1,
        le=256 * 1024 * 1024,
    )
    asset_validation_safetensors_header_max_bytes: int = Field(
        default=1024 * 1024,
        ge=8,
        le=16 * 1024 * 1024,
    )
    asset_validation_safetensors_max_tensors: int = Field(
        default=4096,
        ge=1,
        le=100_000,
    )
    asset_validation_safetensors_max_rank: int = Field(default=8, ge=1, le=32)
    asset_validation_safetensors_max_dimension: int = Field(
        default=1_000_000,
        ge=1,
        le=2_147_483_647,
    )
    asset_validation_safetensors_max_elements: int = Field(
        default=100_000_000,
        ge=1,
        le=10_000_000_000,
    )
    asset_validation_json_maximum_depth: int = Field(default=32, ge=2, le=128)
    asset_validation_json_maximum_nodes: int = Field(
        default=10_000,
        ge=16,
        le=1_000_000,
    )
    asset_validation_content_reference_lifetime_seconds: int = Field(
        default=60,
        ge=10,
        le=300,
    )
    asset_malware_adapter: Literal["deterministic", "clamav"] = "deterministic"
    clamav_host: str = "clamav"
    clamav_port: int = Field(default=3310, ge=1, le=65535)
    clamav_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    clamav_maximum_concurrency: int = Field(default=4, ge=1, le=128)
    clamav_stream_max_bytes: int = Field(
        default=100 * 1024 * 1024,
        ge=1,
        le=512 * 1024 * 1024,
    )
    clamav_chunk_bytes: int = Field(default=64 * 1024, ge=1024, le=1024 * 1024)
    clamav_maximum_response_bytes: int = Field(default=4096, ge=64, le=64 * 1024)
    asset_content_safety_adapter: Literal["deterministic", "alibaba"] = "deterministic"
    deterministic_content_safety_outcome: Literal["PASS", "REVIEW", "BLOCK"] = "PASS"
    content_safety_policy_version: str = "content-safety-policy-v1"
    content_safety_mapping_version: str = "content-safety-map-v1"
    alibaba_content_safety_access_key_id: SecretStr | None = None
    alibaba_content_safety_access_key_secret: SecretStr | None = None
    alibaba_content_safety_endpoint: str = "green-cip.cn-shanghai.aliyuncs.com"
    alibaba_content_safety_endpoint_region: str = "cn-shanghai"
    alibaba_content_safety_service: str = "postImageCheckByVL_ec"
    alibaba_content_safety_sdk_version: str = "3.2.4"
    alibaba_content_safety_connect_timeout_seconds: float = Field(
        default=1.0,
        gt=0,
        le=30,
    )
    alibaba_content_safety_read_timeout_seconds: float = Field(
        default=8.0,
        gt=0,
        le=60,
    )
    alibaba_content_safety_end_to_end_timeout_seconds: float = Field(
        default=12.0,
        gt=0,
        le=120,
    )
    alibaba_content_safety_maximum_concurrency: int = Field(
        default=4,
        ge=1,
        le=128,
    )
    alibaba_content_safety_minimum_url_validity_seconds: float = Field(
        default=20.0,
        gt=0,
        le=300,
    )
    alibaba_content_safety_allowed_url_origins: list[str] = Field(default_factory=list)
    validation_data_transfer_enabled: bool = False
    validation_data_transfer_policy_version: str = "validation-transfer-deny-v1"
    validation_data_transfer_allowed_workspace_ids: list[str] = Field(default_factory=list)
    validation_data_transfer_allowed_asset_kinds: list[AssetKind] = Field(default_factory=list)
    validation_data_transfer_allowed_retention_classes: list[RetentionClass] = Field(
        default_factory=list
    )
    validation_data_transfer_allowed_providers: list[str] = Field(default_factory=list)
    validation_data_transfer_allowed_endpoint_regions: list[str] = Field(default_factory=list)
    validation_data_transfer_allowed_endpoint_hosts: list[str] = Field(default_factory=list)
    vision_adapter: Literal["deterministic", "alibaba"] = "deterministic"
    deterministic_vision_scenario: Literal[
        "success",
        "low_confidence",
        "conflict",
        "sensitive",
        "malformed",
        "timeout",
        "rejected",
        "throttled",
        "unknown",
    ] = "success"
    vision_prompt_version: str = "product-brief-prompt-v1"
    vision_product_facts_maximum_bytes: int = Field(
        default=64 * 1024,
        ge=2,
        le=512 * 1024,
    )
    vision_product_facts_maximum_depth: int = Field(default=8, ge=1, le=32)
    vision_product_facts_maximum_nodes: int = Field(default=1024, ge=1, le=10_000)
    vision_product_facts_maximum_string_bytes: int = Field(
        default=4096,
        ge=1,
        le=64 * 1024,
    )
    product_brief_review_policy_version: str = "product-brief-review-v1"
    product_brief_confidence_threshold: Decimal = Field(
        default=Decimal("0.80"),
        ge=Decimal("0"),
        le=Decimal("1"),
    )
    product_brief_mandatory_review_paths: list[str] = Field(default_factory=list)
    product_brief_sensitive_claim_paths: list[str] = Field(
        default_factory=lambda: list(DEFAULT_PRODUCT_BRIEF_SENSITIVE_CLAIM_PATHS)
    )
    product_brief_analysis_max_attempts: int = Field(default=5, ge=1, le=50)
    product_brief_analysis_max_reconciliation_attempts: int = Field(
        default=8,
        ge=1,
        le=100,
    )
    vision_temporary_reference_lifetime_seconds: int = Field(
        default=60,
        ge=10,
        le=300,
    )
    vision_data_transfer_enabled: bool = False
    vision_data_transfer_policy_version: str = "vision-transfer-deny-v1"
    vision_data_transfer_allowed_workspace_ids: list[str] = Field(default_factory=list)
    vision_data_transfer_allowed_retention_classes: list[RetentionClass] = Field(
        default_factory=list
    )
    vision_data_transfer_allowed_providers: list[str] = Field(default_factory=list)
    vision_data_transfer_allowed_endpoint_regions: list[str] = Field(default_factory=list)
    vision_data_transfer_allowed_endpoint_hosts: list[str] = Field(default_factory=list)
    alibaba_vision_api_key: SecretStr | None = None
    alibaba_vision_api_key_file: str | None = None
    alibaba_vision_api_key_file_max_bytes: int = Field(default=4096, ge=1, le=64 * 1024)
    alibaba_vision_endpoint: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    alibaba_vision_endpoint_region: str = "cn-beijing"
    alibaba_vision_model: str = "qwen-vl-max"
    alibaba_vision_model_snapshot: str = "qwen-vl-max"
    alibaba_vision_adapter_version: str = "alibaba-openai-vision-v1"
    alibaba_vision_connect_timeout_seconds: float = Field(default=3.0, gt=0, le=60)
    alibaba_vision_read_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    alibaba_vision_end_to_end_timeout_seconds: float = Field(
        default=45.0,
        gt=0,
        le=600,
    )
    vision_preflight_budget_seconds: int = Field(default=10, ge=1, le=300)
    vision_operation_lease_margin_seconds: int = Field(default=15, ge=5, le=120)
    alibaba_vision_maximum_concurrency: int = Field(default=4, ge=1, le=128)
    alibaba_vision_maximum_response_bytes: int = Field(
        default=512 * 1024,
        ge=1,
        lt=2 * 1024 * 1024,
    )
    alibaba_vision_maximum_output_tokens: int = Field(default=4096, ge=1, le=32_768)
    alibaba_vision_maximum_repair_attempts: int = Field(default=1, ge=0, le=1)
    alibaba_vision_allowed_image_origins: list[str] = Field(default_factory=list)
    embedding_adapter: Literal["deterministic", "alibaba"] = "deterministic"
    embedding_provider: Literal["fixture", "alibaba-model-studio"] = "fixture"
    deterministic_embedding_scenario: Literal[
        "SUCCESS",
        "THROTTLED",
        "TIMEOUT",
        "UNAVAILABLE",
        "REJECTED",
        "INVALID_RESPONSE",
        "UNKNOWN",
    ] = "SUCCESS"
    embedding_model_family: str = "deterministic-image-embedding"
    embedding_model_id: str = "deterministic-image-embedding-v1"
    embedding_pinned_revision: str = "fixture-epoch-v1"
    embedding_dimension: int = Field(default=256, ge=1, le=32_768)
    embedding_model_configuration_version: str = "image-embedding-config-v1"
    embedding_preprocessing_version: str = "image-preprocess-v1"
    product_fused_preprocessing_version: str = "product-fused-v1"
    retrieval_policy_version: str = "retrieval-policy-v1"
    retrieval_rrf_k: int = Field(default=60, ge=1, le=10_000)
    retrieval_maximum_business_adjustment: float = Field(default=0.25, ge=0, le=1)
    retrieval_milvus_maximum_filter_ids: int = Field(default=1000, ge=1, le=1000)
    retrieval_run_retention_seconds: int = Field(default=86_400, ge=61, le=2_592_000)
    retrieval_preview_token_lifetime_seconds: int = Field(default=45, ge=30, le=60)
    retrieval_preview_reference_lifetime_seconds: int = Field(default=45, ge=30, le=60)
    embedding_collection_schema_version: int = Field(default=1, ge=1, le=2_147_483_647)
    embedding_collection_index_spec_version: str = "hnsw-cosine-v1"
    image_index_temporary_reference_lifetime_seconds: int = Field(
        default=60,
        ge=10,
        le=300,
    )
    image_index_max_attempts: int = Field(default=5, ge=1, le=50)
    image_index_max_reconciliation_attempts: int = Field(default=8, ge=1, le=100)
    collection_rebuild_batch_size: int = Field(default=100, ge=1, le=1000)
    collection_rebuild_validation_maximum_rows: int = Field(default=100_000, ge=1, le=1_000_000)
    collection_rebuild_validation_sample_size: int = Field(default=50, ge=1, le=1000)
    collection_rebuild_minimum_ann_recall_at_10: float = Field(default=0.95, gt=0, le=1)
    collection_rebuild_retirement_delay_seconds: int = Field(default=3600, ge=60, le=604800)
    embedding_data_transfer_enabled: bool = False
    embedding_data_transfer_policy_version: str = "embedding-transfer-deny-v1"
    embedding_data_transfer_allowed_workspace_ids: list[str] = Field(default_factory=list)
    embedding_data_transfer_allowed_retention_classes: list[RetentionClass] = Field(
        default_factory=list
    )
    embedding_data_transfer_allowed_providers: list[str] = Field(default_factory=list)
    embedding_data_transfer_allowed_endpoint_regions: list[str] = Field(default_factory=list)
    embedding_data_transfer_allowed_endpoint_hosts: list[str] = Field(default_factory=list)
    alibaba_embedding_api_key: SecretStr | None = None
    alibaba_embedding_api_key_file: str | None = None
    alibaba_embedding_api_key_file_max_bytes: int = Field(
        default=4096,
        ge=1,
        le=64 * 1024,
    )
    alibaba_embedding_endpoint: str = "https://dashscope.aliyuncs.com/api/v1"
    alibaba_embedding_endpoint_region: str = "cn-beijing"
    alibaba_embedding_connect_timeout_seconds: float = Field(default=3.0, gt=0, le=60)
    alibaba_embedding_read_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    alibaba_embedding_end_to_end_timeout_seconds: float = Field(
        default=45.0,
        gt=0,
        le=600,
    )
    alibaba_embedding_maximum_concurrency: int = Field(default=4, ge=1, le=128)
    alibaba_embedding_maximum_response_bytes: int = Field(
        default=2 * 1024 * 1024,
        ge=1,
        lt=8 * 1024 * 1024,
    )
    alibaba_embedding_allowed_image_origins: list[str] = Field(default_factory=list)
    asset_provenance_adapter: Literal["deterministic", "c2pa"] = "deterministic"
    deterministic_provenance_status: Literal[
        "VERIFIED",
        "UNVERIFIED",
        "CONFLICTING",
        "NOT_PRESENT",
    ] = "NOT_PRESENT"
    c2pa_trust_config_version: str = "c2pa-trust-v1"
    c2pa_trust_anchors_pem: SecretStr | None = None
    c2pa_trust_eku_policy: SecretStr | None = None
    c2pa_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    c2pa_maximum_concurrency: int = Field(default=2, ge=1, le=64)
    c2pa_maximum_report_bytes: int = Field(
        default=1024 * 1024,
        ge=1024,
        le=16 * 1024 * 1024,
    )
    c2pa_maximum_report_depth: int = Field(default=32, ge=1, le=128)
    c2pa_maximum_report_nodes: int = Field(default=50_000, ge=1, le=1_000_000)
    c2pa_maximum_manifests: int = Field(default=128, ge=1, le=1024)
    c2pa_maximum_status_codes: int = Field(default=64, ge=1, le=128)
    c2pa_subprocess_memory_limit_bytes: int = Field(
        default=512 * 1024 * 1024,
        ge=64 * 1024 * 1024,
        le=4 * 1024 * 1024 * 1024,
    )
    c2pa_subprocess_file_descriptor_limit: int = Field(
        default=64,
        ge=16,
        le=1024,
    )

    mysql_pool_size: int = Field(default=10, ge=1, le=100)
    mysql_max_overflow: int = Field(default=20, ge=0, le=200)
    mysql_pool_recycle_seconds: int = Field(default=1800, ge=60, le=86400)
    mysql_connect_timeout_seconds: int = Field(default=5, ge=1, le=60)
    workflow_retention_hours: int = Field(default=72, ge=1, le=168)
    workflow_step_lease_seconds: int = Field(default=300, ge=30, le=3600)
    workflow_message_max_attempts: int = Field(default=8, ge=1, le=50)
    worker_message_retry_initial_seconds: float = Field(default=1.0, gt=0, le=3600)
    worker_message_retry_max_seconds: float = Field(default=300.0, gt=0, le=86400)
    worker_consumer_name: str = "agent-worker"
    worker_queues: list[str] | None = None
    worker_required_operation_kinds: list[OperationKind] = Field(default_factory=list)
    worker_readiness_path: str = "/tmp/commercevision-worker-ready.json"
    worker_readiness_max_age_seconds: float = Field(default=50.0, gt=0, le=600)
    worker_stop_grace_period_seconds: int = Field(default=90, ge=1, le=3600)
    workflow_queue_name: str = "commercevision.workflow"
    asset_queue_name: str = "commercevision.asset"
    index_queue_name: str = "commercevision.index"
    maintenance_queue_name: str = "commercevision.maintenance"
    scheduler_poll_seconds: float = Field(default=2.0, gt=0.1, le=60)
    scheduler_batch_size: int = Field(default=50, ge=1, le=500)
    scheduler_lease_seconds: int = Field(default=30, ge=5, le=300)
    scheduler_recovery_interval_seconds: float = Field(default=10.0, gt=0.5, le=300)
    scheduler_operation_recovery_interval_seconds: float = Field(
        default=10.0,
        gt=0.5,
        le=300,
    )
    rights_expiry_scan_interval_seconds: float = Field(
        default=10.0,
        gt=0.5,
        le=300,
    )
    asset_retention_scan_interval_seconds: float = Field(
        default=10.0,
        gt=0.5,
        le=300,
    )
    asset_deletion_max_attempts: int = Field(default=50, ge=2, le=2000)
    asset_deletion_max_reconciliation_attempts: int = Field(
        default=100,
        ge=2,
        le=2000,
    )
    scheduler_scanner_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    operation_retry_initial_seconds: float = Field(default=1.0, gt=0, le=3600)
    operation_retry_max_seconds: float = Field(default=300.0, gt=0, le=86400)
    operation_retry_max_elapsed_seconds: float = Field(default=86400.0, gt=0, le=604800)
    operation_reconciliation_initial_seconds: float = Field(default=2.0, gt=0, le=3600)
    operation_reconciliation_max_seconds: float = Field(default=3600.0, gt=0, le=86400)
    operation_reconciliation_max_elapsed_seconds: float = Field(
        default=345600.0,
        gt=0,
        le=604800,
    )
    trusted_principal_current_key_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    trusted_principal_current_hmac_secret: SecretStr | None = Field(
        default=None,
        min_length=32,
    )
    trusted_principal_previous_key_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    trusted_principal_previous_hmac_secret: SecretStr | None = Field(
        default=None,
        min_length=32,
    )
    trusted_principal_max_age_seconds: int = Field(default=300, ge=30, le=3600)
    trusted_principal_future_skew_seconds: int = Field(default=30, ge=0, le=300)
    brand_profile_cursor_max_age_seconds: int = Field(
        default=86_400,
        ge=60,
        le=604_800,
    )
    brand_profile_cursor_future_skew_seconds: int = Field(default=30, ge=0, le=300)
    creative_plan_cursor_max_age_seconds: int = Field(
        default=86_400,
        ge=60,
        le=604_800,
    )
    creative_plan_cursor_future_skew_seconds: int = Field(default=30, ge=0, le=300)

    @field_validator(
        "trusted_principal_previous_key_id",
        "trusted_principal_previous_hmac_secret",
        mode="before",
    )
    @classmethod
    def _normalize_empty_previous_trusted_principal_key(
        cls,
        value: object,
    ) -> object:
        # Docker Compose represents an unset optional rotation pair as empty
        # strings. Treat only the exact empty value as absent; whitespace and
        # partial pairs remain invalid configuration.
        return None if value == "" else value

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:13000"])
    mcp_transport: Literal["stdio", "sse", "streamable-http"] = "streamable-http"
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8001
    mcp_tool_policy_version: str = Field(
        default="mcp-tool-policy-v1",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    mcp_max_argument_bytes: int = Field(default=64 * 1024, ge=1_024, le=1024 * 1024)
    mcp_max_output_bytes: int = Field(default=256 * 1024, ge=1_024, le=2 * 1024 * 1024)
    scheduler_host: str = "0.0.0.0"
    scheduler_port: int = 8002

    @model_validator(mode="before")
    @classmethod
    def _apply_legacy_task_bucket(cls, values: object) -> object:
        if not isinstance(values, dict) or "object_store_task_bucket" in values:
            return values
        legacy_bucket = values.get("object_store_bucket")
        if not isinstance(legacy_bucket, str):
            return values
        return {**values, "object_store_task_bucket": legacy_bucket}

    @field_validator(
        "object_store_access_key",
        "object_store_region",
        "object_store_quarantine_bucket",
        "object_store_task_bucket",
        "object_store_foundation_bucket",
        "object_store_provider_result_bucket",
        "object_store_role_session_name",
        "upload_policy_version",
        "upload_integrity_policy_version",
        "asset_validation_policy_version",
        "clamav_host",
        "content_safety_policy_version",
        "content_safety_mapping_version",
        "alibaba_content_safety_endpoint_region",
        "alibaba_content_safety_service",
        "alibaba_content_safety_sdk_version",
        "validation_data_transfer_policy_version",
        "vision_prompt_version",
        "product_brief_review_policy_version",
        "vision_data_transfer_policy_version",
        "alibaba_vision_endpoint_region",
        "alibaba_vision_model",
        "alibaba_vision_model_snapshot",
        "alibaba_vision_adapter_version",
        "embedding_model_family",
        "embedding_model_id",
        "embedding_pinned_revision",
        "embedding_model_configuration_version",
        "embedding_preprocessing_version",
        "product_fused_preprocessing_version",
        "embedding_collection_index_spec_version",
        "embedding_data_transfer_policy_version",
        "alibaba_embedding_endpoint_region",
        "milvus_database",
        "c2pa_trust_config_version",
        "worker_consumer_name",
        "worker_readiness_path",
        "workflow_queue_name",
        "asset_queue_name",
        "index_queue_name",
        "maintenance_queue_name",
    )
    @classmethod
    def _trim_required_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("queue and consumer identities must not be blank")
        return normalized

    @field_validator(
        "content_safety_policy_version",
        "content_safety_mapping_version",
        "alibaba_content_safety_sdk_version",
        "validation_data_transfer_policy_version",
        "vision_prompt_version",
        "product_brief_review_policy_version",
        "vision_data_transfer_policy_version",
        "alibaba_vision_adapter_version",
        "embedding_model_configuration_version",
        "embedding_preprocessing_version",
        "product_fused_preprocessing_version",
        "embedding_collection_index_spec_version",
        "embedding_data_transfer_policy_version",
        "c2pa_trust_config_version",
    )
    @classmethod
    def _validate_validation_identity_width(cls, value: str) -> str:
        if len(value) > 64:
            raise ValueError("validation policy and provider identities must not exceed 64 chars")
        return value

    @field_validator(
        "validation_data_transfer_allowed_workspace_ids",
        "vision_data_transfer_allowed_workspace_ids",
        "embedding_data_transfer_allowed_workspace_ids",
    )
    @classmethod
    def _validate_transfer_workspaces(cls, value: list[str]) -> list[str]:
        try:
            validated = [validate_workspace_id(item) for item in value]
        except ValueError as exc:
            raise ValueError("validation data transfer workspace allowlist is invalid") from exc
        if len(set(validated)) != len(validated):
            raise ValueError("validation data transfer workspace allowlist must be unique")
        return validated

    @field_validator(
        "validation_data_transfer_allowed_providers",
        "validation_data_transfer_allowed_endpoint_regions",
        "vision_data_transfer_allowed_providers",
        "vision_data_transfer_allowed_endpoint_regions",
        "embedding_data_transfer_allowed_providers",
        "embedding_data_transfer_allowed_endpoint_regions",
    )
    @classmethod
    def _validate_transfer_canonical_allowlists(
        cls,
        value: list[str],
    ) -> list[str]:
        normalized = [item.strip().lower() for item in value]
        if any(not item or len(item) > 128 for item in normalized):
            raise ValueError("validation data transfer allowlists contain invalid values")
        if len(set(normalized)) != len(normalized):
            raise ValueError("validation data transfer allowlists must be unique")
        return normalized

    @field_validator(
        "validation_data_transfer_allowed_endpoint_hosts",
        "vision_data_transfer_allowed_endpoint_hosts",
        "embedding_data_transfer_allowed_endpoint_hosts",
    )
    @classmethod
    def _validate_transfer_endpoint_hosts(cls, value: list[str]) -> list[str]:
        validated = [validate_canonical_endpoint_host(item) for item in value]
        if len(set(validated)) != len(validated):
            raise ValueError("validation data transfer endpoint hosts must be unique")
        return validated

    @field_validator(
        "validation_data_transfer_allowed_asset_kinds",
        "validation_data_transfer_allowed_retention_classes",
        "vision_data_transfer_allowed_retention_classes",
        "embedding_data_transfer_allowed_retention_classes",
    )
    @classmethod
    def _validate_transfer_enum_allowlists(cls, value: list[object]) -> list[object]:
        if len(set(value)) != len(value):
            raise ValueError("validation data transfer allowlists must be unique")
        return value

    @field_validator(
        "product_brief_mandatory_review_paths",
        "product_brief_sensitive_claim_paths",
    )
    @classmethod
    def _validate_product_brief_review_paths(cls, value: list[str]) -> list[str]:
        known_paths = set(product_brief_field_paths(ProductBriefCategory.BEAUTY)) | set(
            product_brief_field_paths(ProductBriefCategory.AUTOMOTIVE)
        )
        if any(path != path.strip() or path not in known_paths for path in value):
            raise ValueError("ProductBrief review policy field path is invalid")
        if len(set(value)) != len(value):
            raise ValueError("ProductBrief review policy field paths must be unique")
        return value

    @field_validator("clamav_host")
    @classmethod
    def _validate_validation_hostname(cls, value: str) -> str:
        if (
            not value.isascii()
            or len(value) > 253
            or "://" in value
            or any(character in value for character in "/?#@")
            or any(character.isspace() for character in value)
        ):
            raise ValueError("validation dependency hostnames must be credential-free DNS names")
        return value.lower()

    @field_validator("alibaba_content_safety_endpoint")
    @classmethod
    def _validate_alibaba_endpoint_host(cls, value: str) -> str:
        return validate_canonical_endpoint_host(value)

    @field_validator("alibaba_content_safety_allowed_url_origins")
    @classmethod
    def _validate_content_safety_origins(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for origin in value:
            parsed = urlsplit(origin)
            try:
                _ = parsed.port
            except ValueError as exc:
                raise ValueError("content-safety origins must be valid HTTPS origins") from exc
            if (
                parsed.scheme.lower() != "https"
                or parsed.hostname is None
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("content-safety origins must be credential-free HTTPS origins")
            normalized.append(origin.rstrip("/"))
        if len(set(normalized)) != len(normalized):
            raise ValueError("content-safety origins must be unique")
        return normalized

    @field_validator("alibaba_vision_allowed_image_origins")
    @classmethod
    def _validate_vision_origins(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for origin in value:
            parsed = urlsplit(origin)
            try:
                _ = parsed.port
            except ValueError as exc:
                raise ValueError("Vision origins must be valid HTTPS origins") from exc
            if (
                parsed.scheme.lower() != "https"
                or parsed.hostname is None
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("Vision origins must be credential-free HTTPS origins")
            normalized.append(origin.rstrip("/"))
        if len(set(normalized)) != len(normalized):
            raise ValueError("Vision origins must be unique")
        return normalized

    @field_validator("alibaba_embedding_allowed_image_origins")
    @classmethod
    def _validate_embedding_origins(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for origin in value:
            parsed = urlsplit(origin)
            try:
                _ = parsed.port
            except ValueError as exc:
                raise ValueError("Embedding origins must be valid HTTPS origins") from exc
            if (
                parsed.scheme.lower() != "https"
                or parsed.hostname is None
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("Embedding origins must be credential-free HTTPS origins")
            normalized.append(origin.rstrip("/"))
        if len(set(normalized)) != len(normalized):
            raise ValueError("Embedding origins must be unique")
        return normalized

    @field_validator("alibaba_vision_endpoint")
    @classmethod
    def _validate_alibaba_vision_endpoint(cls, value: str) -> str:
        parsed = urlsplit(value)
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("Alibaba Vision endpoint must be a valid HTTPS URL") from exc
        if (
            parsed.scheme.lower() != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Alibaba Vision endpoint must be a credential-free HTTPS URL")
        return value.rstrip("/")

    @field_validator("alibaba_embedding_endpoint")
    @classmethod
    def _validate_alibaba_embedding_endpoint(cls, value: str) -> str:
        parsed = urlsplit(value)
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("Alibaba embedding endpoint must be a valid HTTPS URL") from exc
        if (
            parsed.scheme.lower() != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/") != "/api/v1"
        ):
            raise ValueError(
                "Alibaba embedding endpoint must be a credential-free /api/v1 HTTPS URL"
            )
        return value.rstrip("/")

    @field_validator(
        "object_store_ram_role_name",
        "object_store_oidc_role_arn",
        "object_store_oidc_provider_arn",
        "object_store_oidc_token_file_path",
        "object_store_sts_endpoint",
        "object_store_session_token",
        "alibaba_vision_api_key",
        "alibaba_vision_api_key_file",
        "alibaba_embedding_api_key",
        "alibaba_embedding_api_key_file",
        "milvus_token",
        mode="before",
    )
    @classmethod
    def _trim_optional_identity(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None

    @field_validator("object_store_sts_endpoint")
    @classmethod
    def _validate_sts_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            parsed = urlsplit(f"//{value}")
            port = parsed.port
        except ValueError as exc:
            raise ValueError("OSS STS endpoint must be a valid DNS hostname") from exc
        hostname = parsed.hostname
        labels = hostname.split(".") if hostname is not None else []
        if (
            "://" in value
            or port is not None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or not value.isascii()
            or hostname != value.lower()
            or len(value) > 253
            or len(labels) < 2
            or not any(character.isalpha() for character in labels[-1])
            or any(
                not label
                or len(label) > 63
                or not label[0].isalnum()
                or not label[-1].isalnum()
                or any(not character.isalnum() and character != "-" for character in label)
                for label in labels
            )
        ):
            raise ValueError("OSS STS endpoint must be a credential-free DNS hostname")
        return value.lower()

    @field_validator("object_store_endpoint", "object_store_presign_endpoint")
    @classmethod
    def _validate_object_store_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            parsed = urlsplit(value)
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("object-store endpoints must be valid HTTP(S) origins") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("object-store endpoints must be credential-free HTTP(S) origins")
        normalized = value.rstrip("/")
        return normalized

    @field_validator("worker_queues")
    @classmethod
    def _normalize_worker_queues(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("worker_queues must not be empty when explicitly configured")
        normalized = [queue_name.strip() for queue_name in value]
        if any(not queue_name for queue_name in normalized):
            raise ValueError("worker queue selections must not be blank")
        if len(set(normalized)) != len(normalized):
            raise ValueError("worker queue selections must be unique")
        return normalized

    @field_validator("worker_required_operation_kinds")
    @classmethod
    def _validate_required_operation_kinds(
        cls,
        value: list[OperationKind],
    ) -> list[OperationKind]:
        if len(set(value)) != len(value):
            raise ValueError("required operation kinds must be unique")
        return value

    @model_validator(mode="after")
    def _validate_queue_topology(self) -> Settings:
        logical_queues = (
            self.workflow_queue_name,
            self.asset_queue_name,
            self.index_queue_name,
            self.maintenance_queue_name,
        )
        if len(set(logical_queues)) != len(logical_queues):
            raise ValueError("logical queue names must be unique")
        if self.worker_queues is not None:
            unknown = set(self.worker_queues).difference(logical_queues)
            if unknown:
                raise ValueError(
                    "worker queue selections must use configured logical queues: "
                    + ", ".join(sorted(unknown))
                )
        if self.worker_message_retry_max_seconds < self.worker_message_retry_initial_seconds:
            raise ValueError("worker message retry maximum must not be below the initial delay")
        if self.operation_retry_max_seconds < self.operation_retry_initial_seconds:
            raise ValueError("operation retry maximum must not be below the initial delay")
        if (
            self.asset_retention_cleanup_max_version_pages
            < self.asset_retention_cleanup_stable_empty_passes
        ):
            raise ValueError("retention cleanup page budget must cover stable empty scans")
        minimum_retry_coverage = 0.0
        retry_base = self.operation_retry_initial_seconds
        for _ in range(self.upload_cleanup_max_attempts - 1):
            minimum_retry_coverage += min(
                retry_base * 0.5,
                self.operation_retry_max_seconds,
            )
            retry_base = min(
                retry_base * 2,
                self.operation_retry_max_seconds,
            )
        if minimum_retry_coverage < self.operation_retry_max_elapsed_seconds:
            raise ValueError(
                "upload cleanup execution attempts do not cover the elapsed retry budget"
            )
        if (
            self.upload_cleanup_reconcile_horizon_seconds
            <= self.upload_cleanup_reconcile_interval_seconds
        ):
            raise ValueError("upload cleanup reconciliation horizon must exceed its interval")
        if (
            self.operation_reconciliation_max_seconds
            < self.upload_cleanup_reconcile_interval_seconds
        ):
            raise ValueError(
                "operation reconciliation maximum delay must cover the upload cleanup interval"
            )
        required_cleanup_attempts = (
            self.upload_cleanup_reconcile_horizon_seconds
            + self.upload_cleanup_reconcile_interval_seconds
            - 1
        ) // self.upload_cleanup_reconcile_interval_seconds + 1
        if self.upload_cleanup_reconcile_max_attempts < required_cleanup_attempts:
            raise ValueError("upload cleanup reconciliation attempts do not cover the horizon")
        if (
            self.operation_reconciliation_max_elapsed_seconds
            <= self.upload_cleanup_reconcile_horizon_seconds
        ):
            raise ValueError("operation reconciliation elapsed budget must exceed cleanup horizon")
        if (
            self.operation_reconciliation_max_seconds
            < self.operation_reconciliation_initial_seconds
        ):
            raise ValueError("operation reconciliation maximum must not be below the initial delay")
        credential_refresh_budget = (
            self.object_store_credential_refresh_timeout_seconds
            if self.object_store_credential_mode != "static"
            else 0
        )
        object_store_request_budget = _FINALIZE_STORAGE_REQUEST_BOUND * (
            credential_refresh_budget
            + self.object_store_connect_timeout_seconds
            + self.object_store_read_timeout_seconds
        )
        if object_store_request_budget >= self.upload_finalize_lease_seconds:
            raise ValueError(
                "object-store request timeout budget must be shorter than the upload finalize lease"
            )
        non_workflow_worker_queues = set(self.configured_worker_queues).difference(
            {self.workflow_queue_name}
        )
        if (
            self.environment == "production"
            and non_workflow_worker_queues
            and not self.worker_required_operation_kinds
        ):
            raise ValueError("production requires explicit required operation kinds")
        if (
            self.service_name == "worker"
            and self.worker_readiness_max_age_seconds < self.worker_readiness_cycle_budget_seconds
        ):
            raise ValueError("Worker readiness marker lease must cover the full remote probe cycle")
        if self.environment == "production":
            if self.object_store_backend == "oss" and self.object_store_force_path_style:
                raise ValueError("production OSS object storage requires virtual-hosted addressing")
            if self.object_store_backend == "oss" and self.object_store_credential_mode == "static":
                raise ValueError(
                    "production OSS object storage requires renewable workload identity"
                )
            endpoints = (
                self.object_store_endpoint,
                self.object_store_presign_endpoint or self.object_store_endpoint,
            )
            if any(not endpoint.startswith("https://") for endpoint in endpoints):
                raise ValueError("production object-store endpoints must use HTTPS")
            if not self.object_store_require_encryption:
                raise ValueError("production object storage requires server-side encryption")
            if not self.object_store_tls_verify:
                raise ValueError("production object storage requires TLS verification")
            physical_buckets = tuple(self.object_store_buckets.values())
            if len(set(physical_buckets)) != len(physical_buckets):
                raise ValueError("production object storage requires distinct physical buckets")
            if (
                self.object_store_credential_mode == "oidc_role_arn"
                and self.object_store_sts_endpoint is None
            ):
                raise ValueError(
                    "production OSS OIDC workload identity requires an explicit STS endpoint"
                )
            if (
                self.object_store_backend == "minio"
                or self.object_store_credential_mode == "static"
            ) and self.object_store_secret_key.get_secret_value() in {
                "change-me",
                "commercevision-secret",
            }:
                raise ValueError("production object storage requires a non-default storage secret")
            if _origin_identity(endpoints[0]) == _origin_identity(endpoints[1]):
                raise ValueError(
                    "production object storage requires a distinct browser presign origin"
                )
        if self.object_store_backend != "oss" and self.object_store_credential_mode != "static":
            raise ValueError("renewable object-store credentials are supported only for OSS")
        if self.object_store_credential_mode == "oidc_role_arn":
            oidc_values = (
                self.object_store_oidc_role_arn,
                self.object_store_oidc_provider_arn,
                self.object_store_oidc_token_file_path,
            )
            if any(value is None for value in oidc_values):
                raise ValueError(
                    "OSS OIDC workload identity requires role ARN, provider ARN, "
                    "and token file path"
                )
            assert self.object_store_oidc_token_file_path is not None
            if not (
                PurePosixPath(self.object_store_oidc_token_file_path).is_absolute()
                or PureWindowsPath(self.object_store_oidc_token_file_path).is_absolute()
            ):
                raise ValueError("OSS OIDC token file path must be absolute")
        elif self.object_store_sts_endpoint is not None:
            raise ValueError("OSS STS endpoint requires oidc_role_arn credential mode")
        elif any(
            value is not None
            for value in (
                self.object_store_oidc_role_arn,
                self.object_store_oidc_provider_arn,
                self.object_store_oidc_token_file_path,
            )
        ):
            raise ValueError(
                "OSS OIDC workload identity fields require oidc_role_arn credential mode"
            )
        current_key_configured = self.trusted_principal_current_key_id is not None
        current_secret_configured = self.trusted_principal_current_hmac_secret is not None
        if current_key_configured != current_secret_configured:
            raise ValueError(
                "current trusted-principal key id and HMAC secret must be configured together"
            )
        previous_key_configured = self.trusted_principal_previous_key_id is not None
        previous_secret_configured = self.trusted_principal_previous_hmac_secret is not None
        if previous_key_configured != previous_secret_configured:
            raise ValueError(
                "previous trusted-principal key id and HMAC secret must be configured together"
            )
        if previous_key_configured and not current_key_configured:
            raise ValueError("a previous trusted-principal key requires a current key")
        if (
            self.trusted_principal_current_key_id is not None
            and self.trusted_principal_current_key_id == self.trusted_principal_previous_key_id
        ):
            raise ValueError("trusted-principal current and previous key ids must be distinct")
        if self.asset_malware_adapter == "clamav":
            largest_upload = max(
                self.upload_max_bytes,
                self.upload_max_lora_bytes,
                self.upload_max_prompt_template_bytes,
                self.upload_max_model_configuration_bytes,
            )
            if self.clamav_stream_max_bytes < largest_upload:
                raise ValueError("ClamAV StreamMaxLength must cover every upload byte limit")
        alibaba_id = self.alibaba_content_safety_access_key_id
        alibaba_secret = self.alibaba_content_safety_access_key_secret
        if (alibaba_id is None) != (alibaba_secret is None):
            raise ValueError("Alibaba content-safety credentials must be configured together")
        if self.asset_content_safety_adapter == "alibaba":
            if alibaba_id is None or alibaba_secret is None:
                raise ValueError("Alibaba content-safety credentials are required")
            if not self.alibaba_content_safety_allowed_url_origins:
                raise ValueError("Alibaba content safety requires controlled HTTPS origins")
            transport_budget = (
                self.alibaba_content_safety_connect_timeout_seconds
                + self.alibaba_content_safety_read_timeout_seconds
            )
            if self.alibaba_content_safety_end_to_end_timeout_seconds <= transport_budget:
                raise ValueError("Alibaba content-safety deadline must exceed transport timeouts")
            if (
                self.alibaba_content_safety_minimum_url_validity_seconds
                < self.alibaba_content_safety_end_to_end_timeout_seconds
                or self.asset_validation_content_reference_lifetime_seconds
                < self.alibaba_content_safety_minimum_url_validity_seconds
            ):
                raise ValueError(
                    "content-safety URL lifetime must cover the full provider deadline"
                )
            if self.environment == "production":
                transfer_allowlists = (
                    self.validation_data_transfer_allowed_workspace_ids,
                    self.validation_data_transfer_allowed_asset_kinds,
                    self.validation_data_transfer_allowed_retention_classes,
                    self.validation_data_transfer_allowed_providers,
                    self.validation_data_transfer_allowed_endpoint_regions,
                    self.validation_data_transfer_allowed_endpoint_hosts,
                )
                if (
                    not self.validation_data_transfer_enabled
                    or any(not allowlist for allowlist in transfer_allowlists)
                    or "alibaba-green" not in self.validation_data_transfer_allowed_providers
                    or AssetKind.IMAGE not in self.validation_data_transfer_allowed_asset_kinds
                    or self.alibaba_content_safety_endpoint_region
                    not in self.validation_data_transfer_allowed_endpoint_regions
                    or self.alibaba_content_safety_endpoint
                    not in self.validation_data_transfer_allowed_endpoint_hosts
                ):
                    raise ValueError(
                        "production Alibaba requires an explicit enabled validation "
                        "data transfer policy with exact allowlists"
                    )
        worker_executes_product_briefs = (
            OperationKind.PRODUCT_BRIEF_ANALYSIS in self.worker_required_operation_kinds
        )
        worker_executes_image_indexing = (
            OperationKind.ASSET_INDEXING in self.worker_required_operation_kinds
        )
        historical_target_identities = tuple(
            (
                target.object_store_backend,
                target.object_store_provider_result_bucket,
            )
            for target in self.provider_artifact_reconciliation_targets
        )
        if len(set(historical_target_identities)) != len(historical_target_identities):
            raise ValueError("provider artifact reconciliation exact targets must be unique")
        current_provider_target = (
            self.object_store_backend,
            self.object_store_provider_result_bucket,
        )
        if current_provider_target in historical_target_identities:
            raise ValueError(
                "provider artifact reconciliation targets must not repeat the current target"
            )
        if self.provider_artifact_reconciliation_targets and not (
            self.service_name in {"commercevision", "worker"}
            and self.asset_queue_name in self.configured_worker_queues
            and (self.service_name == "commercevision" or worker_executes_product_briefs)
        ):
            raise ValueError(
                "provider artifact reconciliation targets require a ProductBrief-executing Worker"
            )
        for target in self.provider_artifact_reconciliation_targets:
            if target.object_store_backend == self.object_store_backend:
                continue
            required_cross_backend_values = (
                target.object_store_credential_mode,
                target.object_store_endpoint,
                target.object_store_presign_endpoint,
                target.object_store_region,
                target.object_store_force_path_style,
            )
            if any(value is None for value in required_cross_backend_values):
                raise ValueError(
                    "cross-backend provider artifact reconciliation targets require "
                    "explicit connection and credential configuration"
                )
            if target.object_store_credential_mode == "static" and (
                target.object_store_access_key is None or target.object_store_secret_key is None
            ):
                raise ValueError(
                    "cross-backend static provider artifact reconciliation targets "
                    "require explicit credentials"
                )
        process_accepts_vision_execution_inputs = self.service_name == "commercevision" or (
            self.service_name == "worker" and worker_executes_product_briefs
        )
        vision_execution_inputs_configured = (
            self.alibaba_vision_api_key is not None
            or self.alibaba_vision_api_key_file is not None
            or bool(self.alibaba_vision_allowed_image_origins)
        )
        if vision_execution_inputs_configured and not process_accepts_vision_execution_inputs:
            raise ValueError(
                "Alibaba Vision execution inputs require a ProductBrief-executing process"
            )
        if self.vision_adapter == "alibaba":
            if (
                self.alibaba_vision_api_key is not None
                and self.alibaba_vision_api_key_file is not None
            ):
                raise ValueError(
                    "Alibaba Vision API key requires exactly one configured credential source"
                )
            if self.alibaba_vision_api_key_file is not None and not (
                PurePosixPath(self.alibaba_vision_api_key_file).is_absolute()
                or PureWindowsPath(self.alibaba_vision_api_key_file).is_absolute()
            ):
                raise ValueError("Alibaba Vision API key file path must be absolute")
            if worker_executes_product_briefs:
                if self.alibaba_vision_api_key is None and self.alibaba_vision_api_key_file is None:
                    raise ValueError(
                        "Alibaba Vision API key requires exactly one configured credential source"
                    )
                if self.environment == "production" and self.alibaba_vision_api_key_file is None:
                    raise ValueError("production Alibaba Vision requires a mounted API key file")
                if not self.alibaba_vision_allowed_image_origins:
                    raise ValueError("Alibaba Vision requires controlled HTTPS origins")
                if (
                    self.alibaba_vision_end_to_end_timeout_seconds
                    <= self.alibaba_vision_connect_timeout_seconds
                    + self.alibaba_vision_read_timeout_seconds
                ):
                    raise ValueError(
                        "Alibaba Vision deadline must exceed its transport timeout budget"
                    )
                if (
                    self.vision_temporary_reference_lifetime_seconds
                    <= self.alibaba_vision_end_to_end_timeout_seconds
                ):
                    raise ValueError(
                        "Vision temporary reference lifetime must exceed provider execution"
                    )
                if self.workflow_step_lease_seconds < (
                    self.vision_preflight_budget_seconds
                    + self.alibaba_vision_end_to_end_timeout_seconds
                    + self.vision_operation_lease_margin_seconds
                ):
                    raise ValueError(
                        "Alibaba Vision operation lease must cover preflight, provider, "
                        "and commit budgets"
                    )
                if self.worker_stop_grace_period_seconds < (
                    self.vision_preflight_budget_seconds
                    + self.alibaba_vision_end_to_end_timeout_seconds
                    + self.vision_operation_lease_margin_seconds
                ):
                    raise ValueError(
                        "Worker shutdown grace must cover preflight, provider, and cleanup budgets"
                    )
            if (
                self.environment == "production"
                and _ALIBABA_IMMUTABLE_MODEL_SNAPSHOT.fullmatch(self.alibaba_vision_model_snapshot)
                is None
            ):
                raise ValueError(
                    "production Alibaba Vision requires a dated immutable model snapshot"
                )
            vision_transfer_allowlists = (
                self.vision_data_transfer_allowed_workspace_ids,
                self.vision_data_transfer_allowed_retention_classes,
                self.vision_data_transfer_allowed_providers,
                self.vision_data_transfer_allowed_endpoint_regions,
                self.vision_data_transfer_allowed_endpoint_hosts,
            )
            if (
                not self.vision_data_transfer_enabled
                or any(not allowlist for allowlist in vision_transfer_allowlists)
                or "alibaba-model-studio" not in self.vision_data_transfer_allowed_providers
                or self.alibaba_vision_endpoint_region
                not in self.vision_data_transfer_allowed_endpoint_regions
                or self.alibaba_vision_endpoint_host
                not in self.vision_data_transfer_allowed_endpoint_hosts
            ):
                raise ValueError(
                    "Alibaba Vision requires an explicit enabled data transfer "
                    "policy with exact allowlists"
                )
        if (
            self.environment == "production"
            and worker_executes_product_briefs
            and self.vision_adapter != "alibaba"
        ):
            raise ValueError("production ProductBrief workers require the Alibaba Vision adapter")
        if self.embedding_adapter == "deterministic":
            if self.embedding_provider != "fixture":
                raise ValueError("deterministic embedding requires the fixture provider identity")
        else:
            if (
                self.embedding_provider != "alibaba-model-studio"
                or self.embedding_model_family != "qwen3-vl-embedding"
                or self.embedding_model_id != "qwen3-vl-embedding"
            ):
                raise ValueError(
                    "Alibaba vector embedding requires the mainline qwen3-vl-embedding "
                    "model identity"
                )
            if self.embedding_dimension not in {256, 512, 768, 1024, 1536, 2048, 2560}:
                raise ValueError("Alibaba qwen3-vl-embedding dimension is unsupported")
            if (
                self.alibaba_embedding_api_key is not None
                and self.alibaba_embedding_api_key_file is not None
            ):
                raise ValueError(
                    "Alibaba embedding requires exactly one configured credential source"
                )
            if self.alibaba_embedding_api_key_file is not None and not (
                PurePosixPath(self.alibaba_embedding_api_key_file).is_absolute()
                or PureWindowsPath(self.alibaba_embedding_api_key_file).is_absolute()
            ):
                raise ValueError("Alibaba embedding API key file path must be absolute")
            if worker_executes_image_indexing:
                if self.environment == "production" and self.alibaba_embedding_api_key_file is None:
                    raise ValueError("production Alibaba embedding requires a mounted API key file")
                if (
                    self.alibaba_embedding_api_key is None
                    and self.alibaba_embedding_api_key_file is None
                ):
                    raise ValueError("Alibaba embedding requires exactly one credential source")
                if not self.alibaba_embedding_allowed_image_origins:
                    raise ValueError("Alibaba embedding requires controlled HTTPS origins")
                if (
                    self.alibaba_embedding_end_to_end_timeout_seconds
                    <= self.alibaba_embedding_connect_timeout_seconds
                    + self.alibaba_embedding_read_timeout_seconds
                ):
                    raise ValueError(
                        "Alibaba embedding deadline must exceed its transport timeout budget"
                    )
                if (
                    self.image_index_temporary_reference_lifetime_seconds
                    <= self.alibaba_embedding_end_to_end_timeout_seconds
                ):
                    raise ValueError(
                        "Embedding temporary reference lifetime must exceed provider execution"
                    )
            embedding_transfer_allowlists = (
                self.embedding_data_transfer_allowed_workspace_ids,
                self.embedding_data_transfer_allowed_retention_classes,
                self.embedding_data_transfer_allowed_providers,
                self.embedding_data_transfer_allowed_endpoint_regions,
                self.embedding_data_transfer_allowed_endpoint_hosts,
            )
            if (
                not self.embedding_data_transfer_enabled
                or any(not allowlist for allowlist in embedding_transfer_allowlists)
                or "alibaba-model-studio" not in self.embedding_data_transfer_allowed_providers
                or self.alibaba_embedding_endpoint_region
                not in self.embedding_data_transfer_allowed_endpoint_regions
                or self.alibaba_embedding_endpoint_host
                not in self.embedding_data_transfer_allowed_endpoint_hosts
            ):
                raise ValueError(
                    "Alibaba embedding requires an explicit enabled data transfer "
                    "policy with exact allowlists"
                )
        if (
            self.environment == "production"
            and worker_executes_image_indexing
            and self.embedding_adapter != "alibaba"
        ):
            raise ValueError("production IMAGE index workers require the Alibaba embedding adapter")
        if (
            self.environment == "production"
            and worker_executes_image_indexing
            and self.milvus_token is None
        ):
            raise ValueError("production IMAGE index workers require Milvus authentication")
        if self.environment == "production" and worker_executes_image_indexing:
            try:
                milvus_endpoint = urlsplit(self.milvus_uri)
                _ = milvus_endpoint.port
            except ValueError:
                raise ValueError("production Milvus endpoint is invalid") from None
            if milvus_endpoint.username is not None or milvus_endpoint.password is not None:
                raise ValueError("production Milvus URI must be credential-free")
            if milvus_endpoint.scheme.lower() != "https":
                raise ValueError("production IMAGE index workers require Milvus TLS")
            if (
                milvus_endpoint.hostname is None
                or milvus_endpoint.path
                or milvus_endpoint.query
                or milvus_endpoint.fragment
            ):
                raise ValueError(
                    "production Milvus endpoint must be an origin without path, query, or fragment"
                )
            assert self.milvus_token is not None
            if self.milvus_token.get_secret_value().strip().casefold() == "root:milvus":
                raise ValueError(
                    "production IMAGE index workers reject the default Milvus credential"
                )
        c2pa_anchors = self.c2pa_trust_anchors_pem
        c2pa_eku = self.c2pa_trust_eku_policy
        if (c2pa_anchors is None) != (c2pa_eku is None):
            raise ValueError("C2PA trust anchors and EKU policy must be configured together")
        if self.asset_provenance_adapter == "c2pa" and (c2pa_anchors is None or c2pa_eku is None):
            raise ValueError("C2PA trust anchors and EKU policy are required")
        if self.retrieval_run_retention_seconds <= self.retrieval_preview_token_lifetime_seconds:
            raise ValueError("Retrieval Run retention must exceed preview token lifetime")
        if self.environment == "production" and self.worker_requires_asset_validation:
            if OperationKind.ASSET_VALIDATION not in self.worker_required_operation_kinds:
                raise ValueError("production Asset workers must require ASSET_VALIDATION")
            if OperationKind.PRODUCT_BRIEF_ANALYSIS not in self.worker_required_operation_kinds:
                raise ValueError("production Asset workers must require PRODUCT_BRIEF_ANALYSIS")
            if (
                self.asset_malware_adapter != "clamav"
                or self.asset_content_safety_adapter != "alibaba"
                or self.asset_provenance_adapter != "c2pa"
            ):
                raise ValueError(
                    "production Asset validation requires ClamAV, Alibaba, and C2PA adapters"
                )
        return self

    @property
    def object_store_buckets(self) -> dict[StorageLocationClass, str]:
        return {
            StorageLocationClass.QUARANTINE: self.object_store_quarantine_bucket,
            StorageLocationClass.TASK: self.object_store_task_bucket,
            StorageLocationClass.FOUNDATION: self.object_store_foundation_bucket,
            StorageLocationClass.PROVIDER_RESULT: self.object_store_provider_result_bucket,
        }

    @property
    def configured_worker_queues(self) -> tuple[str, ...]:
        if self.worker_queues is not None:
            return tuple(self.worker_queues)
        return (
            self.workflow_queue_name,
            self.asset_queue_name,
            self.index_queue_name,
            self.maintenance_queue_name,
        )

    @property
    def worker_requires_object_storage(self) -> bool:
        return bool(
            {
                self.asset_queue_name,
                self.index_queue_name,
                self.maintenance_queue_name,
            }.intersection(self.configured_worker_queues)
        )

    @property
    def worker_requires_asset_validation(self) -> bool:
        return self.asset_queue_name in self.configured_worker_queues

    @property
    def worker_readiness_cycle_budget_seconds(self) -> float:
        mysql_query_budget = WORKER_READINESS_MYSQL_QUERY_TIMEOUT_SECONDS * (
            2 if self.worker_requires_asset_validation else 1
        )
        current_storage_budget = 0.0
        if self.worker_requires_object_storage:
            credential_budget = (
                self.object_store_credential_refresh_timeout_seconds
                if self.object_store_credential_mode != "static"
                else 0.0
            )
            current_storage_budget = _OBJECT_STORAGE_READINESS_REQUEST_BOUND * (
                credential_budget
                + _OBJECT_STORAGE_READINESS_TIMEOUT_PHASE_BOUND
                * self.object_store_readiness_timeout_seconds
            )

        historical_storage_budget = 0.0
        for target in self.provider_artifact_reconciliation_targets:
            credential_mode = (
                target.object_store_credential_mode or self.object_store_credential_mode
            )
            credential_budget = (
                (
                    target.object_store_credential_refresh_timeout_seconds
                    or self.object_store_credential_refresh_timeout_seconds
                )
                if credential_mode != "static"
                else 0.0
            )
            readiness_timeout = (
                target.object_store_readiness_timeout_seconds
                or self.object_store_readiness_timeout_seconds
            )
            target_budget = _OBJECT_STORAGE_READINESS_REQUEST_BOUND * (
                credential_budget
                + _OBJECT_STORAGE_READINESS_TIMEOUT_PHASE_BOUND * readiness_timeout
            )
            historical_storage_budget = max(
                historical_storage_budget,
                target_budget,
            )

        malware_scanner_budget = (
            self.clamav_timeout_seconds
            if self.worker_requires_asset_validation and self.asset_malware_adapter == "clamav"
            else 0.0
        )
        return (
            WORKER_READINESS_PROBE_INTERVAL_SECONDS
            + WORKER_READINESS_BROKER_CONNECT_TIMEOUT_SECONDS
            + self.mysql_connect_timeout_seconds
            + mysql_query_budget
            + current_storage_budget
            + historical_storage_budget
            + malware_scanner_budget
            + WORKER_READINESS_PUBLICATION_MARGIN_SECONDS
        )

    @property
    def alibaba_vision_endpoint_host(self) -> str:
        hostname = urlsplit(self.alibaba_vision_endpoint).hostname
        assert hostname is not None
        return validate_canonical_endpoint_host(hostname)

    @property
    def alibaba_embedding_endpoint_host(self) -> str:
        hostname = urlsplit(self.alibaba_embedding_endpoint).hostname
        assert hostname is not None
        return validate_canonical_endpoint_host(hostname)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        secret_settings = SecretsSettingsSource(
            settings_cls,
            secrets_dir=_secret_directories() or None,
        )
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            secret_settings,
            YamlConfigSettingsSource(settings_cls),
        )


def load_settings(service_name: str) -> Settings:
    """Load settings with a service-specific default name."""

    return Settings(service_name=service_name)
