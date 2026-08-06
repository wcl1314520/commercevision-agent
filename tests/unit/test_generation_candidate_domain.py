from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from commercevision_domain import (
    CandidateImage,
    CandidateSlot,
    GenerationBatch,
    ImageEditingRequest,
    ImageGenerationRequest,
    OperationKind,
    ProviderCall,
    ProviderCallOutcome,
    ProviderPricingUnit,
    UsageEvidenceSource,
    UsageRecord,
    UsageResolutionStatus,
    create_candidate_slots,
    validate_candidate_request_authority,
)

NOW = datetime(2026, 8, 6, 12, 15, tzinfo=UTC)


def _generation_batch(
    *,
    candidate_count: int = 3,
    authorized_asset_version_ids: tuple[str, ...] = (),
    operation_kind: OperationKind = OperationKind.IMAGE_GENERATION,
    retention_deadline: datetime = NOW + timedelta(hours=24),
    workflow_deadline: datetime = NOW + timedelta(hours=48),
    source_rights_deadline: datetime | None = None,
    edit_source_asset_version_id: str | None = None,
    edit_mask_asset_version_id: str | None = None,
    approved_repair_scope: tuple[str, ...] = (),
) -> GenerationBatch:
    return GenerationBatch(
        id="019b0000-0000-7000-8000-000000000501",
        workspace_id="phase4-domain",
        workflow_id="019b0000-0000-7000-8000-000000000502",
        workflow_version=7,
        creative_plan_version_id="019b0000-0000-7000-8000-000000000503",
        plan_approval_id="019b0000-0000-7000-8000-000000000504",
        direction_key="main-hero",
        tool_intent_key="generate-main-hero",
        tool_intent_sha256="a" * 64,
        prompt_sha256="c" * 64,
        context_sha256="d" * 64,
        route_request_sha256="b" * 64,
        operation_kind=operation_kind,
        authorized_asset_version_ids=authorized_asset_version_ids,
        candidate_count=candidate_count,
        route_policy_version="route-policy.v1",
        tool_policy_version="tool-policy.v1",
        rights_policy_version="rights-policy.v1",
        safety_policy_version="media-safety.v1",
        workflow_deadline=workflow_deadline,
        source_rights_deadline=source_rights_deadline,
        edit_source_asset_version_id=edit_source_asset_version_id,
        edit_mask_asset_version_id=edit_mask_asset_version_id,
        approved_repair_scope=approved_repair_scope,
        retention_deadline=retention_deadline,
        created_by="user-42",
        created_at=NOW,
    )


def test_generation_batch_derives_contiguous_candidate_slot_operation_identities() -> None:
    batch = _generation_batch()
    operation_ids = (
        "019b0000-0000-7000-8000-000000000511",
        "019b0000-0000-7000-8000-000000000512",
        "019b0000-0000-7000-8000-000000000513",
    )

    slots = create_candidate_slots(
        batch=batch,
        durable_operation_ids=operation_ids,
    )
    reconstructed = create_candidate_slots(
        batch=batch,
        durable_operation_ids=operation_ids,
    )

    assert tuple(slot.candidate_index for slot in slots) == (0, 1, 2)
    assert tuple(slot.durable_operation_id for slot in slots) == operation_ids
    assert all(slot.operation_kind is OperationKind.IMAGE_GENERATION for slot in slots)
    assert all(slot.generation_batch_id == batch.id for slot in slots)
    assert len({slot.logical_identity_sha256 for slot in slots}) == 3
    assert len({slot.operation_idempotency_key for slot in slots}) == 3
    assert tuple(slot.logical_identity_sha256 for slot in slots) == tuple(
        slot.logical_identity_sha256 for slot in reconstructed
    )
    assert tuple(slot.operation_idempotency_key for slot in slots) == tuple(
        slot.operation_idempotency_key for slot in reconstructed
    )
    assert batch.batch_sha256 == "c556354962613ae0c82255787c5676c9fb32fc4403f401c919c4a8e83ece1fac"
    assert tuple(slot.id for slot in slots) == (
        "fa699a06-f843-573a-b346-a78ff0aa6281",
        "d345862c-a16e-576d-91ef-34bf429f2fe2",
        "35f655a0-e8e7-5b51-be3d-b69d2606c9a1",
    )
    assert tuple(slot.logical_identity_sha256 for slot in slots) == (
        "fc53bfd18cda4cc851860872af9cdd8dfb8bc34056668932963663613a7678bb",
        "d05dc48048e004bde92cfdc3590adb9683805e751a6a738d80a160d69d0f2276",
        "1c0fced62640c354ef228957d910b2d86867a9f5e9e9b3f143d2c75fd5946d20",
    )
    with pytest.raises(FrozenInstanceError):
        slots[0].candidate_index = 7  # type: ignore[misc]


def test_candidate_collections_and_slot_operation_bindings_are_bounded() -> None:
    too_many_assets = tuple(f"019b0000-0000-7000-8000-{index:012d}" for index in range(601, 618))

    with pytest.raises(ValueError, match="candidate count"):
        _generation_batch(candidate_count=17)
    with pytest.raises(ValueError, match="authorized Asset Version"):
        _generation_batch(authorized_asset_version_ids=too_many_assets)
    with pytest.raises(ValueError, match="reference Asset Version"):
        ImageGenerationRequest(
            candidate_slot_id="019b0000-0000-7000-8000-000000000521",
            prompt_sha256="c" * 64,
            context_sha256="d" * 64,
            reference_asset_version_ids=too_many_assets,
        )
    with pytest.raises(ValueError, match="repair scope"):
        ImageEditingRequest(
            candidate_slot_id="019b0000-0000-7000-8000-000000000523",
            prompt_sha256="c" * 64,
            context_sha256="d" * 64,
            source_asset_version_id="019b0000-0000-7000-8000-000000000524",
            mask_asset_version_id="019b0000-0000-7000-8000-000000000525",
            approved_repair_scope=tuple(f"scope-{index}" for index in range(17)),
        )

    batch = _generation_batch(candidate_count=2)
    with pytest.raises(ValueError, match="match the candidate count"):
        create_candidate_slots(
            batch=batch,
            durable_operation_ids=("019b0000-0000-7000-8000-000000000511",),
        )
    with pytest.raises(ValueError, match="match the candidate count"):
        create_candidate_slots(
            batch=batch,
            durable_operation_ids=(
                "019b0000-0000-7000-8000-000000000511",
                "019b0000-0000-7000-8000-000000000511",
            ),
        )


def test_editing_request_cannot_expand_or_swap_batch_authority() -> None:
    source_id = "019b0000-0000-7000-8000-000000000524"
    mask_id = "019b0000-0000-7000-8000-000000000525"
    batch = _generation_batch(
        candidate_count=1,
        authorized_asset_version_ids=(source_id, mask_id),
        operation_kind=OperationKind.IMAGE_EDITING,
        source_rights_deadline=NOW + timedelta(hours=25),
        edit_source_asset_version_id=source_id,
        edit_mask_asset_version_id=mask_id,
        approved_repair_scope=("background", "lighting"),
    )
    slot = create_candidate_slots(
        batch=batch,
        durable_operation_ids=("019b0000-0000-7000-8000-000000000511",),
    )[0]
    approved = ImageEditingRequest(
        candidate_slot_id=slot.id,
        prompt_sha256="c" * 64,
        context_sha256="d" * 64,
        source_asset_version_id=source_id,
        mask_asset_version_id=mask_id,
        approved_repair_scope=("background", "lighting"),
    )

    validate_candidate_request_authority(batch=batch, slot=slot, request=approved)

    expanded = ImageEditingRequest(
        candidate_slot_id=slot.id,
        prompt_sha256="c" * 64,
        context_sha256="d" * 64,
        source_asset_version_id=source_id,
        mask_asset_version_id=mask_id,
        approved_repair_scope=("background", "lighting", "product"),
    )
    with pytest.raises(ValueError, match="repair scope"):
        validate_candidate_request_authority(batch=batch, slot=slot, request=expanded)

    swapped = ImageEditingRequest(
        candidate_slot_id=slot.id,
        prompt_sha256="c" * 64,
        context_sha256="d" * 64,
        source_asset_version_id=mask_id,
        mask_asset_version_id=source_id,
        approved_repair_scope=("background", "lighting"),
    )
    with pytest.raises(ValueError, match="source or mask"):
        validate_candidate_request_authority(batch=batch, slot=slot, request=swapped)


def test_batch_retention_cannot_outlive_workflow_or_source_rights() -> None:
    with pytest.raises(ValueError, match="Workflow deadline"):
        _generation_batch(
            retention_deadline=NOW + timedelta(hours=25),
            workflow_deadline=NOW + timedelta(hours=24),
        )
    with pytest.raises(ValueError, match="Rights deadline"):
        _generation_batch(
            authorized_asset_version_ids=("019b0000-0000-7000-8000-000000000522",),
            retention_deadline=NOW + timedelta(hours=25),
            source_rights_deadline=NOW + timedelta(hours=24),
        )


def test_generation_and_editing_requests_have_distinct_exact_authority() -> None:
    generation = ImageGenerationRequest(
        candidate_slot_id="019b0000-0000-7000-8000-000000000521",
        prompt_sha256="c" * 64,
        context_sha256="d" * 64,
        reference_asset_version_ids=("019b0000-0000-7000-8000-000000000522",),
    )
    editing = ImageEditingRequest(
        candidate_slot_id="019b0000-0000-7000-8000-000000000523",
        prompt_sha256="c" * 64,
        context_sha256="d" * 64,
        source_asset_version_id="019b0000-0000-7000-8000-000000000524",
        mask_asset_version_id="019b0000-0000-7000-8000-000000000525",
        approved_repair_scope=("background", "lighting"),
    )

    assert generation.operation_kind is OperationKind.IMAGE_GENERATION
    assert editing.operation_kind is OperationKind.IMAGE_EDITING
    assert editing.source_asset_version_id != editing.mask_asset_version_id
    assert editing.approved_repair_scope == ("background", "lighting")
    assert generation.request_sha256 == (
        "30dede61940352e9eefa603488425d3e4bb2b5e6d2fdca6ece2531eb25f2954c"
    )
    assert editing.request_sha256 == (
        "993818c7da1ad1436b5f26c6ef32e656df4ba66812b80780749ef5ebe2bdf44a"
    )
    assert not hasattr(generation, "source_asset_version_id")
    assert not hasattr(editing, "provider_url")


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("source_asset_version_id", "019b0000-0000-7000-8000-000000000525"),
        ("mask_asset_version_id", "019b0000-0000-7000-8000-000000000524"),
        ("approved_repair_scope", ()),
    ],
)
def test_editing_request_rejects_ambiguous_or_empty_repair_authority(
    field_name: str,
    value: str | tuple[()],
) -> None:
    values: dict[str, object] = {
        "candidate_slot_id": "019b0000-0000-7000-8000-000000000523",
        "prompt_sha256": "c" * 64,
        "context_sha256": "d" * 64,
        "source_asset_version_id": "019b0000-0000-7000-8000-000000000524",
        "mask_asset_version_id": "019b0000-0000-7000-8000-000000000525",
        "approved_repair_scope": ("background",),
    }
    values[field_name] = value

    with pytest.raises(ValueError):
        ImageEditingRequest(**values)  # type: ignore[arg-type]


def test_candidate_image_is_an_immutable_controlled_asset_fact() -> None:
    candidate = CandidateImage(
        id="019b0000-0000-7000-8000-000000000531",
        workspace_id="phase4-domain",
        workflow_id="019b0000-0000-7000-8000-000000000502",
        generation_batch_id="019b0000-0000-7000-8000-000000000501",
        candidate_slot_id="019b0000-0000-7000-8000-000000000521",
        task_asset_version_id="019b0000-0000-7000-8000-000000000532",
        content_sha256="e" * 64,
        width=1024,
        height=1024,
        image_format="png",
        source_asset_version_ids=("019b0000-0000-7000-8000-000000000522",),
        creative_plan_version_id="019b0000-0000-7000-8000-000000000503",
        prompt_sha256="c" * 64,
        context_sha256="d" * 64,
        retrieval_snapshot_sha256="f" * 64,
        endpoint_capability_version_id="019b0000-0000-7000-8000-000000000533",
        provider_call_id="019b0000-0000-7000-8000-000000000534",
        provider_request_id_sha256="1" * 64,
        moderation_decision_sha256="2" * 64,
        usage_record_id="019b0000-0000-7000-8000-000000000535",
        created_at=NOW,
        retention_deadline=NOW + timedelta(hours=24),
    )

    assert candidate.task_asset_version_id.endswith("0532")
    assert candidate.content_sha256 == "e" * 64
    assert candidate.usage_record_id.endswith("0535")
    assert not hasattr(candidate, "is_available")
    assert not hasattr(candidate, "provider_url")
    assert not hasattr(candidate, "object_key")
    with pytest.raises(FrozenInstanceError):
        candidate.task_asset_version_id = "replacement"  # type: ignore[misc]


def test_candidate_image_rejects_an_output_version_reused_as_its_source() -> None:
    output_version_id = "019b0000-0000-7000-8000-000000000532"

    with pytest.raises(ValueError, match="output.*source"):
        CandidateImage(
            id="019b0000-0000-7000-8000-000000000531",
            workspace_id="phase4-domain",
            workflow_id="019b0000-0000-7000-8000-000000000502",
            generation_batch_id="019b0000-0000-7000-8000-000000000501",
            candidate_slot_id="019b0000-0000-7000-8000-000000000521",
            task_asset_version_id=output_version_id,
            content_sha256="e" * 64,
            width=1024,
            height=1024,
            image_format="png",
            source_asset_version_ids=(output_version_id,),
            creative_plan_version_id="019b0000-0000-7000-8000-000000000503",
            prompt_sha256="c" * 64,
            context_sha256="d" * 64,
            retrieval_snapshot_sha256="f" * 64,
            endpoint_capability_version_id="019b0000-0000-7000-8000-000000000533",
            provider_call_id="019b0000-0000-7000-8000-000000000534",
            provider_request_id_sha256="1" * 64,
            moderation_decision_sha256="2" * 64,
            usage_record_id="019b0000-0000-7000-8000-000000000535",
            created_at=NOW,
            retention_deadline=NOW + timedelta(hours=24),
        )


def test_unknown_provider_call_preserves_reconciliation_identity_and_forbids_resubmit() -> None:
    call = ProviderCall(
        id="019b0000-0000-7000-8000-000000000541",
        workspace_id="phase4-domain",
        candidate_slot_id="019b0000-0000-7000-8000-000000000521",
        durable_operation_id="019b0000-0000-7000-8000-000000000511",
        operation_attempt=1,
        call_index=0,
        route_decision_id="019b0000-0000-7000-8000-000000000542",
        endpoint_capability_version_id="019b0000-0000-7000-8000-000000000533",
        provider="kuaipao",
        model="gpt-image-1",
        request_sha256="3" * 64,
        idempotency_key_sha256="4" * 64,
        outcome=ProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH,
        possible_dispatch=True,
        provider_request_id_sha256="5" * 64,
        latency_ms=30_000,
        observed_at=NOW,
    )

    assert call.must_reconcile is True
    assert call.is_automatic_resubmission_safe is False
    assert call.call_identity_sha256 == (
        "b12a004908d18bfcc511fb6a3df5e2abcc67b665ac85014cdb500b32fc37e19d"
    )
    assert not hasattr(call, "endpoint_url")
    assert not hasattr(call, "provider_request_id")
    with pytest.raises(FrozenInstanceError):
        call.provider_request_id_sha256 = "6" * 64  # type: ignore[misc]


def test_provider_call_rejects_outcome_dispatch_contradictions() -> None:
    values: dict[str, object] = {
        "id": "019b0000-0000-7000-8000-000000000541",
        "workspace_id": "phase4-domain",
        "candidate_slot_id": "019b0000-0000-7000-8000-000000000521",
        "durable_operation_id": "019b0000-0000-7000-8000-000000000511",
        "operation_attempt": 1,
        "call_index": 0,
        "route_decision_id": "019b0000-0000-7000-8000-000000000542",
        "endpoint_capability_version_id": "019b0000-0000-7000-8000-000000000533",
        "provider": "kuaipao",
        "model": "gpt-image-1",
        "request_sha256": "3" * 64,
        "idempotency_key_sha256": "4" * 64,
        "outcome": ProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH,
        "possible_dispatch": False,
        "provider_request_id_sha256": None,
        "latency_ms": 1,
        "observed_at": NOW,
    }

    with pytest.raises(ValueError, match="unknown.*possible dispatch"):
        ProviderCall(**values)  # type: ignore[arg-type]

    values.update(
        outcome=ProviderCallOutcome.SAFE_TO_RETRY_PRE_DISPATCH,
        possible_dispatch=False,
        provider_request_id_sha256="5" * 64,
    )
    with pytest.raises(ValueError, match="pre-dispatch.*request identity"):
        ProviderCall(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("outcome", "possible_dispatch", "must_reconcile", "safe_to_resubmit"),
    [
        (ProviderCallOutcome.CONFIRMED_SUCCESS, True, False, False),
        (ProviderCallOutcome.CONFIRMED_FAILURE, True, False, False),
        (ProviderCallOutcome.CONTENT_REJECTED, True, False, False),
        (ProviderCallOutcome.SAFE_TO_RETRY_PRE_DISPATCH, False, False, True),
        (ProviderCallOutcome.UNKNOWN_AFTER_POSSIBLE_DISPATCH, True, True, False),
    ],
)
def test_provider_call_outcomes_define_the_only_retry_and_reconciliation_lifecycle(
    outcome: ProviderCallOutcome,
    possible_dispatch: bool,
    must_reconcile: bool,
    safe_to_resubmit: bool,
) -> None:
    call = ProviderCall(
        id="019b0000-0000-7000-8000-000000000541",
        workspace_id="phase4-domain",
        candidate_slot_id="019b0000-0000-7000-8000-000000000521",
        durable_operation_id="019b0000-0000-7000-8000-000000000511",
        operation_attempt=1,
        call_index=0,
        route_decision_id="019b0000-0000-7000-8000-000000000542",
        endpoint_capability_version_id="019b0000-0000-7000-8000-000000000533",
        provider="kuaipao",
        model="gpt-image-1",
        request_sha256="3" * 64,
        idempotency_key_sha256="4" * 64,
        outcome=outcome,
        possible_dispatch=possible_dispatch,
        provider_request_id_sha256=None,
        latency_ms=1,
        observed_at=NOW,
    )

    assert call.must_reconcile is must_reconcile
    assert call.is_automatic_resubmission_safe is safe_to_resubmit


def test_usage_record_keeps_provider_pricing_estimate_and_final_evidence_separate() -> None:
    record = UsageRecord(
        id="019b0000-0000-7000-8000-000000000551",
        workspace_id="phase4-domain",
        provider_call_id="019b0000-0000-7000-8000-000000000541",
        provider_call_identity_sha256="6" * 64,
        durable_operation_id="019b0000-0000-7000-8000-000000000511",
        operation_attempt=1,
        provider="kuaipao",
        model="gpt-image-1",
        endpoint_capability_version_id="019b0000-0000-7000-8000-000000000533",
        pricing_unit=ProviderPricingUnit.IMAGE,
        estimated_quantity=Decimal("1.000000"),
        provider_reported_quantity=Decimal("1.000000"),
        configured_unit_price=Decimal("0.250000"),
        estimated_amount=Decimal("0.250000"),
        actual_amount=Decimal("0.200000"),
        currency="CNY",
        unit_price_version="kuaipao-image.v7",
        provider_usage_evidence_sha256="7" * 64,
        pricing_evidence_sha256="8" * 64,
        final_cost_evidence_sha256="9" * 64,
        resolution_status=UsageResolutionStatus.FINALIZED,
        evidence_source=UsageEvidenceSource.DIRECT_RESPONSE,
        latency_ms=30_000,
        recorded_at=NOW,
    )

    assert record.deduplication_key == f"usage:{'6' * 64}"
    assert record.is_budget_releasable is True
    assert record.actual_amount == Decimal("0.200000")
    assert record.estimated_amount != record.actual_amount
    with pytest.raises(FrozenInstanceError):
        record.actual_amount = Decimal("0")  # type: ignore[misc]


def test_missing_provider_usage_remains_unresolved_instead_of_becoming_zero() -> None:
    values: dict[str, object] = {
        "id": "019b0000-0000-7000-8000-000000000551",
        "workspace_id": "phase4-domain",
        "provider_call_id": "019b0000-0000-7000-8000-000000000541",
        "provider_call_identity_sha256": "6" * 64,
        "durable_operation_id": "019b0000-0000-7000-8000-000000000511",
        "operation_attempt": 1,
        "provider": "kuaipao",
        "model": "gpt-image-1",
        "endpoint_capability_version_id": "019b0000-0000-7000-8000-000000000533",
        "pricing_unit": ProviderPricingUnit.IMAGE,
        "estimated_quantity": Decimal("1"),
        "provider_reported_quantity": None,
        "configured_unit_price": Decimal("0.2"),
        "estimated_amount": Decimal("0.2"),
        "actual_amount": None,
        "currency": "CNY",
        "unit_price_version": "kuaipao-image.v7",
        "provider_usage_evidence_sha256": None,
        "pricing_evidence_sha256": "8" * 64,
        "final_cost_evidence_sha256": None,
        "resolution_status": UsageResolutionStatus.UNRESOLVED,
        "evidence_source": UsageEvidenceSource.DIRECT_RESPONSE,
        "latency_ms": 30_000,
        "recorded_at": NOW,
    }

    unresolved = UsageRecord(**values)  # type: ignore[arg-type]

    assert unresolved.actual_amount is None
    assert unresolved.is_budget_releasable is False

    values["actual_amount"] = Decimal("0")
    with pytest.raises(ValueError, match="unresolved.*actual"):
        UsageRecord(**values)  # type: ignore[arg-type]

    values.update(
        actual_amount=None,
        estimated_amount=Decimal("0.3"),
    )
    with pytest.raises(ValueError, match="estimated amount"):
        UsageRecord(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("estimated_quantity", 1.0),
        ("configured_unit_price", 0.2),
        ("currency", "cny"),
    ],
)
def test_usage_record_rejects_float_money_and_noncanonical_currency(
    field_name: str,
    value: object,
) -> None:
    values: dict[str, object] = {
        "id": "019b0000-0000-7000-8000-000000000551",
        "workspace_id": "phase4-domain",
        "provider_call_id": "019b0000-0000-7000-8000-000000000541",
        "provider_call_identity_sha256": "6" * 64,
        "durable_operation_id": "019b0000-0000-7000-8000-000000000511",
        "operation_attempt": 1,
        "provider": "kuaipao",
        "model": "gpt-image-1",
        "endpoint_capability_version_id": "019b0000-0000-7000-8000-000000000533",
        "pricing_unit": ProviderPricingUnit.IMAGE,
        "estimated_quantity": Decimal("1"),
        "provider_reported_quantity": Decimal("1"),
        "configured_unit_price": Decimal("0.2"),
        "estimated_amount": Decimal("0.2"),
        "actual_amount": Decimal("0.2"),
        "currency": "CNY",
        "unit_price_version": "kuaipao-image.v7",
        "provider_usage_evidence_sha256": "7" * 64,
        "pricing_evidence_sha256": "8" * 64,
        "final_cost_evidence_sha256": "9" * 64,
        "resolution_status": UsageResolutionStatus.FINALIZED,
        "evidence_source": UsageEvidenceSource.DIRECT_RESPONSE,
        "latency_ms": 30_000,
        "recorded_at": NOW,
    }
    values[field_name] = value

    with pytest.raises(ValueError):
        UsageRecord(**values)  # type: ignore[arg-type]


def test_generation_domain_facts_exclude_external_authority_and_parallel_state() -> None:
    forbidden_fields = {
        "api_key",
        "base_url",
        "bucket",
        "credential",
        "endpoint_url",
        "headers",
        "object_key",
        "provider_request_id",
        "provider_url",
        "raw_response",
        "secret",
        "secret_ref",
        "state",
    }

    for fact_type in (
        GenerationBatch,
        CandidateSlot,
        ImageGenerationRequest,
        ImageEditingRequest,
        CandidateImage,
        ProviderCall,
        UsageRecord,
    ):
        assert forbidden_fields.isdisjoint(field.name for field in fields(fact_type))
