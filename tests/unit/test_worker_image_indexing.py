from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from commercevision_contracts import MilvusVectorIdentityV1, Settings
from commercevision_contracts.events import (
    ASSET_INDEX_DELETE_REQUESTED_V1,
    ASSET_INDEX_REQUESTED_V1,
    ASSET_RIGHTS_CHANGED_V1,
    PRODUCT_BRIEF_CONFIRMED_V1,
    AssetIndexDeleteRequestedPayload,
    AssetIndexRequestedPayload,
    AssetRightsChangedPayload,
    ProductBriefConfirmedPayload,
)
from commercevision_domain import OperationKind
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from commercevision_persistence import ImageIndexNotApplicable
from commercevision_worker import image_indexing as image_indexing_module
from commercevision_worker import runtime as runtime_module
from commercevision_worker.runtime import WorkerRuntime
from pydantic import ValidationError

ASSET_ID = "018f5f4d-7c11-7d11-8a11-111111111111"
VERSION_ID = "018f5f4d-7c11-7d11-8a11-222222222222"
RIGHTS_ID = "018f5f4d-7c11-7d11-8a11-333333333333"
RECORD_ID = "018f5f4d-7c11-7d11-8a11-444444444444"
COLLECTION_ID = "018f5f4d-7c11-7d11-8a11-555555555555"
OPERATION_ID = "018f5f4d-7c11-7d11-8a11-666666666666"


def _runtime(**overrides: object) -> WorkerRuntime:
    values = {
        "database": SimpleNamespace(),
        "settings": Settings(environment="ci", worker_queues=["commercevision.asset"]),
        "worker_id": "test-worker",
        "inbox": SimpleNamespace(),
        "agent": SimpleNamespace(),
        "event_router": SimpleNamespace(),
        "operation_worker": SimpleNamespace(),
        "operation_executors": SimpleNamespace(),
        "object_storage": None,
        "resources": (),
        "brand_profile_invalidation": SimpleNamespace(invalidate_asset=lambda **_kwargs: None),
    }
    values.update(overrides)
    return WorkerRuntime(**values)  # type: ignore[arg-type]


def _event(contract: object, payload: object, *, aggregate_id: str) -> OutboxEvent:
    envelope = EventEnvelope.create(
        event_type=contract.event_type.value,  # type: ignore[attr-defined]
        aggregate_type=(
            "embedding_record" if contract is ASSET_INDEX_DELETE_REQUESTED_V1 else "Asset"
        ),
        aggregate_id=aggregate_id,
        aggregate_version=3,
        trace_id=OPERATION_ID,
        payload=payload.model_dump(mode="json"),  # type: ignore[attr-defined]
        now=datetime(2026, 7, 31, tzinfo=UTC),
    )
    return OutboxEvent(
        envelope=envelope,
        available_at=envelope.occurred_at,
        workspace_id="catalog-workspace",
    )


def test_typed_delete_retries_unknown_outcome_then_completes_idempotently() -> None:
    payload = AssetIndexDeleteRequestedPayload(
        operation_id=OPERATION_ID,
        embedding_record_id=RECORD_ID,
        workspace_id="catalog-workspace",
        asset_id=ASSET_ID,
        asset_version_id=VERSION_ID,
        collection_id=COLLECTION_ID,
        input_hash="a" * 64,
        embedding_spec_sha256="b" * 64,
        write_generation=4,
        reason="RIGHTS_INVALID",
    )
    identity = MilvusVectorIdentityV1(
        collection_name="cv_image_test",
        embedding_record_id=RECORD_ID,
        milvus_primary_key=f"{RECORD_ID}:g4",
        input_hash="a" * 64,
        embedding_spec_sha256="b" * 64,
        write_generation=4,
    )

    class Authority:
        complete_calls = 0

        @staticmethod
        def load_delete_target(actual: object) -> MilvusVectorIdentityV1:
            assert actual == payload
            return identity

        def complete_delete(self, actual: object) -> bool:
            assert actual == payload
            self.complete_calls += 1
            return self.complete_calls == 1

    class Vectors:
        calls = 0

        def delete_if_generation(self, actual: object) -> bool:
            assert actual == identity
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("delete outcome unknown")
            return self.calls == 2

    authority = Authority()
    vectors = Vectors()
    runtime = _runtime(
        image_index_authority=authority,
        image_vector_index=vectors,
    )
    event = _event(ASSET_INDEX_DELETE_REQUESTED_V1, payload, aggregate_id=RECORD_ID)

    with pytest.raises(TimeoutError, match="unknown"):
        runtime._handle_asset_index_delete(event)
    assert authority.complete_calls == 0

    runtime._handle_asset_index_delete(event)
    runtime._handle_asset_index_delete(event)
    assert vectors.calls == 3
    assert authority.complete_calls == 2


def test_rights_reindex_requests_once_and_non_applicable_assets_are_noop() -> None:
    payload = AssetRightsChangedPayload(
        workspace_id="catalog-workspace",
        asset_id=ASSET_ID,
        asset_version_id=VERSION_ID,
        rights_record_id=RIGHTS_ID,
        rights_record_version=2,
        change="ACTIVATED",
        resulting_asset_state="AVAILABLE",
        required_convergence="REINDEX",
    )
    calls: list[tuple[str, str]] = []
    fused_calls: list[tuple[str, str]] = []

    class Requests:
        def request_current_image(self, *, workspace_id: str, asset_id: str) -> None:
            calls.append((workspace_id, asset_id))
            if len(calls) == 2:
                raise ImageIndexNotApplicable("not an IMAGE")

    class FusedRequests:
        @staticmethod
        def request_current_product_fused_for_asset(
            *, workspace_id: str, asset_id: str
        ) -> tuple[object, ...]:
            fused_calls.append((workspace_id, asset_id))
            return ()

    runtime = _runtime(
        image_index_requests=Requests(),
        product_fused_index_requests=FusedRequests(),
    )
    event = _event(ASSET_RIGHTS_CHANGED_V1, payload, aggregate_id=ASSET_ID)

    runtime._handle_asset_rights_changed(event)
    runtime._handle_asset_rights_changed(replace(event, envelope=event.envelope))

    assert calls == [
        ("catalog-workspace", ASSET_ID),
        ("catalog-workspace", ASSET_ID),
    ]
    assert fused_calls == [
        ("catalog-workspace", ASSET_ID),
        ("catalog-workspace", ASSET_ID),
    ]


def test_confirmed_product_brief_requests_product_fused_indexing_once() -> None:
    brief_id = "018f5f4d-7c11-7d11-8a11-777777777777"
    brief_version_id = "018f5f4d-7c11-7d11-8a11-888888888888"
    payload = ProductBriefConfirmedPayload(
        workspace_id="catalog-workspace",
        product_brief_id=brief_id,
        product_brief_version=3,
        product_brief_version_id=brief_version_id,
        product_brief_version_number=2,
        workflow_id="018f5f4d-7c11-7d11-8a11-999999999999",
        operation_id=OPERATION_ID,
        confirmation_id=None,
        confirmation_source="POLICY",
    )
    calls: list[tuple[str, str, str]] = []

    class Requests:
        @staticmethod
        def request_confirmed_brief(
            *,
            workspace_id: str,
            product_brief_id: str,
            product_brief_version_id: str,
        ) -> tuple[object, ...]:
            calls.append((workspace_id, product_brief_id, product_brief_version_id))
            return ()

    event = _event(PRODUCT_BRIEF_CONFIRMED_V1, payload, aggregate_id=brief_id)
    event = replace(
        event,
        envelope=replace(
            event.envelope,
            aggregate_type="ProductBrief",
            aggregate_version=payload.product_brief_version,
        ),
    )
    runtime = _runtime(product_fused_index_requests=Requests())

    runtime._observe_product_brief_state(event)

    assert calls == [("catalog-workspace", brief_id, brief_version_id)]


def test_settings_maximum_milvus_timeout_builds_exact_adapter_boundary(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def adapter(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(image_indexing_module, "MilvusVectorIndexAdapter", adapter)
    settings = Settings(milvus_timeout_seconds=60)

    image_indexing_module._build_vector_index(settings)

    assert captured["timeout_seconds"] == 60
    with pytest.raises(ValidationError, match="milvus_timeout_seconds"):
        Settings(milvus_timeout_seconds=60.01)


def test_embedding_readiness_resolves_mounted_credential_without_leaking_secret(
    monkeypatch,
    tmp_path,
) -> None:
    class Ready:
        @staticmethod
        def assert_ready() -> None:
            pass

        @staticmethod
        def close() -> None:
            pass

    monkeypatch.setattr(image_indexing_module, "_build_embedding_provider", lambda _s: Ready())
    monkeypatch.setattr(image_indexing_module, "_build_vector_index", lambda _s: Ready())
    missing = (tmp_path / "missing-embedding-key").resolve()
    common = {
        "environment": "ci",
        "worker_queues": ["commercevision.index"],
        "worker_required_operation_kinds": ["ASSET_INDEXING"],
        "embedding_adapter": "alibaba",
        "embedding_provider": "alibaba-model-studio",
        "embedding_model_family": "qwen3-vl-embedding",
        "embedding_model_id": "qwen3-vl-embedding",
        "embedding_pinned_revision": ("commercevision-qwen3-vl-embedding-epoch-2026-07-31"),
        "embedding_dimension": 1024,
        "alibaba_embedding_api_key_file": str(missing),
        "alibaba_embedding_allowed_image_origins": ["https://assets.example"],
        "embedding_data_transfer_enabled": True,
        "embedding_data_transfer_policy_version": "embedding-transfer-v1",
        "embedding_data_transfer_allowed_workspace_ids": ["catalog-workspace"],
        "embedding_data_transfer_allowed_retention_classes": ["FOUNDATION"],
        "embedding_data_transfer_allowed_providers": ["alibaba-model-studio"],
        "embedding_data_transfer_allowed_endpoint_regions": ["cn-beijing"],
        "embedding_data_transfer_allowed_endpoint_hosts": ["dashscope.aliyuncs.com"],
    }

    with pytest.raises(RuntimeError) as missing_error:
        image_indexing_module.probe_image_indexing_dependencies(Settings(**common))
    assert "secret" not in str(missing_error.value).casefold()
    assert missing_error.value.__cause__ is None
    assert missing_error.value.__context__ is None

    invalid = (tmp_path / "invalid-embedding-key").resolve()
    invalid.write_bytes(b"\xffprivate-secret")
    with pytest.raises(RuntimeError) as invalid_error:
        image_indexing_module.probe_image_indexing_dependencies(
            Settings(**(common | {"alibaba_embedding_api_key_file": str(invalid)}))
        )
    assert "private-secret" not in str(invalid_error.value)
    assert invalid_error.value.__cause__ is None
    assert invalid_error.value.__context__ is None

    valid = (tmp_path / "valid-embedding-key").resolve()
    valid.write_text("private-valid-secret\n", encoding="utf-8")
    status = image_indexing_module.probe_image_indexing_dependencies(
        Settings(**(common | {"alibaba_embedding_api_key_file": str(valid)}))
    )
    assert status == {"milvus": "ok", "embedding_provider": "ok"}
    assert "private-valid-secret" not in repr(status)


def test_image_index_readiness_cleanup_normalizes_raw_adapter_failures(monkeypatch) -> None:
    class RawCloseFailure:
        @staticmethod
        def assert_ready() -> None:
            pass

        @staticmethod
        def close() -> None:
            raise RuntimeError("cleanup credential-must-not-leak")

    monkeypatch.setattr(
        image_indexing_module,
        "_build_embedding_provider",
        lambda _settings: RawCloseFailure(),
    )
    monkeypatch.setattr(
        image_indexing_module,
        "_build_vector_index",
        lambda _settings: RawCloseFailure(),
    )

    with pytest.raises(ExceptionGroup) as captured:
        image_indexing_module.probe_image_indexing_dependencies(Settings())

    assert "must-not-leak" not in repr(captured.value)
    assert all(
        item.__cause__ is None and item.__context__ is None for item in captured.value.exceptions
    )


@pytest.mark.parametrize(
    ("operation_epoch", "asset_version_number"),
    [(1, 7), (2, 7)],
)
def test_asset_index_router_validates_operation_embedding_and_asset_identities(
    monkeypatch,
    operation_epoch: int,
    asset_version_number: int,
) -> None:
    payload = AssetIndexRequestedPayload(
        operation_id=OPERATION_ID,
        operation_epoch=operation_epoch,
        operation_input_hash=("c" if operation_epoch == 1 else "d") * 64,
        embedding_record_id=RECORD_ID,
        workspace_id="catalog-workspace",
        asset_id=ASSET_ID,
        asset_version_id=VERSION_ID,
        asset_version_number=asset_version_number,
        rights_record_id=RIGHTS_ID,
        rights_record_version=2,
        collection_id=COLLECTION_ID,
        vector_kind="IMAGE",
        provider="alibaba-model-studio",
        embedding_input_hash="a" * 64,
        embedding_spec_sha256="b" * 64,
    )
    operation = SimpleNamespace(
        kind=OperationKind.ASSET_INDEXING,
        target_type="embedding_record",
        target_id=RECORD_ID,
        target_version=operation_epoch,
        input_hash=payload.operation_input_hash,
    )

    class Uow:
        def __init__(self, _factory: object) -> None:
            self.operations = SimpleNamespace(
                get=lambda operation_id, *, workspace_id: (
                    operation
                    if (operation_id, workspace_id) == (OPERATION_ID, "catalog-workspace")
                    else None
                )
            )

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            pass

    class Authority:
        @staticmethod
        def validate_request_event(actual: AssetIndexRequestedPayload) -> bool:
            assert actual == payload
            assert actual.asset_version_number == 7
            return True

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(runtime_module, "SqlAlchemyOperationUnitOfWork", Uow)
    runtime = _runtime(
        database=SimpleNamespace(session_factory=object()),
        image_index_authority=Authority(),
        operation_worker=SimpleNamespace(
            execute=lambda *, workspace_id, operation_id: calls.append((workspace_id, operation_id))
        ),
    )
    event = _event(ASSET_INDEX_REQUESTED_V1, payload, aggregate_id=RECORD_ID)
    event = replace(
        event,
        envelope=replace(
            event.envelope,
            aggregate_type="embedding_record",
            trace_id=OPERATION_ID,
        ),
    )

    runtime._handle_asset_index(event)

    assert calls == [("catalog-workspace", OPERATION_ID)]
