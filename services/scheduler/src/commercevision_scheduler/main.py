"""Scheduler service entrypoint."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from commercevision_contracts import Settings
from commercevision_contracts.config import load_settings
from commercevision_observability import configure_logging, get_logger
from fastapi import FastAPI, Response, status
from uvicorn import run

from .runtime import SchedulerRuntime


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or load_settings("scheduler")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(runtime_settings.log_level)
        logger = get_logger("commercevision.scheduler")
        runtime = SchedulerRuntime(runtime_settings)
        app.state.runtime = runtime
        logger.info("service_started", service=runtime_settings.service_name, phase="phase-1")
        scheduler_task = asyncio.create_task(runtime.run())
        try:
            yield
        finally:
            scheduler_task.cancel()
            await asyncio.gather(scheduler_task, return_exceptions=True)
            runtime.close()
            logger.info("service_stopped", service=runtime_settings.service_name)

    scheduler = FastAPI(
        title="CommerceVision Scheduler",
        version=runtime_settings.version,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @scheduler.get("/health/live")
    async def liveness() -> dict[str, str]:
        return {
            "status": "ok",
            "service": runtime_settings.service_name,
            "version": runtime_settings.version,
        }

    @scheduler.get("/health/ready")
    async def readiness(response: Response) -> dict[str, object]:
        runtime: SchedulerRuntime = scheduler.state.runtime
        ready = runtime.state.last_error is None
        if not ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        payload = {
            "status": "ok" if ready else "degraded",
            "last_error": runtime.state.last_error,
            "published_total": runtime.state.published_total,
            "publish_failed_total": runtime.state.publish_failed_total,
            "recovered_steps_total": runtime.state.recovered_steps_total,
            "recovered_workflows_total": runtime.state.recovered_workflows_total,
            "recovered_operations_total": runtime.state.recovered_operations_total,
            "scanners": {
                name: {
                    "last_started_at": scanner.last_started_at,
                    "last_success_at": scanner.last_success_at,
                    "last_error": scanner.last_error,
                    "last_duration_ms": scanner.last_duration_ms,
                    "last_count": scanner.last_count,
                    "total_count": scanner.total_count,
                    "in_progress": scanner.in_progress,
                    "timed_out": scanner.timed_out,
                    "timeout_count": scanner.timeout_count,
                }
                for name, scanner in (runtime.state.scanners or {}).items()
            },
        }
        return payload

    return scheduler


app = create_app()


def main() -> None:
    settings = load_settings("scheduler")
    run(
        "commercevision_scheduler.main:app",
        host=settings.scheduler_host,
        port=settings.scheduler_port,
    )
