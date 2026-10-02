"""Run the MATECOS MCP server over stdio or standalone Streamable HTTP."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import structlog

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from src.config import get_settings  # noqa: E402
from src.tools.mcp_adapter import get_mcp_adapter  # noqa: E402


async def _preload_repositories() -> None:
    """Import optional repositories into this process before MCP clients connect."""
    settings = get_settings()
    repositories = [
        repository.strip()
        for repository in settings.mcp_import_repositories.split(",")
        if repository.strip()
    ]
    if not repositories:
        return

    from src.api.routes.github_tools import ImportRequest, import_repo

    for repository in repositories:
        try:
            result = await import_repo(ImportRequest(url=repository))
            print(
                f"Loaded {result['repo']} ({result['tools_found']} tools) for MCP.",
                file=sys.stderr,
                flush=True,
            )
        except Exception as exc:
            print(f"Could not load MCP repository {repository}: {exc}", file=sys.stderr, flush=True)


async def _run_stdio() -> None:
    from mcp.server.lowlevel.server import NotificationOptions
    from mcp.server.stdio import stdio_server

    adapter = get_mcp_adapter()
    await _preload_repositories()
    try:
        async with stdio_server() as (read_stream, write_stream):
            await adapter.server.run(
                read_stream,
                write_stream,
                adapter.server.create_initialization_options(
                    notification_options=NotificationOptions(tools_changed=True),
                ),
            )
    finally:
        adapter.close()


def _run_http() -> None:
    import uvicorn

    settings = get_settings()
    adapter = get_mcp_adapter()
    asyncio.run(_preload_repositories())
    app = adapter.server.streamable_http_app(
        streamable_http_path=settings.mcp_path,
        host=settings.mcp_host,
        stateless_http=False,
    )
    logger = structlog.get_logger(__name__)
    logger.info(
        "mcp.http_transport.started",
        path=settings.mcp_path,
        host=settings.mcp_host,
        port=settings.mcp_port,
    )
    uvicorn.run(
        app,
        host=settings.mcp_host,
        port=settings.mcp_port,
        log_level=settings.log_level.lower(),
    )
    adapter.close()


def main() -> None:
    settings = get_settings()
    if not settings.mcp_enabled:
        raise SystemExit("MATECOS MCP is disabled (MCP_ENABLED=false).")
    print(
        f"Starting {settings.mcp_server_name} MCP server using {settings.mcp_transport}.",
        file=sys.stderr,
        flush=True,
    )
    if settings.mcp_transport == "stdio":
        # stdio is the JSON-RPC wire: application logs must never use stdout.
        structlog.configure(
            processors=[structlog.dev.ConsoleRenderer()],
            logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
            cache_logger_on_first_use=True,
        )
        asyncio.run(_run_stdio())
    else:
        _run_http()


if __name__ == "__main__":
    main()
