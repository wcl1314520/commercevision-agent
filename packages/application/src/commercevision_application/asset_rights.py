"""Rights Record mutations, history, expiry, and current-usability authority."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from commercevision_contracts import (
    AssetAdministratorBlockRequestV1,
    RightsHistoryResponseV1,
    RightsMutationResponseV1,
    RightsRecordMutationRequestV1,
    RightsRecordResponseV1,
    RightsRecordRevokeRequestV1,
    RightsUsabilityRequestV1,
    RightsUsabilityResponseV1,
)
from commercevision_contracts.events import (
    AssetRightsChangedPayload,
    EventType,
)
from commercevision_domain import (
    Asset,
    AssetDeletionReason,
    AssetState,
    ConcurrencyError,
    InvalidTransitionError,
    NotFoundError,
    RetentionClass,
    RightsRecord,
    RightsRecordDecision,
    evaluate_current_usability,
    new_uuid7,
    validate_workspace_id,
)
from commercevision_domain.messaging import EventEnvelope, OutboxEvent

from .asset_deletion import AssetDeletionPolicy, schedule_asset_deletion
from .asset_idempotency import (
    canonical_hash,
    claim_idempotency,
    idempotency_scope,
    key_hash,
)
from .asset_ports import AssetUnitOfWorkFactory, AssetUnitOfWorkPort
from .asset_registry_facts import (
    canonicalize_resource_id,
    idempotency_expiry,
)

_AUDIT_RETENTION = timedelta(days=180)


def rights_record_response(record: RightsRecord) -> RightsRecordResponseV1:
    return RightsRecordResponseV1(
        id=record.id,
        workspace_id=record.workspace_id,
        asset_id=record.asset_id,
        asset_version_id=record.asset_version_id,
        version_number=record.version_number,
        decision=record.decision,
        owner_reference=record.owner_reference,
        source=record.source,
        license_reference=record.license_reference,
        allowed_uses=sorted(record.allowed_uses),
        allowed_providers=sorted(record.allowed_providers),
        derivative_allowed=record.derivative_allowed,
        public_demo_allowed=record.public_demo_allowed,
        evidence_reference=record.evidence_reference,
        terms_sha256=record.terms_sha256,
        valid_from=record.valid_from,
        valid_until=record.valid_until,
        perpetual=record.perpetual,
        supersedes_record_id=record.supersedes_record_id,
        created_by=record.created_by,
        created_at=record.created_at,
    )


class AssetRightsApplicationService:
    def __init__(
        self,
        *,
        uow_factory: AssetUnitOfWorkFactory,
        deletion_policy: AssetDeletionPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._deletion_policy = deletion_policy or AssetDeletionPolicy(
            max_attempts=8,
            max_reconciliation_attempts=20,
            execution_max_elapsed=timedelta(hours=24),
        )
        self._clock = clock or (lambda: datetime.now(UTC))

    def register(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        actor_id: str,
        idempotency_key: str,
        trace_id: str,
        request: RightsRecordMutationRequestV1,
    ) -> RightsMutationResponseV1:
        return self._grant(
            workspace_id=workspace_id,
            asset_id=asset_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
            request=request,
            replacing=False,
        )

    def replace(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        actor_id: str,
        idempotency_key: str,
        trace_id: str,
        request: RightsRecordMutationRequestV1,
    ) -> RightsMutationResponseV1:
        return self._grant(
            workspace_id=workspace_id,
            asset_id=asset_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
            request=request,
            replacing=True,
        )

    def revoke(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        actor_id: str,
        idempotency_key: str,
        trace_id: str,
        request: RightsRecordRevokeRequestV1,
    ) -> RightsMutationResponseV1:
        return self._mutation(
            workspace_id=workspace_id,
            asset_id=asset_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
            operation="rights-revoke",
            request_data=request.model_dump(mode="json"),
            mutate=lambda uow, asset, now: self._revoke(
                uow=uow,
                asset=asset,
                actor_id=actor_id,
                request=request,
                now=now,
            ),
            change="REVOKED",
            convergence="REMOVE_EXTERNAL_DERIVATIVES",
            audit_metadata={
                "reason": request.reason,
                "evidence_reference": request.evidence_reference,
            },
        )

    def administrator_block(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        actor_id: str,
        idempotency_key: str,
        trace_id: str,
        request: AssetAdministratorBlockRequestV1,
    ) -> RightsMutationResponseV1:
        def block(
            uow: AssetUnitOfWorkPort,
            asset: Asset,
            now: datetime,
        ) -> RightsRecord | None:
            del uow
            self._assert_expected_version(asset, request.expected_asset_version)
            asset.block(reason_code="ADMINISTRATIVELY_BLOCKED", now=now)
            return None

        return self._mutation(
            workspace_id=workspace_id,
            asset_id=asset_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
            operation="administrator-block",
            request_data=request.model_dump(mode="json"),
            mutate=block,
            change="ADMINISTRATOR_BLOCKED",
            convergence="REMOVE_EXTERNAL_DERIVATIVES",
            audit_metadata={
                "reason": request.reason,
                "evidence_reference": request.evidence_reference,
            },
        )

    def history(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        before_version: int | None,
        limit: int,
    ) -> RightsHistoryResponseV1:
        validate_workspace_id(workspace_id)
        asset_id = canonicalize_resource_id(asset_id, resource="Asset")
        with self._uow_factory() as uow:
            asset = self._load_asset(uow, workspace_id, asset_id)
            records = uow.assets.list_rights_records(
                workspace_id=workspace_id,
                asset_id=asset.id,
                before_version=before_version,
                limit=limit + 1,
            )
        page = records[:limit]
        return RightsHistoryResponseV1(
            items=[rights_record_response(record) for record in page],
            next_cursor=(page[-1].version_number if len(records) > limit and page else None),
        )

    def current_usability(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        request: RightsUsabilityRequestV1,
    ) -> RightsUsabilityResponseV1:
        validate_workspace_id(workspace_id)
        asset_id = canonicalize_resource_id(asset_id, resource="Asset")
        with self._uow_factory() as uow:
            snapshot = uow.assets.get_current_usability_snapshot(
                workspace_id=workspace_id,
                asset_id=asset_id,
            )
            if snapshot is None:
                raise NotFoundError(f"Asset {asset_id} was not found")
            decision = evaluate_current_usability(
                asset=snapshot.asset,
                rights_record=snapshot.rights_record,
                asset_version_id=request.asset_version_id,
                purpose=request.purpose,
                provider=request.provider,
                requires_derivative=request.requires_derivative,
                decision_time=max(request.decision_time, snapshot.database_now),
            )
            return RightsUsabilityResponseV1.model_validate(decision, from_attributes=True)

    def expire_due_once(self, *, limit: int) -> int:
        if limit < 1:
            raise ValueError("rights expiry scan limit must be positive")
        processed = 0
        for _ in range(limit):
            with self._uow_factory() as uow:
                claimed = uow.assets.claim_expired_rights(limit=1)
                if not claimed:
                    break
                claim = claimed[0]
                asset = claim.asset
                record = claim.rights_record
                now = claim.database_now
                if asset.status == AssetState.AVAILABLE:
                    asset.expire_rights(now=now)
                else:
                    asset.select_unusable_rights(
                        rights_record_id=record.id,
                        reason_code="RIGHTS_EXPIRED",
                        expired=True,
                        now=now,
                    )
                rights_event = self._event(
                    asset=asset,
                    rights_record=record,
                    change="EXPIRED",
                    convergence="REMOVE_EXTERNAL_DERIVATIVES",
                    trace_id=f"rights-expiry:{record.id}",
                    now=now,
                    event_type=EventType.ASSET_RIGHTS_EXPIRED,
                )
                if asset.retention_class == RetentionClass.FOUNDATION:
                    schedule_asset_deletion(
                        uow=uow,
                        asset=asset,
                        reason=AssetDeletionReason.RIGHTS_EXPIRED,
                        requested_by="rights-expiry-scheduler",
                        trace_id=f"rights-expiry-delete:{record.id}",
                        policy=self._deletion_policy,
                        now=now,
                    )
                else:
                    uow.assets.save_asset(asset)
                uow.outbox.add(rights_event)
                uow.audit.add(
                    workspace_id=asset.workspace_id,
                    actor_type="SYSTEM",
                    actor_id="rights-expiry-scheduler",
                    action="asset.rights.expired",
                    resource_type="asset",
                    resource_id=asset.id,
                    trace_id=f"rights-expiry:{record.id}",
                    metadata={
                        "asset_version_id": asset.current_version_id,
                        "rights_record_id": record.id,
                        "rights_record_version": record.version_number,
                        "resulting_asset_state": asset.status.value,
                        "valid_until": (
                            record.valid_until.isoformat()
                            if record.valid_until is not None
                            else None
                        ),
                    },
                    created_at=now,
                    expires_at=now + _AUDIT_RETENTION,
                )
                uow.commit()
                processed += 1
        return processed

    def activate_due_once(self, *, limit: int) -> int:
        if limit < 1:
            raise ValueError("rights activation scan limit must be positive")
        processed = 0
        for _ in range(limit):
            with self._uow_factory() as uow:
                claimed = uow.assets.claim_activatable_rights(limit=1)
                if not claimed:
                    break
                claim = claimed[0]
                asset = claim.asset
                record = claim.rights_record
                now = claim.database_now
                self._select_record(asset=asset, record=record, now=now)
                uow.assets.save_asset(asset)
                convergence = (
                    "REINDEX"
                    if asset.status == AssetState.AVAILABLE
                    else "REMOVE_EXTERNAL_DERIVATIVES"
                )
                uow.outbox.add(
                    self._event(
                        asset=asset,
                        rights_record=record,
                        change="ACTIVATED",
                        convergence=convergence,
                        trace_id=f"rights-activation:{record.id}",
                        now=now,
                    )
                )
                uow.audit.add(
                    workspace_id=asset.workspace_id,
                    actor_type="SYSTEM",
                    actor_id="rights-activation-scheduler",
                    action="asset.rights.activated",
                    resource_type="asset",
                    resource_id=asset.id,
                    trace_id=f"rights-activation:{record.id}",
                    metadata={
                        "asset_version_id": asset.current_version_id,
                        "rights_record_id": record.id,
                        "rights_record_version": record.version_number,
                        "resulting_asset_state": asset.status.value,
                        "valid_from": record.valid_from.isoformat(),
                    },
                    created_at=now,
                    expires_at=now + _AUDIT_RETENTION,
                )
                uow.commit_rights_mutation(
                    workspace_id=asset.workspace_id,
                    asset_id=asset.id,
                    retention_deadline=asset.retention_deadline,
                    available_rights_record_id=(
                        record.id if asset.status == AssetState.AVAILABLE else None
                    ),
                    clock=lambda observed_at=now: observed_at,
                )
                processed += 1
        return processed

    def _grant(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        actor_id: str,
        idempotency_key: str,
        trace_id: str,
        request: RightsRecordMutationRequestV1,
        replacing: bool,
    ) -> RightsMutationResponseV1:
        def grant(
            uow: AssetUnitOfWorkPort,
            asset: Asset,
            now: datetime,
        ) -> RightsRecord:
            self._assert_expected_version(asset, request.expected_asset_version)
            current = self._current_record(uow, asset)
            if replacing and current is None:
                raise InvalidTransitionError("Rights Record replacement requires current rights")
            if not replacing and current is not None:
                raise InvalidTransitionError("Asset already has a current Rights Record")
            if asset.status not in {
                AssetState.PENDING_RIGHTS,
                AssetState.AVAILABLE,
                AssetState.BLOCKED,
                AssetState.RIGHTS_EXPIRED,
            }:
                raise InvalidTransitionError(
                    "Rights may only be selected after mandatory validation"
                )
            if request.asset_version_id not in {None, asset.current_version_id}:
                raise InvalidTransitionError(
                    "Rights Record Asset Version must be the current Asset Version"
                )
            if asset.retention_deadline is not None and now >= asset.retention_deadline:
                raise InvalidTransitionError(
                    "Task Asset retention expired before Rights Record mutation"
                )
            record = RightsRecord(
                id=new_uuid7(),
                workspace_id=asset.workspace_id,
                asset_id=asset.id,
                asset_version_id=request.asset_version_id,
                version_number=(current.version_number + 1 if current else 1),
                decision=RightsRecordDecision.GRANT,
                owner_reference=request.owner_reference,
                source=request.source,
                license_reference=request.license_reference,
                allowed_uses=frozenset(request.allowed_uses),
                allowed_providers=frozenset(request.allowed_providers),
                derivative_allowed=request.derivative_allowed,
                public_demo_allowed=request.public_demo_allowed,
                evidence_reference=request.evidence_reference,
                terms_sha256=request.terms_sha256,
                valid_from=request.valid_from,
                valid_until=request.valid_until,
                perpetual=request.perpetual,
                supersedes_record_id=current.id if current else None,
                created_by=actor_id,
                created_at=now,
            )
            uow.assets.add_rights_record(record)
            self._select_record(asset=asset, record=record, now=now)
            return record

        return self._mutation(
            workspace_id=workspace_id,
            asset_id=asset_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
            operation="rights-replace" if replacing else "rights-register",
            request_data=request.model_dump(mode="json"),
            mutate=grant,
            change="REPLACED" if replacing else "REGISTERED",
            convergence=lambda asset: (
                "REINDEX" if asset.status == AssetState.AVAILABLE else "REMOVE_EXTERNAL_DERIVATIVES"
            ),
        )

    def _mutation(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        actor_id: str,
        idempotency_key: str,
        trace_id: str,
        operation: str,
        request_data: dict[str, object],
        mutate,
        change: str,
        convergence: str | Callable[[Asset], str],
        audit_metadata: dict[str, object] | None = None,
    ) -> RightsMutationResponseV1:
        validate_workspace_id(workspace_id)
        asset_id = canonicalize_resource_id(asset_id, resource="Asset")
        scope = idempotency_scope(operation, workspace_id, asset_id)
        key_digest = key_hash(idempotency_key)
        request_hash = canonical_hash(request_data)
        with self._uow_factory() as uow:
            asset = self._load_asset(uow, workspace_id, asset_id, for_update=True)
            now = self._clock()
            record = claim_idempotency(
                uow=uow,
                scope=scope,
                key_digest=key_digest,
                request_hash=request_hash,
                expires_at=idempotency_expiry(
                    now=now,
                    retention_deadline=asset.retention_deadline,
                ),
            )
            if record.status == "COMPLETED":
                if record.response_data is None:
                    raise ConcurrencyError("idempotency record has no rights response")
                return RightsMutationResponseV1.model_validate(record.response_data)
            selected = mutate(uow, asset, now)
            uow.assets.save_asset(asset)
            current = selected or self._current_record(uow, asset)
            required_convergence = convergence(asset) if callable(convergence) else convergence
            response = RightsMutationResponseV1(
                asset_id=asset.id,
                asset_version=asset.version,
                asset_state=asset.status,
                current_rights_record=(
                    rights_record_response(current) if current is not None else None
                ),
            )
            uow.outbox.add(
                self._event(
                    asset=asset,
                    rights_record=current,
                    change=change,
                    convergence=required_convergence,
                    trace_id=trace_id,
                    now=now,
                )
            )
            uow.idempotency.complete(
                scope=scope,
                key_hash=key_digest,
                request_hash=request_hash,
                resource_type="asset-rights-mutation",
                resource_id=asset.id,
                response_data=response.model_dump(mode="json"),
            )
            uow.audit.add(
                workspace_id=workspace_id,
                actor_type="USER",
                actor_id=actor_id,
                action=f"asset.rights.{change.lower()}",
                resource_type="asset",
                resource_id=asset.id,
                trace_id=trace_id,
                metadata={
                    "asset_version_id": asset.current_version_id,
                    "rights_record_id": current.id if current else None,
                    "rights_record_version": current.version_number if current else None,
                    "resulting_asset_state": asset.status.value,
                    **(audit_metadata or {}),
                },
                created_at=now,
                expires_at=now + _AUDIT_RETENTION,
            )
            uow.commit_rights_mutation(
                workspace_id=asset.workspace_id,
                asset_id=asset.id,
                retention_deadline=asset.retention_deadline,
                available_rights_record_id=(
                    current.id if asset.status == AssetState.AVAILABLE and current else None
                ),
                clock=self._clock,
            )
        return response

    @staticmethod
    def _load_asset(
        uow: AssetUnitOfWorkPort,
        workspace_id: str,
        asset_id: str,
        *,
        for_update: bool = False,
    ) -> Asset:
        asset = uow.assets.get(
            workspace_id=workspace_id,
            asset_id=asset_id,
            for_update=for_update,
        )
        if asset is None:
            raise NotFoundError(f"Asset {asset_id} was not found")
        return asset

    @staticmethod
    def _current_record(
        uow: AssetUnitOfWorkPort,
        asset: Asset,
    ) -> RightsRecord | None:
        if asset.current_rights_record_id is None:
            return None
        record = uow.assets.get_rights_record(
            workspace_id=asset.workspace_id,
            rights_record_id=asset.current_rights_record_id,
        )
        if record is None:
            raise RuntimeError("Asset current Rights Record pointer is incomplete")
        return record

    @staticmethod
    def _assert_expected_version(asset: Asset, expected: int) -> None:
        if asset.version != expected:
            raise ConcurrencyError(
                f"Asset {asset.id} version {asset.version} does not match expected {expected}"
            )

    @staticmethod
    def _select_record(
        *,
        asset: Asset,
        record: RightsRecord,
        now: datetime,
    ) -> None:
        if record.valid_until is not None and now >= record.valid_until:
            asset.select_unusable_rights(
                rights_record_id=record.id,
                reason_code="RIGHTS_EXPIRED",
                expired=True,
                now=now,
            )
            return
        if now < record.valid_from or not record.allowed_uses or not record.allowed_providers:
            if asset.status == AssetState.PENDING_RIGHTS:
                asset.select_pending_rights(rights_record_id=record.id, now=now)
            else:
                asset.select_unusable_rights(
                    rights_record_id=record.id,
                    reason_code=(
                        "RIGHTS_NOT_ACTIVE"
                        if now < record.valid_from
                        else "RIGHTS_PERMISSION_EMPTY"
                    ),
                    expired=False,
                    now=now,
                )
            return
        asset.select_available_rights(rights_record_id=record.id, now=now)

    @staticmethod
    def _revoke(
        *,
        uow: AssetUnitOfWorkPort,
        asset: Asset,
        actor_id: str,
        request: RightsRecordRevokeRequestV1,
        now: datetime,
    ) -> RightsRecord:
        AssetRightsApplicationService._assert_expected_version(
            asset,
            request.expected_asset_version,
        )
        current = AssetRightsApplicationService._current_record(uow, asset)
        if current is None:
            raise InvalidTransitionError("Rights revocation requires current rights")
        if current.decision == RightsRecordDecision.REVOKE:
            raise InvalidTransitionError("Current Rights Record is already revoked")
        record = RightsRecord(
            id=new_uuid7(),
            workspace_id=current.workspace_id,
            asset_id=current.asset_id,
            asset_version_id=current.asset_version_id,
            version_number=current.version_number + 1,
            decision=RightsRecordDecision.REVOKE,
            owner_reference=current.owner_reference,
            source=current.source,
            license_reference=current.license_reference,
            allowed_uses=frozenset(),
            allowed_providers=frozenset(),
            derivative_allowed=False,
            public_demo_allowed=False,
            evidence_reference=request.evidence_reference,
            terms_sha256=current.terms_sha256,
            valid_from=now,
            valid_until=None,
            perpetual=True,
            supersedes_record_id=current.id,
            created_by=actor_id,
            created_at=now,
        )
        uow.assets.add_rights_record(record)
        asset.select_revoked_rights(rights_record_id=record.id, now=now)
        return record

    @staticmethod
    def _event(
        *,
        asset: Asset,
        rights_record: RightsRecord | None,
        change: str,
        convergence: str,
        trace_id: str,
        now: datetime,
        event_type: EventType = EventType.ASSET_RIGHTS_CHANGED,
    ) -> OutboxEvent:
        payload = AssetRightsChangedPayload(
            workspace_id=asset.workspace_id,
            asset_id=asset.id,
            asset_version_id=asset.current_version_id,
            rights_record_id=rights_record.id if rights_record else None,
            rights_record_version=(rights_record.version_number if rights_record else None),
            change=change,
            resulting_asset_state=asset.status.value,
            required_convergence=convergence,
        )
        return OutboxEvent(
            envelope=EventEnvelope.create(
                event_type=event_type.value,
                aggregate_type="Asset",
                aggregate_id=asset.id,
                aggregate_version=asset.version,
                trace_id=trace_id,
                payload=payload.model_dump(mode="json"),
                now=now,
            ),
            available_at=now,
            workspace_id=asset.workspace_id,
        )
