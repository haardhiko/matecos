"""
policies.py
============
Budget tracking and agent execution policies.

The ``BudgetTracker`` enforces hard limits on iterations, cost, and tool
calls.  ``AgentPolicy`` wraps behavioural constraints and is consulted by
the agent runtime before every action.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from src.agents.roles import AgentRoleSpec

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Budget tracking
# ---------------------------------------------------------------------------


class BudgetExhaustedError(Exception):
    """Raised when any budget dimension is exceeded."""

    def __init__(self, dimension: str, limit: float, current: float) -> None:
        self.dimension = dimension
        self.limit = limit
        self.current = current
        super().__init__(f"Budget exhausted: {dimension} — limit={limit}, current={current}")


@dataclass
class BudgetTracker:
    """Tracks resource consumption against configured limits.

    Each call to :meth:`check` verifies that no limit has been exceeded.
    Call :meth:`record_iteration`, :meth:`record_cost`, or
    :meth:`record_tool_call` to update the counters.

    Attributes:
        max_iterations: Maximum allowed ReAct iterations.
        max_cost_usd: Maximum allowed cost in USD.
        max_tool_calls: Maximum allowed tool invocations.
        max_duration_seconds: Maximum wall-clock duration.
        iterations: Current iteration count.
        cost_usd: Accumulated cost in USD.
        tool_calls: Count of tool invocations.
        start_time: Unix timestamp of when tracking started.
    """

    max_iterations: int = 30
    max_cost_usd: float = 5.0
    max_tool_calls: int = 50
    max_duration_seconds: int = 3600
    iterations: int = 0
    cost_usd: float = 0.0
    tool_calls: int = 0
    start_time: float = field(default_factory=time.time)

    @classmethod
    def from_role_spec(cls, spec: AgentRoleSpec) -> BudgetTracker:
        """Create a tracker pre-configured from a role specification."""
        return cls(
            max_iterations=spec.max_iterations,
            max_cost_usd=spec.max_cost_usd,
            max_tool_calls=spec.max_tool_calls,
        )

    def record_iteration(self) -> None:
        """Increment the iteration counter and check limits."""
        self.iterations += 1
        self.check()

    def record_cost(self, cost: float) -> None:
        """Add cost and check limits."""
        self.cost_usd += cost
        self.check()

    def record_tool_call(self) -> None:
        """Increment the tool-call counter and check limits."""
        self.tool_calls += 1
        self.check()

    def check(self) -> None:
        """Verify all budget dimensions are within limits.

        Raises:
            BudgetExhaustedError: If any dimension exceeds its limit.
        """
        if self.iterations > self.max_iterations:
            raise BudgetExhaustedError("iterations", self.max_iterations, self.iterations)
        if self.cost_usd > self.max_cost_usd:
            raise BudgetExhaustedError("cost_usd", self.max_cost_usd, self.cost_usd)
        if self.tool_calls > self.max_tool_calls:
            raise BudgetExhaustedError("tool_calls", self.max_tool_calls, self.tool_calls)

        elapsed = time.time() - self.start_time
        if elapsed > self.max_duration_seconds:
            raise BudgetExhaustedError("duration_seconds", self.max_duration_seconds, elapsed)

    @property
    def remaining(self) -> dict[str, Any]:
        """Return a snapshot of remaining budget for prompt injection."""
        return {
            "iterations": max(0, self.max_iterations - self.iterations),
            "cost_usd": round(max(0.0, self.max_cost_usd - self.cost_usd), 4),
            "tool_calls": max(0, self.max_tool_calls - self.tool_calls),
        }


# ---------------------------------------------------------------------------
# Agent policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentPolicy:
    """Behavioural policy for an agent instance.

    Consulted by the runtime before every tool invocation, delegation,
    or approval request.

    Attributes:
        role_spec: The role specification for this agent.
        allowed_tools: Resolved set of tool IDs this agent may use.
        denied_tools: Resolved set of tool IDs this agent may NOT use.
        can_delegate: Whether the agent may spawn sub-agents.
        requires_human_approval: Whether every action needs human sign-off.
        max_delegation_depth: How many delegation levels deep this agent sits.
        data_sensitivity: Classification of the data being processed.
    """

    role_spec: AgentRoleSpec
    allowed_tools: frozenset[str] = frozenset()
    denied_tools: frozenset[str] = frozenset()
    can_delegate: bool = False
    requires_human_approval: bool = False
    max_delegation_depth: int = 5
    data_sensitivity: str = "internal"

    def is_tool_allowed(self, tool_id: str) -> bool:
        """Check whether a specific tool ID is permitted by this policy.

        Denied tools take precedence over allowed tools.  If
        ``allowed_tools`` is empty, all tools are allowed (unless
        explicitly denied).

        Args:
            tool_id: The tool identifier to check.

        Returns:
            ``True`` if the tool is permitted.
        """
        if tool_id in self.denied_tools:
            return False
        if not self.allowed_tools:
            return True
        return tool_id in self.allowed_tools

    def can_delegate_to(self, depth: int) -> bool:
        """Check if delegation is allowed at the given depth.

        Args:
            depth: The current delegation depth.

        Returns:
            ``True`` if delegation is within bounds.
        """
        return self.can_delegate and depth < self.max_delegation_depth

    @classmethod
    def from_role_spec(
        cls,
        spec: AgentRoleSpec,
        resolved_tools: frozenset[str] | None = None,
    ) -> AgentPolicy:
        """Build a policy from a role specification with optional resolved tool set.

        Args:
            spec: The agent role specification.
            resolved_tools: Pre-resolved set of tool IDs.

        Returns:
            A configured ``AgentPolicy``.
        """
        return cls(
            role_spec=spec,
            allowed_tools=resolved_tools or frozenset(),
            can_delegate=spec.can_delegate,
            requires_human_approval=spec.requires_human_approval,
        )
