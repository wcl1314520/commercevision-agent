"""Prompt Registry lifecycle commands and exact production resolution."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta

from commercevision_contracts import (
    PromptProductionPointerResponseV1,
    PromptProductionSelectionRequestV1,
    PromptRevisionCreateRequestV1,
    PromptRevisionResponseV1,
    PromptRevisionTransitionRequestV1,
    PromptTemplateVariableV1,
)
from commercevision_domain import (
    ConcurrencyError,
    InvalidTransitionError,
    NotFoundError,
    PromptProductionPointer,
    PromptRevision,
    PromptTemplateVariable,
    UniqueConstraintError,
    validate_workspace_id,
)
from commercevision_domain.messaging import EventEnvelope, OutboxEvent
from commercevision_domain.workflow.errors import IdempotencyConflictError

from .prompt_registry_ports import (
    PromptIdempotencyRecordPort,
    PromptRegistryUnitOfWorkFactory,
    PromptRegistryUnitOfWorkPort,
)

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)


def _canonical_hash(value: dict[str, object]) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _key_hash(value: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 255
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("Idempotency key is invalid")
    return hashlib.sha256(value.encode()).hexdigest()


def _workspace_hash(workspace_id: str) -> str:
    return hashlib.sha256(workspace_id.encode()).hexdigest()


def prompt_revision_to_contract(revision: PromptRevision) -> PromptRevisionResponseV1:
    return PromptRevisionResponseV1(
        id=revision.id,
        workspace_id=revision.workspace_id,
        prompt_id=revision.prompt_id,
        semantic_revision=revision.semantic_revision,
        node=revision.node,
        category_applicability=list(revision.category_applicability),
        model_family_applicability=list(revision.model_family_applicability),
        input_schema_version=revision.input_schema_version,
        output_schema_version=revision.output_schema_version,
        policy_version=revision.policy_version,
        content=revision.content,
        variables=[
            PromptTemplateVariableV1(name=item.name, required=item.required)
            for item in revision.variables
        ],
        content_sha256=revision.content_sha256,
        status=revision.status,
        version=revision.version,
        created_by=revision.created_by,
        change_summary=revision.change_summary,
        created_at=revision.created_at,
        updated_at=revision.updated_at,
        submitted_by=revision.submitted_by,
        submitted_at=revision.submitted_at,
        reviewed_by=revision.reviewed_by,
        reviewed_at=revision.reviewed_at,
        published_by=revision.published_by,
        published_at=revision.published_at,
        deprecated_by=revision.deprecated_by,
        deprecated_at=revision.deprecated_at,
    )


def prompt_pointer_to_contract(
    pointer: PromptProductionPointer,
) -> PromptProductionPointerResponseV1:
    return PromptProductionPointerResponseV1(
        workspace_id=pointer.workspace_id,
        prompt_id=pointer.prompt_id,
        node=pointer.node,
        revision_id=pointer.revision_id,
        semantic_revision=pointer.semantic_revision,
        content_sha256=pointer.content_sha256,
        version=pointer.version,
        updated_by=pointer.updated_by,
        updated_at=pointer.updated_at,
    )


class PromptRegistryApplicationService:
    """Keep callers on exact immutable Prompt Revision facts."""

    def __init__(self, uow_factory: PromptRegistryUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def create_revision(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        request: PromptRevisionCreateRequestV1,
        trace_id: str,
        idempotency_key: str,
    ) -> PromptRevisionResponseV1:
        validate_workspace_id(workspace_id)
        identity_digest = _canonical_hash(
            {"prompt_id": request.prompt_id, "semantic_revision": request.semantic_revision}
        )
        scope = f"prompt-registry:create:{_workspace_hash(workspace_id)}:{identity_digest}"
        key_digest = _key_hash(idempotency_key)
        request_digest = _canonical_hash({"actor_id": actor_id, **request.model_dump(mode="json")})
        with self._uow_factory() as uow:
            now = uow.database_now()
            replay = self._claim_idempotency(
                uow=uow,
                scope=scope,
                key_digest=key_digest,
                request_digest=request_digest,
                now=now,
            )
            if replay is not None:
                return self._revision_replay(replay, workspace_id=workspace_id)
            if (
                uow.prompt_revisions.get_by_semantic_revision(
                    workspace_id=workspace_id,
                    prompt_id=request.prompt_id,
                    semantic_revision=request.semantic_revision,
                    for_update=True,
                )
                is not None
            ):
                raise UniqueConstraintError("Prompt semantic revision already exists")
            revision = PromptRevision.create(
                workspace_id=workspace_id,
                prompt_id=request.prompt_id,
                semantic_revision=request.semantic_revision,
                node=request.node,
                category_applicability=tuple(request.category_applicability),
                model_family_applicability=tuple(request.model_family_applicability),
                input_schema_version=request.input_schema_version,
                output_schema_version=request.output_schema_version,
                policy_version=request.policy_version,
                content=request.content,
                variables=tuple(
                    PromptTemplateVariable(name=item.name, required=item.required)
                    for item in request.variables
                ),
                created_by=actor_id,
                change_summary=request.change_summary,
                now=now,
            )
            uow.prompt_revisions.add(revision)
            response = prompt_revision_to_contract(revision)
            self._complete_idempotency(
                uow=uow,
                scope=scope,
                key_digest=key_digest,
                request_digest=request_digest,
                response=response,
            )
            self._record_change(
                uow=uow,
                revision=revision,
                actor_id=actor_id,
                action="prompt.revision.created",
                trace_id=trace_id,
            )
            uow.commit()
            return response

    def submit_for_review(
        self,
        *,
        workspace_id: str,
        revision_id: str,
        actor_id: str,
        request: PromptRevisionTransitionRequestV1,
        trace_id: str,
        idempotency_key: str,
    ) -> PromptRevisionResponseV1:
        return self._transition(
            workspace_id=workspace_id,
            revision_id=revision_id,
            actor_id=actor_id,
            request=request,
            trace_id=trace_id,
            idempotency_key=idempotency_key,
            action="prompt.revision.review-requested",
            transition=lambda revision, now: revision.submit_for_review(
                expected_version=request.expected_version,
                actor_id=actor_id,
                now=now,
            ),
        )

    def stage(
        self,
        *,
        workspace_id: str,
        revision_id: str,
        actor_id: str,
        request: PromptRevisionTransitionRequestV1,
        trace_id: str,
        idempotency_key: str,
    ) -> PromptRevisionResponseV1:
        return self._transition(
            workspace_id=workspace_id,
            revision_id=revision_id,
            actor_id=actor_id,
            request=request,
            trace_id=trace_id,
            idempotency_key=idempotency_key,
            action="prompt.revision.staged",
            transition=lambda revision, now: revision.stage(
                expected_version=request.expected_version,
                reviewer_id=actor_id,
                now=now,
            ),
        )

    def publish(
        self,
        *,
        workspace_id: str,
        revision_id: str,
        actor_id: str,
        request: PromptRevisionTransitionRequestV1,
        trace_id: str,
        idempotency_key: str,
    ) -> PromptRevisionResponseV1:
        validate_workspace_id(workspace_id)
        scope, key_digest, request_digest = self._transition_idempotency_identity(
            operation="publish",
            workspace_id=workspace_id,
            revision_id=revision_id,
            actor_id=actor_id,
            request=request,
            idempotency_key=idempotency_key,
        )
        with self._uow_factory() as uow:
            now = uow.database_now()
            replay = self._claim_idempotency(
                uow=uow,
                scope=scope,
                key_digest=key_digest,
                request_digest=request_digest,
                now=now,
            )
            if replay is not None:
                return self._revision_replay(
                    replay, workspace_id=workspace_id, revision_id=revision_id
                )
            revision = self._get_locked_revision(
                uow=uow, workspace_id=workspace_id, revision_id=revision_id
            )
            published = revision.publish(
                expected_version=request.expected_version,
                actor_id=actor_id,
                now=now,
            )
            uow.prompt_revisions.save_lifecycle(
                published, expected_version=request.expected_version
            )
            pointer = uow.prompt_revisions.get_pointer(
                workspace_id=workspace_id,
                prompt_id=published.prompt_id,
                for_update=True,
            )
            if pointer is None:
                uow.prompt_revisions.add_pointer(
                    PromptProductionPointer.create(
                        revision=published,
                        actor_id=actor_id,
                        now=now,
                    )
                )
            else:
                selected = pointer.repoint(
                    revision=published,
                    expected_version=pointer.version,
                    actor_id=actor_id,
                    now=now,
                )
                uow.prompt_revisions.save_pointer(selected, expected_version=pointer.version)
            response = prompt_revision_to_contract(published)
            self._complete_idempotency(
                uow=uow,
                scope=scope,
                key_digest=key_digest,
                request_digest=request_digest,
                response=response,
            )
            self._record_change(
                uow=uow,
                revision=published,
                actor_id=actor_id,
                action="prompt.revision.published",
                trace_id=trace_id,
            )
            uow.commit()
            return response

    def select_production(
        self,
        *,
        workspace_id: str,
        prompt_id: str,
        actor_id: str,
        request: PromptProductionSelectionRequestV1,
        trace_id: str,
        idempotency_key: str,
    ) -> PromptProductionPointerResponseV1:
        validate_workspace_id(workspace_id)
        if _TOKEN_PATTERN.fullmatch(prompt_id) is None:
            raise ValueError("Prompt id is invalid")
        scope = f"prompt-registry:select-production:{_workspace_hash(workspace_id)}:{prompt_id}"
        key_digest = _key_hash(idempotency_key)
        request_digest = _canonical_hash(
            {
                "actor_id": actor_id,
                "prompt_id": prompt_id,
                **request.model_dump(mode="json"),
            }
        )
        with self._uow_factory() as uow:
            now = uow.database_now()
            replay = self._claim_idempotency(
                uow=uow,
                scope=scope,
                key_digest=key_digest,
                request_digest=request_digest,
                now=now,
            )
            if replay is not None:
                return self._pointer_replay(replay, workspace_id=workspace_id, prompt_id=prompt_id)
            target = self._get_locked_revision(
                uow=uow,
                workspace_id=workspace_id,
                revision_id=request.revision_id,
            )
            if target.prompt_id != prompt_id:
                raise NotFoundError("Prompt Revision was not found")
            pointer = uow.prompt_revisions.get_pointer(
                workspace_id=workspace_id,
                prompt_id=prompt_id,
                for_update=True,
            )
            if pointer is None:
                raise NotFoundError("Prompt production pointer was not found")
            selected = pointer.repoint(
                revision=target,
                expected_version=request.expected_pointer_version,
                actor_id=actor_id,
                now=now,
            )
            if selected is not pointer:
                uow.prompt_revisions.save_pointer(
                    selected,
                    expected_version=request.expected_pointer_version,
                )
                self._record_pointer_change(
                    uow=uow,
                    pointer=selected,
                    actor_id=actor_id,
                    action="prompt.production.selected",
                    trace_id=trace_id,
                )
            response = prompt_pointer_to_contract(selected)
            self._complete_pointer_idempotency(
                uow=uow,
                scope=scope,
                key_digest=key_digest,
                request_digest=request_digest,
                response=response,
            )
            uow.commit()
            return response

    def deprecate(
        self,
        *,
        workspace_id: str,
        revision_id: str,
        actor_id: str,
        request: PromptRevisionTransitionRequestV1,
        trace_id: str,
        idempotency_key: str,
    ) -> PromptRevisionResponseV1:
        validate_workspace_id(workspace_id)
        scope, key_digest, request_digest = self._transition_idempotency_identity(
            operation="deprecate",
            workspace_id=workspace_id,
            revision_id=revision_id,
            actor_id=actor_id,
            request=request,
            idempotency_key=idempotency_key,
        )
        with self._uow_factory() as uow:
            now = uow.database_now()
            replay = self._claim_idempotency(
                uow=uow,
                scope=scope,
                key_digest=key_digest,
                request_digest=request_digest,
                now=now,
            )
            if replay is not None:
                return self._revision_replay(
                    replay, workspace_id=workspace_id, revision_id=revision_id
                )
            revision = self._get_locked_revision(
                uow=uow,
                workspace_id=workspace_id,
                revision_id=revision_id,
            )
            pointer = uow.prompt_revisions.get_pointer(
                workspace_id=workspace_id,
                prompt_id=revision.prompt_id,
                for_update=True,
            )
            if pointer is not None and pointer.revision_id == revision.id:
                raise InvalidTransitionError(
                    "active production Prompt Revision cannot be deprecated"
                )
            deprecated = revision.deprecate(
                expected_version=request.expected_version,
                actor_id=actor_id,
                now=now,
            )
            uow.prompt_revisions.save_lifecycle(
                deprecated, expected_version=request.expected_version
            )
            response = prompt_revision_to_contract(deprecated)
            self._complete_idempotency(
                uow=uow,
                scope=scope,
                key_digest=key_digest,
                request_digest=request_digest,
                response=response,
            )
            self._record_change(
                uow=uow,
                revision=deprecated,
                actor_id=actor_id,
                action="prompt.revision.deprecated",
                trace_id=trace_id,
            )
            uow.commit()
            return response

    def _transition(
        self,
        *,
        workspace_id: str,
        revision_id: str,
        actor_id: str,
        request: PromptRevisionTransitionRequestV1,
        trace_id: str,
        idempotency_key: str,
        action: str,
        transition: Callable[[PromptRevision, datetime], PromptRevision],
    ) -> PromptRevisionResponseV1:
        validate_workspace_id(workspace_id)
        scope, key_digest, request_digest = self._transition_idempotency_identity(
            operation=action,
            workspace_id=workspace_id,
            revision_id=revision_id,
            actor_id=actor_id,
            request=request,
            idempotency_key=idempotency_key,
        )
        with self._uow_factory() as uow:
            now = uow.database_now()
            replay = self._claim_idempotency(
                uow=uow,
                scope=scope,
                key_digest=key_digest,
                request_digest=request_digest,
                now=now,
            )
            if replay is not None:
                return self._revision_replay(
                    replay, workspace_id=workspace_id, revision_id=revision_id
                )
            revision = self._get_locked_revision(
                uow=uow, workspace_id=workspace_id, revision_id=revision_id
            )
            transitioned = transition(revision, now)
            uow.prompt_revisions.save_lifecycle(
                transitioned, expected_version=request.expected_version
            )
            response = prompt_revision_to_contract(transitioned)
            self._complete_idempotency(
                uow=uow,
                scope=scope,
                key_digest=key_digest,
                request_digest=request_digest,
                response=response,
            )
            self._record_change(
                uow=uow,
                revision=transitioned,
                actor_id=actor_id,
                action=action,
                trace_id=trace_id,
            )
            uow.commit()
            return response

    @staticmethod
    def _transition_idempotency_identity(
        *,
        operation: str,
        workspace_id: str,
        revision_id: str,
        actor_id: str,
        request: PromptRevisionTransitionRequestV1,
        idempotency_key: str,
    ) -> tuple[str, str, str]:
        scope = f"prompt-registry:{operation}:{_workspace_hash(workspace_id)}:{revision_id}"
        return (
            scope,
            _key_hash(idempotency_key),
            _canonical_hash(
                {
                    "actor_id": actor_id,
                    "revision_id": revision_id,
                    **request.model_dump(mode="json"),
                }
            ),
        )

    @staticmethod
    def _claim_idempotency(
        *,
        uow: PromptRegistryUnitOfWorkPort,
        scope: str,
        key_digest: str,
        request_digest: str,
        now: datetime,
    ) -> PromptIdempotencyRecordPort | None:
        record = uow.idempotency.claim(
            scope=scope,
            key_hash=key_digest,
            request_hash=request_digest,
            expires_at=now + timedelta(days=30),
        )
        if record.request_hash != request_digest:
            raise IdempotencyConflictError(
                "idempotency key was already used with a different request"
            )
        if record.status == "COMPLETED":
            return record
        if record.status != "PENDING":
            raise ConcurrencyError("idempotency record has an unsupported status")
        return None

    @staticmethod
    def _revision_replay(
        record: PromptIdempotencyRecordPort,
        *,
        workspace_id: str,
        revision_id: str | None = None,
    ) -> PromptRevisionResponseV1:
        if record.resource_type != "prompt-revision" or not isinstance(record.response_data, dict):
            raise ConcurrencyError("idempotency record does not contain a Prompt Revision response")
        response = PromptRevisionResponseV1.model_validate(record.response_data)
        if (
            response.workspace_id != workspace_id
            or record.resource_id != response.id
            or (revision_id is not None and response.id != revision_id)
        ):
            raise ConcurrencyError(
                "idempotency record does not contain the expected Prompt Revision"
            )
        return response

    @staticmethod
    def _complete_idempotency(
        *,
        uow: PromptRegistryUnitOfWorkPort,
        scope: str,
        key_digest: str,
        request_digest: str,
        response: PromptRevisionResponseV1,
    ) -> None:
        uow.idempotency.complete(
            scope=scope,
            key_hash=key_digest,
            request_hash=request_digest,
            resource_type="prompt-revision",
            resource_id=response.id,
            response_data=response.model_dump(mode="json"),
        )

    @staticmethod
    def _pointer_replay(
        record: PromptIdempotencyRecordPort,
        *,
        workspace_id: str,
        prompt_id: str,
    ) -> PromptProductionPointerResponseV1:
        if record.resource_type != "prompt-production-pointer" or not isinstance(
            record.response_data, dict
        ):
            raise ConcurrencyError(
                "idempotency record does not contain a Prompt production pointer response"
            )
        response = PromptProductionPointerResponseV1.model_validate(record.response_data)
        if (
            response.workspace_id != workspace_id
            or response.prompt_id != prompt_id
            or record.resource_id != response.revision_id
        ):
            raise ConcurrencyError(
                "idempotency record does not contain the expected Prompt production pointer"
            )
        return response

    @staticmethod
    def _complete_pointer_idempotency(
        *,
        uow: PromptRegistryUnitOfWorkPort,
        scope: str,
        key_digest: str,
        request_digest: str,
        response: PromptProductionPointerResponseV1,
    ) -> None:
        uow.idempotency.complete(
            scope=scope,
            key_hash=key_digest,
            request_hash=request_digest,
            resource_type="prompt-production-pointer",
            resource_id=response.revision_id,
            response_data=response.model_dump(mode="json"),
        )

    @staticmethod
    def _record_pointer_change(
        *,
        uow: PromptRegistryUnitOfWorkPort,
        pointer: PromptProductionPointer,
        actor_id: str,
        action: str,
        trace_id: str,
    ) -> None:
        payload = {
            "workspace_id": pointer.workspace_id,
            "prompt_id": pointer.prompt_id,
            "revision_id": pointer.revision_id,
            "semantic_revision": pointer.semantic_revision,
            "content_sha256": pointer.content_sha256,
            "version": pointer.version,
        }
        uow.outbox.add(
            OutboxEvent(
                envelope=EventEnvelope.create(
                    event_type=action,
                    aggregate_type="PromptProductionPointer",
                    aggregate_id=pointer.prompt_id,
                    aggregate_version=pointer.version,
                    trace_id=trace_id,
                    payload=payload,
                    now=pointer.updated_at,
                ),
                available_at=pointer.updated_at,
                workspace_id=pointer.workspace_id,
            )
        )
        uow.audit.add(
            workspace_id=pointer.workspace_id,
            actor_type="USER",
            actor_id=actor_id,
            action=action,
            resource_type="prompt-production-pointer",
            resource_id=pointer.prompt_id,
            trace_id=trace_id,
            metadata=payload,
            created_at=pointer.updated_at,
            expires_at=pointer.updated_at + timedelta(days=180),
        )

    @staticmethod
    def _get_locked_revision(
        *,
        uow: PromptRegistryUnitOfWorkPort,
        workspace_id: str,
        revision_id: str,
    ) -> PromptRevision:
        revision = uow.prompt_revisions.get(
            workspace_id=workspace_id,
            revision_id=revision_id,
            for_update=True,
        )
        if revision is None:
            raise NotFoundError("Prompt Revision was not found")
        return revision

    @staticmethod
    def _record_change(
        *,
        uow: PromptRegistryUnitOfWorkPort,
        revision: PromptRevision,
        actor_id: str,
        action: str,
        trace_id: str,
    ) -> None:
        now = revision.updated_at
        payload = {
            "workspace_id": revision.workspace_id,
            "prompt_id": revision.prompt_id,
            "revision_id": revision.id,
            "semantic_revision": revision.semantic_revision,
            "content_sha256": revision.content_sha256,
            "status": revision.status.value,
            "version": revision.version,
        }
        uow.outbox.add(
            OutboxEvent(
                envelope=EventEnvelope.create(
                    event_type=action,
                    aggregate_type="PromptRevision",
                    aggregate_id=revision.id,
                    aggregate_version=revision.version,
                    trace_id=trace_id,
                    payload=payload,
                    now=now,
                ),
                available_at=now,
                workspace_id=revision.workspace_id,
            )
        )
        uow.audit.add(
            workspace_id=revision.workspace_id,
            actor_type="USER",
            actor_id=actor_id,
            action=action,
            resource_type="prompt-revision",
            resource_id=revision.id,
            trace_id=trace_id,
            metadata=payload,
            created_at=now,
            expires_at=now + timedelta(days=180),
        )

    def resolve_production(
        self,
        *,
        workspace_id: str,
        prompt_id: str,
        node: str,
        category: str,
        model_family: str,
    ) -> PromptRevisionResponseV1:
        return prompt_revision_to_contract(
            self.resolve_production_revision(
                workspace_id=workspace_id,
                prompt_id=prompt_id,
                node=node,
                category=category,
                model_family=model_family,
            )
        )

    def resolve_production_revision(
        self,
        *,
        workspace_id: str,
        prompt_id: str,
        node: str,
        category: str,
        model_family: str,
    ) -> PromptRevision:
        """Resolve the domain revision for trusted in-process consumers."""
        validate_workspace_id(workspace_id)
        for value, field_name in (
            (prompt_id, "Prompt id"),
            (node, "Prompt node"),
            (category, "Prompt category"),
            (model_family, "Prompt model family"),
        ):
            if not isinstance(value, str) or _TOKEN_PATTERN.fullmatch(value) is None:
                raise ValueError(f"{field_name} is invalid")
        with self._uow_factory() as uow:
            revision = uow.prompt_revisions.resolve_production(
                workspace_id=workspace_id,
                prompt_id=prompt_id,
                node=node,
                category=category,
                model_family=model_family,
            )
        if revision is None:
            raise NotFoundError("production Prompt Revision was not found")
        return revision
