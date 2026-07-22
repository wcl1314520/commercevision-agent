"""Verify the complete local Phase 0 stack from the host."""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class HttpCheck:
    name: str
    url: str


@dataclass(frozen=True)
class TcpCheck:
    name: str
    host: str
    port: int


def env_port(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


HTTP_CHECKS = (
    HttpCheck("web", f"http://127.0.0.1:{env_port('CV_WEB_HOST_PORT', 13000)}/"),
    HttpCheck(
        "api-live",
        f"http://127.0.0.1:{env_port('CV_API_HOST_PORT', 18000)}/health/live",
    ),
    HttpCheck(
        "api-ready",
        f"http://127.0.0.1:{env_port('CV_API_HOST_PORT', 18000)}/health/ready",
    ),
    HttpCheck(
        "scheduler",
        f"http://127.0.0.1:{env_port('CV_SCHEDULER_HOST_PORT', 18002)}/health/live",
    ),
    HttpCheck(
        "mcp-server",
        f"http://127.0.0.1:{env_port('CV_MCP_HOST_PORT', 18001)}/health/live",
    ),
    HttpCheck(
        "minio",
        (f"http://127.0.0.1:{env_port('CV_MINIO_API_HOST_PORT', 19000)}/minio/health/ready"),
    ),
    HttpCheck(
        "milvus",
        f"http://127.0.0.1:{env_port('CV_MILVUS_HEALTH_HOST_PORT', 19091)}/healthz",
    ),
    HttpCheck(
        "otel-collector",
        f"http://127.0.0.1:{env_port('CV_OTEL_HEALTH_HOST_PORT', 14319)}/",
    ),
)

TCP_CHECKS = (
    TcpCheck("mysql", "127.0.0.1", env_port("CV_MYSQL_HOST_PORT", 13316)),
    TcpCheck("redis", "127.0.0.1", env_port("CV_REDIS_HOST_PORT", 16379)),
    TcpCheck("rabbitmq", "127.0.0.1", env_port("CV_RABBITMQ_HOST_PORT", 15673)),
)


def check_http(check: HttpCheck) -> None:
    with urllib.request.urlopen(check.url, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(f"unexpected HTTP {response.status}")
        if check.name == "api-ready":
            payload = json.load(response)
            if payload.get("status") != "ok":
                raise RuntimeError(f"readiness is {payload!r}")
            failed = {
                name: value for name, value in payload.get("checks", {}).items() if value != "ok"
            }
            if failed:
                raise RuntimeError(f"dependency checks are not ready: {failed!r}")


def check_tcp(check: TcpCheck) -> None:
    with socket.create_connection((check.host, check.port), timeout=5):
        return


def verify(attempts: int = 30, delay_seconds: float = 2) -> None:
    pending: dict[str, str] = {}
    for attempt in range(1, attempts + 1):
        pending.clear()
        for check in HTTP_CHECKS:
            try:
                check_http(check)
            except (OSError, RuntimeError, urllib.error.URLError) as exc:
                pending[check.name] = str(exc)
        for check in TCP_CHECKS:
            try:
                check_tcp(check)
            except OSError as exc:
                pending[check.name] = str(exc)

        if not pending:
            print("Phase 0 verification passed.")
            for check in (*HTTP_CHECKS, *TCP_CHECKS):
                print(f"  OK  {check.name}")
            return

        if attempt < attempts:
            time.sleep(delay_seconds)

    print("Phase 0 verification failed:", file=sys.stderr)
    for name, error in pending.items():
        print(f"  FAIL  {name}: {error}", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    verify()
