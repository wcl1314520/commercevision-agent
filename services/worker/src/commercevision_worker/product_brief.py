"""Built-in ProductBrief Vision dependency composition."""

from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from commercevision_application import (
    ProductBriefAnalysisExecutor,
    ProductBriefPolicy,
    ProductBriefProviderArtifactReconciler,
    ProductBriefProviderArtifactService,
    VisionDataTransferPolicy,
)
from commercevision_application.product_brief_ports import (
    ProviderArtifactTargetReadinessQuery,
)
from commercevision_contracts import Settings
from commercevision_contracts.config import (
    PROVIDER_ARTIFACT_READINESS_TARGET_LIMIT,
    ProviderArtifactReconciliationTargetSettings,
)
from commercevision_contracts.object_storage import ObjectStorage
from commercevision_contracts.product_briefs import VisionAnalyzer
from commercevision_object_storage import (
    ObjectStorageProviderArtifactSink,
    ObjectStorageProviderArtifactTarget,
    ObjectStorageProviderArtifactTargetRegistry,
    build_object_storage,
    close_object_storage,
)
from commercevision_observability import ProductBriefTelemetry
from commercevision_persistence import (
    Database,
    SqlAlchemyProductBriefUnitOfWork,
    SqlAlchemyProviderArtifactTargetReadinessQuery,
)
from commercevision_providers import (
    AlibabaVisionAnalyzer,
    DeterministicVisionAnalyzer,
    DeterministicVisionScenario,
    MountedFileVisionApiKeyProvider,
    StaticVisionApiKeyProvider,
    VisionApiKeyProvider,
)


@dataclass(frozen=True, slots=True)
class BuiltProductBriefExecutor:
    executor: ProductBriefAnalysisExecutor
    closeables: tuple[object, ...]
    artifact_reconciler: ProductBriefProviderArtifactReconciler | None = None


@dataclass(slots=True)
class _ProductBriefArtifactTargets:
    artifact_reader: ObjectStorageProviderArtifactTargetRegistry
    required_historical_targets: tuple[ObjectStorageProviderArtifactTarget, ...]
    historical_targets: tuple[ObjectStorageProviderArtifactTarget, ...]
    _closed: bool = False

    def assert_ready(self) -> None:
        if not self.required_historical_targets:
            return
        with ThreadPoolExecutor(
            max_workers=len(self.required_historical_targets),
            thread_name_prefix="provider-artifact-readiness",
        ) as executor:
            futures = tuple(
                executor.submit(_assert_historical_artifact_target_ready, target)
                for target in self.required_historical_targets
            )

        failures: list[Exception] = []
        for future in futures:
            try:
                future.result()
            except Exception as exc:
                failures.append(exc)
        if len(failures) == 1:
            raise failures[0]
        if failures:
            raise ExceptionGroup(
                "provider artifact target readiness failed",
                failures,
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _close_historical_artifact_targets(self.historical_targets)


def _assert_historical_artifact_target_ready(
    target: ObjectStorageProviderArtifactTarget,
) -> None:
    assert_ready = getattr(target.storage, "assert_ready", None)
    if not callable(assert_ready):
        raise RuntimeError("provider artifact reconciliation target has no readiness capability")
    assert_ready((target.location,))


def _close_historical_artifact_targets(
    targets: Iterable[ObjectStorageProviderArtifactTarget],
) -> None:
    failures: list[Exception] = []
    closed_storage_ids: set[int] = set()
    for target in targets:
        storage_identity = id(target.storage)
        if storage_identity in closed_storage_ids:
            continue
        closed_storage_ids.add(storage_identity)
        try:
            close_object_storage(target.storage)
        except Exception as exc:
            failures.append(exc)
    if failures:
        raise ExceptionGroup("provider artifact target shutdown failed", failures)


def _historical_target_settings(
    settings: Settings,
    target_settings: ProviderArtifactReconciliationTargetSettings,
) -> Settings:
    values = settings.model_dump()
    values.update(target_settings.model_dump(exclude_unset=True))
    values["provider_artifact_reconciliation_targets"] = []
    return Settings.model_validate(values)


def _configured_historical_artifact_targets(
    settings: Settings,
) -> tuple[ObjectStorageProviderArtifactTarget, ...]:
    targets: list[ObjectStorageProviderArtifactTarget] = []
    try:
        for target_settings in settings.provider_artifact_reconciliation_targets:
            storage_settings = _historical_target_settings(settings, target_settings)
            storage = build_object_storage(storage_settings)
            try:
                target = ObjectStorageProviderArtifactTarget(
                    storage=storage,
                    bucket=storage_settings.object_store_provider_result_bucket,
                )
            except Exception as target_error:
                try:
                    close_object_storage(storage)
                except Exception as close_error:
                    raise ExceptionGroup(
                        "provider artifact target construction failed",
                        [target_error, close_error],
                    ) from target_error
                raise
            targets.append(target)
    except Exception as build_error:
        failures: list[Exception] = [build_error]
        for target in targets:
            try:
                close_object_storage(target.storage)
            except Exception as close_error:
                failures.append(close_error)
        if len(failures) == 1:
            raise
        raise ExceptionGroup(
            "provider artifact target construction failed",
            failures,
        ) from build_error
    return tuple(targets)


def _build_artifact_targets(
    *,
    settings: Settings,
    database: Database,
    storage: ObjectStorage,
    artifact_target_readiness_query: ProviderArtifactTargetReadinessQuery | None,
    additional_artifact_targets: Iterable[ObjectStorageProviderArtifactTarget] | None,
) -> _ProductBriefArtifactTargets:
    historical_targets = (
        _configured_historical_artifact_targets(settings)
        if additional_artifact_targets is None
        else tuple(additional_artifact_targets)
    )
    try:
        if len(historical_targets) + 1 > PROVIDER_ARTIFACT_READINESS_TARGET_LIMIT:
            raise RuntimeError("provider artifact target registrations exceed the configured bound")
        current_target = ObjectStorageProviderArtifactTarget(
            storage=storage,
            bucket=settings.object_store_provider_result_bucket,
        )
        registered_targets = (current_target, *historical_targets)
        artifact_reader = ObjectStorageProviderArtifactTargetRegistry(registered_targets)
        readiness_query = (
            artifact_target_readiness_query
            or SqlAlchemyProviderArtifactTargetReadinessQuery(database.session_factory)
        )
        registered_by_identity = {target.physical_target: target for target in registered_targets}
        historical_target_identities = {target.physical_target for target in historical_targets}
        required_historical_targets: list[ObjectStorageProviderArtifactTarget] = []
        reconciliation_targets = readiness_query.list_reconciliation_targets(
            limit=len(registered_targets)
        )
        if len(reconciliation_targets) > len(registered_targets):
            raise RuntimeError(
                "provider artifact reconciliation targets exceed the configured bound"
            )
        for reconciliation_target in reconciliation_targets:
            registered = registered_by_identity.get(reconciliation_target)
            if registered is None:
                raise RuntimeError(
                    "provider artifact reconciliation target registration is incomplete; "
                    "refusing to start"
                )
            if reconciliation_target in historical_target_identities:
                required_historical_targets.append(registered)
        return _ProductBriefArtifactTargets(
            artifact_reader=artifact_reader,
            required_historical_targets=tuple(required_historical_targets),
            historical_targets=historical_targets,
        )
    except Exception as build_error:
        try:
            _close_historical_artifact_targets(historical_targets)
        except Exception as close_error:
            raise ExceptionGroup(
                "provider artifact target composition failed",
                [build_error, close_error],
            ) from build_error
        raise


def build_provider_artifact_targets(
    *,
    settings: Settings,
    database: Database,
    storage: ObjectStorage,
) -> _ProductBriefArtifactTargets:
    """Build the exact current and historical registry shared by retention cleanup."""

    return _build_artifact_targets(
        settings=settings,
        database=database,
        storage=storage,
        artifact_target_readiness_query=None,
        additional_artifact_targets=None,
    )


def probe_product_brief_artifact_targets(
    *,
    settings: Settings,
    database: Database,
    storage: ObjectStorage,
    artifact_target_readiness_query: ProviderArtifactTargetReadinessQuery | None = None,
    additional_artifact_targets: Iterable[ObjectStorageProviderArtifactTarget] | None = None,
) -> None:
    """Probe only exact physical targets needed by unsettled artifact ledger rows."""

    targets = _build_artifact_targets(
        settings=settings,
        database=database,
        storage=storage,
        artifact_target_readiness_query=artifact_target_readiness_query,
        additional_artifact_targets=additional_artifact_targets,
    )
    try:
        targets.assert_ready()
    except Exception as readiness_error:
        try:
            targets.close()
        except Exception as close_error:
            raise ExceptionGroup(
                "provider artifact target readiness and shutdown failed",
                [readiness_error, close_error],
            ) from readiness_error
        raise
    targets.close()


def _build_vision_credential_provider(settings: Settings) -> VisionApiKeyProvider:
    api_key_file = settings.alibaba_vision_api_key_file
    if api_key_file is not None:
        return MountedFileVisionApiKeyProvider(
            path=api_key_file,
            maximum_bytes=settings.alibaba_vision_api_key_file_max_bytes,
        )
    api_key = settings.alibaba_vision_api_key
    if api_key is None:
        raise RuntimeError("Alibaba Vision API key is not configured")
    return StaticVisionApiKeyProvider(api_key.get_secret_value())


def validate_product_brief_vision_credential(settings: Settings) -> str:
    """Validate the same credential source used immediately before dispatch."""

    if (
        settings.vision_adapter != "alibaba"
        or settings.asset_queue_name not in settings.configured_worker_queues
    ):
        return "not_required"
    _build_vision_credential_provider(settings).resolve()
    return "ok"


def build_product_brief_executor(
    *,
    settings: Settings,
    database: Database,
    storage: ObjectStorage,
    artifact_target_readiness_query: ProviderArtifactTargetReadinessQuery | None = None,
    additional_artifact_targets: Iterable[ObjectStorageProviderArtifactTarget] | None = None,
) -> BuiltProductBriefExecutor:
    def uow_factory() -> SqlAlchemyProductBriefUnitOfWork:
        return SqlAlchemyProductBriefUnitOfWork(database.session_factory)

    artifact_sink = ObjectStorageProviderArtifactSink(
        storage,
        bucket=settings.object_store_provider_result_bucket,
    )
    artifact_service = ProductBriefProviderArtifactService(
        uow_factory=uow_factory,
        artifact_store=artifact_sink,
        clock=lambda: datetime.now(UTC),
    )
    artifact_targets = _build_artifact_targets(
        settings=settings,
        database=database,
        storage=storage,
        artifact_target_readiness_query=artifact_target_readiness_query,
        additional_artifact_targets=additional_artifact_targets,
    )
    analyzer: VisionAnalyzer | None = None
    try:
        artifact_reconciler = ProductBriefProviderArtifactReconciler(
            uow_factory=uow_factory,
            artifact_reader=artifact_targets.artifact_reader,
            artifact_store=artifact_sink,
            clock=lambda: datetime.now(UTC),
        )
        closeables: tuple[object, ...] = (
            (artifact_targets,) if artifact_targets.historical_targets else ()
        )
        if settings.vision_adapter == "deterministic":
            analyzer = DeterministicVisionAnalyzer(
                scenario=DeterministicVisionScenario(
                    settings.deterministic_vision_scenario.upper()
                ),
                artifact_sink=artifact_sink,
                prompt_version=settings.vision_prompt_version,
                maximum_output_tokens=settings.alibaba_vision_maximum_output_tokens,
                product_facts_maximum_bytes=settings.vision_product_facts_maximum_bytes,
                product_facts_maximum_depth=settings.vision_product_facts_maximum_depth,
                product_facts_maximum_nodes=settings.vision_product_facts_maximum_nodes,
                product_facts_maximum_string_bytes=(
                    settings.vision_product_facts_maximum_string_bytes
                ),
            )
        else:
            analyzer = AlibabaVisionAnalyzer(
                credential_provider=_build_vision_credential_provider(settings),
                endpoint=settings.alibaba_vision_endpoint,
                endpoint_region=settings.alibaba_vision_endpoint_region,
                requested_model=settings.alibaba_vision_model,
                configured_snapshot=settings.alibaba_vision_model_snapshot,
                prompt_version=settings.vision_prompt_version,
                adapter_version=settings.alibaba_vision_adapter_version,
                connect_timeout_seconds=settings.alibaba_vision_connect_timeout_seconds,
                read_timeout_seconds=settings.alibaba_vision_read_timeout_seconds,
                end_to_end_timeout_seconds=(settings.alibaba_vision_end_to_end_timeout_seconds),
                maximum_concurrency=settings.alibaba_vision_maximum_concurrency,
                maximum_response_bytes=settings.alibaba_vision_maximum_response_bytes,
                maximum_output_tokens=settings.alibaba_vision_maximum_output_tokens,
                product_facts_maximum_bytes=settings.vision_product_facts_maximum_bytes,
                product_facts_maximum_depth=settings.vision_product_facts_maximum_depth,
                product_facts_maximum_nodes=settings.vision_product_facts_maximum_nodes,
                product_facts_maximum_string_bytes=(
                    settings.vision_product_facts_maximum_string_bytes
                ),
                maximum_repair_attempts=settings.alibaba_vision_maximum_repair_attempts,
                allowed_image_origins=frozenset(settings.alibaba_vision_allowed_image_origins),
                artifact_sink=artifact_sink,
            )
            closeables = (*closeables, analyzer)
        return BuiltProductBriefExecutor(
            executor=ProductBriefAnalysisExecutor(
                uow_factory=uow_factory,
                object_storage=storage,
                analyzer=analyzer,
                policy=ProductBriefPolicy.from_settings(settings),
                transfer_policy=VisionDataTransferPolicy.from_settings(settings),
                artifact_service=artifact_service,
                artifact_reconciler=artifact_reconciler,
                observer=ProductBriefTelemetry(),
                submission_reserve=timedelta(
                    seconds=(
                        settings.alibaba_vision_end_to_end_timeout_seconds
                        + settings.vision_operation_lease_margin_seconds
                    )
                ),
            ),
            closeables=closeables,
            artifact_reconciler=artifact_reconciler,
        )
    except Exception as build_error:
        cleanup_errors: list[Exception] = []
        if analyzer is not None:
            close_analyzer = getattr(analyzer, "close", None)
            if callable(close_analyzer):
                try:
                    close_analyzer()
                except Exception as close_error:
                    cleanup_errors.append(close_error)
        try:
            artifact_targets.close()
        except Exception as close_error:
            cleanup_errors.append(close_error)
        if cleanup_errors:
            raise ExceptionGroup(
                "ProductBrief executor composition failed",
                [build_error, *cleanup_errors],
            ) from build_error
        raise
