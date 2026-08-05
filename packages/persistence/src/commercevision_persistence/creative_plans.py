"""MySQL adapter for Creative Plan immutable versions and optimistic heads."""

from __future__ import annotations

from datetime import UTC, datetime
from types import TracebackType
from typing import Any, cast

from commercevision_domain import (
    ConcurrencyError,
    CreativePlanCitationSelection,
    CreativePlanDirection,
    CreativePlanHead,
    CreativePlanPayload,
    CreativePlanProvenance,
    CreativePlanSource,
    CreativePlanVersion,
    DataIntegrityError,
    ImageRole,
    NotFoundError,
    PlanningContextSourceKind,
    PromptRevisionStatus,
    ToolIntentProposal,
)
from sqlalchemy import CursorResult, literal_column, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .creative_plan_models import CreativePlanModel, CreativePlanVersionModel
from .database import enter_unit_of_work, exit_unit_of_work
from .integrity import (
    classify_database_error,
    execute_with_integrity_classification,
    flush_with_integrity_classification,
)
from .planning_context_models import PlanningContextSnapshotModel
from .planning_contexts import PlanningContextSnapshotRepository
from .prompt_registry import PromptRevisionRepository
from .repositories import WorkflowRepository
from .retrieval_models import RetrievalRunModel


def _payload_from_data(value: object) -> CreativePlanPayload:
    if not isinstance(value, dict):
        raise DataIntegrityError("persisted Creative Plan payload is invalid")
    data = cast(dict[str, Any], value)
    directions_data = data.get("directions")
    if not isinstance(directions_data, list):
        raise DataIntegrityError("persisted Creative Plan directions are invalid")
    try:
        directions: list[CreativePlanDirection] = []
        for direction_value in directions_data:
            if not isinstance(direction_value, dict):
                raise TypeError("direction must be an object")
            direction = cast(dict[str, Any], direction_value)
            citations_data = direction["citation_selections"]
            tools_data = direction["tool_intents"]
            if not isinstance(citations_data, list) or not isinstance(tools_data, list):
                raise TypeError("nested Creative Plan collections must be arrays")
            citations = tuple(
                CreativePlanCitationSelection(
                    citation_id=str(item["citation_id"]),
                    reason=str(item["reason"]),
                )
                for item in citations_data
                if isinstance(item, dict)
            )
            tools: list[ToolIntentProposal] = []
            for item in tools_data:
                if not isinstance(item, dict) or not isinstance(item.get("arguments"), dict):
                    raise TypeError("Tool Intent must be an object")
                tools.append(
                    ToolIntentProposal.create(
                        intent_key=str(item["intent_key"]),
                        tool_name=str(item["tool_name"]),
                        schema_version=str(item["schema_version"]),
                        purpose=str(item["purpose"]),
                        arguments=cast(dict[str, object], item["arguments"]),
                        estimated_cost_units=item["estimated_cost_units"],
                    )
                )
            if len(citations) != len(citations_data) or len(tools) != len(tools_data):
                raise TypeError("nested Creative Plan collection contains a non-object")
            directions.append(
                CreativePlanDirection(
                    key=str(direction["key"]),
                    image_role=ImageRole(str(direction["image_role"])),
                    scene=str(direction["scene"]),
                    composition=str(direction["composition"]),
                    camera=str(direction["camera"]),
                    lighting=str(direction["lighting"]),
                    color_direction=str(direction["color_direction"]),
                    product_constraints=tuple(direction["product_constraints"]),
                    required_elements=tuple(direction["required_elements"]),
                    prohibited_elements=tuple(direction["prohibited_elements"]),
                    citation_selections=citations,
                    candidate_count=direction["candidate_count"],
                    quality_targets=tuple(direction["quality_targets"]),
                    repair_scope=tuple(direction["repair_scope"]),
                    tool_intents=tuple(tools),
                )
            )
        payload = CreativePlanPayload(
            directions=tuple(directions),
            schema_version=str(data["schema_version"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DataIntegrityError("persisted Creative Plan payload is invalid") from exc
    if payload.to_canonical_data() != data:
        raise DataIntegrityError("persisted Creative Plan payload is not canonical")
    return payload


def _version_from_model(model: CreativePlanVersionModel) -> CreativePlanVersion:
    payload = _payload_from_data(model.payload_json)
    try:
        provenance = CreativePlanProvenance(
            product_brief_id=model.product_brief_id,
            product_brief_version=model.product_brief_version,
            product_brief_sha256=model.product_brief_sha256,
            brand_profile_id=model.brand_profile_id,
            brand_profile_version=model.brand_profile_version,
            brand_profile_sha256=model.brand_profile_sha256,
            retrieval_run_id=model.retrieval_run_id,
            retrieval_citation_ids=tuple(model.retrieval_citation_ids_json),
            context_policy_version=model.context_policy_version,
            context_sha256=model.context_sha256,
            prompt_id=model.prompt_id,
            prompt_revision=model.prompt_revision,
            prompt_sha256=model.prompt_sha256,
        )
        return CreativePlanVersion(
            id=model.id,
            workspace_id=model.workspace_id,
            workflow_id=model.workflow_id,
            creative_plan_id=model.creative_plan_id,
            version_number=model.version_number,
            supersedes_version_id=model.supersedes_version_id,
            source=CreativePlanSource(model.source),
            payload=payload,
            provenance=provenance,
            payload_sha256=model.payload_sha256,
            actor_id=model.actor_id,
            revision_reason=model.revision_reason,
            created_at=model.created_at,
        )
    except (TypeError, ValueError) as exc:
        raise DataIntegrityError("persisted Creative Plan version is invalid") from exc


def _version_to_model(
    version: CreativePlanVersion,
    *,
    retain_until: datetime,
) -> CreativePlanVersionModel:
    provenance = version.provenance
    return CreativePlanVersionModel(
        id=version.id,
        workspace_id=version.workspace_id,
        workflow_id=version.workflow_id,
        creative_plan_id=version.creative_plan_id,
        version_number=version.version_number,
        supersedes_version_id=version.supersedes_version_id,
        source=version.source.value,
        payload_json=version.payload.to_canonical_data(),
        payload_sha256=version.payload_sha256,
        product_brief_id=provenance.product_brief_id,
        product_brief_version=provenance.product_brief_version,
        product_brief_sha256=provenance.product_brief_sha256,
        brand_profile_id=provenance.brand_profile_id,
        brand_profile_version=provenance.brand_profile_version,
        brand_profile_sha256=provenance.brand_profile_sha256,
        retrieval_run_id=provenance.retrieval_run_id,
        retrieval_citation_ids_json=list(provenance.retrieval_citation_ids),
        context_policy_version=provenance.context_policy_version,
        context_sha256=provenance.context_sha256,
        prompt_id=provenance.prompt_id,
        prompt_revision=provenance.prompt_revision,
        prompt_sha256=provenance.prompt_sha256,
        actor_id=version.actor_id,
        revision_reason=version.revision_reason,
        retain_until=retain_until,
        created_at=version.created_at,
    )


def _head_from_model(model: CreativePlanModel) -> CreativePlanHead:
    try:
        return CreativePlanHead(
            workspace_id=model.workspace_id,
            workflow_id=model.workflow_id,
            creative_plan_id=model.id,
            current_version_id=model.current_version_id,
            current_version_number=model.current_version_number,
            version=model.version,
            retain_until=model.retain_until,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
    except ValueError as exc:
        raise DataIntegrityError("persisted Creative Plan head is invalid") from exc


class CreativePlanRepository:
    """Tenant-first append/read adapter; an append advances exactly one head."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def append_version(
        self,
        version: CreativePlanVersion,
        *,
        expected_head_version: int,
        retain_until: datetime,
        authorized_at: datetime,
    ) -> CreativePlanHead:
        if retain_until.tzinfo is None or retain_until.utcoffset() is None:
            raise ValueError("Creative Plan retention deadline must be timezone-aware")
        if authorized_at.tzinfo is None or authorized_at.utcoffset() is None:
            raise ValueError("Creative Plan authority time must be timezone-aware")
        retain_until = retain_until.astimezone(UTC)
        authorized_at = authorized_at.astimezone(UTC)
        existing = self.get_version(
            workspace_id=version.workspace_id,
            workflow_id=version.workflow_id,
            creative_plan_id=version.creative_plan_id,
            version_id=version.id,
        )
        if existing is not None:
            existing_head = self.get_head(
                workspace_id=version.workspace_id,
                workflow_id=version.workflow_id,
                creative_plan_id=version.creative_plan_id,
            )
            if (
                existing != version
                or existing_head is None
                or existing_head.current_version_id != version.id
            ):
                raise DataIntegrityError("Creative Plan version id stores different facts")
            return existing_head
        head: CreativePlanHead | None = None
        effective_retain_until = retain_until
        if expected_head_version != 0:
            head = self.get_head(
                workspace_id=version.workspace_id,
                workflow_id=version.workflow_id,
                creative_plan_id=version.creative_plan_id,
                for_update=True,
            )
            if head is None:
                raise NotFoundError("Creative Plan does not exist")
            if head.version != expected_head_version:
                raise ConcurrencyError("Creative Plan head changed concurrently")
            if retain_until < head.retain_until:
                raise ConcurrencyError("Workflow retention changed before Creative Plan revision")
            effective_retain_until = head.retain_until
        self._require_provenance_authority(
            version,
            retain_until=effective_retain_until,
            authorized_at=authorized_at,
        )
        if expected_head_version == 0:
            head = CreativePlanHead.from_first_version(
                version,
                retain_until=effective_retain_until,
            )
            self._session.add(_version_to_model(version, retain_until=effective_retain_until))
            flush_with_integrity_classification(self._session)
            self._session.add(
                CreativePlanModel(
                    id=head.creative_plan_id,
                    workspace_id=head.workspace_id,
                    workflow_id=head.workflow_id,
                    current_version_id=head.current_version_id,
                    current_version_number=head.current_version_number,
                    version=head.version,
                    retain_until=head.retain_until,
                    created_at=head.created_at,
                    updated_at=head.updated_at,
                )
            )
            flush_with_integrity_classification(self._session)
            return head
        assert head is not None
        advanced = head.advance(version, expected_version=expected_head_version)
        self._session.add(_version_to_model(version, retain_until=effective_retain_until))
        flush_with_integrity_classification(self._session)
        result = cast(
            CursorResult[Any],
            execute_with_integrity_classification(
                self._session,
                update(CreativePlanModel)
                .where(
                    CreativePlanModel.workspace_id == head.workspace_id,
                    CreativePlanModel.workflow_id == head.workflow_id,
                    CreativePlanModel.id == head.creative_plan_id,
                    CreativePlanModel.version == expected_head_version,
                )
                .values(
                    current_version_id=advanced.current_version_id,
                    current_version_number=advanced.current_version_number,
                    version=advanced.version,
                    updated_at=advanced.updated_at,
                ),
            ),
        )
        if result.rowcount != 1:
            raise ConcurrencyError("Creative Plan head changed concurrently")
        return advanced

    def _require_provenance_authority(
        self,
        version: CreativePlanVersion,
        *,
        retain_until: datetime,
        authorized_at: datetime,
    ) -> None:
        provenance = version.provenance
        context_model = self._session.scalar(
            select(PlanningContextSnapshotModel).where(
                PlanningContextSnapshotModel.workspace_id == version.workspace_id,
                PlanningContextSnapshotModel.workflow_id == version.workflow_id,
                PlanningContextSnapshotModel.context_sha256 == provenance.context_sha256,
                PlanningContextSnapshotModel.policy_version == provenance.context_policy_version,
            )
        )
        if (
            context_model is None
            or context_model.retain_until != retain_until
            or context_model.retain_until <= authorized_at
        ):
            raise NotFoundError("Creative Plan provenance authority does not exist")
        snapshot = PlanningContextSnapshotRepository(self._session).get(
            workspace_id=version.workspace_id,
            workflow_id=version.workflow_id,
            context_sha256=provenance.context_sha256,
        )
        if snapshot is None:
            raise NotFoundError("Creative Plan provenance authority does not exist")
        product_sources = tuple(
            item.source
            for item in snapshot.included_sources
            if item.source.kind is PlanningContextSourceKind.PRODUCT_BRIEF
        )
        brand_sources = tuple(
            item.source
            for item in snapshot.included_sources
            if item.source.kind is PlanningContextSourceKind.BRAND_PROFILE
        )
        citation_sources = tuple(
            item.source
            for item in snapshot.included_sources
            if item.source.kind is PlanningContextSourceKind.RETRIEVAL_CITATION
        )
        product_matches = len(product_sources) == 1 and (
            product_sources[0].source_id,
            product_sources[0].version_number,
            product_sources[0].content_sha256,
        ) == (
            provenance.product_brief_id,
            provenance.product_brief_version,
            provenance.product_brief_sha256,
        )
        expected_brand = (
            provenance.brand_profile_id,
            provenance.brand_profile_version,
            provenance.brand_profile_sha256,
        )
        actual_brand = (
            (None, None, None)
            if not brand_sources
            else (
                brand_sources[0].source_id,
                brand_sources[0].version_number,
                brand_sources[0].content_sha256,
            )
        )
        citation_ids = tuple(source.citation_id for source in citation_sources)
        citations_match = citation_ids == provenance.retrieval_citation_ids and all(
            source.retrieval_run_id == provenance.retrieval_run_id for source in citation_sources
        )
        if (
            not product_matches
            or len(brand_sources) > 1
            or actual_brand != expected_brand
            or not citations_match
        ):
            raise NotFoundError("Creative Plan provenance authority does not exist")
        prompt = PromptRevisionRepository(self._session).get_by_semantic_revision(
            workspace_id=version.workspace_id,
            prompt_id=provenance.prompt_id,
            semantic_revision=provenance.prompt_revision,
        )
        if (
            prompt is None
            or prompt.status is not PromptRevisionStatus.PRODUCTION
            or prompt.content_sha256 != provenance.prompt_sha256
        ):
            raise NotFoundError("Creative Plan provenance authority does not exist")
        retrieval_run = self._session.scalar(
            select(RetrievalRunModel).where(
                RetrievalRunModel.workspace_id == version.workspace_id,
                RetrievalRunModel.id == provenance.retrieval_run_id,
                RetrievalRunModel.created_at <= authorized_at,
                RetrievalRunModel.expires_at > authorized_at,
            )
        )
        if retrieval_run is None:
            raise NotFoundError("Creative Plan provenance authority does not exist")

    def get_head(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
        for_update: bool = False,
    ) -> CreativePlanHead | None:
        statement = select(CreativePlanModel).where(
            CreativePlanModel.workspace_id == workspace_id,
            CreativePlanModel.workflow_id == workflow_id,
            CreativePlanModel.id == creative_plan_id,
        )
        if for_update:
            statement = statement.with_for_update()
        model = self._session.scalar(statement)
        return _head_from_model(model) if model is not None else None

    def get_version(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
        version_id: str,
    ) -> CreativePlanVersion | None:
        model = self._session.scalar(
            select(CreativePlanVersionModel).where(
                CreativePlanVersionModel.workspace_id == workspace_id,
                CreativePlanVersionModel.workflow_id == workflow_id,
                CreativePlanVersionModel.creative_plan_id == creative_plan_id,
                CreativePlanVersionModel.id == version_id,
            )
        )
        return _version_from_model(model) if model is not None else None

    def get_version_by_number(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
        version_number: int,
    ) -> CreativePlanVersion | None:
        model = self._session.scalar(
            select(CreativePlanVersionModel).where(
                CreativePlanVersionModel.workspace_id == workspace_id,
                CreativePlanVersionModel.workflow_id == workflow_id,
                CreativePlanVersionModel.creative_plan_id == creative_plan_id,
                CreativePlanVersionModel.version_number == version_number,
            )
        )
        return _version_from_model(model) if model is not None else None

    def get_current(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
    ) -> tuple[CreativePlanHead, CreativePlanVersion] | None:
        head = self.get_head(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            creative_plan_id=creative_plan_id,
        )
        if head is None:
            return None
        version = self.get_version(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            creative_plan_id=creative_plan_id,
            version_id=head.current_version_id,
        )
        if version is None or version.version_number != head.current_version_number:
            raise DataIntegrityError("Creative Plan head cannot be reconstructed")
        return head, version

    def list_versions(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
    ) -> tuple[CreativePlanVersion, ...] | None:
        head = self.get_head(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            creative_plan_id=creative_plan_id,
        )
        if head is None:
            return None
        models = tuple(
            self._session.scalars(
                select(CreativePlanVersionModel)
                .where(
                    CreativePlanVersionModel.workspace_id == workspace_id,
                    CreativePlanVersionModel.workflow_id == workflow_id,
                    CreativePlanVersionModel.creative_plan_id == creative_plan_id,
                )
                .order_by(CreativePlanVersionModel.version_number)
            )
        )
        versions = tuple(_version_from_model(model) for model in models)
        expected_numbers = tuple(range(1, head.current_version_number + 1))
        actual_numbers = tuple(version.version_number for version in versions)
        lineage_is_valid = all(
            current.supersedes_version_id == previous.id
            for previous, current in zip(versions, versions[1:], strict=False)
        )
        if (
            actual_numbers != expected_numbers
            or not lineage_is_valid
            or not versions
            or versions[-1].id != head.current_version_id
            or any(model.retain_until != head.retain_until for model in models)
        ):
            raise DataIntegrityError("Creative Plan history cannot be reconstructed")
        return versions


class SqlAlchemyCreativePlanUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self.session: Session | None = None
        self._depth_token: object | None = None
        self._committed = False

    def __enter__(self) -> SqlAlchemyCreativePlanUnitOfWork:
        self.session = self._session_factory()
        self._depth_token = enter_unit_of_work()
        self.workflows = WorkflowRepository(self.session)
        self.creative_plans = CreativePlanRepository(self.session)
        return self

    def database_now(self) -> datetime:
        if self.session is None:
            raise RuntimeError("unit of work is not active")
        value = self.session.scalar(select(literal_column("UTC_TIMESTAMP(6)")))
        if not isinstance(value, datetime):
            raise RuntimeError("database did not return a timestamp")
        return value.replace(tzinfo=UTC)

    def commit(self) -> None:
        if self.session is None:
            raise RuntimeError("unit of work is not active")
        try:
            self.session.commit()
        except DBAPIError as exc:
            self.session.rollback()
            classified = classify_database_error(exc)
            if classified is None:
                raise
            raise classified from exc
        self._committed = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if self.session is not None and (exc_type is not None or not self._committed):
                self.session.rollback()
        finally:
            if self.session is not None:
                self.session.close()
            if self._depth_token is not None:
                exit_unit_of_work(self._depth_token)
