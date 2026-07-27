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
from commercevision_domain import OperationKind
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from commercevision_worker.executors import available_builtin_operation_kinds
from commercevision_worker.runtime import WorkerRuntime
from pydantic import ValidationError

worker_module = import_module("commercevision_worker.celery_app")
executor_module = import_module("commercevision_worker.executors")
runtime_module = import_module("commercevision_worker.runtime")


class FakeRuntime:
    def __init__(self) -> None:
        self.closed = False

    def process_event(self, event_id: str) -> str:
        return f"handled:{event_id}"

    def operation_executor_readiness(self) -> dict[str, object]:
        return {
            "ready": True,
            "required_kinds": [OperationKind.ASSET_VALIDATION.value],
            "registered_kinds": [OperationKind.ASSET_VALIDATION.value],
            "missing_kinds": [],
        }

    def close(self) -> None:
        self.closed = True


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
) -> Settings:
    return Settings(
        environment="production",
        object_store_endpoint="https://object-storage.internal.example",
        object_store_presign_endpoint="https://uploads.example",
        object_store_secret_key="production-worker-object-store-secret",
        object_store_require_encryption=True,
        worker_queues=[queue],
        worker_required_operation_kinds=[required_kind],
        worker_readiness_path=str(readiness_path),
        asset_malware_adapter="clamav",
        asset_content_safety_adapter="alibaba",
        alibaba_content_safety_access_key_id="test-access-key-id",
        alibaba_content_safety_access_key_secret="test-access-key-secret",
        alibaba_content_safety_allowed_url_origins=["https://uploads.example"],
        validation_data_transfer_enabled=True,
        validation_data_transfer_policy_version="enterprise-validation-transfer-v1",
        validation_data_transfer_allowed_workspace_ids=["production-workspace"],
        validation_data_transfer_allowed_asset_kinds=["IMAGE"],
        validation_data_transfer_allowed_retention_classes=["TASK", "FOUNDATION"],
        validation_data_transfer_allowed_providers=["alibaba-green"],
        validation_data_transfer_allowed_endpoint_regions=["cn-shanghai"],
        validation_data_transfer_allowed_endpoint_hosts=["green-cip.cn-shanghai.aliyuncs.com"],
        asset_provenance_adapter="c2pa",
        c2pa_trust_anchors_pem="test-trust-anchor",
        c2pa_trust_eku_policy="test-eku-policy",
    )


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


def _dependencies_ready(_settings: Settings) -> dict[str, str]:
    return {
        "mysql": "ok",
        "object_storage": "ok",
        "malware_scanner": "ok",
    }


def test_celery_task_delegates_without_business_retry(monkeypatch) -> None:
    monkeypatch.setattr(worker_module, "_get_runtime", lambda: FakeRuntime())

    assert worker_module.process_outbox_event.run("event-1") == "handled:event-1"
    assert worker_module.celery_app.conf.task_acks_late is True
    assert worker_module.celery_app.conf.task_acks_on_failure_or_timeout is False
    assert worker_module.celery_app.conf.task_reject_on_worker_lost is True
    assert {queue.name for queue in worker_module.celery_app.conf.task_queues} == set(
        worker_module.settings.configured_worker_queues
    )


def test_asset_queue_has_a_builtin_validation_executor_and_requires_storage() -> None:
    settings = Settings(
        environment="ci",
        worker_queues=["commercevision.asset"],
        worker_required_operation_kinds=[OperationKind.ASSET_VALIDATION],
    )

    assert available_builtin_operation_kinds(settings) == {OperationKind.ASSET_VALIDATION}
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

    worker_module.mark_worker_ready()

    assert worker_module.worker_bootstrap_readiness() == {
        "ready": True,
        "consumer_ready": True,
        "master_pid": os.getpid(),
        "required_kinds": [OperationKind.ASSET_VALIDATION.value],
        "registered_kinds": [
            OperationKind.ASSET_VALIDATION.value,
        ],
        "missing_kinds": [],
        "mysql": "ok",
        "object_storage": "ok",
        "malware_scanner": "ok",
        "error": None,
    }
    assert readiness_path.is_file()
    assert json.loads(readiness_path.read_text(encoding="utf-8")) == (
        worker_module.worker_bootstrap_readiness()
    )
    worker_module.close_runtime()
    assert runtime.closed is True
    assert worker_module._child_readiness_path().exists() is False
    assert readiness_path.is_file()
    worker_module.remove_worker_readiness()
    assert readiness_path.exists() is False


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
    worker_module.mark_worker_ready()
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
    worker_module.mark_worker_ready()
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
