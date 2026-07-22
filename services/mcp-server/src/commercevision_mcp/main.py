"""MCP server entrypoint.

Business tools are intentionally registered only in their roadmap phases.
"""

from commercevision_contracts.config import load_settings
from commercevision_observability import configure_logging
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

settings = load_settings("mcp-server")
configure_logging(settings.log_level)

server = FastMCP(
    "commercevision-tools",
    host=settings.mcp_host,
    port=settings.mcp_port,
)


@server.custom_route("/health/live", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "service": settings.service_name,
            "version": settings.version,
        }
    )


def main() -> None:
    server.run(transport=settings.mcp_transport)
