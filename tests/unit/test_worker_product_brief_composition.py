from __future__ import annotations

from threading import Barrier, Event

import pytest
from commercevision_contracts import Settings
from commercevision_contracts.product_briefs import ProviderArtifactPhysicalTarget
from commercevision_domain import StorageBackend, StorageLocationClass
from commercevision_object_storage import ObjectStorageProviderArtifactTarget
from commercevision_worker import product_brief as product_brief_module
from commercevision_worker.product_brief import (
    build_product_brief_executor,
    probe_product_brief_artifact_targets,
)


class PhysicalTargetStorage:
    def __init__(
        self,
        *,
        backend: StorageBackend,
        bucket: str,
        close_error: Exception | None = None,
    ) -> None:
        self.backend = backend
        self.bucket = bucket
        self.close_error = close_error
        self.readiness_calls: list[tuple[StorageLocationClass, ...]] = []
        self.close_count = 0

    def configured_bucket(self, location: StorageLocationClass) -> str:
        assert location is StorageLocationClass.PROVIDER_RESULT
        return self.bucket

    def assert_ready(self, locations: tuple[StorageLocationClass, ...]) -> None:
        self.readiness_calls.append(tuple(locations))

    def close(self) -> None:
        self.close_count += 1
        if self.close_error is not None:
            raise self.close_error


class RecordingTargetReadinessQuery:
    def __init__(
        self,
        *,
        unresolved_target: ProviderArtifactPhysicalTarget | None = None,
        unresolved_targets: tuple[ProviderArtifactPhysicalTarget, ...] = (),
    ) -> None:
        self.unresolved_targets = (
            (unresolved_target,) if unresolved_target is not None else unresolved_targets
        )
        self.limits: list[int] = []

    def list_reconciliation_targets(
        self,
        *,
        limit: int,
    ) -> tuple[ProviderArtifactPhysicalTarget, ...]:
        self.limits.append(limit)
        return self.unresolved_targets


def test_product_brief_builder_accepts_the_current_reconciliation_target() -> None:
    query = RecordingTargetReadinessQuery()
    storage = PhysicalTargetStorage(
        backend=StorageBackend.MINIO,
        bucket="provider-results",
    )

    built = build_product_brief_executor(
        settings=Settings(),
        database=object(),  # type: ignore[arg-type]
        storage=storage,  # type: ignore[arg-type]
        artifact_target_readiness_query=query,
    )

    assert built.artifact_reconciler is not None
    assert query.limits == [1]


def test_product_brief_builder_refuses_an_unregistered_reconciliation_target() -> None:
    query = RecordingTargetReadinessQuery(
        unresolved_target=ProviderArtifactPhysicalTarget(
            storage_backend=StorageBackend.OSS,
            location=StorageLocationClass.PROVIDER_RESULT,
            bucket="provider-results-legacy",
        )
    )
    storage = PhysicalTargetStorage(
        backend=StorageBackend.MINIO,
        bucket="provider-results",
    )

    with pytest.raises(
        RuntimeError,
        match="provider artifact reconciliation target registration is incomplete",
    ) as raised:
        build_product_brief_executor(
            settings=Settings(),
            database=object(),  # type: ignore[arg-type]
            storage=storage,  # type: ignore[arg-type]
            artifact_target_readiness_query=query,
        )

    assert str(raised.value) == (
        "provider artifact reconciliation target registration is incomplete; refusing to start"
    )
    assert "provider-results-legacy" not in str(raised.value)
    assert query.limits == [1]


def test_product_brief_builder_accepts_an_explicit_legacy_target() -> None:
    legacy_target = ProviderArtifactPhysicalTarget(
        storage_backend=StorageBackend.OSS,
        location=StorageLocationClass.PROVIDER_RESULT,
        bucket="provider-results-legacy",
    )
    query = RecordingTargetReadinessQuery(unresolved_target=legacy_target)
    current_storage = PhysicalTargetStorage(
        backend=StorageBackend.MINIO,
        bucket="provider-results",
    )
    legacy_storage = PhysicalTargetStorage(
        backend=StorageBackend.OSS,
        bucket="provider-results-legacy",
    )

    built = build_product_brief_executor(
        settings=Settings(),
        database=object(),  # type: ignore[arg-type]
        storage=current_storage,  # type: ignore[arg-type]
        artifact_target_readiness_query=query,
        additional_artifact_targets=(
            ObjectStorageProviderArtifactTarget(
                storage=legacy_storage,  # type: ignore[arg-type]
                bucket="provider-results-legacy",
            ),
        ),
    )

    assert built.artifact_reconciler is not None
    assert query.limits == [2]


def test_product_brief_builder_rejects_and_closes_injected_targets_above_the_hard_bound() -> None:
    query = RecordingTargetReadinessQuery()
    current_storage = PhysicalTargetStorage(
        backend=StorageBackend.MINIO,
        bucket="provider-results",
    )
    historical_storages = tuple(
        PhysicalTargetStorage(
            backend=StorageBackend.OSS,
            bucket=f"provider-results-{index}",
        )
        for index in range(17)
    )

    with pytest.raises(
        RuntimeError,
        match="provider artifact target registrations exceed the configured bound",
    ):
        build_product_brief_executor(
            settings=Settings(),
            database=object(),  # type: ignore[arg-type]
            storage=current_storage,  # type: ignore[arg-type]
            artifact_target_readiness_query=query,
            additional_artifact_targets=tuple(
                ObjectStorageProviderArtifactTarget(
                    storage=historical_storage,  # type: ignore[arg-type]
                    bucket=historical_storage.bucket,
                )
                for historical_storage in historical_storages
            ),
        )

    assert query.limits == []
    assert all(storage.close_count == 1 for storage in historical_storages)
    assert current_storage.close_count == 0


def test_product_brief_builder_loads_and_owns_configured_historical_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_target = ProviderArtifactPhysicalTarget(
        storage_backend=StorageBackend.MINIO,
        location=StorageLocationClass.PROVIDER_RESULT,
        bucket="provider-results-legacy",
    )
    query = RecordingTargetReadinessQuery(unresolved_target=legacy_target)
    current_storage = PhysicalTargetStorage(
        backend=StorageBackend.MINIO,
        bucket="provider-results",
    )
    legacy_storage = PhysicalTargetStorage(
        backend=StorageBackend.MINIO,
        bucket="provider-results-legacy",
    )
    monkeypatch.setattr(
        product_brief_module,
        "build_object_storage",
        lambda target_settings: (
            legacy_storage
            if target_settings.object_store_provider_result_bucket == "provider-results-legacy"
            else pytest.fail("builder requested an unexpected object-storage target")
        ),
    )
    settings = Settings(
        service_name="worker",
        worker_queues=["commercevision.asset"],
        worker_required_operation_kinds=["PRODUCT_BRIEF_ANALYSIS"],
        provider_artifact_reconciliation_targets=[
            {
                "object_store_backend": "minio",
                "object_store_provider_result_bucket": "provider-results-legacy",
            }
        ],
    )

    built = build_product_brief_executor(
        settings=settings,
        database=object(),  # type: ignore[arg-type]
        storage=current_storage,  # type: ignore[arg-type]
        artifact_target_readiness_query=query,
    )

    assert built.artifact_reconciler is not None
    assert legacy_storage.close_count == 0
    for resource in reversed(built.closeables):
        resource.close()
    assert legacy_storage.close_count == 1
    assert current_storage.close_count == 0


def test_product_brief_builder_closes_a_misconfigured_historical_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_storage = PhysicalTargetStorage(
        backend=StorageBackend.MINIO,
        bucket="provider-results",
    )
    mismatched_storage = PhysicalTargetStorage(
        backend=StorageBackend.MINIO,
        bucket="different-provider-results",
    )
    monkeypatch.setattr(
        product_brief_module,
        "build_object_storage",
        lambda _target_settings: mismatched_storage,
    )
    settings = Settings(
        service_name="worker",
        worker_queues=["commercevision.asset"],
        worker_required_operation_kinds=["PRODUCT_BRIEF_ANALYSIS"],
        provider_artifact_reconciliation_targets=[
            {
                "object_store_backend": "minio",
                "object_store_provider_result_bucket": "provider-results-legacy",
            }
        ],
    )

    with pytest.raises(
        ValueError,
        match="target bucket does not match storage adapter",
    ):
        build_product_brief_executor(
            settings=settings,
            database=object(),  # type: ignore[arg-type]
            storage=current_storage,  # type: ignore[arg-type]
            artifact_target_readiness_query=RecordingTargetReadinessQuery(),
        )

    assert mismatched_storage.close_count == 1
    assert current_storage.close_count == 0


def test_product_brief_builder_closes_history_when_current_target_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_storage = PhysicalTargetStorage(
        backend=StorageBackend.MINIO,
        bucket="different-provider-results",
    )
    legacy_storage = PhysicalTargetStorage(
        backend=StorageBackend.MINIO,
        bucket="provider-results-legacy",
    )
    monkeypatch.setattr(
        product_brief_module,
        "build_object_storage",
        lambda _target_settings: legacy_storage,
    )
    settings = Settings(
        service_name="worker",
        worker_queues=["commercevision.asset"],
        worker_required_operation_kinds=["PRODUCT_BRIEF_ANALYSIS"],
        provider_artifact_reconciliation_targets=[
            {
                "object_store_backend": "minio",
                "object_store_provider_result_bucket": "provider-results-legacy",
            }
        ],
    )

    with pytest.raises(
        ValueError,
        match="target bucket does not match storage adapter",
    ):
        build_product_brief_executor(
            settings=settings,
            database=object(),  # type: ignore[arg-type]
            storage=current_storage,  # type: ignore[arg-type]
            artifact_target_readiness_query=RecordingTargetReadinessQuery(),
        )

    assert legacy_storage.close_count == 1
    assert current_storage.close_count == 0


def test_product_brief_builder_closes_history_when_analyzer_composition_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_storage = PhysicalTargetStorage(
        backend=StorageBackend.MINIO,
        bucket="provider-results",
    )
    legacy_storage = PhysicalTargetStorage(
        backend=StorageBackend.MINIO,
        bucket="provider-results-legacy",
    )
    monkeypatch.setattr(
        product_brief_module,
        "build_object_storage",
        lambda _target_settings: legacy_storage,
    )
    monkeypatch.setattr(
        product_brief_module,
        "DeterministicVisionAnalyzer",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("analyzer construction failed")),
    )
    settings = Settings(
        service_name="worker",
        worker_queues=["commercevision.asset"],
        worker_required_operation_kinds=["PRODUCT_BRIEF_ANALYSIS"],
        provider_artifact_reconciliation_targets=[
            {
                "object_store_backend": "minio",
                "object_store_provider_result_bucket": "provider-results-legacy",
            }
        ],
    )

    with pytest.raises(RuntimeError, match="analyzer construction failed"):
        build_product_brief_executor(
            settings=settings,
            database=object(),  # type: ignore[arg-type]
            storage=current_storage,  # type: ignore[arg-type]
            artifact_target_readiness_query=RecordingTargetReadinessQuery(),
        )

    assert legacy_storage.close_count == 1
    assert current_storage.close_count == 0


def test_product_brief_builder_closes_all_owned_resources_when_executor_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BuiltAnalyzer:
        def __init__(self) -> None:
            self.close_count = 0

        def close(self) -> None:
            self.close_count += 1

    current_storage = PhysicalTargetStorage(
        backend=StorageBackend.MINIO,
        bucket="provider-results",
    )
    legacy_storage = PhysicalTargetStorage(
        backend=StorageBackend.MINIO,
        bucket="provider-results-legacy",
    )
    analyzer = BuiltAnalyzer()
    monkeypatch.setattr(
        product_brief_module,
        "build_object_storage",
        lambda _target_settings: legacy_storage,
    )
    monkeypatch.setattr(
        product_brief_module,
        "DeterministicVisionAnalyzer",
        lambda **_kwargs: analyzer,
    )
    monkeypatch.setattr(
        product_brief_module,
        "ProductBriefAnalysisExecutor",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("executor construction failed")),
    )
    settings = Settings(
        service_name="worker",
        worker_queues=["commercevision.asset"],
        worker_required_operation_kinds=["PRODUCT_BRIEF_ANALYSIS"],
        provider_artifact_reconciliation_targets=[
            {
                "object_store_backend": "minio",
                "object_store_provider_result_bucket": "provider-results-legacy",
            }
        ],
    )

    with pytest.raises(RuntimeError, match="executor construction failed"):
        build_product_brief_executor(
            settings=settings,
            database=object(),  # type: ignore[arg-type]
            storage=current_storage,  # type: ignore[arg-type]
            artifact_target_readiness_query=RecordingTargetReadinessQuery(),
        )

    assert analyzer.close_count == 1
    assert legacy_storage.close_count == 1
    assert current_storage.close_count == 0


def test_product_brief_artifact_probe_checks_only_unsettled_exact_targets() -> None:
    required_target = ProviderArtifactPhysicalTarget(
        storage_backend=StorageBackend.OSS,
        location=StorageLocationClass.PROVIDER_RESULT,
        bucket="provider-results-required",
    )
    query = RecordingTargetReadinessQuery(unresolved_target=required_target)
    current_storage = PhysicalTargetStorage(
        backend=StorageBackend.MINIO,
        bucket="provider-results",
    )
    required_storage = PhysicalTargetStorage(
        backend=StorageBackend.OSS,
        bucket="provider-results-required",
    )
    settled_storage = PhysicalTargetStorage(
        backend=StorageBackend.OSS,
        bucket="provider-results-settled",
    )

    probe_product_brief_artifact_targets(
        settings=Settings(),
        database=object(),  # type: ignore[arg-type]
        storage=current_storage,  # type: ignore[arg-type]
        artifact_target_readiness_query=query,
        additional_artifact_targets=(
            ObjectStorageProviderArtifactTarget(
                storage=required_storage,  # type: ignore[arg-type]
                bucket="provider-results-required",
            ),
            ObjectStorageProviderArtifactTarget(
                storage=settled_storage,  # type: ignore[arg-type]
                bucket="provider-results-settled",
            ),
        ),
    )

    assert required_storage.readiness_calls == [(StorageLocationClass.PROVIDER_RESULT,)]
    assert settled_storage.readiness_calls == []
    assert current_storage.readiness_calls == []
    assert required_storage.close_count == 1
    assert settled_storage.close_count == 1
    assert current_storage.close_count == 0


def test_product_brief_artifact_probe_checks_required_targets_in_one_readiness_window() -> None:
    readiness_window = Barrier(2, timeout=1)

    class ReadinessWindowStorage(PhysicalTargetStorage):
        def assert_ready(self, locations: tuple[StorageLocationClass, ...]) -> None:
            super().assert_ready(locations)
            readiness_window.wait()

    first_target = ProviderArtifactPhysicalTarget(
        storage_backend=StorageBackend.OSS,
        location=StorageLocationClass.PROVIDER_RESULT,
        bucket="provider-results-first",
    )
    second_target = ProviderArtifactPhysicalTarget(
        storage_backend=StorageBackend.OSS,
        location=StorageLocationClass.PROVIDER_RESULT,
        bucket="provider-results-second",
    )
    query = RecordingTargetReadinessQuery(
        unresolved_targets=(first_target, second_target),
    )
    current_storage = PhysicalTargetStorage(
        backend=StorageBackend.MINIO,
        bucket="provider-results",
    )
    first_storage = ReadinessWindowStorage(
        backend=StorageBackend.OSS,
        bucket="provider-results-first",
    )
    second_storage = ReadinessWindowStorage(
        backend=StorageBackend.OSS,
        bucket="provider-results-second",
    )

    probe_product_brief_artifact_targets(
        settings=Settings(),
        database=object(),  # type: ignore[arg-type]
        storage=current_storage,  # type: ignore[arg-type]
        artifact_target_readiness_query=query,
        additional_artifact_targets=(
            ObjectStorageProviderArtifactTarget(
                storage=first_storage,  # type: ignore[arg-type]
                bucket="provider-results-first",
            ),
            ObjectStorageProviderArtifactTarget(
                storage=second_storage,  # type: ignore[arg-type]
                bucket="provider-results-second",
            ),
        ),
    )

    assert first_storage.readiness_calls == [(StorageLocationClass.PROVIDER_RESULT,)]
    assert second_storage.readiness_calls == [(StorageLocationClass.PROVIDER_RESULT,)]
    assert first_storage.close_count == 1
    assert second_storage.close_count == 1
    assert current_storage.close_count == 0


def test_product_brief_artifact_probe_settles_all_failures_before_closing_targets() -> None:
    first_failure_observed = Event()
    all_readiness_settled = Event()

    class CloseAfterSettlementStorage(PhysicalTargetStorage):
        def close(self) -> None:
            assert all_readiness_settled.is_set()
            super().close()

    class ImmediateFailureStorage(CloseAfterSettlementStorage):
        def assert_ready(self, locations: tuple[StorageLocationClass, ...]) -> None:
            super().assert_ready(locations)
            first_failure_observed.set()
            raise RuntimeError("first historical readiness failed")

    class SettlingFailureStorage(CloseAfterSettlementStorage):
        def assert_ready(self, locations: tuple[StorageLocationClass, ...]) -> None:
            super().assert_ready(locations)
            assert first_failure_observed.wait(timeout=1)
            all_readiness_settled.set()
            raise RuntimeError("second historical readiness failed")

    first_target = ProviderArtifactPhysicalTarget(
        storage_backend=StorageBackend.OSS,
        location=StorageLocationClass.PROVIDER_RESULT,
        bucket="provider-results-first",
    )
    second_target = ProviderArtifactPhysicalTarget(
        storage_backend=StorageBackend.OSS,
        location=StorageLocationClass.PROVIDER_RESULT,
        bucket="provider-results-second",
    )
    query = RecordingTargetReadinessQuery(
        unresolved_targets=(first_target, second_target),
    )
    current_storage = PhysicalTargetStorage(
        backend=StorageBackend.MINIO,
        bucket="provider-results",
    )
    first_storage = ImmediateFailureStorage(
        backend=StorageBackend.OSS,
        bucket="provider-results-first",
    )
    second_storage = SettlingFailureStorage(
        backend=StorageBackend.OSS,
        bucket="provider-results-second",
    )

    with pytest.raises(
        ExceptionGroup,
        match="provider artifact target readiness failed",
    ) as raised:
        probe_product_brief_artifact_targets(
            settings=Settings(),
            database=object(),  # type: ignore[arg-type]
            storage=current_storage,  # type: ignore[arg-type]
            artifact_target_readiness_query=query,
            additional_artifact_targets=(
                ObjectStorageProviderArtifactTarget(
                    storage=first_storage,  # type: ignore[arg-type]
                    bucket="provider-results-first",
                ),
                ObjectStorageProviderArtifactTarget(
                    storage=second_storage,  # type: ignore[arg-type]
                    bucket="provider-results-second",
                ),
            ),
        )

    assert {str(error) for error in raised.value.exceptions} == {
        "first historical readiness failed",
        "second historical readiness failed",
    }
    assert first_storage.readiness_calls == [(StorageLocationClass.PROVIDER_RESULT,)]
    assert second_storage.readiness_calls == [(StorageLocationClass.PROVIDER_RESULT,)]
    assert first_storage.close_count == 1
    assert second_storage.close_count == 1
    assert current_storage.close_count == 0


def test_product_brief_artifact_probe_closes_every_historical_client_after_failure() -> None:
    query = RecordingTargetReadinessQuery()
    current_storage = PhysicalTargetStorage(
        backend=StorageBackend.MINIO,
        bucket="provider-results",
    )
    first_storage = PhysicalTargetStorage(
        backend=StorageBackend.OSS,
        bucket="provider-results-first",
        close_error=RuntimeError("first historical close failed"),
    )
    second_storage = PhysicalTargetStorage(
        backend=StorageBackend.OSS,
        bucket="provider-results-second",
    )

    with pytest.raises(ExceptionGroup, match="provider artifact target shutdown failed"):
        probe_product_brief_artifact_targets(
            settings=Settings(),
            database=object(),  # type: ignore[arg-type]
            storage=current_storage,  # type: ignore[arg-type]
            artifact_target_readiness_query=query,
            additional_artifact_targets=(
                ObjectStorageProviderArtifactTarget(
                    storage=first_storage,  # type: ignore[arg-type]
                    bucket="provider-results-first",
                ),
                ObjectStorageProviderArtifactTarget(
                    storage=second_storage,  # type: ignore[arg-type]
                    bucket="provider-results-second",
                ),
            ),
        )

    assert first_storage.close_count == 1
    assert second_storage.close_count == 1
    assert current_storage.close_count == 0
