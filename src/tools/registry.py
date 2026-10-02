"""
registry.py
===========
Static tool registry — stores, searches, and manages tool manifests.

The ``ToolRegistry`` is the single source of truth for tool metadata.  Agents
never interact with tools directly; they go through the registry which handles
discovery, validation, health tracking, and executor dispatch.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

from src.tools.manifests import ToolManifest

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Supporting models
# ---------------------------------------------------------------------------


@dataclass
class ToolExecutionContext:
    """Context passed to every tool invocation for tracing and policy enforcement.

    Attributes:
        execution_id: Parent execution ULID.
        agent_id: Invoking agent's ULID.
        task_id: Task ULID (optional).
        user_id: Originating user (for audit).
        timeout_override: Override the manifest timeout (seconds).
        delegation_depth: Current delegation depth.
    """

    execution_id: str = ""
    agent_id: str = ""
    task_id: str = ""
    user_id: str = ""
    timeout_override: int | None = None
    delegation_depth: int = 0


@dataclass
class ToolExecutionResult:
    """Result of a single tool invocation."""

    invocation_id: str
    tool_id: str
    status: str  # SUCCEEDED, FAILED, TIMED_OUT, BLOCKED
    output: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: int = 0
    cost_usd: float = 0.0
    tokens_used: int = 0
    resource_usage: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolSearchQuery:
    """Query parameters for searching the tool registry."""

    capabilities: list[str] = field(default_factory=list)
    name_pattern: str | None = None
    max_risk_level: str = "critical"
    runtime_type: str | None = None
    owner: str | None = None


@dataclass
class ToolRecord:
    """A registered tool with its manifest and operational metadata."""

    tool_id: str
    manifest: ToolManifest
    registered_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    health_status: str = "unknown"  # healthy, degraded, unhealthy, unknown
    last_health_check: datetime | None = None
    invocation_count: int = 0
    failure_count: int = 0
    avg_latency_ms: float = 0.0
    enabled: bool = True


# ---------------------------------------------------------------------------
# Risk ordering helper
# ---------------------------------------------------------------------------

_RISK_ORDER = ["low", "medium", "high", "critical"]


def _risk_index(level: str) -> int:
    try:
        return _RISK_ORDER.index(level.lower())
    except ValueError:
        return len(_RISK_ORDER)


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------


class ToolRegistry:
    """In-memory tool registry with search, registration, and health tracking.

    This is the Phase 2 static registry — tools are registered at startup.
    Phase 7 adds dynamic repository-based tool discovery.

    Usage::

        registry = ToolRegistry()
        registry.register(my_manifest)
        results = await registry.search(ToolSearchQuery(capabilities=["csv_processing"]))
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolRecord] = {}
        self._change_listeners: set[Callable[[str, ToolRecord | None], None]] = set()
        self._log = logger.bind(component="ToolRegistry")

    def subscribe(self, listener: Callable[[str, ToolRecord | None], None]) -> Callable[[], None]:
        """Subscribe to registrations/removals; return a function that unsubscribes."""
        self._change_listeners.add(listener)

        def unsubscribe() -> None:
            self._change_listeners.discard(listener)

        return unsubscribe

    def _notify_changed(self, tool_id: str, record: ToolRecord | None) -> None:
        for listener in tuple(self._change_listeners):
            try:
                listener(tool_id, record)
            except Exception:
                self._log.exception("tool.change_listener_failed", tool_id=tool_id)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, manifest: ToolManifest) -> ToolRecord:
        """Register a tool manifest.

        If a tool with the same ``tool_id`` is already registered, it is
        replaced (upsert semantics).

        Args:
            manifest: The tool manifest to register.

        Returns:
            The created or updated ``ToolRecord``.
        """
        record = ToolRecord(tool_id=manifest.tool_id, manifest=manifest)
        self._tools[manifest.tool_id] = record
        self._log.info("tool.registered", tool_id=manifest.tool_id, version=manifest.version)
        self._notify_changed(manifest.tool_id, record)
        return record

    def unregister(self, tool_id: str) -> bool:
        """Remove a tool from the registry.

        Args:
            tool_id: The tool identifier.

        Returns:
            ``True`` if the tool was found and removed.
        """
        if tool_id in self._tools:
            del self._tools[tool_id]
            self._log.info("tool.unregistered", tool_id=tool_id)
            self._notify_changed(tool_id, None)
            return True
        return False

    def get(self, tool_id: str) -> ToolRecord | None:
        """Get a tool record by ID.

        Args:
            tool_id: The tool identifier.

        Returns:
            The ``ToolRecord`` or ``None``.
        """
        return self._tools.get(tool_id)

    def get_manifest(self, tool_id: str) -> ToolManifest | None:
        """Get the manifest for a tool by ID.

        Args:
            tool_id: The tool identifier.

        Returns:
            The ``ToolManifest`` or ``None``.
        """
        record = self._tools.get(tool_id)
        return record.manifest if record else None

    def list_all(self) -> list[ToolRecord]:
        """Return all registered tools.

        Returns:
            List of all ``ToolRecord`` objects.
        """
        return list(self._tools.values())

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(self, query: ToolSearchQuery) -> list[ToolRecord]:
        """Search for tools matching the given query.

        Filters:
        - ``capabilities``: At least one capability must match.
        - ``name_pattern``: Glob pattern against ``tool_id``.
        - ``max_risk_level``: Exclude tools above this risk level.
        - ``runtime_type``: Filter by runtime type.
        - ``owner``: Filter by tool owner.

        Only enabled tools are returned.

        Args:
            query: The search parameters.

        Returns:
            List of matching ``ToolRecord`` objects.
        """
        results: list[ToolRecord] = []

        for record in self._tools.values():
            if not record.enabled:
                continue

            manifest = record.manifest

            # Risk filter
            if _risk_index(manifest.risk_level) > _risk_index(query.max_risk_level):
                continue

            # Capability filter
            if query.capabilities:
                tool_caps = set(manifest.capabilities)
                if not tool_caps.intersection(query.capabilities):
                    continue

            # Name pattern filter
            if query.name_pattern:
                if not fnmatch.fnmatch(record.tool_id, query.name_pattern):
                    continue

            # Runtime type filter
            if query.runtime_type:
                if manifest.runtime.type != query.runtime_type:
                    continue

            # Owner filter
            if query.owner:
                if manifest.owner != query.owner:
                    continue

            results.append(record)

        self._log.debug(
            "tool.search",
            query_capabilities=query.capabilities,
            results_count=len(results),
        )
        return results

    # ------------------------------------------------------------------
    # Health tracking
    # ------------------------------------------------------------------

    def update_health(self, tool_id: str, healthy: bool) -> None:
        """Update the health status of a registered tool.

        Args:
            tool_id: The tool identifier.
            healthy: Whether the tool is healthy.
        """
        record = self._tools.get(tool_id)
        if record:
            record.health_status = "healthy" if healthy else "unhealthy"
            record.last_health_check = datetime.now(UTC)

    def record_invocation(
        self,
        tool_id: str,
        success: bool,
        latency_ms: float,
    ) -> None:
        """Record a tool invocation for operational metrics.

        Updates the running average latency, invocation count, and
        failure count.

        Args:
            tool_id: The tool identifier.
            success: Whether the invocation succeeded.
            latency_ms: Latency in milliseconds.
        """
        record = self._tools.get(tool_id)
        if not record:
            return

        record.invocation_count += 1
        if not success:
            record.failure_count += 1

        # Exponential moving average for latency
        alpha = 0.2
        if record.avg_latency_ms == 0.0:
            record.avg_latency_ms = latency_ms
        else:
            record.avg_latency_ms = alpha * latency_ms + (1 - alpha) * record.avg_latency_ms

    # ------------------------------------------------------------------
    # Resolve tools for an agent
    # ------------------------------------------------------------------

    def resolve_tools_for_patterns(
        self,
        allowed_patterns: list[str],
        denied_patterns: list[str] | None = None,
    ) -> frozenset[str]:
        """Resolve glob patterns to a concrete set of tool IDs.

        Args:
            allowed_patterns: Glob patterns to include (e.g. ``["data.*", "math.*"]``).
            denied_patterns: Glob patterns to exclude.

        Returns:
            A frozenset of matching tool IDs.
        """
        denied = denied_patterns or []
        result: set[str] = set()

        for tool_id in self._tools:
            allowed = any(fnmatch.fnmatch(tool_id, p) for p in allowed_patterns)
            blocked = any(fnmatch.fnmatch(tool_id, p) for p in denied)
            if allowed and not blocked:
                result.add(tool_id)

        return frozenset(result)

    @property
    def tool_count(self) -> int:
        """Return the number of registered tools."""
        return len(self._tools)
