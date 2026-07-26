"""Built-in Asset validation dependency composition."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from commercevision_application import (
    AssetValidationExecutor,
    AssetValidationExecutorPolicy,
    DeterministicContentSafetyRequestFactory,
    PresignedContentSafetyRequestFactory,
    ValidationDataTransferPolicy,
)
from commercevision_application.asset_integrity import UploadIntegrityVerifier
from commercevision_application.asset_local_validation import AssetLocalValidator
from commercevision_application.asset_promotion import UploadPromoter
from commercevision_contracts import Settings
from commercevision_contracts.object_storage import ObjectStorage
from commercevision_contracts.validation import (
    ContentSafetyOutcome,
    MalwareScanOutcome,
    ProvenanceEvidenceStatus,
)
from commercevision_persistence import (
    Database,
    SqlAlchemyAssetUnitOfWork,
    is_unit_of_work_active,
)
from commercevision_providers import (
    AlibabaImageModerationAdapter,
    C2paProvenanceAdapter,
    ClamdMalwareScanner,
    DeterministicContentSafetyAdapter,
    DeterministicMalwareScanner,
    DeterministicProvenanceAdapter,
)

from .asset_validation_observability import AssetValidationTelemetry


@dataclass(frozen=True, slots=True)
class BuiltAssetValidationExecutor:
    executor: AssetValidationExecutor
    closeables: tuple[object, ...]


def build_malware_scanner(
    settings: Settings,
) -> ClamdMalwareScanner | DeterministicMalwareScanner:
    if settings.asset_malware_adapter == "deterministic":
        return DeterministicMalwareScanner(outcome=MalwareScanOutcome.CLEAN)
    return ClamdMalwareScanner(
        host=settings.clamav_host,
        port=settings.clamav_port,
        timeout_seconds=settings.clamav_timeout_seconds,
        maximum_concurrency=settings.clamav_maximum_concurrency,
        stream_max_bytes=settings.clamav_stream_max_bytes,
        chunk_bytes=settings.clamav_chunk_bytes,
        maximum_response_bytes=settings.clamav_maximum_response_bytes,
    )


def build_asset_validation_executor(
    *,
    settings: Settings,
    database: Database,
    storage: ObjectStorage,
) -> BuiltAssetValidationExecutor:
    local_validator = AssetLocalValidator(
        maximum_image_bytes=settings.upload_max_bytes,
        maximum_image_dimension=settings.upload_max_image_dimension,
        maximum_image_pixels=settings.upload_max_image_pixels,
        maximum_image_frames=settings.upload_max_image_frames,
        maximum_image_decoded_bytes=(settings.asset_validation_image_decoded_max_bytes),
        maximum_metadata_bytes=settings.upload_max_metadata_bytes,
        maximum_lora_bytes=settings.upload_max_lora_bytes,
        maximum_safetensors_header_bytes=(settings.asset_validation_safetensors_header_max_bytes),
        maximum_safetensors_tensors=(settings.asset_validation_safetensors_max_tensors),
        maximum_safetensors_rank=settings.asset_validation_safetensors_max_rank,
        maximum_safetensors_dimension=(settings.asset_validation_safetensors_max_dimension),
        maximum_safetensors_elements=(settings.asset_validation_safetensors_max_elements),
        maximum_prompt_bytes=settings.upload_max_prompt_template_bytes,
        maximum_model_configuration_bytes=(settings.upload_max_model_configuration_bytes),
        maximum_json_depth=settings.asset_validation_json_maximum_depth,
        maximum_json_nodes=settings.asset_validation_json_maximum_nodes,
    )
    verifier = UploadIntegrityVerifier(
        storage=storage,
        transaction_active=is_unit_of_work_active,
        maximum_bytes=settings.upload_max_bytes,
        maximum_dimension=settings.upload_max_image_dimension,
        maximum_pixels=settings.upload_max_image_pixels,
        maximum_frames=settings.upload_max_image_frames,
        maximum_metadata_bytes=settings.upload_max_metadata_bytes,
        maximum_lora_bytes=settings.upload_max_lora_bytes,
        maximum_prompt_template_bytes=settings.upload_max_prompt_template_bytes,
        maximum_model_configuration_bytes=(settings.upload_max_model_configuration_bytes),
    )
    malware = build_malware_scanner(settings)
    closeables: list[object] = []
    if settings.asset_content_safety_adapter == "deterministic":
        content_safety = DeterministicContentSafetyAdapter(
            outcome=ContentSafetyOutcome(settings.deterministic_content_safety_outcome),
            policy_version=settings.content_safety_policy_version,
            mapping_version=settings.content_safety_mapping_version,
        )
        content_request_factory = DeterministicContentSafetyRequestFactory()
    else:
        access_key_id = settings.alibaba_content_safety_access_key_id
        access_key_secret = settings.alibaba_content_safety_access_key_secret
        assert access_key_id is not None
        assert access_key_secret is not None
        content_safety = AlibabaImageModerationAdapter.from_credentials(
            access_key_id=access_key_id.get_secret_value(),
            access_key_secret=access_key_secret.get_secret_value(),
            endpoint=settings.alibaba_content_safety_endpoint,
            service=settings.alibaba_content_safety_service,
            sdk_version=settings.alibaba_content_safety_sdk_version,
            policy_version=settings.content_safety_policy_version,
            mapping_version=settings.content_safety_mapping_version,
            risk_mapping={
                "none": ContentSafetyOutcome.PASS,
                "low": ContentSafetyOutcome.PASS,
                "medium": ContentSafetyOutcome.REVIEW,
                "high": ContentSafetyOutcome.BLOCK,
            },
            connect_timeout_seconds=(settings.alibaba_content_safety_connect_timeout_seconds),
            read_timeout_seconds=settings.alibaba_content_safety_read_timeout_seconds,
            end_to_end_timeout_seconds=(settings.alibaba_content_safety_end_to_end_timeout_seconds),
            maximum_concurrency=(settings.alibaba_content_safety_maximum_concurrency),
            minimum_url_validity_seconds=(
                settings.alibaba_content_safety_minimum_url_validity_seconds
            ),
            allowed_url_origins=frozenset(settings.alibaba_content_safety_allowed_url_origins),
            clock=lambda: datetime.now(UTC),
        )
        content_request_factory = PresignedContentSafetyRequestFactory(
            storage,
            provider="alibaba-green",
            endpoint_region=settings.alibaba_content_safety_endpoint_region,
        )
        closeables.append(content_safety)

    if settings.asset_provenance_adapter == "deterministic":
        provenance = DeterministicProvenanceAdapter(
            status=ProvenanceEvidenceStatus(settings.deterministic_provenance_status),
            trust_config_version=settings.c2pa_trust_config_version,
        )
    else:
        trust_anchors = settings.c2pa_trust_anchors_pem
        trust_eku = settings.c2pa_trust_eku_policy
        assert trust_anchors is not None
        assert trust_eku is not None
        provenance = C2paProvenanceAdapter.from_runtime(
            trust_config_version=settings.c2pa_trust_config_version,
            trust_anchors_pem=trust_anchors.get_secret_value(),
            trust_eku_policy=trust_eku.get_secret_value(),
            timeout_seconds=settings.c2pa_timeout_seconds,
            maximum_concurrency=settings.c2pa_maximum_concurrency,
            maximum_asset_bytes=settings.upload_max_bytes,
            maximum_report_bytes=settings.c2pa_maximum_report_bytes,
            maximum_report_depth=settings.c2pa_maximum_report_depth,
            maximum_report_nodes=settings.c2pa_maximum_report_nodes,
            maximum_manifests=settings.c2pa_maximum_manifests,
            maximum_status_codes=settings.c2pa_maximum_status_codes,
            subprocess_memory_limit_bytes=(settings.c2pa_subprocess_memory_limit_bytes),
            subprocess_file_descriptor_limit=(settings.c2pa_subprocess_file_descriptor_limit),
        )
        closeables.append(provenance)

    executor = AssetValidationExecutor(
        uow_factory=lambda: SqlAlchemyAssetUnitOfWork(database.session_factory),
        storage=storage,
        local_validator=local_validator,
        malware_scanner=malware,
        content_safety=content_safety,
        content_safety_request_factory=content_request_factory,
        provenance=provenance,
        promoter=UploadPromoter(
            storage=storage,
            verifier=verifier,
            retention_version_page_size=(settings.asset_retention_cleanup_version_page_size),
            retention_max_version_pages=(settings.asset_retention_cleanup_max_version_pages),
            retention_max_versions=settings.asset_retention_cleanup_max_versions,
            retention_stable_empty_passes=(settings.asset_retention_cleanup_stable_empty_passes),
        ),
        validation_transfer_policy=ValidationDataTransferPolicy.from_settings(settings),
        observer=AssetValidationTelemetry(),
        policy=AssetValidationExecutorPolicy(
            content_reference_lifetime=timedelta(
                seconds=settings.asset_validation_content_reference_lifetime_seconds
            ),
            content_reference_minimum_validity=timedelta(
                seconds=settings.alibaba_content_safety_minimum_url_validity_seconds
            ),
        ),
    )
    return BuiltAssetValidationExecutor(
        executor=executor,
        closeables=tuple(closeables),
    )
