from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from commercevision_application import (
    BrandProfileInvalidationApplicationService,
    BrandProfileInvalidationResult,
    EventRoutingError,
)
from commercevision_contracts import Settings
from commercevision_contracts.events import (
    ASSET_DELETE_COMPLETED_V1,
    ASSET_RIGHTS_CHANGED_V1,
    ASSET_RIGHTS_EXPIRED_V1,
    BRAND_PROFILE_PUBLISHED_V1,
    AssetDeleteCompletedPayload,
    AssetRightsChangedPayload,
    BrandProfilePublishedPayload,
)
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from commercevision_worker.runtime import WorkerRuntime

ASSET_ID = "018f5f4d-7c11-7d11-8a11-111111111111"
ASSET_VERSION_ID = "018f5f4d-7c11-7d11-8a11-222222222222"


class RecordingBrandProfileInvalidation:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, datetime]] = []
        self.deletion_calls: list[tuple[str, str, str, int, datetime]] = []

    def invalidate_asset(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        occurred_at: datetime,
    ) -> BrandProfileInvalidationResult:
        self.calls.append((workspace_id, asset_id, occurred_at))
        return BrandProfileInvalidationResult(matched_profiles=1, marked_profiles=1)

    def invalidate_foundation_asset_deletion(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        asset_version_id: str,
        deletion_generation: int,
        occurred_at: datetime,
    ) -> BrandProfileInvalidationResult:
        self.deletion_calls.append(
            (
                workspace_id,
                asset_id,
                asset_version_id,
                deletion_generation,
                occurred_at,
            )
        )
        return BrandProfileInvalidationResult(matched_profiles=1, marked_profiles=1)


def _published_event() -> OutboxEvent:
    payload = BrandProfilePublishedPayload(
        workspace_id="workspace-a",
        profile_id="0198a541-8e77-7000-8000-000000000001",
        profile_version_id="0198a541-8e77-7000-8000-000000000002",
        profile_version_number=1,
        content_sha256="a" * 64,
        member_count=2,
        published_by="brand-admin",
    )
    envelope = EventEnvelope.create(
        event_type=BRAND_PROFILE_PUBLISHED_V1.event_type.value,
        aggregate_type="BrandProfile",
        aggregate_id=payload.profile_id,
        aggregate_version=2,
        trace_id="trace-profile-published",
        payload=payload.model_dump(mode="json"),
    )
    return OutboxEvent(
        envelope=envelope,
        available_at=envelope.occurred_at,
        workspace_id=payload.workspace_id,
    )


def _foundation_asset_deleted_event() -> OutboxEvent:
    occurred_at = datetime(2026, 7, 30, 8, 30, 0, 654321, tzinfo=UTC)
    payload = AssetDeleteCompletedPayload(
        workspace_id="workspace-a",
        asset_id=ASSET_ID,
        asset_version_id=ASSET_VERSION_ID,
        retention_class="FOUNDATION",
        deletion_generation=4,
    ).model_dump(mode="json")
    payload["future_convergence_receipt"] = "forward-compatible"
    envelope = EventEnvelope.create(
        event_type=ASSET_DELETE_COMPLETED_V1.event_type.value,
        aggregate_type="Asset",
        aggregate_id=ASSET_ID,
        aggregate_version=4,
        trace_id="trace-foundation-delete",
        payload=payload,
        now=occurred_at,
    )
    return OutboxEvent(
        envelope=envelope,
        available_at=occurred_at,
        workspace_id="workspace-a",
    )


def test_worker_observes_typed_brand_profile_publication() -> None:
    runtime = WorkerRuntime.build(
        Settings(environment="ci", worker_queues=["commercevision.workflow"])
    )
    event = _published_event()
    try:
        handler = runtime.event_router.resolve(event.envelope)

        handler(event)

        with pytest.raises(EventRoutingError, match="workspace"):
            handler(replace(event, workspace_id="other-workspace"))
        with pytest.raises(EventRoutingError, match="aggregate"):
            handler(
                replace(
                    event,
                    envelope=replace(event.envelope, aggregate_id="other-profile"),
                )
            )
    finally:
        runtime.close()


def test_worker_build_composes_brand_profile_invalidation_by_default() -> None:
    runtime = WorkerRuntime.build(
        Settings(environment="ci", worker_queues=["commercevision.workflow"])
    )
    try:
        assert isinstance(
            runtime.brand_profile_invalidation,
            BrandProfileInvalidationApplicationService,
        )
    finally:
        runtime.close()


def test_worker_rechecks_brand_profile_authority_after_foundation_asset_deletion() -> None:
    invalidation = RecordingBrandProfileInvalidation()
    runtime = WorkerRuntime.build(
        Settings(environment="ci", worker_queues=["commercevision.workflow"]),
        brand_profile_invalidation=invalidation,
    )
    event = _foundation_asset_deleted_event()
    try:
        runtime.event_router.resolve(event.envelope)(event)

        assert invalidation.calls == []
        assert invalidation.deletion_calls == [
            (
                "workspace-a",
                ASSET_ID,
                ASSET_VERSION_ID,
                4,
                event.envelope.occurred_at,
            )
        ]
    finally:
        runtime.close()


@pytest.mark.parametrize(
    ("event_mutation", "message"),
    [
        (
            lambda event: replace(event, workspace_id="other-workspace"),
            "workspace",
        ),
        (
            lambda event: replace(
                event,
                envelope=replace(event.envelope, aggregate_type="Workflow"),
            ),
            "aggregate",
        ),
        (
            lambda event: replace(
                event,
                envelope=replace(
                    event.envelope,
                    aggregate_id="018f5f4d-7c11-7d11-8a11-333333333333",
                ),
            ),
            "aggregate",
        ),
        (
            lambda event: replace(
                event,
                envelope=replace(event.envelope, aggregate_version=3),
            ),
            "generation",
        ),
    ],
)
def test_worker_fails_closed_on_mismatched_foundation_asset_deletion_identity(
    event_mutation,
    message: str,
) -> None:
    invalidation = RecordingBrandProfileInvalidation()
    runtime = WorkerRuntime.build(
        Settings(environment="ci", worker_queues=["commercevision.workflow"]),
        brand_profile_invalidation=invalidation,
    )
    event = _foundation_asset_deleted_event()
    try:
        with pytest.raises(EventRoutingError, match=message):
            runtime.event_router.resolve(event.envelope)(event_mutation(event))
        assert invalidation.calls == []
        assert invalidation.deletion_calls == []
    finally:
        runtime.close()


@pytest.mark.parametrize(
    ("change", "asset_state", "convergence", "expired_contract"),
    [
        ("REGISTERED", "AVAILABLE", "REINDEX", False),
        ("REPLACED", "AVAILABLE", "REINDEX", False),
        ("ACTIVATED", "AVAILABLE", "REINDEX", False),
        ("REVOKED", "BLOCKED", "REMOVE_EXTERNAL_DERIVATIVES", False),
        ("EXPIRED", "RIGHTS_EXPIRED", "REMOVE_EXTERNAL_DERIVATIVES", True),
        ("ADMINISTRATOR_BLOCKED", "BLOCKED", "REMOVE_EXTERNAL_DERIVATIVES", False),
    ],
)
def test_worker_rechecks_current_brand_profile_authority_for_every_rights_transition(
    change: str,
    asset_state: str,
    convergence: str,
    expired_contract: bool,
) -> None:
    invalidation = RecordingBrandProfileInvalidation()
    runtime = WorkerRuntime.build(
        Settings(environment="ci", worker_queues=["commercevision.workflow"]),
        brand_profile_invalidation=invalidation,
    )
    occurred_at = datetime(2026, 7, 30, 8, 15, 12, 123456, tzinfo=UTC)
    rights_identity = change != "ADMINISTRATOR_BLOCKED"
    payload = AssetRightsChangedPayload(
        workspace_id="workspace-a",
        asset_id=ASSET_ID,
        asset_version_id=ASSET_VERSION_ID,
        rights_record_id=("018f5f4d-7c11-7d11-8a11-333333333333" if rights_identity else None),
        rights_record_version=2 if rights_identity else None,
        change=change,  # type: ignore[arg-type]
        resulting_asset_state=asset_state,  # type: ignore[arg-type]
        required_convergence=convergence,  # type: ignore[arg-type]
    )
    contract = ASSET_RIGHTS_EXPIRED_V1 if expired_contract else ASSET_RIGHTS_CHANGED_V1
    envelope = EventEnvelope.create(
        event_type=contract.event_type.value,
        aggregate_type="Asset",
        aggregate_id=payload.asset_id,
        # A delayed event's stale aggregate version must not bypass the live authority recheck.
        aggregate_version=1,
        trace_id=f"trace-rights-{change.lower()}",
        payload=payload.model_dump(mode="json"),
        now=occurred_at,
    )
    event = OutboxEvent(
        envelope=envelope,
        available_at=occurred_at,
        workspace_id=payload.workspace_id,
    )
    try:
        runtime.event_router.resolve(envelope)(event)

        assert invalidation.calls == [("workspace-a", ASSET_ID, occurred_at)]
    finally:
        runtime.close()


def test_worker_permanently_rejects_noncanonical_asset_rights_identity() -> None:
    invalidation = RecordingBrandProfileInvalidation()
    runtime = WorkerRuntime.build(
        Settings(environment="ci", worker_queues=["commercevision.workflow"]),
        brand_profile_invalidation=invalidation,
    )
    payload = AssetRightsChangedPayload(
        workspace_id="workspace-a",
        asset_id="asset-1",
        asset_version_id="asset-version-1",
        rights_record_id="rights-record-2",
        rights_record_version=2,
        change="REVOKED",
        resulting_asset_state="BLOCKED",
        required_convergence="REMOVE_EXTERNAL_DERIVATIVES",
    )
    envelope = EventEnvelope.create(
        event_type=ASSET_RIGHTS_CHANGED_V1.event_type.value,
        aggregate_type="Asset",
        aggregate_id=payload.asset_id,
        aggregate_version=2,
        trace_id="trace-malformed-asset-rights",
        payload=payload.model_dump(mode="json"),
    )
    event = OutboxEvent(
        envelope=envelope,
        available_at=envelope.occurred_at,
        workspace_id=payload.workspace_id,
    )
    try:
        with pytest.raises(EventRoutingError, match="canonical Asset identity") as error:
            runtime.event_router.resolve(envelope)(event)

        assert error.value.reason == "malformed_asset_identity"
        assert invalidation.calls == []
    finally:
        runtime.close()
