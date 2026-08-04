"""Authenticated Product Catalog and Asset MCP server."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

import anyio
import uvicorn
from commercevision_contracts import (
    AssetsGetTemporaryReferenceInputV1,
    AssetsGetTemporaryReferenceOutputV1,
    AssetsSearchInputV1,
    AssetsSearchOutputV1,
    BrandGetProfileInputV1,
    BrandGetProfileOutputV1,
    CatalogGetProductBriefInputV1,
    CatalogGetProductBriefOutputV1,
    CatalogGetProductInputV1,
    CatalogGetProductOutputV1,
    Settings,
)
from commercevision_contracts.config import load_settings
from commercevision_domain import (
    AuthenticationError,
    AuthorizationError,
    DomainError,
    NotFoundError,
    StorageUnavailableError,
)
from commercevision_observability import (
    Phase2Span,
    Phase2Telemetry,
    TelemetryDimensions,
    TelemetryIdentity,
    configure_logging,
    configure_telemetry,
    get_logger,
)
from commercevision_tool_runtime import ToolExecutionError, ToolPolicyError, ToolRegistryError
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.session import ServerSession
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

from .container import McpContainer
from .identity import SignedMcpIdentityResolver, identity_from_request
from .tools import CommerceMcpGateway

logger = get_logger("commercevision.mcp")


def _public_error(exc: Exception) -> ToolError:
    if isinstance(exc, AuthenticationError):
        code, retryable = "AUTHENTICATION_REQUIRED", False
    elif isinstance(exc, ToolPolicyError):
        code, retryable = "TOOL_POLICY_DENIED", False
    elif isinstance(exc, ToolRegistryError):
        code, retryable = "TOOL_NOT_AVAILABLE", False
    elif isinstance(exc, ToolExecutionError):
        code, retryable = "TOOL_EXECUTION_REJECTED", exc.retryable
    elif isinstance(exc, AuthorizationError):
        code, retryable = "ACCESS_DENIED", False
    elif isinstance(exc, NotFoundError):
        code, retryable = "NOT_FOUND", False
    elif isinstance(exc, StorageUnavailableError):
        code, retryable = "DEPENDENCY_UNAVAILABLE", True
    elif isinstance(exc, DomainError):
        code, retryable = "DOMAIN_OPERATION_FAILED", False
    else:
        logger.error("unhandled_mcp_tool_error", exception_type=type(exc).__name__)
        code, retryable = "INTERNAL_ERROR", False
    return ToolError(f'{{"code":"{code}","retryable":{str(retryable).lower()}}}')


def create_server(
    settings: Settings,
    *,
    container_factory: Callable[[Settings], McpContainer] = McpContainer.build,
) -> tuple[FastMCP, dict[str, Any]]:
    holder: dict[str, Any] = {
        "container": None,
        "gateway": None,
        "http_app": None,
        "telemetry": Phase2Telemetry(),
    }
    keys: dict[str, str] = {}
    if settings.trusted_principal_current_key_id and settings.trusted_principal_current_hmac_secret:
        keys[settings.trusted_principal_current_key_id] = (
            settings.trusted_principal_current_hmac_secret.get_secret_value()
        )
    if (
        settings.trusted_principal_previous_key_id
        and settings.trusted_principal_previous_hmac_secret
    ):
        keys[settings.trusted_principal_previous_key_id] = (
            settings.trusted_principal_previous_hmac_secret.get_secret_value()
        )
    identity_resolver = SignedMcpIdentityResolver(
        keys=keys,
        max_age_seconds=settings.trusted_principal_max_age_seconds,
        future_skew_seconds=settings.trusted_principal_future_skew_seconds,
    )

    mcp = FastMCP(
        "commercevision_mcp",
        host=settings.mcp_host,
        port=settings.mcp_port,
        json_response=True,
        stateless_http=True,
    )

    def invoke(
        name: str,
        arguments: dict[str, object],
        ctx: Context[ServerSession, McpContainer],
    ) -> dict[str, object]:
        gateway = holder.get("gateway")
        if not isinstance(gateway, CommerceMcpGateway):
            raise ToolError('{"code":"SERVICE_UNAVAILABLE","retryable":true}')
        try:
            identity = identity_from_request(ctx.request_context.request, identity_resolver)
            telemetry: Phase2Telemetry = holder["telemetry"]
            with telemetry.span(
                Phase2Span.MCP_TOOL,
                identity=TelemetryIdentity(workspace_id=identity.workspace_id),
                dimensions=TelemetryDimensions(component=name.replace(".", "_")),
            ):
                return gateway.execute(name, arguments, identity=identity)
        except Exception as exc:
            raise _public_error(exc) from exc

    annotations = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )

    @mcp.tool(name="catalog.get_product.v1", annotations=annotations)
    def get_product(
        product_id: str, ctx: Context[ServerSession, McpContainer]
    ) -> CatalogGetProductOutputV1:
        """Read one workspace-scoped Product and its SKU snapshot."""
        return CatalogGetProductOutputV1.model_validate(
            invoke("catalog.get_product.v1", {"product_id": product_id}, ctx)
        )

    @mcp.tool(name="catalog.get_product_brief.v1", annotations=annotations)
    def get_product_brief(
        product_brief_id: str,
        ctx: Context[ServerSession, McpContainer],
        product_brief_version_id: str | None = None,
    ) -> CatalogGetProductBriefOutputV1:
        """Read the current confirmed or exact confirmed ProductBrief with evidence."""
        return CatalogGetProductBriefOutputV1.model_validate(
            invoke(
                "catalog.get_product_brief.v1",
                {
                    "product_brief_id": product_brief_id,
                    "product_brief_version_id": product_brief_version_id,
                },
                ctx,
            )
        )

    @mcp.tool(name="brand.get_profile.v1", annotations=annotations)
    def get_brand_profile(
        profile_id: str,
        ctx: Context[ServerSession, McpContainer],
        version_number: int | None = None,
    ) -> BrandGetProfileOutputV1:
        """Read an immutable published Brand Profile and current member usability."""
        return BrandGetProfileOutputV1.model_validate(
            invoke(
                "brand.get_profile.v1",
                {"profile_id": profile_id, "version_number": version_number},
                ctx,
            )
        )

    @mcp.tool(name="assets.search.v1", annotations=annotations)
    def search_assets(
        vector_kinds: list[str],
        ctx: Context[ServerSession, McpContainer],
        product_id: str | None = None,
        product_brief_id: str | None = None,
        category: str | None = None,
        brand: str | None = None,
        roles: list[str] | None = None,
        query_text: str | None = None,
        query_image_asset_version_id: str | None = None,
        explicit_reference_asset_version_ids: list[str] | None = None,
        brand_profile_id: str | None = None,
        brand_profile_version: int | None = None,
        top_k: int = 10,
    ) -> AssetsSearchOutputV1:
        """Run rights-first hybrid asset retrieval with complete citations."""
        return AssetsSearchOutputV1.model_validate(
            invoke(
                "assets.search.v1",
                {
                    "product_id": product_id,
                    "product_brief_id": product_brief_id,
                    "category": category,
                    "brand": brand,
                    "roles": roles or [],
                    "vector_kinds": vector_kinds,
                    "query_text": query_text,
                    "query_image_asset_version_id": query_image_asset_version_id,
                    "explicit_reference_asset_version_ids": (
                        explicit_reference_asset_version_ids or []
                    ),
                    "brand_profile_id": brand_profile_id,
                    "brand_profile_version": brand_profile_version,
                    "top_k": top_k,
                },
                ctx,
            )
        )

    @mcp.tool(name="assets.get_temporary_reference.v1", annotations=annotations)
    def get_temporary_reference(
        retrieval_run_id: str,
        rank: int,
        preview_reference_token: str,
        ctx: Context[ServerSession, McpContainer],
    ) -> AssetsGetTemporaryReferenceOutputV1:
        """Exchange a Retrieval Citation token for a short-lived controlled reference."""
        return AssetsGetTemporaryReferenceOutputV1.model_validate(
            invoke(
                "assets.get_temporary_reference.v1",
                {
                    "retrieval_run_id": retrieval_run_id,
                    "rank": rank,
                    "preview_reference_token": preview_reference_token,
                },
                ctx,
            )
        )

    # FastMCP v1 builds permissive generated argument models for flat function
    # signatures. Pin both discovery and runtime validation to our closed public
    # contracts until the v2 migration provides a public schema override hook.
    input_contracts = {
        "catalog.get_product.v1": CatalogGetProductInputV1,
        "catalog.get_product_brief.v1": CatalogGetProductBriefInputV1,
        "brand.get_profile.v1": BrandGetProfileInputV1,
        "assets.search.v1": AssetsSearchInputV1,
        "assets.get_temporary_reference.v1": AssetsGetTemporaryReferenceInputV1,
    }
    for tool_name, input_contract in input_contracts.items():
        registered = mcp._tool_manager._tools[tool_name]
        registered.parameters = input_contract.model_json_schema()
        registered.fn_metadata.arg_model.model_config["extra"] = "forbid"
        registered.fn_metadata.arg_model.model_rebuild(force=True)

    @mcp.custom_route("/health/live", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        return JSONResponse(
            {"status": "ok", "service": settings.service_name, "version": settings.version}
        )

    @mcp.custom_route("/health/ready", methods=["GET"])
    async def readiness(_: Request) -> JSONResponse:
        container = holder.get("container")
        if container is None:
            return JSONResponse(
                {"status": "degraded", "checks": {"runtime": "failed"}}, status_code=503
            )
        checks: dict[str, str] = {}
        for name, check in (
            ("mysql", container.assert_database_ready),
            ("object_storage", container.assert_object_storage_ready),
        ):
            try:
                await anyio.to_thread.run_sync(check)
            except Exception as exc:
                logger.warning(
                    "mcp_readiness_dependency_failed",
                    dependency=name,
                    exception_type=type(exc).__name__,
                )
                checks[name] = "failed"
            else:
                checks[name] = "ok"
        healthy = all(value == "ok" for value in checks.values())
        return JSONResponse(
            {
                "status": "ok" if healthy else "degraded",
                "service": settings.service_name,
                "version": settings.version,
                "checks": checks,
            },
            status_code=200 if healthy else 503,
        )

    http_app = mcp.streamable_http_app()
    mcp_lifespan = http_app.router.lifespan_context

    @asynccontextmanager
    async def application_lifespan(starlette_app):
        telemetry_runtime = configure_telemetry(
            service_name=settings.service_name,
            service_version=settings.version,
            environment=settings.environment,
        )
        holder["telemetry"] = (
            telemetry_runtime.phase2() if telemetry_runtime is not None else Phase2Telemetry()
        )
        container = container_factory(settings)
        holder["container"] = container
        try:
            holder["gateway"] = CommerceMcpGateway(
                container,
                policy_version=settings.mcp_tool_policy_version,
                maximum_argument_bytes=settings.mcp_max_argument_bytes,
                maximum_output_bytes=settings.mcp_max_output_bytes,
            )
            async with mcp_lifespan(starlette_app):
                yield
        finally:
            holder["gateway"] = None
            holder["container"] = None
            container.close()
            if telemetry_runtime is not None:
                telemetry_runtime.shutdown()

    http_app.router.lifespan_context = application_lifespan
    holder["http_app"] = http_app
    return mcp, holder


settings = load_settings("mcp-server")
configure_logging(settings.log_level)
server, _holder = create_server(settings)
app = _holder["http_app"]


def main() -> None:
    if settings.mcp_transport != "streamable-http":
        raise RuntimeError("signed MCP identity requires the streamable-http transport")
    uvicorn.run(app, host=settings.mcp_host, port=settings.mcp_port)
