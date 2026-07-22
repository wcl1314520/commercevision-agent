from pathlib import Path

ROOT = Path(__file__).parents[2]
ROUTE = ROOT / "apps" / "web" / "app" / "api" / "v1" / "[...path]" / "route.ts"
NEXT_CONFIG = ROOT / "apps" / "web" / "next.config.mjs"


def test_catalog_proxy_is_runtime_configured_for_the_container_api() -> None:
    route = ROUTE.read_text(encoding="utf-8")
    config = NEXT_CONFIG.read_text(encoding="utf-8")

    assert 'DEFAULT_API_PROXY_URL = "http://api:8000"' in route
    assert "process.env.CV_API_PROXY_URL" in route
    assert "FORWARDED_HEADERS" in route
    assert "request_id" in route
    assert "trace_id" in route
    assert "rewrites" not in config
    assert "127.0.0.1:18000" not in config
    assert "127.0.0.1:18000" not in route
