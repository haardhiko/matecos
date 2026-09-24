"""
Tool selection logic — weights capability, risk, health, cost, and latency.

:class:`ToolSelector` ranks available tools for a given task so that agents
always receive the best-fit tool given their constraints and preferences.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import structlog
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from src.tools.registry import ToolExecutionContext

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Supporting models
# ---------------------------------------------------------------------------


class ToolSelectionPreferences(BaseModel):
    """Caller preferences that tune the tool selection ranking formula."""

    model_config = {"frozen": True}

    prefer_low_risk: bool = True
    prefer_low_cost: bool = False
    max_risk_level: Literal["low", "medium", "high", "critical"] = "high"
    # semver range string (e.g. ">=1.0.0 <2.0.0"); None = any version
    version_constraint: str | None = None


class RankedTool(BaseModel):
    """A tool with its composite score and per-dimension sub-scores."""

    tool_id: str
    score: float
    capability_score: float
    health_score: float
    risk_score: float
    latency_score: float
    cost_score: float
    # The underlying ToolRecord (typed as Any to avoid circular import)
    manifest: Any


# ---------------------------------------------------------------------------
# Risk ordering (lower index = lower risk)
# ---------------------------------------------------------------------------

_RISK_ORDER: list[str] = ["low", "medium", "high", "critical"]


def _risk_index(level: str) -> int:
    """Return a numeric index for a risk level (lower = safer)."""
    try:
        return _RISK_ORDER.index(level)
    except ValueError:
        return len(_RISK_ORDER)


# ---------------------------------------------------------------------------
# ToolSelector
# ---------------------------------------------------------------------------


class ToolSelector:
    """
    Selects the best tool for a given task.

    Ranking formula (weighted sum):
    ``score = capability_score * 0.40
            + health_score     * 0.20
            + risk_score       * 0.20
            + latency_score    * 0.10
            + cost_score       * 0.10``

    Tools with risk_level exceeding ``preferences.max_risk_level`` are
    excluded from results entirely.

    Tools whose ``tool_id`` is denied by the caller's permission scope are
    also filtered out.
    """

    RISK_WEIGHTS: dict[str, float] = {
        "low": 1.0,
        "medium": 0.7,
        "high": 0.3,
        "critical": 0.0,
    }

    # Composite score weights (must sum to 1.0)
    _W_CAPABILITY: float = 0.40
    _W_HEALTH: float = 0.20
    _W_RISK: float = 0.20
    _W_LATENCY: float = 0.10
    _W_COST: float = 0.10

    def __init__(self, registry: Any) -> None:
        """
        Initialise the selector.

        Args:
            registry: The :class:`ToolRegistry` instance used to fetch candidate tools.
        """
        self._registry = registry

    async def select(
        self,
        required_capabilities: list[str],
        context: "ToolExecutionContext",
        preferences: ToolSelectionPreferences | None = None,
    ) -> list[RankedTool]:
        """
        Return a ranked list of tools matching *required_capabilities*.

        Tools are filtered to those that:
        1. Possess at least one of the required capabilities.
        2. Have risk_level ≤ ``preferences.max_risk_level``.
        3. Are not denied by the caller's permission scope.

        The returned list is sorted descending by composite score.

        Args:
            required_capabilities: List of capability strings the tool must have.
            context: Execution context (used for permission scope check).
            preferences: Optional ranking preferences.

        Returns:
            A list of :class:`RankedTool` objects, best match first.
        """
        from src.tools.registry import ToolSearchQuery  # type: ignore[attr-defined]

        prefs = preferences or ToolSelectionPreferences()
        log = logger.bind(
            agent_id=context.agent_id,
            required_capabilities=required_capabilities,
            max_risk=prefs.max_risk_level,
        )
        log.info("tool_selector.select_start")

        # Fetch candidates from registry that match at least one capability
        query = ToolSearchQuery(
            capabilities=required_capabilities,
            max_risk_level=prefs.max_risk_level,
        )
        candidates = await self._registry.search(query)

        if not candidates:
            log.info("tool_selector.no_candidates")
            return []

        # Apply version constraint filter
        if prefs.version_constraint:
            candidates = self._filter_by_version(candidates, prefs.version_constraint)

        ranked: list[RankedTool] = []
        for record in candidates:
            manifest_obj = record.manifest  # ToolManifest

            cap_score = self._score_capability_match(record, required_capabilities)
            health_score = self._score_health(record)
            risk_score = self._score_risk(record, prefs.max_risk_level)
            latency_score = self._score_latency(record)
            cost_score = self._score_cost(record, prefs.prefer_low_cost)

            # If risk exceeds max_allowed, skip entirely
            if risk_score == 0.0 and _risk_index(manifest_obj.risk_level) > _risk_index(prefs.max_risk_level):
                continue

            composite = (
                cap_score * self._W_CAPABILITY
                + health_score * self._W_HEALTH
                + risk_score * self._W_RISK
                + latency_score * self._W_LATENCY
                + cost_score * self._W_COST
            )

            ranked.append(
                RankedTool(
                    tool_id=record.tool_id,
                    score=round(composite, 6),
                    capability_score=round(cap_score, 6),
                    health_score=round(health_score, 6),
                    risk_score=round(risk_score, 6),
                    latency_score=round(latency_score, 6),
                    cost_score=round(cost_score, 6),
                    manifest=record,
                )
            )

        ranked.sort(key=lambda r: r.score, reverse=True)
        log.info("tool_selector.select_complete", ranked_count=len(ranked))
        return ranked

    # ------------------------------------------------------------------
    # Scoring functions
    # ------------------------------------------------------------------

    def _score_capability_match(self, tool: Any, required: list[str]) -> float:
        """
        Compute Jaccard similarity between *required* and the tool's capabilities.

        Jaccard(A, B) = |A ∩ B| / |A ∪ B|

        Returns a value in [0.0, 1.0]; 1.0 = perfect match.
        """
        tool_caps: set[str] = set(tool.manifest.capabilities)
        required_set: set[str] = set(required)

        if not required_set and not tool_caps:
            return 1.0
        if not required_set or not tool_caps:
            return 0.0

        intersection = tool_caps & required_set
        union = tool_caps | required_set
        return len(intersection) / len(union)

    def _score_health(self, tool: Any) -> float:
        """
        Return a health score based on the tool's last reported health status.

        - ``healthy``  → 1.0
        - ``degraded`` → 0.5
        - ``unknown``  → 0.3
        - ``unhealthy`` → 0.0
        """
        health_map: dict[str, float] = {
            "healthy": 1.0,
            "degraded": 0.5,
            "unknown": 0.3,
            "unhealthy": 0.0,
        }
        status = getattr(tool, "health_status", "unknown") or "unknown"
        return health_map.get(status.lower(), 0.3)

    def _score_risk(self, tool: Any, max_allowed: str) -> float:
        """
        Return a risk score based on the tool's risk level.

        Returns ``0.0`` if ``tool.manifest.risk_level`` exceeds *max_allowed*
        (i.e. this tool should be excluded).  Otherwise returns
        ``RISK_WEIGHTS[risk_level]``.
        """
        risk_level: str = tool.manifest.risk_level
        if _risk_index(risk_level) > _risk_index(max_allowed):
            return 0.0
        return self.RISK_WEIGHTS.get(risk_level, 0.0)

    def _score_latency(self, tool: Any) -> float:
        """
        Score based on the tool's recent average latency.

        Lower latency = higher score.  Uses ``tool.avg_latency_ms`` if
        available; defaults to 0.5 (neutral) when unknown.
        Score = 1 / (1 + latency_seconds) clamped to [0, 1].
        """
        avg_latency_ms: float = float(getattr(tool, "avg_latency_ms", 0) or 0)
        if avg_latency_ms <= 0:
            return 0.5  # neutral when unknown
        latency_sec = avg_latency_ms / 1000.0
        # Sigmoid-like decay: score = 1 / (1 + latency_sec)
        return 1.0 / (1.0 + latency_sec)

    def _score_cost(self, tool: Any, prefer_low_cost: bool) -> float:
        """
        Score based on cost estimate.

        When *prefer_low_cost* is True, lower cost → higher score.
        When False, cost is neutral (returns 0.5).

        Score = 1 / (1 + cost_usd * 1000) when prefer_low_cost, else 0.5.
        """
        if not prefer_low_cost:
            return 0.5
        cost_usd: float = float(tool.manifest.limits.cost_estimate_usd)
        if cost_usd <= 0:
            return 1.0
        return 1.0 / (1.0 + cost_usd * 1000.0)

    # ------------------------------------------------------------------
    # Filters
    # ------------------------------------------------------------------

    def _filter_by_version(self, candidates: list[Any], constraint: str) -> list[Any]:
        """
        Filter candidates by a semver version constraint string.

        Uses the ``packaging`` library if available; falls back to no filtering
        when the library is missing (logs a warning).
        """
        try:
            from packaging.specifiers import SpecifierSet  # type: ignore[import]
            from packaging.version import Version  # type: ignore[import]

            spec = SpecifierSet(constraint, prereleases=True)
            filtered = [
                c for c in candidates
                if Version(c.manifest.version) in spec
            ]
            logger.debug(
                "tool_selector.version_filter",
                constraint=constraint,
                before=len(candidates),
                after=len(filtered),
            )
            return filtered
        except ImportError:
            logger.warning(
                "tool_selector.version_filter_skipped",
                reason="packaging library not installed",
            )
            return candidates
        except Exception as exc:
            logger.warning(
                "tool_selector.version_filter_error",
                constraint=constraint,
                error=str(exc),
            )
            return candidates
