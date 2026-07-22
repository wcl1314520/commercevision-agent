"""External dependency readiness probes for the control plane."""

import asyncio
from collections.abc import Awaitable, Callable

import aio_pika
import httpx
import redis.asyncio as redis
from commercevision_contracts import Settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

Probe = Callable[[], Awaitable[None]]


async def _probe_mysql(settings: Settings) -> None:
    engine = create_async_engine(settings.mysql_dsn, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


async def _probe_redis(settings: Settings) -> None:
    client = redis.from_url(settings.redis_url)
    try:
        await client.ping()
    finally:
        await client.aclose()


async def _probe_rabbitmq(settings: Settings) -> None:
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    await connection.close()


async def _probe_http(url: str) -> None:
    async with httpx.AsyncClient(timeout=3) as client:
        response = await client.get(url)
        response.raise_for_status()


async def _run_probe(probe: Probe) -> str:
    try:
        async with asyncio.timeout(3):
            await probe()
    except Exception:
        return "failed"
    return "ok"


async def probe_dependencies(settings: Settings) -> dict[str, str]:
    """Probe dependencies required for accepting new workflows."""

    names = ("mysql", "redis", "rabbitmq", "object_store", "milvus")
    probes: tuple[Probe, ...] = (
        lambda: _probe_mysql(settings),
        lambda: _probe_redis(settings),
        lambda: _probe_rabbitmq(settings),
        lambda: _probe_http(f"{settings.object_store_endpoint}/minio/health/ready"),
        lambda: _probe_http(settings.milvus_health_uri),
    )
    results = await asyncio.gather(*(_run_probe(probe) for probe in probes))
    return dict(zip(names, results, strict=True))
