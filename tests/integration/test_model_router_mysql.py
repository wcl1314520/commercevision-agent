from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Event

import pytest
from commercevision_application import ModelRouterApplicationService
from commercevision_domain import (
    CircuitState,
    ConcurrencyError,
    ImageRole,
    ModelRoutePolicy,
    ModelRoutePolicyVersion,
    ModelRouteRejectionCode,
    ModelRouteRequest,
    NoEligibleModelRouteError,
    NotFoundError,
    ProviderCapability,
    ProviderDataRetentionMode,
    ProviderEndpointCapabilityVersion,
    ProviderExecutionMode,
    ProviderPricingUnit,
    ProviderProtocol,
    ProviderTrainingUsePolicy,
)
from commercevision_persistence import SqlAlchemyModelRouterUnitOfWork
from commercevision_persistence.creative_plan_models import (
    CreativePlanModel,
    CreativePlanVersionModel,
)
from commercevision_persistence.model_router_models import ModelRouteDecisionModel
from commercevision_persistence.models import (
    ApprovalModel,
    AuditEventModel,
    IdempotencyKeyModel,
    WorkflowModel,
)
from commercevision_persistence.provider_control_plane_models import (
    ModelRoutePolicyHeadModel,
    ModelRoutePolicyVersionModel,
    ProviderEndpointCapabilityHeadModel,
    ProviderEndpointCapabilityVersionModel,
    ProviderEndpointObservationModel,
    ProviderIdentityModel,
)
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration

WORKSPACE_ID = "phase4-router-mysql"
WORKFLOW_ID = "019b0000-0000-7000-8000-000000000701"
PLAN_ID = "019b0000-0000-7000-8000-000000000702"
PLAN_VERSION_ID = "019b0000-0000-7000-8000-000000000703"
APPROVAL_ID = "019b0000-0000-7000-8000-000000000704"
POLICY_VERSION_ID = "019b0000-0000-7000-8000-000000000705"
CAPABILITY_VERSION_ID = "019b0000-0000-7000-8000-000000000706"
OBSERVATION_ID = "019b0000-0000-7000-8000-000000000707"


def _capability(now: datetime) -> ProviderEndpointCapabilityVersion:
    return ProviderEndpointCapabilityVersion.create(
        id=CAPABILITY_VERSION_ID,
        provider_id="provider-a",
        endpoint_id="provider-a-images",
        version_number=1,
        endpoint_host="provider-a.example.com",
        endpoint_region="cn-beijing",
        model_family="image-generation",
        model_id="image-model-v1",
        model_revision="2026-08-01",
        adapter_version="1.0.0",
        configuration_sha256="a" * 64,
        capabilities=frozenset({ProviderCapability.IMAGE_GENERATION}),
        protocol=ProviderProtocol.OPENAI_IMAGES_JSON,
        execution_mode=ProviderExecutionMode.SYNC,
        supports_query=False,
        supports_cancel=False,
        supports_provider_idempotency=False,
        allowed_categories=frozenset({"general-merchandise"}),
        allowed_image_roles=frozenset({ImageRole.MAIN}),
        output_formats=frozenset({"image/png"}),
        minimum_width=512,
        maximum_width=2048,
        minimum_height=512,
        maximum_height=2048,
        maximum_candidates=4,
        safety_policy_version="media-safety.v1",
        data_region="cn-beijing",
        data_retention_mode=ProviderDataRetentionMode.NONE,
        maximum_retention_days=0,
        training_use_policy=ProviderTrainingUsePolicy.PROHIBITED,
        secret_reference="secret-ref:router-test-provider-key",
        maximum_reference_images=0,
        supports_mask=False,
        supports_seed=False,
        supports_lora=False,
        maximum_request_bytes=4 * 1024 * 1024,
        maximum_result_bytes=16 * 1024 * 1024,
        pricing_unit=ProviderPricingUnit.IMAGE,
        enabled=True,
        unit_price=Decimal("0.10"),
        currency="CNY",
        created_at=now,
    )


def _request(now: datetime) -> ModelRouteRequest:
    return ModelRouteRequest(
        workspace_id=WORKSPACE_ID,
        workflow_id=WORKFLOW_ID,
        creative_plan_version_id=PLAN_VERSION_ID,
        plan_approval_id=APPROVAL_ID,
        required_capability=ProviderCapability.IMAGE_GENERATION,
        allowed_providers=frozenset({"provider-a"}),
        allowed_endpoint_regions=frozenset({"cn-beijing"}),
        maximum_cost=Decimal("0.10"),
        currency="CNY",
        route_policy_version="price-first.v1",
        deadline_at=now + timedelta(minutes=5),
        required_execution_mode=ProviderExecutionMode.SYNC,
        requires_query=False,
        required_quota_units=1,
        product_category="general-merchandise",
        image_role=ImageRole.MAIN,
        required_output_format="image/png",
        width=1024,
        height=1024,
        candidate_count=1,
        required_safety_policy_version="media-safety.v1",
        allowed_data_regions=frozenset({"cn-beijing"}),
        maximum_retention_days=0,
        allow_training_use=False,
    )


def _database_now(session) -> datetime:
    value = session.scalar(select(func.utc_timestamp(6)))
    assert isinstance(value, datetime)
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _seed_route_authority(integration_database) -> datetime:
    with integration_database.session_factory() as session:
        now = _database_now(session)
        capability = _capability(now)
        capability_json = capability.to_canonical_data()
        capability_json.pop("secret_reference")
        policy_version = ModelRoutePolicyVersion.create(
            id=POLICY_VERSION_ID,
            workspace_id=WORKSPACE_ID,
            policy_key="default-images",
            version_number=1,
            policy=ModelRoutePolicy(
                version="price-first.v1",
                quality_weight=Decimal("0"),
                availability_weight=Decimal("0"),
                latency_weight=Decimal("0"),
                quota_weight=Decimal("0"),
                price_weight=Decimal("1"),
            ),
            actor_id="provider-admin",
            now=now,
        )
        session.add_all(
            [
                WorkflowModel(
                    id=WORKFLOW_ID,
                    workspace_id=WORKSPACE_ID,
                    created_by="test-user",
                    workflow_type="CREATIVE_PRODUCTION",
                    status="GENERATING",
                    retention_status="ACTIVE",
                    current_node="GENERATE_CANDIDATES",
                    version=1,
                    input_json={},
                    result_json=None,
                    expires_at=now + timedelta(days=1),
                    cancellation_requested_at=None,
                    created_at=now,
                    updated_at=now,
                ),
                ProviderIdentityModel(
                    id="provider-a",
                    display_name="Provider A",
                    enabled=True,
                    version=1,
                    created_by="provider-admin",
                    created_at=now,
                    updated_by="provider-admin",
                    updated_at=now,
                ),
                ModelRoutePolicyVersionModel(
                    workspace_id=WORKSPACE_ID,
                    id=POLICY_VERSION_ID,
                    policy_key="default-images",
                    version_number=1,
                    policy_version=policy_version.policy.version,
                    policy_sha256=policy_version.policy_sha256,
                    policy_json=policy_version.to_canonical_data(),
                    quality_weight=policy_version.policy.quality_weight,
                    availability_weight=policy_version.policy.availability_weight,
                    latency_weight=policy_version.policy.latency_weight,
                    quota_weight=policy_version.policy.quota_weight,
                    price_weight=policy_version.policy.price_weight,
                    created_by="provider-admin",
                    created_at=now,
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                CreativePlanVersionModel(
                    id=PLAN_VERSION_ID,
                    workspace_id=WORKSPACE_ID,
                    workflow_id=WORKFLOW_ID,
                    creative_plan_id=PLAN_ID,
                    version_number=1,
                    supersedes_version_id=None,
                    source="AGENT",
                    payload_json={"schema_version": "creative-plan.v1"},
                    payload_sha256="b" * 64,
                    product_brief_id="019b0000-0000-7000-8000-000000000708",
                    product_brief_version=1,
                    product_brief_sha256="c" * 64,
                    brand_profile_id=None,
                    brand_profile_version=None,
                    brand_profile_sha256=None,
                    retrieval_run_id="019b0000-0000-7000-8000-000000000709",
                    retrieval_citation_ids_json=[],
                    context_policy_version="context.v1",
                    context_sha256="d" * 64,
                    prompt_id="creative-planner",
                    prompt_revision="1.0.0",
                    prompt_sha256="e" * 64,
                    actor_id="fixture-planner",
                    revision_reason=None,
                    retain_until=now + timedelta(days=1),
                    created_at=now,
                ),
                ProviderEndpointCapabilityVersionModel(
                    id=capability.id,
                    provider_id=capability.provider_id,
                    endpoint_id=capability.endpoint_id,
                    version_number=capability.version_number,
                    capability_sha256=capability.capability_sha256,
                    configuration_sha256=capability.configuration_sha256,
                    secret_reference=capability.secret_reference,
                    capability_json=capability_json,
                    unit_price=capability.unit_price,
                    currency=capability.currency,
                    created_by="provider-admin",
                    created_at=now,
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                CreativePlanModel(
                    id=PLAN_ID,
                    workspace_id=WORKSPACE_ID,
                    workflow_id=WORKFLOW_ID,
                    current_version_id=PLAN_VERSION_ID,
                    current_version_number=1,
                    version=1,
                    retain_until=now + timedelta(days=1),
                    created_at=now,
                    updated_at=now,
                ),
                ApprovalModel(
                    id=APPROVAL_ID,
                    workflow_id=WORKFLOW_ID,
                    approval_type="CREATIVE_PLAN",
                    subject_id=PLAN_ID,
                    subject_version=1,
                    decision="APPROVED",
                    reason_code=None,
                    comment_ref=None,
                    approved_by="reviewer",
                    expected_workflow_version=1,
                    created_at=now,
                ),
                ProviderEndpointCapabilityHeadModel(
                    provider_id=capability.provider_id,
                    endpoint_id=capability.endpoint_id,
                    current_version_id=capability.id,
                    current_version_number=1,
                    latest_version_number=1,
                    version=1,
                    updated_by="provider-admin",
                    updated_at=now,
                ),
                ModelRoutePolicyHeadModel(
                    workspace_id=WORKSPACE_ID,
                    policy_key="default-images",
                    current_version_id=POLICY_VERSION_ID,
                    current_version_number=1,
                    latest_version_number=1,
                    version=1,
                    updated_by="provider-admin",
                    updated_at=now,
                ),
                ProviderEndpointObservationModel(
                    workspace_id=WORKSPACE_ID,
                    id=OBSERVATION_ID,
                    endpoint_capability_version_id=capability.id,
                    quality_score=Decimal("1"),
                    availability_score=Decimal("1"),
                    latency_score=Decimal("1"),
                    quota_score=Decimal("1"),
                    circuit_state=CircuitState.CLOSED.value,
                    remaining_quota_units=10,
                    observation_source="HEALTH_PROBE",
                    idempotency_key_sha256="f" * 64,
                    observed_at=now,
                    created_by="health-monitor",
                    created_at=now,
                ),
            ]
        )
        session.commit()
        return now


def test_model_router_persists_and_replays_one_atomic_decision(integration_database) -> None:
    now = _seed_route_authority(integration_database)
    service = ModelRouterApplicationService(
        lambda: SqlAlchemyModelRouterUnitOfWork(integration_database.session_factory)
    )

    first = service.route(
        request=_request(now),
        policy_key="default-images",
        actor_id="generation-service",
        idempotency_key="test:route-command-mysql-001",
        trace_id="trace-route-mysql-001",
    )
    with integration_database.session_factory() as session:
        session.add(
            ProviderEndpointObservationModel(
                workspace_id=WORKSPACE_ID,
                id="019b0000-0000-7000-8000-000000000708",
                endpoint_capability_version_id=CAPABILITY_VERSION_ID,
                quality_score=Decimal("0"),
                availability_score=Decimal("0"),
                latency_score=Decimal("0"),
                quota_score=Decimal("0"),
                circuit_state=CircuitState.OPEN.value,
                remaining_quota_units=0,
                observation_source="CIRCUIT_TRANSITION",
                idempotency_key_sha256="1" * 64,
                observed_at=now,
                created_by="health-monitor",
                created_at=now,
            )
        )
        session.commit()
    replay = service.route(
        request=_request(now),
        policy_key="default-images",
        actor_id="generation-service",
        idempotency_key="test:route-command-mysql-001",
        trace_id="trace-route-mysql-replay",
    )

    assert first.replayed is False
    assert first.estimated_cost == Decimal("0.100000")
    assert replay.replayed is True
    assert replay.decision == first.decision
    with integration_database.session_factory() as session:
        decisions = tuple(session.scalars(select(ModelRouteDecisionModel)))
        audits = tuple(
            session.scalars(
                select(AuditEventModel).where(
                    AuditEventModel.resource_type == "model-route-decision"
                )
            )
        )
        assert len(decisions) == 1
        assert decisions[0].decision_sha256 == first.decision.decision_sha256
        assert decisions[0].estimated_cost == Decimal("0.100000")
        assert len(audits) == 1
        replay_facts = tuple(
            session.scalars(
                select(IdempotencyKeyModel).where(
                    IdempotencyKeyModel.resource_type == "model-route-decision"
                )
            )
        )
        assert len(replay_facts) == 1
        assert replay_facts[0].response_json is not None
        assert "secret-ref:" not in str(replay_facts[0].response_json)
        assert "secret-ref:" not in str(audits[0].metadata_json)

    with (
        pytest.raises(DBAPIError, match="immutable"),
        integration_database.engine.begin() as connection,
    ):
        connection.execute(
            text(
                "UPDATE model_route_decisions SET estimated_cost = 0.010000 "
                "WHERE workspace_id = :workspace_id AND decision_sha256 = :decision_sha256"
            ),
            {
                "workspace_id": WORKSPACE_ID,
                "decision_sha256": first.decision.decision_sha256,
            },
        )


@pytest.mark.parametrize(
    ("maximum_cost", "circuit_state", "remaining_quota", "expected_rejection"),
    [
        (
            Decimal("0.099999"),
            CircuitState.CLOSED,
            10,
            ModelRouteRejectionCode.BUDGET_EXCEEDED,
        ),
        (
            Decimal("0.100000"),
            CircuitState.OPEN,
            10,
            ModelRouteRejectionCode.CIRCUIT_OPEN,
        ),
        (
            Decimal("0.100000"),
            CircuitState.CLOSED,
            0,
            ModelRouteRejectionCode.QUOTA_EXHAUSTED,
        ),
    ],
)
def test_model_router_fails_closed_at_mysql_budget_circuit_and_quota_boundaries(
    integration_database,
    maximum_cost: Decimal,
    circuit_state: CircuitState,
    remaining_quota: int,
    expected_rejection: ModelRouteRejectionCode,
) -> None:
    now = _seed_route_authority(integration_database)
    if circuit_state is not CircuitState.CLOSED or remaining_quota == 0:
        with integration_database.session_factory() as session:
            session.add(
                ProviderEndpointObservationModel(
                    workspace_id=WORKSPACE_ID,
                    id="019b0000-0000-7000-8000-000000000708",
                    endpoint_capability_version_id=CAPABILITY_VERSION_ID,
                    quality_score=Decimal("1"),
                    availability_score=Decimal("1"),
                    latency_score=Decimal("1"),
                    quota_score=Decimal("1"),
                    circuit_state=circuit_state.value,
                    remaining_quota_units=remaining_quota,
                    observation_source="CIRCUIT_TRANSITION",
                    idempotency_key_sha256="1" * 64,
                    observed_at=now,
                    created_by="health-monitor",
                    created_at=now,
                )
            )
            session.commit()
    service = ModelRouterApplicationService(
        lambda: SqlAlchemyModelRouterUnitOfWork(integration_database.session_factory)
    )

    with pytest.raises(NoEligibleModelRouteError) as caught:
        service.route(
            request=replace(_request(now), maximum_cost=maximum_cost),
            policy_key="default-images",
            actor_id="generation-service",
            idempotency_key=f"test:route-denied-{expected_rejection.value.lower()}",
            trace_id="trace-route-denied",
        )

    assert caught.value.rejection_counts == ((expected_rejection, 1),)
    with integration_database.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ModelRouteDecisionModel)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEventModel)
                .where(AuditEventModel.resource_type == "model-route-decision")
            )
            == 0
        )


def test_model_router_rejects_stale_policy_pointer_and_foreign_workspace(
    integration_database,
) -> None:
    now = _seed_route_authority(integration_database)
    second = ModelRoutePolicyVersion.create(
        id="019b0000-0000-7000-8000-000000000710",
        workspace_id=WORKSPACE_ID,
        policy_key="default-images",
        version_number=2,
        policy=ModelRoutePolicy(
            version="price-first.v2",
            quality_weight=Decimal("0"),
            availability_weight=Decimal("0"),
            latency_weight=Decimal("0"),
            quota_weight=Decimal("0"),
            price_weight=Decimal("1"),
        ),
        actor_id="provider-admin",
        now=now,
    )
    with integration_database.session_factory() as session:
        session.add(
            ModelRoutePolicyVersionModel(
                workspace_id=WORKSPACE_ID,
                id=second.id,
                policy_key=second.policy_key,
                version_number=second.version_number,
                policy_version=second.policy.version,
                policy_sha256=second.policy_sha256,
                policy_json=second.to_canonical_data(),
                quality_weight=second.policy.quality_weight,
                availability_weight=second.policy.availability_weight,
                latency_weight=second.policy.latency_weight,
                quota_weight=second.policy.quota_weight,
                price_weight=second.policy.price_weight,
                created_by=second.created_by,
                created_at=second.created_at,
            )
        )
        session.flush()
        head = session.get(
            ModelRoutePolicyHeadModel,
            {"workspace_id": WORKSPACE_ID, "policy_key": "default-images"},
        )
        assert head is not None
        head.current_version_id = second.id
        head.current_version_number = 2
        head.latest_version_number = 2
        head.version = 2
        session.commit()
    service = ModelRouterApplicationService(
        lambda: SqlAlchemyModelRouterUnitOfWork(integration_database.session_factory)
    )

    with pytest.raises(ConcurrencyError, match="version changed"):
        service.route(
            request=_request(now),
            policy_key="default-images",
            actor_id="generation-service",
            idempotency_key="test:route-stale-policy",
            trace_id="trace-route-stale-policy",
        )
    with pytest.raises(NotFoundError, match="policy"):
        service.route(
            request=replace(_request(now), workspace_id="phase4-router-other"),
            policy_key="default-images",
            actor_id="generation-service",
            idempotency_key="test:route-foreign-workspace",
            trace_id="trace-route-foreign-workspace",
        )

    with integration_database.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ModelRouteDecisionModel)) == 0


def test_model_router_waits_for_inflight_circuit_transition_and_fails_closed(
    integration_database,
) -> None:
    now = _seed_route_authority(integration_database)
    transition_inserted = Event()
    allow_transition_commit = Event()
    route_read_started = Event()

    def observe_route_read(
        conn,
        cursor,
        statement: str,
        parameters,
        context,
        executemany: bool,
    ) -> None:
        del conn, cursor, parameters, context, executemany
        if "provider_endpoint_capability_versions" in statement and "FOR UPDATE" in statement:
            route_read_started.set()

    def commit_open_circuit() -> None:
        with integration_database.session_factory() as session:
            session.add(
                ProviderEndpointObservationModel(
                    workspace_id=WORKSPACE_ID,
                    id="019b0000-0000-7000-8000-000000000708",
                    endpoint_capability_version_id=CAPABILITY_VERSION_ID,
                    quality_score=Decimal("0"),
                    availability_score=Decimal("0"),
                    latency_score=Decimal("0"),
                    quota_score=Decimal("0"),
                    circuit_state=CircuitState.OPEN.value,
                    remaining_quota_units=0,
                    observation_source="CIRCUIT_TRANSITION",
                    idempotency_key_sha256="1" * 64,
                    observed_at=now,
                    created_by="health-monitor",
                    created_at=now,
                )
            )
            session.flush()
            transition_inserted.set()
            assert allow_transition_commit.wait(timeout=10)
            session.commit()

    service = ModelRouterApplicationService(
        lambda: SqlAlchemyModelRouterUnitOfWork(integration_database.session_factory)
    )
    event.listen(integration_database.engine, "before_cursor_execute", observe_route_read)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            transition = executor.submit(commit_open_circuit)
            assert transition_inserted.wait(timeout=10)
            route = executor.submit(
                service.route,
                request=_request(now),
                policy_key="default-images",
                actor_id="generation-service",
                idempotency_key="test:route-circuit-race",
                trace_id="trace-route-circuit-race",
            )
            assert route_read_started.wait(timeout=10)
            allow_transition_commit.set()
            transition.result(timeout=10)
            with pytest.raises(NoEligibleModelRouteError) as caught:
                route.result(timeout=10)
    finally:
        allow_transition_commit.set()
        event.remove(integration_database.engine, "before_cursor_execute", observe_route_read)

    assert caught.value.rejection_counts == ((ModelRouteRejectionCode.CIRCUIT_OPEN, 1),)
    with integration_database.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ModelRouteDecisionModel)) == 0
