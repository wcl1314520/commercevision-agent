"""Workspace-scoped operation inspection and administrator DLQ routes."""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime, timedelta
from typing import Annotated

from commercevision_application import DeadLetterDetail, canonicalize_dead_letter_id
from commercevision_contracts import (
    DeadLetterDetailResponseV1,
    DeadLetterListResponseV1,
    DeadLetterReplayRequestV1,
    DeadLetterReplayResponseV1,
    DeadLetterResponseV1,
    ErrorResponse,
    OperationListResponseV1,
    OperationResponseV1,
)
from commercevision_contracts.operations import OperationErrorResponseV1
from commercevision_domain.messaging import DeadLetterMessage, DeadLetterReplay
from commercevision_domain.operations import DurableOperation
from fastapi import APIRouter, Header, Query, Request, status

from .workspace_identity import WorkspaceHeader

router = APIRouter(tags=["operations"])
# Operation reads are identity-agnostic application queries, so workspace membership is
# enforced here. DLQ admin policy remains in its application service for non-HTTP callers.
OPERATOR_ERROR_RESPONSES = {
    400: {"model": ErrorResponse, "description": "Invalid operator argument"},
    401: {"model": ErrorResponse, "description": "Trusted principal required"},
    403: {"model": ErrorResponse, "description": "Administrator privileges required"},
    404: {"model": ErrorResponse, "description": "Operator resource not found"},
    409: {"model": ErrorResponse, "description": "Operator request conflict"},
    422: {"model": ErrorResponse, "description": "Operator request validation failed"},
}

PrincipalHeader = Annotated[
    str | None,
    Header(alias="X-Trusted-Principal", max_length=4096),
]
IdempotencyHeader = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=256)]
CursorQuery = Annotated[str | None, Query(max_length=1024)]


def _encode_cursor(created_at: datetime, resource_id: str) -> str:
    value = json.dumps(
        [created_at.isoformat(), resource_id],
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    if cursor is None:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True).decode())
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError
        created_at = datetime.fromisoformat(value[0])
        resource_id = value[1]
        if (
            created_at.tzinfo is None
            or created_at.utcoffset() != timedelta(0)
            or not isinstance(resource_id, str)
            or not resource_id
        ):
            raise ValueError
        return created_at, resource_id
    except (
        ValueError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        binascii.Error,
    ) as exc:
        raise ValueError("invalid operator cursor") from exc


def _operation_response(operation: DurableOperation) -> OperationResponseV1:
    error = operation.error
    return OperationResponseV1(
        id=operation.id,
        workspace_id=operation.workspace_id,
        kind=operation.kind,
        target_type=operation.target_type,
        target_id=operation.target_id,
        target_version=operation.target_version,
        input_hash=operation.input_hash,
        input_ref=operation.input_ref,
        output_ref=operation.output_ref,
        provider_request_id=operation.provider_request_id,
        state=operation.state,
        lease_owner=operation.lease_owner,
        lease_expires_at=operation.lease_expires_at,
        attempt_count=operation.attempt_count,
        max_attempts=operation.max_attempts,
        next_attempt_at=operation.next_attempt_at,
        execution_deadline_at=operation.execution_deadline_at,
        reconciliation_attempt_count=operation.reconciliation_attempt_count,
        max_reconciliation_attempts=operation.max_reconciliation_attempts,
        next_reconciliation_at=operation.next_reconciliation_at,
        reconciliation_started_at=operation.reconciliation_started_at,
        reconciliation_deadline_at=operation.reconciliation_deadline_at,
        reconciliation_required=operation.reconciliation_required,
        reconciliation_outcome=operation.reconciliation_outcome,
        dead_letter_id=operation.dead_letter_id,
        replay_source_dead_letter_id=operation.replay_source_dead_letter_id,
        replay_attempt=operation.replay_attempt,
        recovery_generation=operation.recovery_generation,
        recovery_consumed_generation=operation.recovery_consumed_generation,
        error=(
            OperationErrorResponseV1(
                code=error.code,
                category=error.category,
                message=error.message,
                retryable=error.retryable,
                provider_request_id=error.provider_request_id,
            )
            if error
            else None
        ),
        created_at=operation.created_at,
        updated_at=operation.updated_at,
        last_attempt_at=operation.last_attempt_at,
        started_at=operation.started_at,
        completed_at=operation.completed_at,
        version=operation.version,
    )


def _dead_letter_response(dead_letter: DeadLetterMessage) -> DeadLetterResponseV1:
    return DeadLetterResponseV1(
        id=dead_letter.id,
        consumer=dead_letter.consumer,
        message_id=dead_letter.message_id,
        event_type=dead_letter.event_type,
        payload=dead_letter.payload,
        reason=dead_letter.reason,
        error_class=dead_letter.error_class,
        error_message=dead_letter.error_message,
        attempt_count=dead_letter.attempt_count,
        original_created_at=dead_letter.original_created_at,
        created_at=dead_letter.created_at,
        source_dead_letter_id=dead_letter.source_dead_letter_id,
        replay_attempt=dead_letter.replay_attempt,
    )


def _replay_response(replay: DeadLetterReplay) -> DeadLetterReplayResponseV1:
    return DeadLetterReplayResponseV1(
        id=replay.id,
        source_dead_letter_id=replay.source_dead_letter_id,
        actor_id=replay.actor_id,
        reason=replay.reason,
        replayed_at=replay.replayed_at,
        replay_attempt=replay.replay_attempt,
        replay_event_id=replay.replay_event_id,
    )


def _dead_letter_detail_response(detail: DeadLetterDetail) -> DeadLetterDetailResponseV1:
    return DeadLetterDetailResponseV1(
        dead_letter=_dead_letter_response(detail.dead_letter),
        replays=[_replay_response(replay) for replay in detail.replays],
        replays_next_cursor=(
            _encode_cursor(*detail.replays_next_cursor)
            if detail.replays_next_cursor is not None
            else None
        ),
        child_dead_letters=[
            _dead_letter_response(dead_letter) for dead_letter in detail.child_dead_letters
        ],
        child_dead_letters_next_cursor=(
            _encode_cursor(*detail.child_dead_letters_next_cursor)
            if detail.child_dead_letters_next_cursor is not None
            else None
        ),
    )


@router.get(
    "/api/v1/operations",
    response_model=OperationListResponseV1,
    responses=OPERATOR_ERROR_RESPONSES,
)
def list_operations(
    request: Request,
    workspace_id: WorkspaceHeader,
    trusted_principal: PrincipalHeader = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: CursorQuery = None,
) -> OperationListResponseV1:
    principal = request.app.state.container.principal_resolver.resolve(trusted_principal)
    request.app.state.container.access_policy.require_workspace(
        workspace_id=workspace_id,
        principal=principal,
    )
    operations = request.app.state.container.operations.list(
        workspace_id=workspace_id,
        limit=limit + 1,
        cursor=_decode_cursor(cursor),
    )
    page = operations[:limit]
    return OperationListResponseV1(
        items=[_operation_response(operation) for operation in page],
        next_cursor=(
            _encode_cursor(page[-1].created_at, page[-1].id) if len(operations) > limit else None
        ),
    )


@router.get(
    "/api/v1/operations/{operation_id}",
    response_model=OperationResponseV1,
    responses=OPERATOR_ERROR_RESPONSES,
)
def get_operation(
    operation_id: str,
    request: Request,
    workspace_id: WorkspaceHeader,
    trusted_principal: PrincipalHeader = None,
) -> OperationResponseV1:
    principal = request.app.state.container.principal_resolver.resolve(trusted_principal)
    request.app.state.container.access_policy.require_workspace(
        workspace_id=workspace_id,
        principal=principal,
    )
    operation = request.app.state.container.operations.get(
        workspace_id=workspace_id,
        operation_id=operation_id,
    )
    return _operation_response(operation)


@router.get(
    "/api/v1/operator/dead-letters",
    response_model=DeadLetterListResponseV1,
    responses=OPERATOR_ERROR_RESPONSES,
)
def list_dead_letters(
    request: Request,
    workspace_id: WorkspaceHeader,
    trusted_principal: PrincipalHeader = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: CursorQuery = None,
) -> DeadLetterListResponseV1:
    principal = request.app.state.container.principal_resolver.resolve(trusted_principal)
    dead_letters = request.app.state.container.dead_letters.list(
        workspace_id=workspace_id,
        principal=principal,
        limit=limit + 1,
        cursor=_decode_cursor(cursor),
    )
    page = dead_letters[:limit]
    return DeadLetterListResponseV1(
        items=[_dead_letter_response(item) for item in page],
        next_cursor=(
            _encode_cursor(page[-1].created_at, page[-1].id) if len(dead_letters) > limit else None
        ),
    )


@router.get(
    "/api/v1/operator/dead-letters/{dead_letter_id}",
    response_model=DeadLetterDetailResponseV1,
    responses=OPERATOR_ERROR_RESPONSES,
)
def get_dead_letter(
    dead_letter_id: str,
    request: Request,
    workspace_id: WorkspaceHeader,
    trusted_principal: PrincipalHeader = None,
    replay_limit: Annotated[int, Query(ge=1, le=100)] = 20,
    replay_cursor: CursorQuery = None,
    child_limit: Annotated[int, Query(ge=1, le=100)] = 20,
    child_cursor: CursorQuery = None,
) -> DeadLetterDetailResponseV1:
    principal = request.app.state.container.principal_resolver.resolve(trusted_principal)
    dead_letter_id = canonicalize_dead_letter_id(dead_letter_id)
    detail = request.app.state.container.dead_letters.get(
        workspace_id=workspace_id,
        dead_letter_id=dead_letter_id,
        principal=principal,
        replay_limit=replay_limit,
        replay_cursor=_decode_cursor(replay_cursor),
        child_limit=child_limit,
        child_cursor=_decode_cursor(child_cursor),
    )
    return _dead_letter_detail_response(detail)


@router.get(
    "/api/v1/operator/legacy-dead-letters",
    response_model=DeadLetterListResponseV1,
    responses=OPERATOR_ERROR_RESPONSES,
)
def list_legacy_dead_letters(
    request: Request,
    trusted_principal: PrincipalHeader = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: CursorQuery = None,
) -> DeadLetterListResponseV1:
    principal = request.app.state.container.principal_resolver.resolve(trusted_principal)
    dead_letters = request.app.state.container.dead_letters.list_legacy(
        principal=principal,
        limit=limit + 1,
        cursor=_decode_cursor(cursor),
    )
    page = dead_letters[:limit]
    return DeadLetterListResponseV1(
        items=[_dead_letter_response(item) for item in page],
        next_cursor=(
            _encode_cursor(page[-1].created_at, page[-1].id) if len(dead_letters) > limit else None
        ),
    )


@router.get(
    "/api/v1/operator/legacy-dead-letters/{dead_letter_id}",
    response_model=DeadLetterDetailResponseV1,
    responses=OPERATOR_ERROR_RESPONSES,
)
def get_legacy_dead_letter(
    dead_letter_id: str,
    request: Request,
    trusted_principal: PrincipalHeader = None,
    replay_limit: Annotated[int, Query(ge=1, le=100)] = 20,
    replay_cursor: CursorQuery = None,
    child_limit: Annotated[int, Query(ge=1, le=100)] = 20,
    child_cursor: CursorQuery = None,
) -> DeadLetterDetailResponseV1:
    principal = request.app.state.container.principal_resolver.resolve(trusted_principal)
    dead_letter_id = canonicalize_dead_letter_id(dead_letter_id)
    detail = request.app.state.container.dead_letters.get_legacy(
        dead_letter_id=dead_letter_id,
        principal=principal,
        replay_limit=replay_limit,
        replay_cursor=_decode_cursor(replay_cursor),
        child_limit=child_limit,
        child_cursor=_decode_cursor(child_cursor),
    )
    return _dead_letter_detail_response(detail)


@router.post(
    "/api/v1/operator/dead-letters/{dead_letter_id}:replay",
    response_model=DeadLetterReplayResponseV1,
    status_code=status.HTTP_202_ACCEPTED,
    responses=OPERATOR_ERROR_RESPONSES,
)
def replay_dead_letter(
    dead_letter_id: str,
    payload: DeadLetterReplayRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    idempotency_key: IdempotencyHeader,
    trusted_principal: PrincipalHeader = None,
) -> DeadLetterReplayResponseV1:
    principal = request.app.state.container.principal_resolver.resolve(trusted_principal)
    dead_letter_id = canonicalize_dead_letter_id(dead_letter_id)
    replay = request.app.state.container.dead_letters.replay(
        workspace_id=workspace_id,
        dead_letter_id=dead_letter_id,
        principal=principal,
        reason=payload.reason,
        idempotency_key=idempotency_key,
        trace_id=request.state.trace_id,
    )
    return _replay_response(replay)
