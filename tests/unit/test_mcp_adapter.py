"""Tests for the MCP adapter over the existing MATECOS tool layers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from mcp import Client

from src.tools.executor import ToolExecutor
from src.tools.manifests import RuntimeConfig, ToolManifest
from src.tools.mcp_adapter import MCPToolAdapter
from src.tools.registry import ToolExecutionContext, ToolExecutionResult, ToolRegistry


def make_manifest(tool_id: str = "demo.echo", *, source_repo: str | None = None) -> ToolManifest:
    return ToolManifest(
        tool_id=tool_id,
        name="Echo",
        version="1.0.0",
        description="Return the supplied text.",
        capabilities=["text.echo"],
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        risk_level="low",
        runtime=RuntimeConfig(type="builtin"),
        source_repo=source_repo,
    )


class FakeExecutor:
    def __init__(self, status: str = "SUCCEEDED") -> None:
        self.status = status
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(
        self,
        manifest: ToolManifest,
        payload: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        self.calls.append((manifest.tool_id, payload))
        return ToolExecutionResult(
            invocation_id="test-invocation",
            tool_id=manifest.tool_id,
            status=self.status,
            output={"echo": payload.get("text")} if self.status == "SUCCEEDED" else None,
            error="Sandbox is unavailable." if self.status == "FAILED" else None,
        )


@pytest.mark.asyncio
async def test_mcp_handshake_schema_listing_and_invocation() -> None:
    registry = ToolRegistry()
    manifest = make_manifest()
    registry.register(manifest)
    executor = FakeExecutor()
    adapter = MCPToolAdapter(registry, executor)  # type: ignore[arg-type]

    try:
        async with Client(adapter.server) as client:
            assert client.server_info is not None
            assert client.server_info.name == "MATECOS"

            listed = await client.list_tools()
            tool = next(item for item in listed.tools if item.name == manifest.tool_id)
            assert tool.description == manifest.description
            assert tool.input_schema == manifest.input_schema
            assert tool.output_schema == manifest.output_schema

            result = await client.call_tool(manifest.tool_id, {"text": "hello"})
            assert not result.is_error
            assert result.structured_content == {"echo": "hello"}
            assert executor.calls == [(manifest.tool_id, {"text": "hello"})]
    finally:
        adapter.close()


@pytest.mark.asyncio
async def test_legacy_mcp_initialize_handshake_is_supported() -> None:
    registry = ToolRegistry()
    registry.register(make_manifest())
    adapter = MCPToolAdapter(registry, FakeExecutor())  # type: ignore[arg-type]

    try:
        async with Client(adapter.server, mode="legacy") as client:
            assert client.server_info is not None
            assert client.server_info.name == "MATECOS"
            assert "demo.echo" in {tool.name for tool in (await client.list_tools()).tools}
    finally:
        adapter.close()


@pytest.mark.asyncio
async def test_invalid_arguments_and_unknown_tool_are_mcp_errors() -> None:
    registry = ToolRegistry()
    manifest = make_manifest()
    registry.register(manifest)
    executor = FakeExecutor()
    adapter = MCPToolAdapter(registry, executor)  # type: ignore[arg-type]

    try:
        async with Client(adapter.server) as client:
            invalid = await client.call_tool(manifest.tool_id, {"unexpected": True})
            unknown = await client.call_tool("missing.tool", {})

        assert invalid.is_error
        assert "Invalid arguments" in invalid.content[0].text
        assert unknown.is_error
        assert "Unknown or unavailable tool" in unknown.content[0].text
        assert executor.calls == []
    finally:
        adapter.close()


@pytest.mark.asyncio
async def test_execution_failure_is_returned_without_exposing_traceback() -> None:
    registry = ToolRegistry()
    manifest = make_manifest()
    registry.register(manifest)
    adapter = MCPToolAdapter(registry, FakeExecutor(status="FAILED"))  # type: ignore[arg-type]

    try:
        async with Client(adapter.server) as client:
            result = await client.call_tool(manifest.tool_id, {"text": "hello"})
        assert result.is_error
        assert "could not complete" in result.content[0].text
        assert "Sandbox is unavailable" in result.content[0].text
    finally:
        adapter.close()


@pytest.mark.asyncio
async def test_timeout_is_returned_as_a_clear_tool_error() -> None:
    registry = ToolRegistry()
    manifest = make_manifest()
    registry.register(manifest)
    adapter = MCPToolAdapter(registry, FakeExecutor(status="TIMED_OUT"))  # type: ignore[arg-type]

    try:
        async with Client(adapter.server) as client:
            result = await client.call_tool(manifest.tool_id, {"text": "hello"})
        assert result.is_error
        assert "timed out" in result.content[0].text.lower()
    finally:
        adapter.close()


@pytest.mark.asyncio
async def test_tools_registered_and_removed_at_runtime_are_reflected() -> None:
    registry = ToolRegistry()
    executor = FakeExecutor()
    adapter = MCPToolAdapter(registry, executor)  # type: ignore[arg-type]

    try:
        async with Client(adapter.server) as client:
            assert (await client.list_tools()).tools == []
            async with client.listen(tools_list_changed=True) as subscription:
                manifest = make_manifest("demo.added")
                registry.register(manifest)
                event = await asyncio.wait_for(anext(subscription), timeout=2)
                assert type(event).__name__ == "ToolsListChanged"
                assert [tool.name for tool in (await client.list_tools()).tools] == ["demo.added"]

                registry.unregister(manifest.tool_id)
                event = await asyncio.wait_for(anext(subscription), timeout=2)
                assert type(event).__name__ == "ToolsListChanged"
                assert (await client.list_tools()).tools == []
    finally:
        adapter.close()


@pytest.mark.asyncio
async def test_executor_blocks_untrusted_repository_tools_by_default() -> None:
    executor = ToolExecutor(
        settings=SimpleNamespace(allow_untrusted_tools=False),
        sandbox_runner=object(),  # type: ignore[arg-type]
    )

    result = await executor.execute(
        make_manifest(source_repo="https://github.com/example/untrusted"),
        {"text": "hello"},
        ToolExecutionContext(agent_id="test"),
    )

    assert result.status == "BLOCKED"
    assert "blocked until it is reviewed" in (result.error or "")
