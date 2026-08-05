"""Deterministic, side-effect-free Creative Plan fixture implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID, uuid5

from commercevision_contracts import CreativePlanCreateRequestV1
from commercevision_domain import (
    ConcurrencyError,
    CreativePlanCitationSelection,
    CreativePlanDirection,
    CreativePlanPayload,
    CreativePlanProvenance,
    ImageRole,
    NotFoundError,
    PlanningContextSnapshot,
    PlanningContextSource,
    PlanningContextSourceKind,
    PromptRevision,
    PromptRevisionStatus,
    ToolIntentProposal,
    canonicalize_uuid,
    validate_workspace_id,
)

from .creative_plans import CreativePlanWriteResult
from .planning_contexts import (
    PlanningContextBuildRequest,
    PlanningContextExactReference,
)

_MODEL_FAMILY = "fixture-planner"
_NODE = "CREATE_CREATIVE_PLAN"
_INPUT_SCHEMA = "planning-context.v1"
_OUTPUT_SCHEMA = "creative-plan.v1"
_PLAN_NAMESPACE = UUID("d5eec8b7-bc8f-43a2-845b-ec96dd6bd2f4")


@dataclass(frozen=True, slots=True)
class FixturePlannerRequest:
    context: PlanningContextSnapshot
    prompt: PromptRevision
    retrieval_run_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.context, PlanningContextSnapshot):
            raise ValueError("Fixture Planner context is invalid")
        if not isinstance(self.prompt, PromptRevision):
            raise ValueError("Fixture Planner Prompt is invalid")
        if canonicalize_uuid(self.retrieval_run_id) != self.retrieval_run_id:
            raise ValueError("Fixture Planner Retrieval Run id is invalid")


@dataclass(frozen=True, slots=True)
class FixturePlannerDraft:
    payload: CreativePlanPayload
    provenance: CreativePlanProvenance


@dataclass(frozen=True, slots=True)
class FixturePlanningAuthority:
    product_brief: PlanningContextExactReference
    brand_profile: PlanningContextExactReference | None
    retrieval_citations: tuple[PlanningContextExactReference, ...]
    retrieval_run_id: str
    category: str


@dataclass(frozen=True, slots=True)
class DurableFixturePlannerCommand:
    workspace_id: str
    workflow_id: str
    product_brief_version_id: str
    product_brief_version_number: int
    actor_id: str
    expected_workflow_version: int
    trace_id: str
    idempotency_key: str

    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)
        for value, field in (
            (self.workflow_id, "Workflow id"),
            (self.product_brief_version_id, "ProductBrief Version id"),
        ):
            if canonicalize_uuid(value) != value:
                raise ValueError(f"Fixture Planner {field} is invalid")
        if (
            type(self.product_brief_version_number) is not int
            or self.product_brief_version_number < 1
            or type(self.expected_workflow_version) is not int
            or self.expected_workflow_version < 1
        ):
            raise ValueError("Fixture Planner expected versions are invalid")
        if not self.actor_id or not self.trace_id or len(self.idempotency_key) < 8:
            raise ValueError("Fixture Planner command identity is invalid")


@dataclass(frozen=True, slots=True)
class DurableFixturePlanResult:
    creative_plan_id: str
    version_id: str
    version_number: int
    payload_sha256: str
    context_sha256: str
    prompt_id: str
    prompt_revision: str

    def to_step_output(self) -> dict[str, object]:
        return {
            "creative_plan_ref": self.creative_plan_id,
            "creative_plan_version_id": self.version_id,
            "creative_plan_version": self.version_number,
            "creative_plan_payload_sha256": self.payload_sha256,
            "planning_context_sha256": self.context_sha256,
            "prompt_id": self.prompt_id,
            "prompt_revision": self.prompt_revision,
            "plan_decision": None,
        }


class FixturePlanningAuthorityPort(Protocol):
    def load(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        product_brief_version_id: str,
        product_brief_version_number: int,
        expected_workflow_version: int,
    ) -> FixturePlanningAuthority: ...


class PlanningContextBuilderPort(Protocol):
    def build(self, request: PlanningContextBuildRequest) -> PlanningContextSnapshot: ...


class ProductionPromptResolverPort(Protocol):
    def resolve_production_revision(
        self,
        *,
        workspace_id: str,
        prompt_id: str,
        node: str,
        category: str,
        model_family: str,
    ) -> PromptRevision: ...


class CreativePlanWriterPort(Protocol):
    def get_current(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        creative_plan_id: str,
    ) -> CreativePlanWriteResult: ...

    def create_plan(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        request: CreativePlanCreateRequestV1,
        trace_id: str,
        idempotency_key: str,
    ) -> CreativePlanWriteResult: ...


class DurableFixturePlanner:
    """Compose authoritative inputs; the final Plan append rechecks them at commit."""

    def __init__(
        self,
        *,
        authority: FixturePlanningAuthorityPort,
        contexts: PlanningContextBuilderPort,
        prompts: ProductionPromptResolverPort,
        plans: CreativePlanWriterPort,
        planner: DeterministicFixturePlanner | None = None,
        context_policy_version: str = "planning-context-v1",
        prompt_id: str = "creative-planner",
    ) -> None:
        self._authority = authority
        self._contexts = contexts
        self._prompts = prompts
        self._plans = plans
        self._planner = planner or DeterministicFixturePlanner()
        self._context_policy_version = context_policy_version
        self._prompt_id = prompt_id

    def create_plan(self, command: DurableFixturePlannerCommand) -> DurableFixturePlanResult:
        if not isinstance(command, DurableFixturePlannerCommand):
            raise ValueError("Durable Fixture Planner command is invalid")
        authority = self._authority.load(
            workspace_id=command.workspace_id,
            workflow_id=command.workflow_id,
            product_brief_version_id=command.product_brief_version_id,
            product_brief_version_number=command.product_brief_version_number,
            expected_workflow_version=command.expected_workflow_version,
        )
        creative_plan_id = str(
            uuid5(_PLAN_NAMESPACE, f"{command.workspace_id}\0{command.workflow_id}")
        )
        try:
            stored = self._plans.get_current(
                workspace_id=command.workspace_id,
                workflow_id=command.workflow_id,
                creative_plan_id=creative_plan_id,
            )
        except NotFoundError:
            pass
        else:
            self._validate_replay(
                stored,
                authority=authority,
                command=command,
                creative_plan_id=creative_plan_id,
            )
            return self._to_result(stored)
        context = self._contexts.build(
            PlanningContextBuildRequest(
                workspace_id=command.workspace_id,
                workflow_id=command.workflow_id,
                purpose="creative-planning",
                product_brief=authority.product_brief,
                brand_profile=authority.brand_profile,
                retrieval_citations=authority.retrieval_citations,
                context_policy_version=self._context_policy_version,
            )
        )
        prompt = self._prompts.resolve_production_revision(
            workspace_id=command.workspace_id,
            prompt_id=self._prompt_id,
            node=_NODE,
            category=authority.category,
            model_family=_MODEL_FAMILY,
        )
        draft = self._planner.plan(
            FixturePlannerRequest(
                context=context,
                prompt=prompt,
                retrieval_run_id=authority.retrieval_run_id,
            )
        )
        provenance = draft.provenance
        stored = self._plans.create_plan(
            workspace_id=command.workspace_id,
            actor_id=command.actor_id,
            request=CreativePlanCreateRequestV1.model_validate(
                {
                    "workflow_id": command.workflow_id,
                    "creative_plan_id": creative_plan_id,
                    "payload": draft.payload.to_canonical_data(),
                    "provenance": {
                        "product_brief_id": provenance.product_brief_id,
                        "product_brief_version": provenance.product_brief_version,
                        "product_brief_sha256": provenance.product_brief_sha256,
                        "brand_profile_id": provenance.brand_profile_id,
                        "brand_profile_version": provenance.brand_profile_version,
                        "brand_profile_sha256": provenance.brand_profile_sha256,
                        "retrieval_run_id": provenance.retrieval_run_id,
                        "retrieval_citation_ids": list(provenance.retrieval_citation_ids),
                        "context_policy_version": provenance.context_policy_version,
                        "context_sha256": provenance.context_sha256,
                        "prompt_id": provenance.prompt_id,
                        "prompt_revision": provenance.prompt_revision,
                        "prompt_sha256": provenance.prompt_sha256,
                    },
                    "expected_workflow_version": command.expected_workflow_version,
                    "expected_head_version": 0,
                }
            ),
            trace_id=command.trace_id,
            idempotency_key=command.idempotency_key,
        )
        return self._to_result(stored)

    @staticmethod
    def _to_result(stored: CreativePlanWriteResult) -> DurableFixturePlanResult:
        return DurableFixturePlanResult(
            creative_plan_id=stored.head.creative_plan_id,
            version_id=stored.version.id,
            version_number=stored.version.version_number,
            payload_sha256=stored.version.payload_sha256,
            context_sha256=stored.version.provenance.context_sha256,
            prompt_id=stored.version.provenance.prompt_id,
            prompt_revision=stored.version.provenance.prompt_revision,
        )

    @staticmethod
    def _validate_replay(
        stored: CreativePlanWriteResult,
        *,
        authority: FixturePlanningAuthority,
        command: DurableFixturePlannerCommand,
        creative_plan_id: str,
    ) -> None:
        version = stored.version
        provenance = version.provenance
        if (
            stored.head.workspace_id != command.workspace_id
            or stored.head.workflow_id != command.workflow_id
            or stored.head.creative_plan_id != creative_plan_id
            or stored.head.current_version_id != version.id
            or stored.head.current_version_number != 1
            or stored.head.version != 1
            or version.version_number != 1
            or version.provenance.product_brief_id != authority.product_brief.source_id
            or provenance.product_brief_version != command.product_brief_version_number
            or provenance.product_brief_sha256 != authority.product_brief.content_sha256
            or provenance.retrieval_run_id != authority.retrieval_run_id
        ):
            raise ConcurrencyError("Fixture Planner durable replay authority changed")


class DurableFixturePlannerNode:
    """Agent-core adapter that keeps command construction out of the graph."""

    def __init__(self, planner: DurableFixturePlanner) -> None:
        self._planner = planner

    def create_plan(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        product_brief_version_id: str | None,
        product_brief_version_number: int | None,
        actor_id: str,
        expected_workflow_version: int,
        trace_id: str,
        idempotency_key: str,
    ) -> DurableFixturePlanResult:
        if product_brief_version_id is None or product_brief_version_number is None:
            raise ValueError("Fixture Planner requires an exact confirmed ProductBrief")
        return self._planner.create_plan(
            DurableFixturePlannerCommand(
                workspace_id=workspace_id,
                workflow_id=workflow_id,
                product_brief_version_id=product_brief_version_id,
                product_brief_version_number=product_brief_version_number,
                actor_id=actor_id,
                expected_workflow_version=expected_workflow_version,
                trace_id=trace_id,
                idempotency_key=idempotency_key,
            )
        )


class DeterministicFixturePlanner:
    """Produce bounded known-good examples without a model or provider call."""

    def plan(self, request: FixturePlannerRequest) -> FixturePlannerDraft:
        if not isinstance(request, FixturePlannerRequest):
            raise ValueError("Fixture Planner request is invalid")
        product, brand, citations = self._sources(request.context)
        category = self._category(product)
        self._validate_prompt(
            request.prompt,
            workspace_id=request.context.workspace_id,
            category=category,
        )
        citation_ids = tuple(
            citation.citation_id for citation in citations if citation.citation_id is not None
        )
        if any(citation.retrieval_run_id != request.retrieval_run_id for citation in citations):
            raise ValueError("Fixture Planner citations belong to another Retrieval Run")
        payload = self._payload(category, citation_ids)
        return FixturePlannerDraft(
            payload=payload,
            provenance=CreativePlanProvenance(
                product_brief_id=product.source_id,
                product_brief_version=self._version(product, "ProductBrief"),
                product_brief_sha256=product.content_sha256,
                brand_profile_id=brand.source_id if brand is not None else None,
                brand_profile_version=(
                    self._version(brand, "Brand Profile") if brand is not None else None
                ),
                brand_profile_sha256=(brand.content_sha256 if brand is not None else None),
                retrieval_run_id=request.retrieval_run_id,
                retrieval_citation_ids=citation_ids,
                context_policy_version=request.context.policy.version,
                context_sha256=request.context.context_sha256,
                prompt_id=request.prompt.prompt_id,
                prompt_revision=request.prompt.semantic_revision,
                prompt_sha256=request.prompt.content_sha256,
            ),
        )

    @staticmethod
    def _sources(
        context: PlanningContextSnapshot,
    ) -> tuple[
        PlanningContextSource,
        PlanningContextSource | None,
        tuple[PlanningContextSource, ...],
    ]:
        product = tuple(
            item.source
            for item in context.included_sources
            if item.source.kind is PlanningContextSourceKind.PRODUCT_BRIEF
        )
        brand = tuple(
            item.source
            for item in context.included_sources
            if item.source.kind is PlanningContextSourceKind.BRAND_PROFILE
        )
        citations = tuple(
            item.source
            for item in context.included_sources
            if item.source.kind is PlanningContextSourceKind.RETRIEVAL_CITATION
        )
        if len(product) != 1 or len(brand) > 1:
            raise ValueError("Fixture Planner context has invalid source authority")
        return product[0], brand[0] if brand else None, citations

    @staticmethod
    def _category(product: PlanningContextSource) -> str:
        value = product.content().get("category")
        normalized = {
            "BEAUTY": "beauty",
            "AUTOMOTIVE": "automotive-parts",
            "beauty": "beauty",
            "automotive-parts": "automotive-parts",
        }.get(value if isinstance(value, str) else "")
        if normalized is None:
            raise ValueError("Fixture Planner ProductBrief category is unsupported")
        return normalized

    @staticmethod
    def _version(source: PlanningContextSource, label: str) -> int:
        if source.version_number is None:
            raise ValueError(f"Fixture Planner {label} version is unavailable")
        return cast(int, source.version_number)

    @staticmethod
    def _validate_prompt(prompt: PromptRevision, *, workspace_id: str, category: str) -> None:
        if (
            prompt.workspace_id != workspace_id
            or prompt.status is not PromptRevisionStatus.PRODUCTION
            or prompt.node != _NODE
            or prompt.input_schema_version != _INPUT_SCHEMA
            or prompt.output_schema_version != _OUTPUT_SCHEMA
            or _MODEL_FAMILY not in prompt.model_family_applicability
        ):
            raise ValueError("Fixture Planner requires an applicable production Prompt")
        if category not in prompt.category_applicability:
            raise ValueError("Fixture Planner Prompt does not apply to the ProductBrief category")

    @classmethod
    def _payload(cls, category: str, citation_ids: tuple[str, ...]) -> CreativePlanPayload:
        citations = tuple(
            CreativePlanCitationSelection(
                citation_id=citation_id,
                reason="Selected by deterministic Retrieval rank",
            )
            for citation_id in citation_ids[:2]
        )
        if category == "beauty":
            directions = (
                cls._direction(
                    key="beauty-hero",
                    image_role=ImageRole.HERO,
                    scene="Premium vanity studio with a clean reflective surface",
                    composition="Centered packshot with generous commerce-safe negative space",
                    camera="Three-quarter eye-level product view",
                    lighting="Soft diffused key light with controlled cosmetic highlights",
                    color_direction="Warm pearl neutrals with restrained brand accents",
                    required=("Primary package", "Legible product silhouette"),
                    prohibited=("Skin claims", "Unverified before-and-after results"),
                    citations=citations,
                ),
                cls._direction(
                    key="beauty-detail",
                    image_role=ImageRole.DETAIL,
                    scene="Macro material and applicator detail on a neutral surface",
                    composition="Tight detail crop while preserving package identity",
                    camera="Macro close-up",
                    lighting="Raking soft light for texture without glare",
                    color_direction="Tonal neutrals matched to the hero direction",
                    required=("Material detail", "Recognizable package cue"),
                    prohibited=("Medical imagery", "Unsupported efficacy symbols"),
                    citations=citations,
                ),
            )
        else:
            directions = (
                cls._direction(
                    key="automotive-hero",
                    image_role=ImageRole.HERO,
                    scene="Precision workshop bay with a clean technical backdrop",
                    composition="Centered component hero with installation-safe clearance",
                    camera="Three-quarter technical product view",
                    lighting="Crisp directional studio light with controlled metal reflections",
                    color_direction="Graphite, steel, and restrained safety accents",
                    required=("Complete component geometry", "Visible mounting interfaces"),
                    prohibited=("Incorrect vehicle fitment", "Altered safety markings"),
                    citations=citations,
                ),
                cls._direction(
                    key="automotive-detail",
                    image_role=ImageRole.DETAIL,
                    scene="Technical close-up on a calibrated neutral bench",
                    composition="Detail crop focused on connector and finish quality",
                    camera="Macro technical close-up",
                    lighting="Cross-polarized detail lighting",
                    color_direction="Neutral metal tones with accurate material color",
                    required=("Connector detail", "Surface finish"),
                    prohibited=("Fabricated certifications", "Hidden damage"),
                    citations=citations,
                ),
            )
        return CreativePlanPayload(directions=directions)

    @staticmethod
    def _direction(
        *,
        key: str,
        image_role: ImageRole,
        scene: str,
        composition: str,
        camera: str,
        lighting: str,
        color_direction: str,
        required: tuple[str, ...],
        prohibited: tuple[str, ...],
        citations: tuple[CreativePlanCitationSelection, ...],
    ) -> CreativePlanDirection:
        return CreativePlanDirection(
            key=key,
            image_role=image_role,
            scene=scene,
            composition=composition,
            camera=camera,
            lighting=lighting,
            color_direction=color_direction,
            product_constraints=(
                "Preserve exact product identity and geometry",
                "Do not invent labels, claims, fitment, or included accessories",
            ),
            required_elements=required,
            prohibited_elements=prohibited,
            citation_selections=citations,
            candidate_count=1,
            quality_targets=(
                "Commerce-ready subject clarity",
                "Faithful materials and proportions",
            ),
            repair_scope=("background", "lighting", "composition"),
            tool_intents=(
                ToolIntentProposal.create(
                    intent_key=f"{key}-fixture-image",
                    tool_name="fixture.image.generate",
                    schema_version="1.0",
                    purpose="Generate one deterministic fixture candidate",
                    arguments={"count": 1},
                    estimated_cost_units=1,
                ),
            ),
        )
