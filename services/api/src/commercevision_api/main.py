"""CommerceVision Control API entrypoint."""

from contextlib import asynccontextmanager

import uvicorn
from commercevision_contracts import HealthResponse, ServiceMetadata, Settings
from commercevision_contracts.config import load_settings
from commercevision_domain import new_uuid7
from commercevision_observability import configure_logging, get_logger
from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware

from .catalog_routes import router as catalog_router
from .container import ApiContainer
from .errors import install_error_handlers
from .operation_routes import router as operation_router
from .readiness import probe_dependencies
from .workflow_routes import router as workflow_router


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or load_settings("control-api")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        configure_logging(runtime_settings.log_level)
        logger = get_logger("commercevision.api")
        container = ApiContainer.build(runtime_settings)
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
            checks.update(await probe_dependencies(runtime_settings))
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

    api.include_router(catalog_router)
    api.include_router(operation_router)
    api.include_router(workflow_router)
    return api


app = create_app()


def run() -> None:
    uvicorn.run("commercevision_api.main:app", host="0.0.0.0", port=8000)
