"""
planner.py
==========
Goal decomposition planner — uses the LLM to break goals into task DAGs.

The ``Planner`` takes a natural-language goal and produces a validated
``TaskGraph`` ready for scheduling.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from src.agents.llm_interface import LLMConfig, LLMProvider, Message
from src.agents.prompts import PromptRenderer
from src.agents.roles import ROLE_REGISTRY
from src.orchestration.task_graph import TaskGraph

logger = structlog.get_logger(__name__)


class PlanningError(Exception):
    """Raised when the planner fails to produce a valid plan."""


class Planner:
    """Decomposes a natural-language goal into a task graph.

    Uses the PLANNER_SYSTEM prompt template to instruct the LLM to
    produce a structured JSON task graph, then validates and constructs
    a ``TaskGraph`` from the result.

    Parameters
    ----------
    llm:
        The LLM provider for inference.
    renderer:
        Optional prompt renderer (defaults to a new ``PromptRenderer``).
    """

    def __init__(
        self,
        llm: LLMProvider,
        renderer: PromptRenderer | None = None,
    ) -> None:
        self._llm = llm
        self._renderer = renderer or PromptRenderer()
        self._log = logger.bind(component="Planner")

    async def plan(
        self,
        goal: str,
        context: str | None = None,
        model: str = "gpt-4o",
    ) -> TaskGraph:
        """Decompose a goal into a validated task graph.

        Args:
            goal: The user's natural-language goal.
            context: Optional additional context.
            model: LLM model to use for planning.

        Returns:
            A validated ``TaskGraph``.

        Raises:
            PlanningError: If the LLM response cannot be parsed or
                the resulting graph is invalid.
        """
        available_roles = sorted(ROLE_REGISTRY.keys())

        prompt_context = {
            "goal": goal,
            "available_roles": available_roles,
            "context": context or "",
        }
        messages = self._renderer.build_messages("PLANNER_SYSTEM", prompt_context)

        config = LLMConfig(
            model=model,
            temperature=0.2,
            max_tokens=4096,
            response_format="json",
        )

        self._log.info("planner.planning", goal_length=len(goal))

        response = await self._llm.complete(messages, config)

        # Parse the response
        raw_json = response.raw_json
        if raw_json is None:
            try:
                raw_json = json.loads(response.content)
            except json.JSONDecodeError as exc:
                raise PlanningError(
                    f"Planner LLM response is not valid JSON: {response.content[:200]}"
                ) from exc

        # Extract task_graph from the response
        tool_input = raw_json.get("tool_input", {})
        task_list = tool_input.get("task_graph")
        if not task_list or not isinstance(task_list, list):
            raise PlanningError(
                "Planner response missing 'tool_input.task_graph' list."
            )

        # Validate each task has required fields
        for task in task_list:
            missing = [f for f in ("task_id", "role", "objective") if f not in task]
            if missing:
                raise PlanningError(
                    f"Task missing required fields: {missing}. Task: {task}"
                )
            if task["role"].lower() not in ROLE_REGISTRY:
                raise PlanningError(
                    f"Unknown role '{task['role']}' in task '{task['task_id']}'. "
                    f"Available: {available_roles}"
                )

        # Build and validate graph
        try:
            graph = TaskGraph.from_plan(task_list)
        except Exception as exc:
            raise PlanningError(f"Invalid task graph: {exc}") from exc

        self._log.info(
            "planner.plan_complete",
            task_count=graph.node_count,
            cost_usd=response.usage.cost_usd,
        )

        return graph
