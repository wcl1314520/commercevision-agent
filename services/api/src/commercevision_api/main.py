"""CommerceVision Control API entrypoint."""

from contextlib import asynccontextmanager

import uvicorn
from commercevision_contracts import HealthResponse, ServiceMetadata, Settings
from commercevision_contracts.config import load_settings
from commercevision_domain import StorageLocationClass, new_uuid7
from commercevision_observability import (
    Phase2Span,
    Phase2Telemetry,
    TelemetryDimensions,
    TelemetryIdentity,
    configure_logging,
    configure_telemetry,
    get_logger,
)
from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware

from .asset_routes import asset_router, upload_router
from .brand_profile_routes import router as brand_profile_router
from .catalog_routes import router as catalog_router
from .collection_rebuild_routes import router as collection_rebuild_router
from .container import ApiContainer, ApiTrustKeyRing
from .errors import install_error_handlers
from .operation_routes import router as operation_router
from .product_brief_routes import router as product_brief_router
from .readiness import probe_dependencies
from .retrieval_routes import router as retrieval_router
from .workflow_routes import router as workflow_router


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or load_settings("control-api")
    trust_key_ring = ApiTrustKeyRing.from_settings(runtime_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        configure_logging(runtime_settings.log_level)
        telemetry_runtime = configure_telemetry(
            service_name=runtime_settings.service_name,
            service_version=runtime_settings.version,
            environment=runtime_settings.environment,
        )
        api.state.telemetry = (
            telemetry_runtime.phase2() if telemetry_runtime is not None else Phase2Telemetry()
        )
        logger = get_logger("commercevision.api")
        container = ApiContainer.build(
            runtime_settings,
            trust_key_ring=trust_key_ring,
        )
        api.state.container = container
        logger.info(
            "service_started",
            service=runtime_settings.service_name,
            version=runtime_settings.version,
            environment=runtime_settings.environment,
        )
        try:
            yield
        finally:
            container.close()
            if telemetry_runtime is not None:
                telemetry_runtime.shutdown()
            logger.info("service_stopped", service=runtime_settings.service_name)

    api = FastAPI(
        title="CommerceVision Control API",
        summary="Control plane for durable ecommerce visual workflows",
        version=runtime_settings.version,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    api.state.settings = runtime_settings
    install_error_handlers(api)
    api.add_middleware(
        CORSMiddleware,
        allow_origins=runtime_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @api.middleware("http")
    async def correlation_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-Id") or new_uuid7()
        trace_id = request.headers.get("X-Trace-Id") or request_id
        request.state.request_id = request_id
        request.state.trace_id = trace_id
        telemetry: Phase2Telemetry | None = getattr(api.state, "telemetry", None)
        telemetry = telemetry or Phase2Telemetry()
        with telemetry.span(
            Phase2Span.HTTP_REQUEST,
            identity=TelemetryIdentity(
                trace_id=trace_id,
                workspace_id=request.headers.get("X-Workspace-Id"),
            ),
            dimensions=TelemetryDimensions(component=request.method.lower()),
        ):
            response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Trace-Id"] = trace_id
        return response

    @api.get("/health/live", response_model=HealthResponse, tags=["health"])
    async def liveness() -> HealthResponse:
        return HealthResponse(
            status="ok",
            service=runtime_settings.service_name,
            version=runtime_settings.version,
            checks={"process": "ok"},
        )

    @api.get("/health/ready", response_model=HealthResponse, tags=["health"])
    async def readiness(response: Response) -> HealthResponse:
        checks: dict[str, str] = {"configuration": "ok"}
        if runtime_settings.readiness_probe_external:
            checks.update(
                await probe_dependencies(
                    runtime_settings,
                    object_storage_probe=lambda: (
                        api.state.container.object_storage_readiness.assert_ready(
                            (StorageLocationClass.QUARANTINE,)
                        )
                    ),
                )
            )
        else:
            checks["external_dependencies"] = "skipped"

        ready = all(value != "failed" for value in checks.values())
        if not ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            status="ok" if ready else "degraded",
            service=runtime_settings.service_name,
            version=runtime_settings.version,
            checks=checks,
        )

    @api.get("/api/v1/meta", response_model=ServiceMetadata, tags=["system"])
    async def metadata() -> ServiceMetadata:
        return ServiceMetadata(
            service=runtime_settings.service_name,
            version=runtime_settings.version,
            environment=runtime_settings.environment,
            phase="phase-1",
        )

    api.include_router(upload_router)
    api.include_router(asset_router)
    api.include_router(brand_profile_router)
    api.include_router(catalog_router)
    api.include_router(collection_rebuild_router)
    api.include_router(operation_router)
    api.include_router(product_brief_router)
    api.include_router(retrieval_router)
    api.include_router(workflow_router)
    return api


app = create_app()


def run() -> None:
    uvicorn.run("commercevision_api.main:app", host="0.0.0.0", port=8000)
