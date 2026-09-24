"""
test_registry.py
================
Tests for the tool registry.
"""

from __future__ import annotations

import pytest

from src.tools.manifests import (
    ResourceLimits,
    RuntimeConfig,
    SecurityPolicy,
    ToolManifest,
)
from src.tools.registry import (
    ToolExecutionContext,
    ToolExecutionResult,
    ToolRegistry,
    ToolSearchQuery,
)


def _make_manifest(
    tool_id: str,
    capabilities: list[str] | None = None,
    risk_level: str = "low",
    runtime_type: str = "builtin",
    owner: str = "system",
) -> ToolManifest:
    return ToolManifest(
        tool_id=tool_id,
        name=tool_id.split(".")[-1],
        description=f"Test tool {tool_id}",
        version="1.0.0",
        owner=owner,
        capabilities=capabilities or [],
        risk_level=risk_level,
        runtime=RuntimeConfig(type=runtime_type),
        resource_limits=ResourceLimits(),
        security=SecurityPolicy(scan_passed=True),
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )


class TestToolRegistry:
    def test_register_and_get(self) -> None:
        registry = ToolRegistry()
        manifest = _make_manifest("math.calculator", capabilities=["calculation"])
        record = registry.register(manifest)
        assert record.tool_id == "math.calculator"

        retrieved = registry.get("math.calculator")
        assert retrieved is not None
        assert retrieved.manifest.name == "calculator"

    def test_unregister(self) -> None:
        registry = ToolRegistry()
        registry.register(_make_manifest("math.calculator"))
        assert registry.unregister("math.calculator") is True
        assert registry.get("math.calculator") is None
        assert registry.unregister("nonexistent") is False

    def test_list_all(self) -> None:
        registry = ToolRegistry()
        registry.register(_make_manifest("math.calculator"))
        registry.register(_make_manifest("data.csv.profile"))
        assert len(registry.list_all()) == 2

    @pytest.mark.asyncio
    async def test_search_by_capability(self) -> None:
        registry = ToolRegistry()
        registry.register(
            _make_manifest("math.calculator", capabilities=["calculation"])
        )
        registry.register(
            _make_manifest("data.csv.profile", capabilities=["csv_processing"])
        )

        results = await registry.search(
            ToolSearchQuery(capabilities=["calculation"])
        )
        assert len(results) == 1
        assert results[0].tool_id == "math.calculator"

    @pytest.mark.asyncio
    async def test_search_by_risk_level(self) -> None:
        registry = ToolRegistry()
        registry.register(_make_manifest("safe.tool", risk_level="low"))
        registry.register(_make_manifest("risky.tool", risk_level="high"))

        results = await registry.search(
            ToolSearchQuery(max_risk_level="medium")
        )
        assert len(results) == 1
        assert results[0].tool_id == "safe.tool"

    @pytest.mark.asyncio
    async def test_search_by_name_pattern(self) -> None:
        registry = ToolRegistry()
        registry.register(_make_manifest("math.calculator"))
        registry.register(_make_manifest("math.statistics"))
        registry.register(_make_manifest("data.csv.profile"))

        results = await registry.search(
            ToolSearchQuery(name_pattern="math.*")
        )
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_search_excludes_disabled(self) -> None:
        registry = ToolRegistry()
        record = registry.register(_make_manifest("math.calculator"))
        record.enabled = False

        results = await registry.search(ToolSearchQuery())
        assert len(results) == 0

    def test_resolve_tools_for_patterns(self) -> None:
        registry = ToolRegistry()
        registry.register(_make_manifest("data.csv.profile"))
        registry.register(_make_manifest("data.json.parse"))
        registry.register(_make_manifest("math.calculator"))
        registry.register(_make_manifest("web.search"))

        resolved = registry.resolve_tools_for_patterns(
            allowed_patterns=["data.*"],
            denied_patterns=["data.json.*"],
        )
        assert "data.csv.profile" in resolved
        assert "data.json.parse" not in resolved
        assert "math.calculator" not in resolved

    def test_health_tracking(self) -> None:
        registry = ToolRegistry()
        registry.register(_make_manifest("math.calculator"))

        registry.update_health("math.calculator", healthy=True)
        record = registry.get("math.calculator")
        assert record is not None
        assert record.health_status == "healthy"

    def test_invocation_recording(self) -> None:
        registry = ToolRegistry()
        registry.register(_make_manifest("math.calculator"))

        registry.record_invocation("math.calculator", success=True, latency_ms=50)
        registry.record_invocation("math.calculator", success=False, latency_ms=200)

        record = registry.get("math.calculator")
        assert record is not None
        assert record.invocation_count == 2
        assert record.failure_count == 1
        assert record.avg_latency_ms > 0

    def test_tool_count(self) -> None:
        registry = ToolRegistry()
        assert registry.tool_count == 0
        registry.register(_make_manifest("math.calculator"))
        assert registry.tool_count == 1
