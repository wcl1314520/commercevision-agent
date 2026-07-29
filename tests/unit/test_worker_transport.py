from __future__ import annotations

import json
import os
from dataclasses import replace
from importlib import import_module
from pathlib import Path

import pytest
from celery.worker.worker import WorkController
from commercevision_application import (
    EventRoutingError,
    MalformedEventPayloadError,
    UnknownEventTypeError,
    UnsupportedSchemaVersionError,
)
from commercevision_contracts import Settings
from commercevision_contracts.events import (
    ASSET_RIGHTS_CHANGED_V1,
    ASSET_UPLOAD_FINALIZED_V1,
    ASSET_VALIDATION_COMPLETED_V1,
    ASSET_VALIDATION_FAILED_V1,
    AssetRightsChangedPayload,
    AssetUploadFinalizedPayload,
    AssetValidationCompletedPayload,
    AssetValidationFailedPayload,
)
from commercevision_domain import OperationKind, StorageBackend
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from commercevision_worker.executors import available_builtin_operation_kinds
from commercevision_worker.runtime import WorkerRuntime
from pydantic import ValidationError

worker_module = import_module("commercevision_worker.celery_app")
executor_module = import_module("commercevision_worker.executors")
product_brief_module = import_module("commercevision_worker.product_brief")
runtime_module = import_module("commercevision_worker.runtime")


class FakeOperationExecutorRegistry:
    registered_kinds = frozenset(
        {
            OperationKind.ASSET_VALIDATION,
            OperationKind.PRODUCT_BRIEF_ANALYSIS,
        }
    )

    def missing(
        self,
        required: frozenset[OperationKind],
    ) -> frozenset[OperationKind]:
        return required.difference(self.registered_kinds)


class FakeRuntime:
    def __init__(self) -> None:
        self.closed = False
        self.local_readiness_checks = 0
        self.local_readiness_error: Exception | None = None
        self.operation_executors = FakeOperationExecutorRegistry()

    def process_event(self, event_id: str) -> str:
        return f"handled:{event_id}"

    def assert_local_resources_ready(self) -> None:
        self.local_readiness_checks += 1
        if self.local_readiness_error is not None:
            raise self.local_readiness_error

    def operation_executor_readiness(self) -> dict[str, object]:
        return {
            "ready": True,
            "required_kinds": [
                OperationKind.ASSET_VALIDATION.value,
                OperationKind.PRODUCT_BRIEF_ANALYSIS.value,
            ],
            "registered_kinds": [
                OperationKind.ASSET_VALIDATION.value,
                OperationKind.PRODUCT_BRIEF_ANALYSIS.value,
            ],
            "missing_kinds": [],
        }

    def close(self) -> None:
        self.closed = True


class FakeWorkerController:
    def __init__(self) -> None:
        self.stop_exitcodes: list[int | None] = []

    def stop(self, *, exitcode: int | None = None) -> None:
        self.stop_exitcodes.append(exitcode)


class FakeWorkerConsumer:
    def __init__(self) -> None:
        self.controller = FakeWorkerController()


class FakeExecutor:
    def execute(self, request):  # pragma: no cover - bootstrap contract only
        raise AssertionError(f"unexpected execution for {request.operation_id}")

    def reconcile(self, request):  # pragma: no cover - bootstrap contract only
        raise AssertionError(f"unexpected reconciliation for {request.operation_id}")


class FakeExecutorEntryPoint:
    name = OperationKind.ASSET_VALIDATION.value

    def __init__(self, executor: FakeExecutor) -> None:
        self._executor = executor

    def load(self):
        return lambda _settings: self._executor


def _production_worker_settings(
    readiness_path: Path,
    *,
    queue: str = "commercevision.asset",
    required_kind: OperationKind = OperationKind.ASSET_VALIDATION,
    readiness_max_age_seconds: float | None = None,
) -> Settings:
    values = {
        "environment": "production",
        "object_store_endpoint": "https://object-storage.internal.example",
        "object_store_presign_endpoint": "https://uploads.example",
        "object_store_secret_key": "production-worker-object-store-secret",
        "object_store_require_encryption": True,
        "worker_queues": [queue],
        "worker_required_operation_kinds": [required_kind],
        "worker_readiness_path": str(readiness_path),
        "asset_malware_adapter": "clamav",
        "asset_content_safety_adapter": "alibaba",
        "alibaba_content_safety_access_key_id": "test-access-key-id",
        "alibaba_content_safety_access_key_secret": "test-access-key-secret",
        "alibaba_content_safety_allowed_url_origins": ["https://uploads.example"],
        "validation_data_transfer_enabled": True,
        "validation_data_transfer_policy_version": "enterprise-validation-transfer-v1",
        "validation_data_transfer_allowed_workspace_ids": ["production-workspace"],
        "validation_data_transfer_allowed_asset_kinds": ["IMAGE"],
        "validation_data_transfer_allowed_retention_classes": ["TASK", "FOUNDATION"],
        "validation_data_transfer_allowed_providers": ["alibaba-green"],
        "validation_data_transfer_allowed_endpoint_regions": ["cn-shanghai"],
        "validation_data_transfer_allowed_endpoint_hosts": ["green-cip.cn-shanghai.aliyuncs.com"],
        "asset_provenance_adapter": "c2pa",
        "c2pa_trust_anchors_pem": "test-trust-anchor",
        "c2pa_trust_eku_policy": "test-eku-policy",
    }
    if readiness_max_age_seconds is not None:
        values["worker_readiness_max_age_seconds"] = readiness_max_age_seconds
    if queue == "commercevision.asset":
        api_key_path = (readiness_path.parent / "model-studio-api-key").resolve()
        api_key_path.parent.mkdir(parents=True, exist_ok=True)
        api_key_path.write_text("test-mounted-vision-key\n", encoding="utf-8")
        values.update(
            {
                "worker_required_operation_kinds": [
                    OperationKind.ASSET_VALIDATION,
                    OperationKind.PRODUCT_BRIEF_ANALYSIS,
                ],
                "vision_adapter": "alibaba",
                "alibaba_vision_api_key_file": str(api_key_path),
                "alibaba_vision_model": "qwen3-vl-plus",
                "alibaba_vision_model_snapshot": "qwen3-vl-plus-2025-12-19",
                "alibaba_vision_allowed_image_origins": ["https://uploads.example"],
                "vision_data_transfer_enabled": True,
                "vision_data_transfer_policy_version": ("enterprise-vision-transfer-v1"),
                "vision_data_transfer_allowed_workspace_ids": ["production-workspace"],
                "vision_data_transfer_allowed_retention_classes": [
                    "TASK",
                    "FOUNDATION",
                ],
                "vision_data_transfer_allowed_providers": ["alibaba-model-studio"],
                "vision_data_transfer_allowed_endpoint_regions": ["cn-beijing"],
                "vision_data_transfer_allowed_endpoint_hosts": ["dashscope.aliyuncs.com"],
            }
        )
    return Settings(**values)


def test_product_brief_worker_composes_mounted_rotating_vision_credential(
    monkeypatch,
    tmp_path: Path,
) -> None:
    api_key_path = tmp_path / "model-studio-api-key"
    api_key_path.write_text("rotating-test-key\n", encoding="utf-8")
    captured: dict[str, object] = {}

    class CapturingAlibabaVisionAnalyzer:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def close(self) -> None:
            pass

    class ReadyArtifactTargetQuery:
        def list_reconciliation_targets(self, *, limit: int) -> tuple[object, ...]:
            assert limit == 1
            return ()

    monkeypatch.setattr(
        product_brief_module,
        "AlibabaVisionAnalyzer",
        CapturingAlibabaVisionAnalyzer,
    )
    settings = Settings(
        vision_adapter="alibaba",
        worker_required_operation_kinds=[OperationKind.PRODUCT_BRIEF_ANALYSIS],
        alibaba_vision_api_key_file=str(api_key_path),
        alibaba_vision_api_key_file_max_bytes=256,
        alibaba_vision_maximum_output_tokens=2048,
        vision_product_facts_maximum_bytes=32 * 1024,
        vision_product_facts_maximum_depth=6,
        vision_product_facts_maximum_nodes=512,
        vision_product_facts_maximum_string_bytes=2048,
        alibaba_vision_allowed_image_origins=["https://assets.example.com"],
        vision_data_transfer_enabled=True,
        vision_data_transfer_allowed_workspace_ids=["Catalog-A"],
        vision_data_transfer_allowed_retention_classes=["TASK"],
        vision_data_transfer_allowed_providers=["alibaba-model-studio"],
        vision_data_transfer_allowed_endpoint_regions=["cn-beijing"],
        vision_data_transfer_allowed_endpoint_hosts=["dashscope.aliyuncs.com"],
    )

    product_brief_module.build_product_brief_executor(
        settings=settings,
        database=object(),
        storage=type(
            "FakeStorage",
            (),
            {
                "backend": StorageBackend.MINIO,
                "configured_bucket": lambda _self, _location: "provider-results",
            },
        )(),
        artifact_target_readiness_query=ReadyArtifactTargetQuery(),  # type: ignore[arg-type]
    )

    credential_provider = captured["credential_provider"]
    assert credential_provider.resolve() == "rotating-test-key"
    assert "api_key" not in captured
    assert captured["maximum_output_tokens"] == 2048
    assert captured["product_facts_maximum_bytes"] == 32 * 1024
    assert captured["product_facts_maximum_depth"] == 6
    assert captured["product_facts_maximum_nodes"] == 512
    assert captured["product_facts_maximum_string_bytes"] == 2048


def test_worker_runtime_uses_canonical_product_brief_builder(monkeypatch) -> None:
    storage = object()
    executor = FakeExecutor()
    calls: list[tuple[Settings, object, object]] = []

    def build_product_brief_executor(*, settings, database, storage):
        calls.append((settings, database, storage))
        return product_brief_module.BuiltProductBriefExecutor(
            executor=executor,
            closeables=(),
        )

    monkeypatch.setattr(
        product_brief_module,
        "build_product_brief_executor",
        build_product_brief_executor,
    )
    monkeypatch.setattr(runtime_module, "build_object_storage", lambda _settings: storage)
    monkeypatch.setattr(runtime_module, "close_object_storage", lambda _storage: None)
    settings = Settings(
        environment="ci",
        worker_queues=["commercevision.asset"],
        worker_required_operation_kinds=[OperationKind.PRODUCT_BRIEF_ANALYSIS],
    )

    runtime = WorkerRuntime.build(
        settings,
        operation_executors={OperationKind.ASSET_VALIDATION: FakeExecutor()},
    )
    try:
        assert len(calls) == 1
        assert calls[0][0] is settings
        assert calls[0][2] is storage
        assert OperationKind.PRODUCT_BRIEF_ANALYSIS in (
            runtime.operation_executors.registered_kinds
        )
    finally:
        runtime.close()


def test_worker_runtime_close_is_best_effort_and_aggregates_every_failure(
    monkeypatch,
) -> None:
    close_order: list[str] = []

    class FailingResource:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            close_order.append(self.name)
            raise RuntimeError(f"{self.name} close failed")

    class FailingDatabase:
        def dispose(self) -> None:
            close_order.append("database")
            raise RuntimeError("database dispose failed")

    storage = object()

    def fail_storage_close(closed_storage: object) -> None:
        assert closed_storage is storage
        close_order.append("object-storage")
        raise RuntimeError("object storage close failed")

    monkeypatch.setattr(runtime_module, "close_object_storage", fail_storage_close)
    runtime = WorkerRuntime(
        database=FailingDatabase(),  # type: ignore[arg-type]
        settings=Settings(environment="ci"),
        worker_id="best-effort-close",
        inbox=object(),  # type: ignore[arg-type]
        agent=object(),  # type: ignore[arg-type]
        event_router=object(),  # type: ignore[arg-type]
        operation_worker=object(),  # type: ignore[arg-type]
        operation_executors=FakeOperationExecutorRegistry(),  # type: ignore[arg-type]
        object_storage=storage,  # type: ignore[arg-type]
        resources=(
            FailingResource("first"),
            FailingResource("second"),
        ),
    )

    with pytest.raises(ExceptionGroup, match="Worker runtime shutdown failed") as captured:
        runtime.close()

    assert close_order == ["second", "first", "object-storage", "database"]
    assert [str(error) for error in captured.value.exceptions] == [
        "second close failed",
        "first close failed",
        "object storage close failed",
        "database dispose failed",
    ]


def test_worker_runtime_keeps_shared_dependencies_open_until_artifact_lifecycle_drains(
    monkeypatch,
) -> None:
    close_order: list[str] = []

    class ActiveArtifactLifecycle:
        shutdown_drained = False

        @staticmethod
        def close() -> None:
            close_order.append("analyzer")
            raise TimeoutError("artifact lifecycle did not drain")

    class IndependentResource:
        @staticmethod
        def close() -> None:
            close_order.append("independent")

    class TrackingDatabase:
        @staticmethod
        def dispose() -> None:
            close_order.append("database")

    storage = object()

    def close_storage(closed_storage: object) -> None:
        assert closed_storage is storage
        close_order.append("object-storage")

    monkeypatch.setattr(runtime_module, "close_object_storage", close_storage)
    runtime = WorkerRuntime(
        database=TrackingDatabase(),  # type: ignore[arg-type]
        settings=Settings(environment="ci"),
        worker_id="drain-aware-close",
        inbox=object(),  # type: ignore[arg-type]
        agent=object(),  # type: ignore[arg-type]
        event_router=object(),  # type: ignore[arg-type]
        operation_worker=object(),  # type: ignore[arg-type]
        operation_executors=FakeOperationExecutorRegistry(),  # type: ignore[arg-type]
        object_storage=storage,  # type: ignore[arg-type]
        resources=(IndependentResource(), ActiveArtifactLifecycle()),
    )

    with pytest.raises(ExceptionGroup, match="Worker runtime shutdown failed") as captured:
        runtime.close()

    assert close_order == ["analyzer", "independent"]
    assert [str(error) for error in captured.value.exceptions] == [
        "artifact lifecycle did not drain",
        "Worker shared dependencies remain open until resource lifecycles drain",
    ]


def test_child_executor_readiness_checks_process_local_resources(monkeypatch) -> None:
    runtime = FakeRuntime()
    monkeypatch.setattr(
        worker_module,
        "settings",
        Settings(
            environment="ci",
            worker_required_operation_kinds=[
                OperationKind.ASSET_VALIDATION,
                OperationKind.PRODUCT_BRIEF_ANALYSIS,
            ],
        ),
    )

    assert worker_module._child_executor_readiness(runtime)["ready"] is True
    assert runtime.local_readiness_checks == 1


def test_production_alibaba_requires_explicit_validation_transfer_policy(
    tmp_path: Path,
) -> None:
    valid = _production_worker_settings(tmp_path / "ready")
    values = valid.model_dump()
    values["validation_data_transfer_enabled"] = False

    with pytest.raises(
        ValidationError,
        match="explicit enabled validation data transfer policy",
    ):
        Settings(**values)


def test_production_alibaba_endpoint_must_match_transfer_allowlist(
    tmp_path: Path,
) -> None:
    valid = _production_worker_settings(tmp_path / "ready")
    values = valid.model_dump()
    values["alibaba_content_safety_endpoint"] = "collector.example"

    with pytest.raises(
        ValidationError,
        match="explicit enabled validation data transfer policy",
    ):
        Settings(**values)


def test_production_asset_worker_requires_product_brief_executor(
    tmp_path: Path,
) -> None:
    valid = _production_worker_settings(tmp_path / "ready")
    values = valid.model_dump()
    values["worker_required_operation_kinds"] = [OperationKind.ASSET_VALIDATION]

    with pytest.raises(
        ValidationError,
        match="must require PRODUCT_BRIEF_ANALYSIS",
    ):
        Settings(**values)


def _dependencies_ready(settings: Settings) -> dict[str, str]:
    return {
        "broker": "ok",
        "mysql": "ok",
        "object_storage": "ok",
        "malware_scanner": "ok",
        "provider_result_storage": "ok",
        "vision_credential": (
            product_brief_module.validate_product_brief_vision_credential(settings)
        ),
    }


def test_worker_startup_rejects_invalid_mounted_vision_credential_without_echoing_it(
    monkeypatch,
    tmp_path: Path,
) -> None:
    readiness_path = tmp_path / "worker-ready.json"
    settings = _production_worker_settings(readiness_path)
    credential_path = Path(settings.alibaba_vision_api_key_file or "")
    exposed_value = "must-not-appear-in-startup-error"
    credential_path.write_text(f"{exposed_value}\nsecond-line", encoding="utf-8")

    monkeypatch.setattr(worker_module, "settings", settings)
    monkeypatch.setattr(executor_module, "entry_points", lambda **_kwargs: ())
    monkeypatch.setattr(worker_module, "probe_worker_dependencies", _dependencies_ready)

    with pytest.raises(SystemExit) as captured:
        worker_module.validate_worker_startup()

    assert exposed_value not in str(captured.value)
    assert exposed_value not in str(worker_module.worker_bootstrap_readiness()["error"])
    assert readiness_path.exists() is False


def test_deterministic_worker_does_not_require_mounted_vision_credential(
    tmp_path: Path,
) -> None:
    settings = Settings(
        environment="ci",
        worker_queues=["commercevision.asset"],
        worker_required_operation_kinds=[OperationKind.PRODUCT_BRIEF_ANALYSIS],
        vision_adapter="deterministic",
        alibaba_vision_api_key_file=str((tmp_path / "missing-key").resolve()),
    )

    assert product_brief_module.validate_product_brief_vision_credential(settings) == "not_required"


def test_alibaba_asset_worker_validates_credential_when_product_brief_is_optional(
    tmp_path: Path,
) -> None:
    credential_path = (tmp_path / "model-studio-api-key").resolve()
    credential_path.write_text("invalid\nsecond-line", encoding="utf-8")
    settings = Settings(
        environment="ci",
        worker_queues=["commercevision.asset"],
        worker_required_operation_kinds=[OperationKind.ASSET_VALIDATION],
        vision_adapter="alibaba",
        alibaba_vision_api_key_file=str(credential_path),
        vision_data_transfer_enabled=True,
        vision_data_transfer_allowed_workspace_ids=["Catalog-A"],
        vision_data_transfer_allowed_retention_classes=["TASK"],
        vision_data_transfer_allowed_providers=["alibaba-model-studio"],
        vision_data_transfer_allowed_endpoint_regions=["cn-beijing"],
        vision_data_transfer_allowed_endpoint_hosts=["dashscope.aliyuncs.com"],
    )

    with pytest.raises(RuntimeError, match="Vision API key is unavailable"):
        product_brief_module.validate_product_brief_vision_credential(settings)


def test_celery_task_delegates_without_business_retry(monkeypatch) -> None:
    runtime = FakeRuntime()
    monkeypatch.setattr(worker_module, "_get_runtime", lambda: runtime)

    assert worker_module.process_outbox_event.run("event-1") == "handled:event-1"
    assert runtime.local_readiness_checks == 2
    assert worker_module.celery_app.conf.task_acks_late is True
    assert worker_module.celery_app.conf.task_acks_on_failure_or_timeout is False
    assert worker_module.celery_app.conf.task_reject_on_worker_lost is True
    assert {queue.name for queue in worker_module.celery_app.conf.task_queues} == set(
        worker_module.settings.configured_worker_queues
    )


def test_celery_task_retires_child_when_local_resource_fails_after_processing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = Settings(
        environment="ci",
        worker_readiness_path=str(tmp_path / "worker-ready.json"),
    )

    class RuntimeThatBecomesUnhealthy(FakeRuntime):
        def process_event(self, event_id: str) -> str:
            self.local_readiness_error = RuntimeError("private provider transport failure details")
            return super().process_event(event_id)

    runtime = RuntimeThatBecomesUnhealthy()
    monkeypatch.setattr(worker_module, "settings", settings)
    monkeypatch.setattr(worker_module, "_runtime", runtime)
    worker_module._write_child_readiness_file({"ready": True})

    with pytest.raises(SystemExit, match="worker runtime became unhealthy") as captured:
        worker_module.process_outbox_event.run("event-provider-health-flip")

    assert "private provider" not in str(captured.value)
    assert runtime.local_readiness_checks == 2
    assert runtime.closed is True
    assert worker_module._runtime is None
    assert worker_module._child_readiness_path().exists() is False


def test_celery_task_preserves_business_failure_after_healthy_postflight(
    monkeypatch,
) -> None:
    class BusinessFailure(RuntimeError):
        pass

    class FailingRuntime(FakeRuntime):
        def process_event(self, event_id: str) -> str:
            raise BusinessFailure(f"business failure for {event_id}")

    runtime = FailingRuntime()
    monkeypatch.setattr(worker_module, "_get_runtime", lambda: runtime)

    with pytest.raises(BusinessFailure, match="business failure for event-failed"):
        worker_module.process_outbox_event.run("event-failed")

    assert runtime.local_readiness_checks == 2
    assert runtime.closed is False


def test_celery_task_fail_closes_when_business_and_postflight_both_fail(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = Settings(
        environment="ci",
        worker_readiness_path=str(tmp_path / "worker-ready.json"),
    )

    class BusinessAndResourceFailureRuntime(FakeRuntime):
        def process_event(self, event_id: str) -> str:
            self.local_readiness_error = RuntimeError("private provider transport failure details")
            raise RuntimeError(f"private business failure for {event_id}")

    runtime = BusinessAndResourceFailureRuntime()
    monkeypatch.setattr(worker_module, "settings", settings)
    monkeypatch.setattr(worker_module, "_runtime", runtime)
    worker_module._write_child_readiness_file({"ready": True})

    with pytest.raises(SystemExit, match="worker runtime became unhealthy") as captured:
        worker_module.process_outbox_event.run("event-dual-failure")

    assert "private provider" not in str(captured.value)
    assert "private business" not in str(captured.value)
    assert runtime.local_readiness_checks == 2
    assert runtime.closed is True
    assert worker_module._runtime is None
    assert worker_module._child_readiness_path().exists() is False


def test_asset_queue_has_a_builtin_validation_executor_and_requires_storage() -> None:
    settings = Settings(
        environment="ci",
        worker_queues=["commercevision.asset"],
        worker_required_operation_kinds=[OperationKind.ASSET_VALIDATION],
    )

    assert available_builtin_operation_kinds(settings) == {
        OperationKind.ASSET_VALIDATION,
        OperationKind.PRODUCT_BRIEF_ANALYSIS,
    }
    assert settings.worker_requires_object_storage is True


def test_workflow_only_runtime_does_not_construct_object_storage(monkeypatch) -> None:
    storage_built = False

    def build_storage(_settings: Settings):
        nonlocal storage_built
        storage_built = True
        raise AssertionError("workflow-only Worker must not construct object storage")

    monkeypatch.setattr(runtime_module, "build_object_storage", build_storage)
    runtime = WorkerRuntime.build(
        Settings(
            environment="ci",
            worker_queues=["commercevision.workflow"],
        )
    )
    try:
        assert OperationKind.ASSET_DELETION not in runtime.operation_executors.registered_kinds
        assert runtime.object_storage is None
        assert storage_built is False
    finally:
        runtime.close()


def test_worker_binds_known_upload_observation_without_weakening_event_routing() -> None:
    runtime = WorkerRuntime.build(
        Settings(
            environment="ci",
            worker_queues=["commercevision.workflow"],
        )
    )
    payload = AssetUploadFinalizedPayload(
        workspace_id="catalog-workspace",
        upload_session_id="upload-session-1",
        asset_id="asset-1",
        asset_version_id="asset-version-1",
        object_fact_id="object-fact-1",
        validation_operation_id="operation-1",
    )
    envelope = EventEnvelope.create(
        event_type=ASSET_UPLOAD_FINALIZED_V1.event_type.value,
        aggregate_type="Asset",
        aggregate_id=payload.asset_id,
        aggregate_version=1,
        trace_id="trace-upload-finalized",
        payload=payload.model_dump(mode="json"),
    )
    event = OutboxEvent(
        envelope=envelope,
        available_at=envelope.occurred_at,
        workspace_id=payload.workspace_id,
    )
    try:
        handler = runtime.event_router.resolve(event.envelope)
        handler(event)

        with pytest.raises(UnknownEventTypeError):
            runtime.event_router.resolve(
                replace(event.envelope, event_type="asset.event.never-registered")
            )
        with pytest.raises(UnsupportedSchemaVersionError):
            runtime.event_router.resolve(replace(event.envelope, schema_version=2))
        with pytest.raises(MalformedEventPayloadError):
            runtime.event_router.resolve(replace(event.envelope, payload={}))
        rights_payload = AssetRightsChangedPayload(
            workspace_id=payload.workspace_id,
            asset_id=payload.asset_id,
            asset_version_id=payload.asset_version_id,
            rights_record_id="rights-record-1",
            rights_record_version=1,
            change="REGISTERED",
            resulting_asset_state="AVAILABLE",
            required_convergence="REINDEX",
        )
        rights_envelope = EventEnvelope.create(
            event_type=ASSET_RIGHTS_CHANGED_V1.event_type.value,
            aggregate_type="Asset",
            aggregate_id=payload.asset_id,
            aggregate_version=2,
            trace_id="trace-rights-changed",
            payload=rights_payload.model_dump(mode="json"),
        )
        rights_event = OutboxEvent(
            envelope=rights_envelope,
            available_at=rights_envelope.occurred_at,
            workspace_id=payload.workspace_id,
        )
        runtime.event_router.resolve(rights_envelope)(rights_event)
        with pytest.raises(EventRoutingError, match="workspace"):
            handler(replace(event, workspace_id="other-workspace"))
        with pytest.raises(EventRoutingError, match="aggregate"):
            handler(
                replace(
                    event,
                    envelope=replace(event.envelope, aggregate_id="other-asset"),
                )
            )
    finally:
        runtime.close()


@pytest.mark.parametrize(
    ("contract", "payload"),
    [
        (
            ASSET_VALIDATION_COMPLETED_V1,
            AssetValidationCompletedPayload(
                workspace_id="catalog-workspace",
                asset_id="asset-1",
                asset_version_id="asset-version-1",
                operation_id="operation-1",
                attempt_number=1,
                outcome="PENDING_RIGHTS",
                reason_code=None,
            ),
        ),
        (
            ASSET_VALIDATION_FAILED_V1,
            AssetValidationFailedPayload(
                workspace_id="catalog-workspace",
                asset_id="asset-1",
                asset_version_id="asset-version-1",
                operation_id="operation-1",
                attempt_number=2,
                outcome="FAILED",
                reason_code="PROVIDER_HTTP_403",
            ),
        ),
    ],
)
def test_worker_observes_known_asset_validation_terminal_events(
    contract: object,
    payload: object,
) -> None:
    runtime = WorkerRuntime.build(
        Settings(
            environment="ci",
            worker_queues=["commercevision.workflow"],
        )
    )
    envelope = EventEnvelope.create(
        event_type=contract.event_type.value,  # type: ignore[attr-defined]
        aggregate_type="Asset",
        aggregate_id=payload.asset_id,  # type: ignore[attr-defined]
        aggregate_version=3,
        trace_id="trace-validation-terminal",
        payload=payload.model_dump(mode="json"),  # type: ignore[attr-defined]
    )
    event = OutboxEvent(
        envelope=envelope,
        available_at=envelope.occurred_at,
        workspace_id=payload.workspace_id,  # type: ignore[attr-defined]
    )
    try:
        handler = runtime.event_router.resolve(envelope)
        handler(event)

        with pytest.raises(EventRoutingError, match="workspace"):
            handler(replace(event, workspace_id="other-workspace"))
        with pytest.raises(EventRoutingError, match="aggregate"):
            handler(
                replace(
                    event,
                    envelope=replace(envelope, aggregate_id="other-asset"),
                )
            )
        with pytest.raises(MalformedEventPayloadError):
            runtime.event_router.resolve(
                replace(
                    envelope,
                    payload={
                        **envelope.payload,
                        "raw_provider_payload": {"secret": "must-not-pass"},
                    },
                )
            )
    finally:
        runtime.close()


def test_celery_worker_fails_fast_before_consumer_when_executor_is_missing(
    monkeypatch,
    tmp_path,
) -> None:
    settings = _production_worker_settings(
        tmp_path / "worker-ready.json",
        queue="commercevision.index",
        required_kind=OperationKind.ASSET_INDEXING,
    )
    monkeypatch.setattr(worker_module, "settings", settings)
    monkeypatch.setattr(executor_module, "entry_points", lambda **_kwargs: ())

    with pytest.raises(SystemExit, match="ASSET_INDEXING"):
        WorkController(
            app=worker_module.celery_app,
            pool="solo",
            concurrency=1,
            hostname="bootstrap-test@localhost",
        )

    assert worker_module.worker_bootstrap_readiness()["ready"] is False
    assert not Path(settings.worker_readiness_path).exists()


@pytest.mark.parametrize(
    ("dependency", "message"),
    (
        ("mysql", "MySQL control plane is unavailable"),
        ("object_storage", "object storage bucket is not accessible"),
    ),
)
def test_celery_master_fails_before_consumer_when_dependency_probe_fails(
    monkeypatch,
    tmp_path,
    dependency: str,
    message: str,
) -> None:
    settings = _production_worker_settings(tmp_path / f"{dependency}-worker-ready.json")
    executor = FakeExecutor()
    runtime_built = False

    monkeypatch.setattr(worker_module, "settings", settings)
    monkeypatch.setattr(
        executor_module,
        "entry_points",
        lambda **_kwargs: (FakeExecutorEntryPoint(executor),),
    )

    def fail_dependencies(_settings: Settings) -> dict[str, str]:
        raise RuntimeError(message)

    def fail_if_runtime_is_built(*_args, **_kwargs):
        nonlocal runtime_built
        runtime_built = True
        raise AssertionError("runtime must not be built after a master dependency failure")

    monkeypatch.setattr(worker_module, "probe_worker_dependencies", fail_dependencies)
    monkeypatch.setattr(WorkerRuntime, "build", fail_if_runtime_is_built)

    with pytest.raises(SystemExit, match=message):
        WorkController(
            app=worker_module.celery_app,
            pool="solo",
            concurrency=1,
            hostname=f"bootstrap-{dependency}-failure-test@localhost",
        )

    assert runtime_built is False
    assert worker_module.worker_bootstrap_readiness()["ready"] is False
    assert not Path(settings.worker_readiness_path).exists()


def test_worker_process_eagerly_builds_runtime_with_discovered_executors(
    monkeypatch,
    tmp_path,
) -> None:
    readiness_path = tmp_path / "worker-ready.json"
    settings = _production_worker_settings(readiness_path)
    executor = FakeExecutor()
    runtime = FakeRuntime()
    built_with: list[dict[OperationKind, FakeExecutor]] = []

    monkeypatch.setattr(worker_module, "settings", settings)
    monkeypatch.setattr(
        executor_module,
        "entry_points",
        lambda **_kwargs: (FakeExecutorEntryPoint(executor),),
    )
    monkeypatch.setattr(worker_module, "probe_worker_dependencies", _dependencies_ready)

    def build_runtime(_settings, *, operation_executors):
        built_with.append(operation_executors)
        return runtime

    monkeypatch.setattr(WorkerRuntime, "build", build_runtime)

    controller = WorkController(
        app=worker_module.celery_app,
        pool="solo",
        concurrency=1,
        hostname="bootstrap-success-test@localhost",
    )
    assert controller.blueprint is not None

    assert built_with == [{OperationKind.ASSET_VALIDATION: executor}]
    assert worker_module.process_outbox_event.run("event-eager") == "handled:event-eager"
    assert readiness_path.exists() is False
    assert worker_module._child_readiness_path().is_file()
    assert worker_module.worker_bootstrap_readiness()["ready"] is False

    worker_module.mark_worker_ready(sender=FakeWorkerConsumer())

    assert worker_module.worker_bootstrap_readiness() == {
        "ready": True,
        "consumer_ready": True,
        "master_pid": os.getpid(),
        "required_kinds": [
            OperationKind.ASSET_VALIDATION.value,
            OperationKind.PRODUCT_BRIEF_ANALYSIS.value,
        ],
        "registered_kinds": [
            OperationKind.ASSET_VALIDATION.value,
            OperationKind.PRODUCT_BRIEF_ANALYSIS.value,
        ],
        "missing_kinds": [],
        "broker": "ok",
        "mysql": "ok",
        "object_storage": "ok",
        "malware_scanner": "ok",
        "provider_result_storage": "ok",
        "vision_credential": "ok",
        "error": None,
    }
    assert readiness_path.is_file()
    marker = json.loads(readiness_path.read_text(encoding="utf-8"))
    assert {
        key: marker[key] for key in worker_module.worker_bootstrap_readiness() if key != "error"
    } == {
        key: value
        for key, value in worker_module.worker_bootstrap_readiness().items()
        if key != "error"
    }
    assert marker["checked_at"] < marker["fresh_until"]
    assert "error" not in marker
    worker_module.close_runtime()
    assert runtime.closed is True
    assert worker_module._child_readiness_path().exists() is False
    assert readiness_path.is_file()
    worker_module.remove_worker_readiness()
    assert readiness_path.exists() is False


def test_worker_ready_publishes_fresh_sanitized_marker_and_starts_supervisor(
    monkeypatch,
    tmp_path,
) -> None:
    readiness_path = tmp_path / "worker-ready.json"
    settings = _production_worker_settings(
        readiness_path,
        readiness_max_age_seconds=73,
    )
    executor = FakeExecutor()

    monkeypatch.setattr(worker_module, "settings", settings)
    monkeypatch.setattr(
        executor_module,
        "entry_points",
        lambda **_kwargs: (FakeExecutorEntryPoint(executor),),
    )
    monkeypatch.setattr(worker_module, "probe_worker_dependencies", _dependencies_ready)
    probe_completion_times = iter((111.0, 999.0))
    monkeypatch.setattr(worker_module, "time", lambda: next(probe_completion_times))

    worker_module.validate_worker_startup()
    try:
        worker_module.mark_worker_ready(sender=FakeWorkerConsumer())

        marker = json.loads(readiness_path.read_text(encoding="utf-8"))
        assert marker["checked_at"] == 111.0
        assert marker["fresh_until"] == 184.0
        assert "error" not in marker
        assert worker_module._readiness_supervisor is not None
    finally:
        worker_module.remove_worker_readiness()

    assert readiness_path.exists() is False
    assert worker_module._readiness_supervisor is None


def test_worker_ready_fails_closed_without_master_shutdown_control(
    monkeypatch,
    tmp_path,
) -> None:
    readiness_path = tmp_path / "worker-ready.json"
    settings = _production_worker_settings(readiness_path)
    executor = FakeExecutor()

    monkeypatch.setattr(worker_module, "settings", settings)
    monkeypatch.setattr(
        executor_module,
        "entry_points",
        lambda **_kwargs: (FakeExecutorEntryPoint(executor),),
    )
    monkeypatch.setattr(worker_module, "probe_worker_dependencies", _dependencies_ready)

    worker_module.validate_worker_startup()
    with pytest.raises(SystemExit, match="shutdown control"):
        worker_module.mark_worker_ready(sender=object())

    assert readiness_path.exists() is False
    assert worker_module._readiness_supervisor is None


def test_live_dependency_failure_revokes_marker_before_controlled_master_shutdown(
    monkeypatch,
    tmp_path,
) -> None:
    readiness_path = tmp_path / "worker-ready.json"
    settings = _production_worker_settings(readiness_path)
    executor = FakeExecutor()
    lifecycle_events: list[str] = []

    class OrderingController:
        @staticmethod
        def stop(*, exitcode: int | None = None) -> None:
            assert readiness_path.exists() is False
            assert exitcode == worker_module.EX_FAILURE
            lifecycle_events.append("shutdown")

    class OrderingConsumer:
        controller = OrderingController()

    monkeypatch.setattr(worker_module, "settings", settings)
    monkeypatch.setattr(
        executor_module,
        "entry_points",
        lambda **_kwargs: (FakeExecutorEntryPoint(executor),),
    )
    monkeypatch.setattr(worker_module, "probe_worker_dependencies", _dependencies_ready)

    worker_module.validate_worker_startup()
    worker_module.mark_worker_ready(sender=OrderingConsumer())
    supervisor = worker_module._readiness_supervisor
    assert supervisor is not None

    def fail_dependencies(_settings: Settings) -> dict[str, str]:
        raise RuntimeError("private provider response body")

    monkeypatch.setattr(worker_module, "probe_worker_dependencies", fail_dependencies)
    try:
        assert supervisor.check() is False
        assert readiness_path.is_file()
        assert lifecycle_events == []

        assert supervisor.check() is False
        assert readiness_path.exists() is False
        assert lifecycle_events == ["shutdown"]
    finally:
        worker_module.remove_worker_readiness()


def test_pool_child_initialization_never_runs_network_dependency_probes(
    monkeypatch,
    tmp_path,
) -> None:
    readiness_path = tmp_path / "worker-ready.json"
    settings = _production_worker_settings(readiness_path)
    executor = FakeExecutor()
    runtime = FakeRuntime()
    probe_count = 0

    monkeypatch.setattr(worker_module, "settings", settings)
    monkeypatch.setattr(
        executor_module,
        "entry_points",
        lambda **_kwargs: (FakeExecutorEntryPoint(executor),),
    )
    monkeypatch.setattr(worker_module, "probe_worker_dependencies", _dependencies_ready)
    monkeypatch.setattr(
        WorkerRuntime,
        "build",
        lambda _settings, *, operation_executors: runtime,
    )

    def fail_runtime_dependency_readiness() -> dict[str, object]:
        raise AssertionError("pool child must not run runtime dependency readiness")

    runtime.operation_executor_readiness = fail_runtime_dependency_readiness

    worker_module.validate_worker_startup()

    def fail_if_probed(_settings: Settings) -> dict[str, str]:
        nonlocal probe_count
        probe_count += 1
        raise AssertionError("pool child must not perform network readiness probes")

    monkeypatch.setattr(worker_module, "probe_worker_dependencies", fail_if_probed)
    worker_module.initialize_worker_process()

    assert probe_count == 0
    assert worker_module.worker_bootstrap_readiness()["ready"] is False
    assert readiness_path.exists() is False
    assert worker_module._child_readiness_path().is_file()
    worker_module.mark_worker_ready(sender=FakeWorkerConsumer())
    assert worker_module.worker_bootstrap_readiness()["ready"] is True
    assert readiness_path.is_file()
    worker_module.close_runtime()
    assert runtime.closed is True
    assert worker_module._child_readiness_path().exists() is False
    assert readiness_path.is_file()
    worker_module.remove_worker_readiness()


def test_pool_child_bootstrap_failure_does_not_remove_master_readiness(
    monkeypatch,
    tmp_path,
) -> None:
    readiness_path = tmp_path / "worker-ready.json"
    settings = _production_worker_settings(readiness_path)
    executor = FakeExecutor()

    monkeypatch.setattr(worker_module, "settings", settings)
    monkeypatch.setattr(
        executor_module,
        "entry_points",
        lambda **_kwargs: (FakeExecutorEntryPoint(executor),),
    )
    monkeypatch.setattr(worker_module, "probe_worker_dependencies", _dependencies_ready)
    worker_module.validate_worker_startup()
    worker_module.mark_worker_ready(sender=FakeWorkerConsumer())
    assert readiness_path.is_file()

    def fail_runtime(*_args, **_kwargs):
        raise RuntimeError("local child runtime failed")

    monkeypatch.setattr(WorkerRuntime, "build", fail_runtime)
    with pytest.raises(SystemExit, match="local child runtime failed"):
        worker_module.initialize_worker_process()

    assert readiness_path.is_file()
    assert worker_module._child_readiness_path().exists() is False
    worker_module.remove_worker_readiness()


def test_celery_task_never_lazily_initializes_runtime(monkeypatch) -> None:
    monkeypatch.setattr(worker_module, "_runtime", None)

    with pytest.raises(RuntimeError, match="worker process is not initialized"):
        worker_module.process_outbox_event.run("event-before-bootstrap")
