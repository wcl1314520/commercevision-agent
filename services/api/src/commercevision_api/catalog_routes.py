"""Versioned Product Catalog HTTP routes."""

from typing import Annotated

from commercevision_contracts import (
    CatalogDeleteRequestV1,
    ErrorResponse,
    ProductCreateRequestV1,
    ProductListResponseV1,
    ProductResponseV1,
    ProductUpdateRequestV1,
    SKUCreateRequestV1,
    SKUResponseV1,
    SKUUpdateRequestV1,
)
from fastapi import APIRouter, Header, Query, Request, status
from fastapi.responses import Response

from .workspace_identity import WorkspaceHeader

router = APIRouter(prefix="/api/v1/products", tags=["products"])
CATALOG_ERROR_RESPONSES = {
    400: {"model": ErrorResponse, "description": "Invalid catalog argument"},
    404: {"model": ErrorResponse, "description": "Catalog resource not found"},
    409: {"model": ErrorResponse, "description": "Catalog conflict"},
    422: {"model": ErrorResponse, "description": "Catalog validation failed"},
}

ActorHeader = Annotated[str, Header(alias="X-Actor-Id", min_length=1, max_length=128)]
IdempotencyHeader = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=256)]


@router.post(
    "",
    response_model=ProductResponseV1,
    status_code=status.HTTP_201_CREATED,
    responses=CATALOG_ERROR_RESPONSES,
)
def create_product(
    payload: ProductCreateRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
) -> ProductResponseV1:
    return request.app.state.container.catalog.create_product(
        request=payload,
        workspace_id=workspace_id,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        trace_id=request.state.trace_id,
    )


@router.get("", response_model=ProductListResponseV1, responses=CATALOG_ERROR_RESPONSES)
def list_products(
    request: Request,
    workspace_id: WorkspaceHeader,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: str | None = None,
) -> ProductListResponseV1:
    return request.app.state.container.catalog.list_products(
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
    )


@router.get(
    "/{product_id}",
    response_model=ProductResponseV1,
    responses=CATALOG_ERROR_RESPONSES,
)
def get_product(
    product_id: str,
    request: Request,
    workspace_id: WorkspaceHeader,
) -> ProductResponseV1:
    return request.app.state.container.catalog.get_product(
        workspace_id=workspace_id,
        product_id=product_id,
    )


@router.put(
    "/{product_id}",
    response_model=ProductResponseV1,
    responses=CATALOG_ERROR_RESPONSES,
)
def update_product(
    product_id: str,
    payload: ProductUpdateRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
) -> ProductResponseV1:
    return request.app.state.container.catalog.update_product(
        product_id=product_id,
        request=payload,
        workspace_id=workspace_id,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        trace_id=request.state.trace_id,
    )


@router.post(
    "/{product_id}/skus",
    response_model=SKUResponseV1,
    status_code=status.HTTP_201_CREATED,
    responses=CATALOG_ERROR_RESPONSES,
)
def create_sku(
    product_id: str,
    payload: SKUCreateRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
) -> SKUResponseV1:
    return request.app.state.container.catalog.create_sku(
        product_id=product_id,
        request=payload,
        workspace_id=workspace_id,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        trace_id=request.state.trace_id,
    )


@router.put(
    "/{product_id}/skus/{sku_id}",
    response_model=SKUResponseV1,
    responses=CATALOG_ERROR_RESPONSES,
)
def update_sku(
    product_id: str,
    sku_id: str,
    payload: SKUUpdateRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
) -> SKUResponseV1:
    return request.app.state.container.catalog.update_sku(
        product_id=product_id,
        sku_id=sku_id,
        request=payload,
        workspace_id=workspace_id,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        trace_id=request.state.trace_id,
    )


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=CATALOG_ERROR_RESPONSES,
)
def delete_product(
    product_id: str,
    payload: CatalogDeleteRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
) -> Response:
    request.app.state.container.catalog.delete_product(
        product_id=product_id,
        request=payload,
        workspace_id=workspace_id,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        trace_id=request.state.trace_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/{product_id}/skus/{sku_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=CATALOG_ERROR_RESPONSES,
)
def delete_sku(
    product_id: str,
    sku_id: str,
    payload: CatalogDeleteRequestV1,
    request: Request,
    workspace_id: WorkspaceHeader,
    actor_id: ActorHeader,
    idempotency_key: IdempotencyHeader,
) -> Response:
    request.app.state.container.catalog.delete_sku(
        product_id=product_id,
        sku_id=sku_id,
        request=payload,
        workspace_id=workspace_id,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        trace_id=request.state.trace_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
