"""Small application seam for one authorized image-generation dispatch."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid5

from commercevision_contracts.image_provider import (
    ControlledImageInput,
    ImageEditingProviderRequest,
    ImageGenerationProviderRequest,
    ImageProviderCallOutcome,
    ImageProviderInputRole,
    ImageProviderMediaRequirements,
    ImageProviderOutputFormat,
    ImageProviderSubmitRequest,
    ImageProviderTaskState,
    NormalizedImageProviderOutcome,
)
from commercevision_contracts.object_storage import ObjectStat, ServerSideEncryptionState
from commercevision_domain import (
    CandidateSlot,
    CreativePlanDirection,
    CreativePlanVersion,
    GenerationBatch,
    ImageGenerationRequest,
    ModelRouteRequest,
    OperationKind,
    ProviderCapability,
    ToolIntentProposal,
    validate_candidate_request_authority,
)

from .asset_idempotency import canonical_hash
from .generation_commands import ApprovedGenerationAuthority
from .operations import OperationExecutionRequest, OperationExecutionResult

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$", re.ASCII)


def _canonical_uuid(value: str, field_name: str) -> None:
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError):
        raise ValueError(f"authorized generation {field_name} is invalid") from None
    if canonical != value:
        raise ValueError(f"authorized generation {field_name} is invalid")


class GenerationDispatchAuthorityDenied(Exception):
    """Current durable facts no longer authorize one generation dispatch."""


@dataclass(frozen=True, slots=True)
class AuthorizedGenerationDispatch:
    """Credential-free, immutable input for one exact routed Provider call."""

    operation: OperationExecutionRequest
    endpoint_capability_version_id: str
    adapter_configuration_sha256: str
    provider_request: ImageProviderSubmitRequest
    request_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.operation, OperationExecutionRequest):
            raise ValueError("authorized generation Operation is invalid")
        _canonical_uuid(self.operation.operation_id, "Operation id")
        _canonical_uuid(
            self.endpoint_capability_version_id,
            "Endpoint Capability id",
        )
        if _SHA256_PATTERN.fullmatch(self.adapter_configuration_sha256) is None:
            raise ValueError("authorized generation Adapter configuration hash is invalid")
        if not isinstance(
            self.provider_request,
            ImageGenerationProviderRequest | ImageEditingProviderRequest,
        ):
            raise ValueError("authorized generation Provider request is invalid")
        object.__setattr__(self, "request_sha256", _provider_request_sha256(self.provider_request))

    @property
    def operation_id(self) -> str:
        return self.operation.operation_id


class GenerationDispatchAuthority(Protocol):
    def prepare_dispatch(
        self,
        request: OperationExecutionRequest,
    ) -> AuthorizedGenerationDispatch: ...


@dataclass(frozen=True, slots=True)
class GenerationDispatchFacts:
    """Exact, credential-free facts locked and revalidated for one dispatch."""

    operation: OperationExecutionRequest
    batch: GenerationBatch
    slot: CandidateSlot
    approved_authority: ApprovedGenerationAuthority
    creative_plan: CreativePlanVersion
    route_request: ModelRouteRequest
    endpoint_capability_version_id: str
    adapter_configuration_sha256: str


class GenerationDispatchBuilder(Protocol):
    def build(self, facts: GenerationDispatchFacts) -> AuthorizedGenerationDispatch: ...


class GenerationReferenceImageResolver(Protocol):
    def resolve(
        self,
        *,
        workspace_id: str,
        asset_version_ids: tuple[str, ...],
        deadline: datetime,
    ) -> tuple[ControlledImageInput, ...]: ...


class StructuredGenerationDispatchBuilder:
    """Render one versioned Provider request from approved structured Plan facts."""

    def __init__(
        self,
        *,
        reference_images: GenerationReferenceImageResolver | None = None,
    ) -> None:
        self._reference_images = reference_images

    def build(self, facts: GenerationDispatchFacts) -> AuthorizedGenerationDispatch:
        self._validate_facts(facts)
        direction = next(
            item
            for item in facts.creative_plan.payload.directions
            if item.key == facts.batch.direction_key
        )
        intent = next(
            item
            for item in direction.tool_intents
            if item.intent_key == facts.batch.tool_intent_key
        )
        lease_deadline = facts.operation.lease_expires_at
        if lease_deadline is None:
            raise ValueError("generation dispatch lease deadline is unavailable")
        deadline = min(
            facts.route_request.deadline_at,
            facts.batch.retention_deadline,
            lease_deadline,
        )
        reference_images = self._resolve_reference_images(facts, deadline=deadline)
        request = ImageGenerationProviderRequest(
            provider_idempotency_key=facts.operation.idempotency_key,
            prompt_text=self._render_prompt(direction=direction, intent=intent),
            negative_prompt_text=None,
            media=ImageProviderMediaRequirements(
                width=facts.route_request.width,
                height=facts.route_request.height,
                output_format=self._output_format(facts.route_request.required_output_format),
            ),
            reference_images=reference_images,
            deadline=deadline,
        )
        return AuthorizedGenerationDispatch(
            operation=facts.operation,
            endpoint_capability_version_id=facts.endpoint_capability_version_id,
            adapter_configuration_sha256=facts.adapter_configuration_sha256,
            provider_request=request,
        )

    @staticmethod
    def _validate_facts(facts: GenerationDispatchFacts) -> None:
        if not isinstance(facts, GenerationDispatchFacts):
            raise ValueError("generation dispatch facts are invalid")
        direction = next(
            (
                item
                for item in facts.creative_plan.payload.directions
                if item.key == facts.batch.direction_key
            ),
            None,
        )
        intent = (
            next(
                (
                    item
                    for item in direction.tool_intents
                    if item.intent_key == facts.batch.tool_intent_key
                ),
                None,
            )
            if direction is not None
            else None
        )
        candidate_request = ImageGenerationRequest(
            candidate_slot_id=facts.slot.id,
            prompt_sha256=facts.batch.prompt_sha256,
            context_sha256=facts.batch.context_sha256,
            reference_asset_version_ids=facts.batch.authorized_asset_version_ids,
        )
        validate_candidate_request_authority(
            batch=facts.batch,
            slot=facts.slot,
            request=candidate_request,
        )
        if (
            direction is None
            or intent is None
            or facts.operation.kind is not OperationKind.IMAGE_GENERATION
            or facts.route_request.required_capability is not ProviderCapability.IMAGE_GENERATION
            or facts.operation.target_id != facts.slot.id
            or facts.operation.operation_id != facts.slot.durable_operation_id
            or facts.creative_plan.id != facts.batch.creative_plan_version_id
            or facts.creative_plan.workspace_id != facts.batch.workspace_id
            or facts.creative_plan.workflow_id != facts.batch.workflow_id
            or facts.creative_plan.provenance.prompt_sha256 != facts.batch.prompt_sha256
            or facts.creative_plan.provenance.context_sha256 != facts.batch.context_sha256
            or canonical_hash(intent.to_canonical_data()) != facts.batch.tool_intent_sha256
            or facts.route_request.request_sha256 != facts.batch.route_request_sha256
            or facts.route_request.reference_image_count
            != len(facts.batch.authorized_asset_version_ids)
            or facts.operation.lease_expires_at is None
        ):
            raise ValueError("generation dispatch facts do not match approved authority")

    def _resolve_reference_images(
        self,
        facts: GenerationDispatchFacts,
        *,
        deadline: datetime,
    ) -> tuple[ControlledImageInput, ...]:
        asset_version_ids = facts.batch.authorized_asset_version_ids
        if not asset_version_ids:
            return ()
        if self._reference_images is None:
            raise ValueError("generation reference image resolver is unavailable")
        images = self._reference_images.resolve(
            workspace_id=facts.batch.workspace_id,
            asset_version_ids=asset_version_ids,
            deadline=deadline,
        )
        if (
            not isinstance(images, tuple)
            or len(images) != len(asset_version_ids)
            or any(
                not isinstance(image, ControlledImageInput)
                or image.role is not ImageProviderInputRole.REFERENCE
                for image in images
            )
        ):
            raise ValueError("generation reference images do not match approved authority")
        return images

    @staticmethod
    def _output_format(media_type: str) -> ImageProviderOutputFormat:
        formats = {
            "image/jpeg": ImageProviderOutputFormat.JPEG,
            "image/png": ImageProviderOutputFormat.PNG,
            "image/webp": ImageProviderOutputFormat.WEBP,
        }
        try:
            return formats[media_type]
        except KeyError:
            raise ValueError("generation route output format is unsupported") from None

    @staticmethod
    def _render_prompt(*, direction: object, intent: object) -> str:
        if not isinstance(direction, CreativePlanDirection) or not isinstance(
            intent, ToolIntentProposal
        ):
            raise ValueError("generation structured direction is invalid")

        def section(label: str, values: tuple[str, ...]) -> list[str]:
            return [f"{label}:", *(f"- {value}" for value in values)]

        return "\n".join(
            [
                "CommerceVision approved image direction (creative-plan-image.v1)",
                f"Image role: {direction.image_role.value}",
                f"Execution purpose: {intent.purpose}",
                f"Scene: {direction.scene}",
                f"Composition: {direction.composition}",
                f"Camera: {direction.camera}",
                f"Lighting: {direction.lighting}",
                f"Color direction: {direction.color_direction}",
                *section("Product constraints", direction.product_constraints),
                *section("Required elements", direction.required_elements),
                *section(
                    "Prohibited elements",
                    direction.prohibited_elements or ("None",),
                ),
                *section("Quality targets", direction.quality_targets),
            ]
        )


class GenerationProviderDispatcher(Protocol):
    def submit(
        self,
        dispatch: AuthorizedGenerationDispatch,
    ) -> OperationExecutionResult: ...


@dataclass(frozen=True, slots=True)
class GenerationDispatchAttemptClaim:
    """One durable decision about whether this exact attempt may call a Provider."""

    attempt_id: str
    submit_authorized: bool
    provider_request_id: str | None = None
    provider_task_id: str | None = None

    def __post_init__(self) -> None:
        _canonical_uuid(self.attempt_id, "dispatch Attempt id")
        if not isinstance(self.submit_authorized, bool):
            raise ValueError("generation dispatch Attempt decision is invalid")
        for value, field_name in (
            (self.provider_request_id, "request"),
            (self.provider_task_id, "task"),
        ):
            if value is not None and (
                not isinstance(value, str)
                or not value
                or len(value) > 256
                or value.lower().startswith(("sk-", "bearer "))
            ):
                raise ValueError(f"generation dispatch Provider {field_name} identity is invalid")
        if self.submit_authorized and (
            self.provider_request_id is not None or self.provider_task_id is not None
        ):
            raise ValueError("new generation dispatch Attempt cannot have Provider identity")


class GenerationDispatchAttemptCoordinator(Protocol):
    """Persist the no-resubmit fence around one exact external dispatch attempt."""

    def claim(
        self,
        dispatch: AuthorizedGenerationDispatch,
    ) -> GenerationDispatchAttemptClaim: ...

    def record_outcome(
        self,
        *,
        claim: GenerationDispatchAttemptClaim,
        dispatch: AuthorizedGenerationDispatch,
        outcome: NormalizedImageProviderOutcome,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class GenerationWorkflowContinuationClaim:
    """MySQL-authorized state for one complete immutable Generation Batch."""

    workspace_id: str
    workflow_id: str
    workflow_version: int
    actor_id: str
    input_data: dict[str, object]
    generation_batch_id: str
    creative_plan_id: str
    creative_plan_version_id: str
    creative_plan_version: int
    generation_iteration: int
    candidate_refs: tuple[str, ...]


class GenerationWorkflowContinuationAuthority(Protocol):
    def claim_ready_batch(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        generation_batch_id: str,
        candidate_slot_id: str,
        candidate_image_id: str,
        asset_version_id: str,
        operation_id: str,
        usage_record_id: str,
    ) -> GenerationWorkflowContinuationClaim | None: ...


def generation_provider_call_id(operation: OperationExecutionRequest) -> str:
    """Return the stable identity for call zero of one generation attempt."""

    if (
        not isinstance(operation, OperationExecutionRequest)
        or operation.kind is not OperationKind.IMAGE_GENERATION
    ):
        raise ValueError("generation Provider call Operation is invalid")
    return str(
        uuid5(
            UUID(operation.target_id),
            f"provider-call:{operation.attempt_count}:0",
        )
    )


def _provider_request_sha256(request: ImageProviderSubmitRequest) -> str:
    def image_data(image: ControlledImageInput) -> dict[str, object]:
        return {
            "handle": image.handle,
            "role": image.role.value,
            "content_sha256": image.content_sha256,
            "media_type": image.media_type.value,
            "width": image.width,
            "height": image.height,
        }

    common: dict[str, object] = {
        "schema": "image-provider-submit.v1",
        "provider_idempotency_key": request.provider_idempotency_key,
        "prompt_text": request.prompt_text,
        "negative_prompt_text": request.negative_prompt_text,
        "media": {
            "width": request.media.width,
            "height": request.media.height,
            "output_format": request.media.output_format.value,
            "seed": request.media.seed,
        },
        "deadline": request.deadline.isoformat().replace("+00:00", "Z"),
    }
    if isinstance(request, ImageGenerationProviderRequest):
        common.update(
            {
                "kind": "generation",
                "reference_images": [image_data(image) for image in request.reference_images],
            }
        )
    else:
        common.update(
            {
                "kind": "editing",
                "source_image": image_data(request.source_image),
                "mask_image": image_data(request.mask_image),
            }
        )
    return canonical_hash(common)


@dataclass(frozen=True, slots=True)
class GenerationSuccessCommit:
    """External facts accepted by the atomic result-convergence authority."""

    operation: OperationExecutionRequest
    provider_outcome: NormalizedImageProviderOutcome
    controlled_object: ObjectStat
    request_sha256: str
    moderation_decision_sha256: str
    trace_id: str

    def __post_init__(self) -> None:
        outcome = self.provider_outcome
        result = outcome.result
        reference = self.controlled_object.reference
        if (
            self.operation.lease_token is None
            or outcome.call_outcome is not ImageProviderCallOutcome.CONFIRMED_SUCCESS
            or outcome.task_state is not ImageProviderTaskState.SUCCEEDED
            or outcome.identity is None
            or result is None
            or outcome.error is not None
        ):
            raise ValueError("generation success commit requires one terminal Provider result")
        if (
            reference.version_id is None
            or self.controlled_object.content_length != len(result.content)
            or self.controlled_object.content_type != result.media_type.value
            or self.controlled_object.metadata.get("sha256") != result.content_sha256
            or self.controlled_object.server_side_encryption is ServerSideEncryptionState.NONE
        ):
            raise ValueError("generation success commit object does not match Provider bytes")
        for value, field_name in (
            (self.request_sha256, "request"),
            (self.moderation_decision_sha256, "moderation decision"),
        ):
            if _SHA256_PATTERN.fullmatch(value) is None:
                raise ValueError(f"generation success commit {field_name} hash is invalid")
        if not self.trace_id or len(self.trace_id) > 128:
            raise ValueError("generation success commit trace id is invalid")


class GenerationResultConverger(Protocol):
    def commit_success(
        self,
        commit: GenerationSuccessCommit,
    ) -> OperationExecutionResult: ...
