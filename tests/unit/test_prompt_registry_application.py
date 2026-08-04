from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from commercevision_application import PromptRegistryApplicationService
from commercevision_contracts import (
    PromptProductionSelectionRequestV1,
    PromptRevisionCreateRequestV1,
    PromptRevisionTransitionRequestV1,
    PromptTemplateVariableV1,
)
from commercevision_domain import (
    InvalidTransitionError,
    NotFoundError,
    PromptProductionPointer,
    PromptRevision,
    PromptTemplateVariable,
)
from commercevision_domain.workflow.errors import IdempotencyConflictError

NOW = datetime(2026, 8, 4, 11, 0, tzinfo=UTC)


def _production_revision() -> PromptRevision:
    draft = PromptRevision.create(
        workspace_id="planning-domain",
        prompt_id="creative-planner",
        semantic_revision="1.0.0",
        node="CREATE_CREATIVE_PLAN",
        category_applicability=("beauty",),
        model_family_applicability=("fixture-planner",),
        input_schema_version="planning-context.v1",
        output_schema_version="creative-plan.v1",
        policy_version="prompt-policy.v1",
        content="Plan from {{ planning_context }} into {{ output_schema }}.",
        variables=(
            PromptTemplateVariable(name="planning_context", required=True),
            PromptTemplateVariable(name="output_schema", required=True),
        ),
        created_by="admin-42",
        change_summary="Initial deterministic Planner prompt",
        now=NOW,
    )
    review = draft.submit_for_review(
        expected_version=1,
        actor_id="admin-42",
        now=NOW + timedelta(minutes=1),
    )
    staging = review.stage(
        expected_version=2,
        reviewer_id="reviewer-7",
        now=NOW + timedelta(minutes=2),
    )
    return staging.publish(
        expected_version=3,
        actor_id="release-manager",
        now=NOW + timedelta(minutes=3),
    )


class _PromptRevisionRepository:
    def __init__(self, revision: PromptRevision | None) -> None:
        self.revision = revision
        self.calls: list[dict[str, str]] = []

    def resolve_production(
        self,
        *,
        workspace_id: str,
        prompt_id: str,
        node: str,
        category: str,
        model_family: str,
    ) -> PromptRevision | None:
        self.calls.append(
            {
                "workspace_id": workspace_id,
                "prompt_id": prompt_id,
                "node": node,
                "category": category,
                "model_family": model_family,
            }
        )
        return self.revision


class _PromptUnitOfWork:
    def __init__(self, revision: PromptRevision | None) -> None:
        self.prompt_revisions = _PromptRevisionRepository(revision)

    def __enter__(self) -> _PromptUnitOfWork:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None


class _MutablePromptRevisionRepository(_PromptRevisionRepository):
    def __init__(self) -> None:
        super().__init__(None)
        self.revisions: dict[str, PromptRevision] = {}
        self.pointer: PromptProductionPointer | None = None

    def add(self, revision: PromptRevision) -> None:
        self.revisions[revision.id] = revision
        self.revision = revision

    def get(
        self, *, workspace_id: str, revision_id: str, for_update: bool = False
    ) -> PromptRevision | None:
        revision = self.revisions.get(revision_id)
        return revision if revision is not None and revision.workspace_id == workspace_id else None

    def get_by_semantic_revision(
        self,
        *,
        workspace_id: str,
        prompt_id: str,
        semantic_revision: str,
        for_update: bool = False,
    ) -> PromptRevision | None:
        return next(
            (
                revision
                for revision in self.revisions.values()
                if revision.workspace_id == workspace_id
                and revision.prompt_id == prompt_id
                and revision.semantic_revision == semantic_revision
            ),
            None,
        )

    def save_lifecycle(self, revision: PromptRevision, *, expected_version: int) -> None:
        current = self.revisions[revision.id]
        assert current.version == expected_version
        self.revisions[revision.id] = revision
        self.revision = revision

    def get_pointer(
        self, *, workspace_id: str, prompt_id: str, for_update: bool = False
    ) -> PromptProductionPointer | None:
        return self.pointer

    def add_pointer(self, pointer: PromptProductionPointer) -> None:
        self.pointer = pointer

    def save_pointer(self, pointer: PromptProductionPointer, *, expected_version: int) -> None:
        assert self.pointer is not None
        assert self.pointer.version == expected_version
        self.pointer = pointer


class _Recorder:
    def __init__(self) -> None:
        self.items: list[object] = []

    def add(self, item: object | None = None, **kwargs: object) -> None:
        self.items.append(item if item is not None else kwargs)


class _IdempotencyRepository:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], SimpleNamespace] = {}

    def claim(
        self,
        *,
        scope: str,
        key_hash: str,
        request_hash: str,
        expires_at: datetime,
    ) -> SimpleNamespace:
        identity = (scope, key_hash)
        record = self.records.get(identity)
        if record is None:
            record = SimpleNamespace(
                request_hash=request_hash,
                resource_type="PENDING",
                resource_id="",
                response_data=None,
                status="PENDING",
                expires_at=expires_at,
            )
            self.records[identity] = record
        return record

    def complete(
        self,
        *,
        scope: str,
        key_hash: str,
        request_hash: str,
        resource_type: str,
        resource_id: str,
        response_data: dict[str, object],
    ) -> None:
        record = self.records[(scope, key_hash)]
        assert record.request_hash == request_hash
        record.resource_type = resource_type
        record.resource_id = resource_id
        record.response_data = response_data
        record.status = "COMPLETED"


class _MutablePromptUnitOfWork:
    def __init__(self) -> None:
        self.prompt_revisions = _MutablePromptRevisionRepository()
        self.audit = _Recorder()
        self.outbox = _Recorder()
        self.idempotency = _IdempotencyRepository()
        self.now = NOW
        self.commits = 0

    def __enter__(self) -> _MutablePromptUnitOfWork:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def database_now(self) -> datetime:
        return self.now

    def commit(self) -> None:
        self.commits += 1


def test_resolve_production_returns_one_exact_revision_and_hash() -> None:
    revision = _production_revision()
    unit_of_work = _PromptUnitOfWork(revision)
    service = PromptRegistryApplicationService(lambda: unit_of_work)

    response = service.resolve_production(
        workspace_id="planning-domain",
        prompt_id="creative-planner",
        node="CREATE_CREATIVE_PLAN",
        category="beauty",
        model_family="fixture-planner",
    )

    assert response.id == revision.id
    assert response.semantic_revision == "1.0.0"
    assert response.content_sha256 == revision.content_sha256
    assert response.content == revision.content
    assert unit_of_work.prompt_revisions.calls == [
        {
            "workspace_id": "planning-domain",
            "prompt_id": "creative-planner",
            "node": "CREATE_CREATIVE_PLAN",
            "category": "beauty",
            "model_family": "fixture-planner",
        }
    ]


def test_resolve_production_hides_missing_or_inapplicable_revision() -> None:
    service = PromptRegistryApplicationService(lambda: _PromptUnitOfWork(None))

    with pytest.raises(NotFoundError, match="production Prompt Revision"):
        service.resolve_production(
            workspace_id="planning-domain",
            prompt_id="creative-planner",
            node="CREATE_CREATIVE_PLAN",
            category="automotive-parts",
            model_family="fixture-planner",
        )


def test_prompt_lifecycle_commands_persist_exact_production_pointer_and_facts() -> None:
    unit_of_work = _MutablePromptUnitOfWork()
    service = PromptRegistryApplicationService(lambda: unit_of_work)
    request = PromptRevisionCreateRequestV1(
        prompt_id="creative-planner",
        semantic_revision="1.0.0",
        node="CREATE_CREATIVE_PLAN",
        category_applicability=["beauty"],
        model_family_applicability=["fixture-planner"],
        input_schema_version="planning-context.v1",
        output_schema_version="creative-plan.v1",
        policy_version="prompt-policy.v1",
        content="Plan from {{ planning_context }} into {{ output_schema }}.",
        variables=[
            PromptTemplateVariableV1(name="planning_context", required=True),
            PromptTemplateVariableV1(name="output_schema", required=True),
        ],
        change_summary="Initial deterministic Planner prompt",
    )

    created = service.create_revision(
        workspace_id="planning-domain",
        actor_id="admin-42",
        request=request,
        trace_id="trace-prompt-lifecycle",
        idempotency_key="create-1.0.0",
    )
    unit_of_work.now += timedelta(minutes=1)
    review = service.submit_for_review(
        workspace_id="planning-domain",
        revision_id=created.id,
        actor_id="admin-42",
        request=PromptRevisionTransitionRequestV1(expected_version=1),
        trace_id="trace-prompt-lifecycle",
        idempotency_key="review-1.0.0",
    )
    unit_of_work.now += timedelta(minutes=1)
    staging = service.stage(
        workspace_id="planning-domain",
        revision_id=created.id,
        actor_id="reviewer-7",
        request=PromptRevisionTransitionRequestV1(expected_version=2),
        trace_id="trace-prompt-lifecycle",
        idempotency_key="stage-1.0.0",
    )
    unit_of_work.now += timedelta(minutes=1)
    production = service.publish(
        workspace_id="planning-domain",
        revision_id=created.id,
        actor_id="release-manager",
        request=PromptRevisionTransitionRequestV1(expected_version=3),
        trace_id="trace-prompt-lifecycle",
        idempotency_key="publish-1.0.0",
    )

    assert [created.status, review.status, staging.status, production.status] == [
        "DRAFT",
        "REVIEW",
        "STAGING",
        "PRODUCTION",
    ]
    assert unit_of_work.prompt_revisions.pointer is not None
    assert unit_of_work.prompt_revisions.pointer.revision_id == created.id
    assert unit_of_work.prompt_revisions.pointer.content_sha256 == production.content_sha256
    assert unit_of_work.commits == 4
    assert len(unit_of_work.audit.items) == 4
    assert len(unit_of_work.outbox.items) == 4


def test_create_revision_replays_exact_response_and_rejects_changed_payload() -> None:
    unit_of_work = _MutablePromptUnitOfWork()
    service = PromptRegistryApplicationService(lambda: unit_of_work)
    request = PromptRevisionCreateRequestV1(
        prompt_id="creative-planner",
        semantic_revision="1.1.0",
        node="CREATE_CREATIVE_PLAN",
        category_applicability=["beauty"],
        model_family_applicability=["fixture-planner"],
        input_schema_version="planning-context.v1",
        output_schema_version="creative-plan.v1",
        policy_version="prompt-policy.v1",
        content="Plan {{ planning_context }} into {{ output_schema }}.",
        variables=[
            PromptTemplateVariableV1(name="planning_context", required=True),
            PromptTemplateVariableV1(name="output_schema", required=True),
        ],
        change_summary="Improve deterministic planning",
    )

    first = service.create_revision(
        workspace_id="planning-domain",
        actor_id="admin-42",
        request=request,
        trace_id="trace-create-first",
        idempotency_key="create-1.1.0",
    )
    replay = service.create_revision(
        workspace_id="planning-domain",
        actor_id="admin-42",
        request=request,
        trace_id="trace-create-replay",
        idempotency_key="create-1.1.0",
    )

    assert replay == first
    assert unit_of_work.commits == 1
    assert len(unit_of_work.audit.items) == 1
    assert len(unit_of_work.outbox.items) == 1

    changed = request.model_copy(update={"change_summary": "Different request"})
    with pytest.raises(IdempotencyConflictError):
        service.create_revision(
            workspace_id="planning-domain",
            actor_id="admin-42",
            request=changed,
            trace_id="trace-create-conflict",
            idempotency_key="create-1.1.0",
        )


def test_select_production_rolls_back_exact_pointer_before_old_active_is_deprecated() -> None:
    unit_of_work = _MutablePromptUnitOfWork()
    first = _production_revision()
    second_draft = PromptRevision.create(
        workspace_id="planning-domain",
        prompt_id="creative-planner",
        semantic_revision="1.1.0",
        node="CREATE_CREATIVE_PLAN",
        category_applicability=("beauty",),
        model_family_applicability=("fixture-planner",),
        input_schema_version="planning-context.v1",
        output_schema_version="creative-plan.v1",
        policy_version="prompt-policy.v1",
        content="Improve {{ planning_context }} into {{ output_schema }}.",
        variables=(
            PromptTemplateVariable(name="planning_context", required=True),
            PromptTemplateVariable(name="output_schema", required=True),
        ),
        created_by="admin-42",
        change_summary="Second production candidate",
        now=NOW + timedelta(minutes=4),
    )
    second = (
        second_draft.submit_for_review(
            expected_version=1,
            actor_id="admin-42",
            now=NOW + timedelta(minutes=5),
        )
        .stage(
            expected_version=2,
            reviewer_id="reviewer-7",
            now=NOW + timedelta(minutes=6),
        )
        .publish(
            expected_version=3,
            actor_id="release-manager",
            now=NOW + timedelta(minutes=7),
        )
    )
    unit_of_work.prompt_revisions.add(first)
    unit_of_work.prompt_revisions.add(second)
    unit_of_work.prompt_revisions.add_pointer(
        PromptProductionPointer.create(
            revision=second,
            actor_id="release-manager",
            now=NOW + timedelta(minutes=7),
        )
    )
    unit_of_work.now = NOW + timedelta(minutes=8)
    service = PromptRegistryApplicationService(lambda: unit_of_work)

    selected = service.select_production(
        workspace_id="planning-domain",
        prompt_id="creative-planner",
        actor_id="release-manager",
        request=PromptProductionSelectionRequestV1(
            revision_id=first.id,
            expected_pointer_version=1,
        ),
        trace_id="trace-rollback",
        idempotency_key="rollback-to-1.0.0",
    )

    assert selected.revision_id == first.id
    assert selected.content_sha256 == first.content_sha256
    assert selected.version == 2

    with pytest.raises(InvalidTransitionError, match="active production"):
        service.deprecate(
            workspace_id="planning-domain",
            revision_id=first.id,
            actor_id="release-manager",
            request=PromptRevisionTransitionRequestV1(expected_version=4),
            trace_id="trace-active-deprecate",
            idempotency_key="deprecate-active-1.0.0",
        )

    deprecated = service.deprecate(
        workspace_id="planning-domain",
        revision_id=second.id,
        actor_id="release-manager",
        request=PromptRevisionTransitionRequestV1(expected_version=4),
        trace_id="trace-deprecate-old",
        idempotency_key="deprecate-old-1.1.0",
    )
    assert deprecated.status == "DEPRECATED"
