"""Versioned durable Workflow HTTP routes."""

from typing import Annotated

from commercevision_application import WorkflowEventStreamService
from commercevision_contracts.workflow import (
    ApprovalRequest,
    EventResponse,
    WorkflowCancelRequest,
    WorkflowCreateRequest,
    WorkflowListResponse,
    WorkflowResponse,
)
from commercevision_domain import ApprovalDecision, ApprovalType
from fastapi import APIRouter, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from .workflow_sse import WorkflowSsePolicy, stream_workflow_events
from .workspace_identity import WorkspaceHeader

router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])

ActorHeader = Annotated[str, Header(alias="X-Actor-Id", min_length=1, max_length=128)]
IdempotencyHeader = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=256)]


@router.post("", response_model=WorkflowResponse, status_code=status.HTTP_202_ACCEPTED)
def create_workflow(
    payload: WorkflowCreateRequest,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
) -> WorkflowResponse:
    return request.app.state.container.workflows.create(
        request=payload,
        workspace_id=workspace_id,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        trace_id=request.state.trace_id,
    )


@router.get("/{workflow_id}", response_model=WorkflowResponse)
def get_workflow(
    workflow_id: str,
    request: Request,
    workspace_id: WorkspaceHeader,
) -> WorkflowResponse:
    return request.app.state.container.workflows.get(
        workflow_id=workflow_id,
        workspace_id=workspace_id,
    )


@router.get("", response_model=WorkflowListResponse)
def list_workflows(
    request: Request,
    workspace_id: WorkspaceHeader,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: str | None = None,
) -> WorkflowListResponse:
    return request.app.state.container.workflows.list(
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
    )


@router.post("/{workflow_id}:cancel", response_model=WorkflowResponse)
def cancel_workflow(
    workflow_id: str,
    payload: WorkflowCancelRequest,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
) -> WorkflowResponse:
    return request.app.state.container.workflows.cancel(
        workflow_id=workflow_id,
        workspace_id=workspace_id,
        actor_id=actor_id,
        expected_version=payload.expected_workflow_version,
        idempotency_key=idempotency_key,
        trace_id=request.state.trace_id,
    )


@router.get(
    "/{workflow_id}/events",
    response_model=list[EventResponse],
    responses={
        200: {
            "description": "Persisted Workflow events as JSON or a resumable SSE stream.",
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                }
            },
        }
    },
)
async def workflow_events(
    workflow_id: str,
    request: Request,
    workspace_id: WorkspaceHeader,
    last_event_id: Annotated[
        str | None,
        Header(alias="Last-Event-ID", min_length=1, max_length=256),
    ] = None,
) -> list[EventResponse] | Response:
    accept = request.headers.get("Accept", "")
    service: WorkflowEventStreamService = request.app.state.container.workflow_events
    if "text/event-stream" not in accept.lower():
        return service.events(
            workflow_id=workflow_id,
            workspace_id=workspace_id,
        )
    cursor = last_event_id
    first_page = await run_in_threadpool(
        service.event_page,
        workflow_id=workflow_id,
        workspace_id=workspace_id,
        cursor=cursor,
    )
    policy = WorkflowSsePolicy.from_settings(request.app.state.settings)
    return StreamingResponse(
        stream_workflow_events(
            request=request,
            service=service,
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            trace_id=getattr(request.state, "trace_id", None),
            cursor=cursor,
            first_page=first_page,
            policy=policy,
            observer=getattr(request.app.state, "planning_telemetry", None),
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


def _approve(
    *,
    workflow_id: str,
    payload: ApprovalRequest,
    request: Request,
    workspace_id: str,
    actor_id: str,
    idempotency_key: str,
    approval_type: ApprovalType,
    decision: ApprovalDecision,
) -> WorkflowResponse:
    normalized = payload.model_copy(update={"decision": decision})
    return request.app.state.container.workflows.approve(
        workflow_id=workflow_id,
        workspace_id=workspace_id,
        actor_id=actor_id,
        approval_type=approval_type,
        request=normalized,
        idempotency_key=idempotency_key,
        trace_id=request.state.trace_id,
    )


@router.post("/{workflow_id}/creative-plan:approve", response_model=WorkflowResponse)
def approve_plan(
    workflow_id: str,
    payload: ApprovalRequest,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
) -> WorkflowResponse:
    return _approve(
        workflow_id=workflow_id,
        payload=payload,
        request=request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        approval_type=ApprovalType.CREATIVE_PLAN,
        decision=ApprovalDecision.APPROVE,
    )


@router.post("/{workflow_id}/creative-plan:reject", response_model=WorkflowResponse)
def reject_plan(
    workflow_id: str,
    payload: ApprovalRequest,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
) -> WorkflowResponse:
    return _approve(
        workflow_id=workflow_id,
        payload=payload,
        request=request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        approval_type=ApprovalType.CREATIVE_PLAN,
        decision=ApprovalDecision.REJECT,
    )


@router.post("/{workflow_id}/results:approve", response_model=WorkflowResponse)
def approve_results(
    workflow_id: str,
    payload: ApprovalRequest,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
) -> WorkflowResponse:
    return _approve(
        workflow_id=workflow_id,
        payload=payload,
        request=request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        approval_type=ApprovalType.RESULTS,
        decision=ApprovalDecision.APPROVE,
    )


@router.post("/{workflow_id}/results:regenerate", response_model=WorkflowResponse)
def regenerate_results(
    workflow_id: str,
    payload: ApprovalRequest,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
) -> WorkflowResponse:
    return _approve(
        workflow_id=workflow_id,
        payload=payload,
        request=request,
        workspace_id=workspace_id,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        approval_type=ApprovalType.RESULTS,
        decision=ApprovalDecision.REGENERATE,
    )
