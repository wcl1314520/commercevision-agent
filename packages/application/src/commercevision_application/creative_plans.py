"""Application authority for appending immutable Creative Plan versions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

from commercevision_contracts import (
    CreativePlanCreateRequestV1,
    CreativePlanPayloadV1,
    CreativePlanProvenanceV1,
    CreativePlanRevisionRequestV1,
)
from commercevision_domain import (
    ConcurrencyError,
    CreativePlanCitationSelection,
    CreativePlanDirection,
    CreativePlanHead,
    CreativePlanPayload,
    CreativePlanProvenance,
    CreativePlanSource,
    CreativePlanVersion,
    ImageRole,
    InvalidTransitionError,
    NotFoundError,
    RetentionStatus,
    ToolIntentProposal,
    Workflow,
    WorkflowStatus,
)
from commercevision_domain.workflow.errors import IdempotencyConflictError

from .creative_plan_ports import (
    CreativePlanCursorCodecPort,
    CreativePlanUnitOfWorkFactory,
    CreativePlanUnitOfWorkPort,
)

_IDEMPOTENCY_RETENTION = timedelta(days=30)
_IDEMPOTENCY_RESOURCE_TYPE = "CREATIVE_PLAN_VERSION"


def _canonical_hash(value: dict[str, object]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _key_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _workspace_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_idempotency_key(value: str) -> None:
    if (
        not isinstance(value, str)
        or not 8 <= len(value) <= 255
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("idempotency key is invalid")


def _payload_from_contract(value: CreativePlanPayloadV1) -> CreativePlanPayload:
    return CreativePlanPayload(
        schema_version=value.schema_version,
        directions=tuple(
            CreativePlanDirection(
                key=direction.key,
                image_role=ImageRole(direction.image_role),
                scene=direction.scene,
                composition=direction.composition,
                camera=direction.camera,
                lighting=direction.lighting,
                color_direction=direction.color_direction,
                product_constraints=tuple(direction.product_constraints),
                required_elements=tuple(direction.required_elements),
                prohibited_elements=tuple(direction.prohibited_elements),
                citation_selections=tuple(
                    CreativePlanCitationSelection(
                        citation_id=selection.citation_id,
                        reason=selection.reason,
                    )
                    for selection in direction.citation_selections
                ),
                candidate_count=direction.candidate_count,
                quality_targets=tuple(direction.quality_targets),
                repair_scope=tuple(direction.repair_scope),
                tool_intents=tuple(
                    ToolIntentProposal.create(
                        intent_key=intent.intent_key,
                        tool_name=intent.tool_name,
                        schema_version=intent.schema_version,
                        purpose=intent.purpose,
                        arguments=cast(dict[str, object], intent.arguments),
                        estimated_cost_units=intent.estimated_cost_units,
                    )
                    for intent in direction.tool_intents
                ),
            )
            for direction in value.directions
        ),
    )


def _provenance_from_contract(value: CreativePlanProvenanceV1) -> CreativePlanProvenance:
    return CreativePlanProvenance(
        product_brief_id=value.product_brief_id,
        product_brief_version=value.product_brief_version,
        product_brief_sha256=value.product_brief_sha256,
        brand_profile_id=value.brand_profile_id,
        brand_profile_version=value.brand_profile_version,
        brand_profile_sha256=value.brand_profile_sha256,
        retrieval_run_id=value.retrieval_run_id,
        retrieval_citation_ids=tuple(value.retrieval_citation_ids),
        context_policy_version=value.context_policy_version,
        context_sha256=value.context_sha256,
        prompt_id=value.prompt_id,
        prompt_revision=value.prompt_revision,
        prompt_sha256=value.prompt_sha256,
    )


def _replay_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ConcurrencyError("idempotency replay timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ConcurrencyError("idempotency replay timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ConcurrencyError("idempotency replay timestamp is invalid")
    return parsed


@dataclass(frozen=True, slots=True)
class CreativePlanWriteResult:
    head: CreativePlanHead
    version: CreativePlanVersion


@dataclass(frozen=True, slots=True)
class CreativePlanVersionPage:
    items: tuple[CreativePlanVersion, ...]
    next_cursor: str | None


class CreativePlanApplicationService:
    """Fence a version against current Workflow authority before persistence."""

    def __init__(
        self,
        unit_of_work_factory: CreativePlanUnitOfWorkFactory,
        *,
        cursor_codec: CreativePlanCursorCodecPort | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._cursor_codec = cursor_codec

    def create_plan(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        request: CreativePlanCreateRequestV1,
        trace_id: str,
        idempotency_key: str,
    ) -> CreativePlanWriteResult:
        _validate_idempotency_key(idempotency_key)
        payload = _payload_from_contract(request.payload)
        provenance = _provenance_from_contract(request.provenance)
        scope, key_digest, request_digest = self._idempotency_identity(
            operation="create",
            workspace_id=workspace_id,
            workflow_id=request.workflow_id,
            creative_plan_id=request.creative_plan_id,
            actor_id=actor_id,
            request=request.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
        with self._unit_of_work_factory() as unit_of_work:
            workflow = unit_of_work.workflows.get(
                request.workflow_id,
                workspace_id=workspace_id,
                for_update=True,
            )
            if workflow is None:
                raise NotFoundError("Workflow does not exist")
            now = unit_of_work.database_now()
            replay = self._claim_or_replay(
                unit_of_work=unit_of_work,
                scope=scope,
                key_digest=key_digest,
                request_digest=request_digest,
                workspace_id=workspace_id,
                workflow_id=request.workflow_id,
                creative_plan_id=request.creative_plan_id,
                expires_at=min(workflow.expires_at, now + _IDEMPOTENCY_RETENTION),
            )
            if replay is not None:
                return replay
            version = CreativePlanVersion.create(
                workspace_id=workspace_id,
                workflow_id=request.workflow_id,
                creative_plan_id=request.creative_plan_id,
                version_number=1,
                supersedes_version_id=None,
                source=CreativePlanSource.AGENT,
                payload=payload,
                provenance=provenance,
                actor_id=actor_id,
                revision_reason=None,
                now=now,
            )
            result = self._append_authorized(
                unit_of_work=unit_of_work,
                workflow=workflow,
                version=version,
                expected_workflow_version=request.expected_workflow_version,
                expected_head_version=request.expected_head_version,
                now=now,
            )
            self._complete_idempotency(
                unit_of_work=unit_of_work,
                scope=scope,
                key_digest=key_digest,
                request_digest=request_digest,
                result=result,
            )
            self._record_audit(
                unit_of_work=unit_of_work,
                result=result,
                actor_id=actor_id,
                action="creative_plan.created",
                trace_id=trace_id,
                expected_workflow_version=request.expected_workflow_version,
                expected_head_version=request.expected_head_version,
            )
            unit_of_work.commit()
            return result

    def revise_plan(
        self,
        *,
        workspace_id: str,
        creative_plan_id: str,
        actor_id: str,
        request: CreativePlanRevisionRequestV1,
        trace_id: str,
        idempotency_key: str,
    ) -> CreativePlanWriteResult:
        _validate_idempotency_key(idempotency_key)
        payload = _payload_from_contract(request.payload)
        scope, key_digest, request_digest = self._idempotency_identity(
            operation="revise",
            workspace_id=workspace_id,
            workflow_id=request.workflow_id,
            creative_plan_id=creative_plan_id,
            actor_id=actor_id,
            request=request.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
        with self._unit_of_work_factory() as unit_of_work:
            workflow = unit_of_work.workflows.get(
                request.workflow_id,
                workspace_id=workspace_id,
                for_update=True,
            )
            if workflow is None:
                raise NotFoundError("Workflow does not exist")
            now = unit_of_work.database_now()
            replay = self._claim_or_replay(
                unit_of_work=unit_of_work,
                scope=scope,
                key_digest=key_digest,
                request_digest=request_digest,
                workspace_id=workspace_id,
                workflow_id=request.workflow_id,
                creative_plan_id=creative_plan_id,
                expires_at=min(workflow.expires_at, now + _IDEMPOTENCY_RETENTION),
            )
            if replay is not None:
                return replay
            current = unit_of_work.creative_plans.get_current(
                workspace_id=workspace_id,
                workflow_id=request.workflow_id,
                creative_plan_id=creative_plan_id,
            )
            if current is None:
                raise NotFoundError("Creative Plan does not exist")
            _, current_version = current
            version = current_version.revise_by_user(
                payload=payload,
                actor_id=actor_id,
                reason=request.revision_reason,
                now=now,
            )
            result = self._append_authorized(
                unit_of_work=unit_of_work,
                workflow=workflow,
                version=version,
                expected_workflow_version=request.expected_workflow_version,
                expected_head_version=request.expected_head_version,
                now=now,
            )
            self._complete_idempotency(
                unit_of_work=unit_of_work,
                scope=scope,
                key_digest=key_digest,
                request_digest=request_digest,
                result=result,
            )
            self._record_audit(
                unit_of_work=unit_of_work,
                result=result,
                actor_id=actor_id,
                action="creative_plan.revised",
                trace_id=trace_id,
                expected_workflow_version=request.expected_workflow_version,
                expected_head_version=request.expected_head_version,
            )
            unit_of_work.commit()
            return result

    def append_version(
        self,
        *,
        version: CreativePlanVersion,
        expected_workflow_version: int,
        expected_head_version: int,
    ) -> CreativePlanWriteResult:
        with self._unit_of_work_factory() as unit_of_work:
            workflow = unit_of_work.workflows.get(
                version.workflow_id,
                workspace_id=version.workspace_id,
                for_update=True,
            )
            if workflow is None:
                raise NotFoundError("Workflow does not exist")
            now = unit_of_work.database_now()
            result = self._append_authorized(
                unit_of_work=unit_of_work,
                workflow=workflow,
                version=version,
                expected_workflow_version=expected_workflow_version,
                expected_head_version=expected_head_version,
                now=now,
            )
            unit_of_work.commit()
        return result

    @staticmethod
    def _append_authorized(
        *,
        unit_of_work: CreativePlanUnitOfWorkPort,
        workflow: Workflow,
        version: CreativePlanVersion,
        expected_workflow_version: int,
        expected_head_version: int,
        now: datetime,
    ) -> CreativePlanWriteResult:
        workflow.assert_version(expected_workflow_version)
        if workflow.retention_status is not RetentionStatus.ACTIVE or now >= workflow.expires_at:
            raise InvalidTransitionError("Workflow retention is not active")
        planning_write = (
            workflow.status is WorkflowStatus.PLANNING and workflow.current_node == "create_plan"
        )
        review_edit = (
            version.source is CreativePlanSource.USER
            and workflow.status is WorkflowStatus.AWAITING_PLAN_APPROVAL
            and workflow.current_node == "approve_plan"
        )
        if not (planning_write or review_edit):
            raise InvalidTransitionError("Workflow is not accepting Creative Plan versions")
        head = unit_of_work.creative_plans.append_version(
            version,
            expected_head_version=expected_head_version,
            retain_until=workflow.expires_at,
            authorized_at=now,
        )
        return CreativePlanWriteResult(head=head, version=version)

    @staticmethod
    def _idempotency_identity(
        *,
        operation: str,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
        actor_id: str,
        request: dict[str, object],
        idempotency_key: str,
    ) -> tuple[str, str, str]:
        scope = (
            f"creative-plan:{operation}:{_workspace_hash(workspace_id)}:"
            f"{workflow_id}:{creative_plan_id}"
        )
        return (
            scope,
            _key_hash(idempotency_key),
            _canonical_hash({"actor_id": actor_id, **request}),
        )

    @staticmethod
    def _claim_or_replay(
        *,
        unit_of_work: CreativePlanUnitOfWorkPort,
        scope: str,
        key_digest: str,
        request_digest: str,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
        expires_at: datetime,
    ) -> CreativePlanWriteResult | None:
        record = unit_of_work.idempotency.claim(
            scope=scope,
            key_hash=key_digest,
            request_hash=request_digest,
            expires_at=expires_at,
        )
        if record.request_hash != request_digest:
            raise IdempotencyConflictError(
                "idempotency key was already used with a different request"
            )
        if record.status == "PENDING":
            return None
        if record.status != "COMPLETED":
            raise ConcurrencyError("idempotency record has an unsupported status")
        if record.resource_type != _IDEMPOTENCY_RESOURCE_TYPE or not isinstance(
            record.response_data, dict
        ):
            raise ConcurrencyError("idempotency record does not contain a Creative Plan response")
        data = cast(dict[str, object], record.response_data)
        if (
            data.get("workspace_id") != workspace_id
            or data.get("workflow_id") != workflow_id
            or data.get("creative_plan_id") != creative_plan_id
            or data.get("current_version_id") != record.resource_id
        ):
            raise ConcurrencyError("idempotency record does not contain the expected Creative Plan")
        version = unit_of_work.creative_plans.get_version_by_id(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            creative_plan_id=creative_plan_id,
            version_id=record.resource_id,
        )
        if version is None or version.version_number != data.get("current_version_number"):
            raise ConcurrencyError("idempotency Creative Plan version cannot be reconstructed")
        try:
            head = CreativePlanHead(
                workspace_id=workspace_id,
                workflow_id=workflow_id,
                creative_plan_id=creative_plan_id,
                current_version_id=record.resource_id,
                current_version_number=cast(int, data["current_version_number"]),
                version=cast(int, data["head_version"]),
                retain_until=_replay_datetime(data["retain_until"]),
                created_at=_replay_datetime(data["created_at"]),
                updated_at=_replay_datetime(data["updated_at"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConcurrencyError(
                "idempotency Creative Plan head cannot be reconstructed"
            ) from exc
        return CreativePlanWriteResult(head=head, version=version)

    @staticmethod
    def _complete_idempotency(
        *,
        unit_of_work: CreativePlanUnitOfWorkPort,
        scope: str,
        key_digest: str,
        request_digest: str,
        result: CreativePlanWriteResult,
    ) -> None:
        head = result.head
        unit_of_work.idempotency.complete(
            scope=scope,
            key_hash=key_digest,
            request_hash=request_digest,
            resource_type=_IDEMPOTENCY_RESOURCE_TYPE,
            resource_id=result.version.id,
            response_data={
                "workspace_id": head.workspace_id,
                "workflow_id": head.workflow_id,
                "creative_plan_id": head.creative_plan_id,
                "current_version_id": head.current_version_id,
                "current_version_number": head.current_version_number,
                "head_version": head.version,
                "retain_until": head.retain_until.isoformat(),
                "created_at": head.created_at.isoformat(),
                "updated_at": head.updated_at.isoformat(),
            },
        )

    @staticmethod
    def _record_audit(
        *,
        unit_of_work: CreativePlanUnitOfWorkPort,
        result: CreativePlanWriteResult,
        actor_id: str,
        action: str,
        trace_id: str,
        expected_workflow_version: int,
        expected_head_version: int,
    ) -> None:
        version = result.version
        unit_of_work.audit.add(
            workspace_id=version.workspace_id,
            actor_type="USER",
            actor_id=actor_id,
            action=action,
            resource_type="creative-plan",
            resource_id=version.creative_plan_id,
            trace_id=trace_id,
            metadata={
                "version_number": version.version_number,
                "source": version.source.value,
                "direction_count": len(version.payload.directions),
                "payload_sha256": version.payload_sha256,
                "expected_workflow_version": expected_workflow_version,
                "expected_head_version": expected_head_version,
            },
            created_at=version.created_at,
            expires_at=result.head.retain_until,
        )

    def get_current(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
    ) -> CreativePlanWriteResult:
        with self._unit_of_work_factory() as unit_of_work:
            current = unit_of_work.creative_plans.get_current(
                workspace_id=workspace_id,
                workflow_id=workflow_id,
                creative_plan_id=creative_plan_id,
            )
            if current is None:
                raise NotFoundError("Creative Plan does not exist")
            head, version = current
        return CreativePlanWriteResult(head=head, version=version)

    def get_version(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
        version_number: int,
    ) -> CreativePlanVersion:
        with self._unit_of_work_factory() as unit_of_work:
            version = unit_of_work.creative_plans.get_version_by_number(
                workspace_id=workspace_id,
                workflow_id=workflow_id,
                creative_plan_id=creative_plan_id,
                version_number=version_number,
            )
            if version is None:
                raise NotFoundError("Creative Plan version does not exist")
        return version

    def list_versions(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
    ) -> tuple[CreativePlanVersion, ...]:
        with self._unit_of_work_factory() as unit_of_work:
            versions = unit_of_work.creative_plans.list_versions(
                workspace_id=workspace_id,
                workflow_id=workflow_id,
                creative_plan_id=creative_plan_id,
            )
            if versions is None:
                raise NotFoundError("Creative Plan does not exist")
        return versions

    def list_version_page(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
        limit: int,
        cursor: str | None,
    ) -> CreativePlanVersionPage:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Creative Plan history limit must be between 1 and 100")
        if self._cursor_codec is None:
            raise RuntimeError("Creative Plan cursor codec is not configured")
        after_version_number = (
            None
            if cursor is None
            else self._cursor_codec.decode(
                cursor,
                workspace_id=workspace_id,
                workflow_id=workflow_id,
                creative_plan_id=creative_plan_id,
            )
        )
        with self._unit_of_work_factory() as unit_of_work:
            versions = unit_of_work.creative_plans.list_version_page(
                workspace_id=workspace_id,
                workflow_id=workflow_id,
                creative_plan_id=creative_plan_id,
                after_version_number=after_version_number,
                limit=limit + 1,
            )
            if versions is None:
                raise NotFoundError("Creative Plan does not exist")
        items = versions[:limit]
        next_cursor = (
            self._cursor_codec.encode(
                workspace_id=workspace_id,
                workflow_id=workflow_id,
                creative_plan_id=creative_plan_id,
                version_number=items[-1].version_number,
            )
            if len(versions) > limit
            else None
        )
        return CreativePlanVersionPage(items=items, next_cursor=next_cursor)
