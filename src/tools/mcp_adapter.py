"""Expose MATECOS registry tools through the official MCP Python SDK."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import structlog
from jsonschema import Draft202012Validator
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler, ToolsListChanged
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)

from src.tools.executor import ToolExecutor
from src.tools.registry import ToolExecutionContext, ToolRecord, ToolRegistry

logger = structlog.get_logger(__name__)


class MCPToolAdapter:
    """Translate live registry entries to MCP tools and dispatch calls via MATECOS."""

    def __init__(
        self,
        registry: ToolRegistry,
        executor: ToolExecutor,
        server_name: str = "MATECOS",
    ) -> None:
        self.registry = registry
        self.executor = executor
        self._subscriptions = InMemorySubscriptionBus()
        self._listen_handler = ListenHandler(self._subscriptions)
        self.server = Server(
            server_name,
            version="0.1.0",
            instructions=(
                "Use the registered MATECOS tools. Tool arguments follow each tool's JSON schema."
            ),
            on_list_tools=self.list_tools,
            on_call_tool=self.call_tool,
            on_subscriptions_listen=self._listen_handler,
        )
        self._unsubscribe = registry.subscribe(self._registry_changed)
        logger.info("mcp.adapter.ready", server_name=server_name)

    def close(self) -> None:
        """Detach the registry listener when this adapter is no longer needed."""
        self._unsubscribe()

    def _registry_changed(self, tool_id: str, record: ToolRecord | None) -> None:
        logger.info(
            "mcp.registry_changed",
            tool_id=tool_id,
            registered=record is not None,
        )
        # Registry mutations can happen at startup (without an event loop) or
        # during an API request. Initial tools are visible on the first list;
        # runtime mutations publish the SDK's standard change event.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._subscriptions.publish(ToolsListChanged()))

    @staticmethod
    def _object_schema(schema: dict[str, Any]) -> dict[str, Any]:
        """Return an MCP-compatible object schema while preserving JSON Schema."""
        if not isinstance(schema, dict) or not schema:
            return {"type": "object", "properties": {}}
        if schema.get("type") == "object":
            return schema
        # MATECOS handlers always accept a dictionary payload. Keep an unusual
        # root schema representable without advertising a non-object tool input.
        return {
            "type": "object",
            "properties": {"input": schema},
            "required": ["input"],
        }

    async def list_tools(
        self,
        _context: ServerRequestContext[Any],
        _params: PaginatedRequestParams | None,
    ) -> ListToolsResult:
        """Build the MCP tool catalog from the current registry on every request."""
        tools: list[Tool] = []
        for record in self.registry.list_all():
            if not record.enabled:
                continue
            manifest = record.manifest
            tools.append(
                Tool(
                    name=manifest.tool_id,
                    title=manifest.name,
                    description=manifest.description,
                    input_schema=self._object_schema(manifest.input_schema),
                    output_schema=self._object_schema(manifest.output_schema),
                )
            )
        logger.info("mcp.tools_listed", count=len(tools))
        return ListToolsResult(tools=tools)

    async def call_tool(
        self,
        _context: ServerRequestContext[Any],
        params: CallToolRequestParams,
    ) -> CallToolResult:
        """Validate arguments, then dispatch through the existing MATECOS executor."""
        record = self.registry.get(params.name)
        if record is None or not record.enabled:
            logger.warning("mcp.unknown_tool", tool_id=params.name)
            return self._error(f"Unknown or unavailable tool: {params.name}")

        arguments = params.arguments or {}
        if not isinstance(arguments, dict):
            return self._error("Tool arguments must be a JSON object.")

        schema = self._object_schema(record.manifest.input_schema)
        try:
            errors = sorted(
                Draft202012Validator(schema).iter_errors(arguments),
                key=lambda error: (list(map(str, error.absolute_path)), error.message),
            )
        except Exception:
            logger.exception("mcp.invalid_tool_schema", tool_id=params.name)
            return self._error("This tool has an invalid input schema and cannot be called.")
        if errors:
            error = errors[0]
            location = ".".join(str(part) for part in error.absolute_path) or "arguments"
            return self._error(f"Invalid arguments at {location}: {error.message}")

        logger.info("mcp.tool_call_started", tool_id=params.name)
        context = ToolExecutionContext(
            execution_id=f"mcp-{uuid.uuid4()}",
            agent_id="mcp-client",
            user_id="mcp-client",
        )
        try:
            result = await self.executor.execute(record.manifest, arguments, context)
        except Exception as exc:
            logger.exception("mcp.tool_call_failed", tool_id=params.name)
            return self._error(f"Tool execution failed: {type(exc).__name__}.")

        if result.status != "SUCCEEDED":
            message = result.error or f"Tool execution ended with status {result.status}."
            if result.status == "TIMED_OUT":
                message = f"The tool timed out. {message}"
            elif result.status == "BLOCKED":
                message = f"The tool was blocked by MATECOS security rules. {message}"
            else:
                message = f"The tool could not complete. {message}"
            logger.warning(
                "mcp.tool_call_failed",
                tool_id=params.name,
                status=result.status,
                error=result.error,
            )
            return self._error(message)

        output = result.output or {}
        logger.info("mcp.tool_call_complete", tool_id=params.name, duration_ms=result.duration_ms)
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(output, ensure_ascii=False, indent=2),
                )
            ],
            structuredContent=output,
            isError=False,
        )

    @staticmethod
    def _error(message: str) -> CallToolResult:
        return CallToolResult(
            content=[TextContent(type="text", text=message)],
            isError=True,
        )


def get_mcp_adapter() -> MCPToolAdapter:
    """Create an adapter over the app's shared registry/executor."""
    # Resolve lazily so the adapter remains independent from GitHub import
    # details while the application decides which shared registry to expose.
    from src.api.routes.github_tools import get_executor, get_registry
    from src.config import get_settings

    return MCPToolAdapter(
        get_registry(),
        get_executor(),
        server_name=get_settings().mcp_server_name,
    )
