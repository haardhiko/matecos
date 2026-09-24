"""
scheduler.py
============
Task scheduler — determines which tasks are ready and dispatches agents.

The ``Scheduler`` consumes the ``TaskGraph``, resolves ready tasks,
enforces concurrency limits, and spawns agent executions.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
import ulid

from src.agents.policies import AgentPolicy, BudgetTracker
from src.agents.roles import get_role_spec
from src.agents.runtime import AgentResult, AgentRuntime
from src.orchestration.task_graph import TaskGraph, TaskNode
from src.tools.registry import ToolRegistry

logger = structlog.get_logger(__name__)


class Scheduler:
    """Schedules and dispatches tasks from a TaskGraph.

    Manages concurrency by limiting the number of simultaneously
    running agents. Checks the graph for ready tasks on each cycle.

    Parameters
    ----------
    runtime:
        The agent runtime for executing tasks.
    tool_registry:
        The tool registry for resolving available tools per role.
    max_concurrent:
        Maximum number of agents running simultaneously.
    """

    def __init__(
        self,
        runtime: AgentRuntime,
        tool_registry: ToolRegistry,
        max_concurrent: int = 10,
    ) -> None:
        self._runtime = runtime
        self._registry = tool_registry
        self._max_concurrent = max_concurrent
        self._log = logger.bind(component="Scheduler")
        self._running_tasks: dict[str, asyncio.Task[AgentResult]] = {}

    async def execute_graph(
        self,
        execution_id: str,
        graph: TaskGraph,
        user_id: str = "",
    ) -> dict[str, AgentResult]:
        """Execute all tasks in the graph respecting dependencies.

        Runs tasks in waves: on each cycle, finds ready tasks,
        dispatches up to ``max_concurrent`` agents, waits for at least
        one to complete, then repeats.

        Args:
            execution_id: Parent execution ULID.
            graph: The task graph to execute.
            user_id: Originating user ID for audit.

        Returns:
            Dict mapping task_id → AgentResult.
        """
        results: dict[str, AgentResult] = {}
        self._log.info(
            "scheduler.execute_start",
            execution_id=execution_id,
            task_count=graph.node_count,
        )

        while not graph.is_complete:
            # Find tasks ready to run
            ready_tasks = graph.get_ready_tasks()

            # Dispatch up to max_concurrent - currently_running
            available_slots = self._max_concurrent - len(self._running_tasks)
            tasks_to_start = ready_tasks[:available_slots]

            for task_node in tasks_to_start:
                agent_id = ulid.new().str
                graph.mark_running(task_node.task_id, agent_id)

                asyncio_task = asyncio.create_task(
                    self._run_agent_for_task(
                        agent_id=agent_id,
                        execution_id=execution_id,
                        task_node=task_node,
                        user_id=user_id,
                    ),
                    name=f"agent-{task_node.task_id}",
                )
                self._running_tasks[task_node.task_id] = asyncio_task
                self._log.info(
                    "scheduler.task_dispatched",
                    task_id=task_node.task_id,
                    role=task_node.role,
                    agent_id=agent_id,
                )

            if not self._running_tasks:
                # No running tasks and no ready tasks — either all done or deadlock
                if not ready_tasks:
                    self._log.warning("scheduler.no_progress")
                    break

            # Wait for at least one task to complete
            if self._running_tasks:
                done, _ = await asyncio.wait(
                    self._running_tasks.values(),
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for completed_task in done:
                    # Find which task_id this corresponds to
                    completed_tid: str | None = None
                    for tid, atask in self._running_tasks.items():
                        if atask is completed_task:
                            completed_tid = tid
                            break

                    if completed_tid is None:
                        continue

                    del self._running_tasks[completed_tid]

                    try:
                        result = completed_task.result()
                        results[completed_tid] = result

                        if result.status == "COMPLETED":
                            graph.mark_completed(
                                completed_tid, result.result or {}
                            )
                            self._log.info(
                                "scheduler.task_completed",
                                task_id=completed_tid,
                            )
                        else:
                            graph.mark_failed(
                                completed_tid,
                                result.error or "Unknown failure",
                            )
                            self._log.warning(
                                "scheduler.task_failed",
                                task_id=completed_tid,
                                error=result.error,
                            )
                    except Exception as exc:  # noqa: BLE001
                        graph.mark_failed(completed_tid, str(exc))
                        self._log.exception(
                            "scheduler.task_exception",
                            task_id=completed_tid,
                        )

        self._log.info(
            "scheduler.execute_complete",
            execution_id=execution_id,
            completed=sum(1 for r in results.values() if r.status == "COMPLETED"),
            failed=sum(1 for r in results.values() if r.status != "COMPLETED"),
        )

        return results

    async def _run_agent_for_task(
        self,
        agent_id: str,
        execution_id: str,
        task_node: TaskNode,
        user_id: str,
    ) -> AgentResult:
        """Create an agent and run it for a specific task.

        Args:
            agent_id: ULID for the new agent.
            execution_id: Parent execution ULID.
            task_node: The task to execute.
            user_id: Originating user ID.

        Returns:
            The ``AgentResult`` from the agent runtime.
        """
        role_spec = get_role_spec(task_node.role)

        # Resolve tools for this role
        resolved_tools = self._registry.resolve_tools_for_patterns(
            allowed_patterns=role_spec.allowed_tool_patterns,
            denied_patterns=role_spec.denied_tool_patterns,
        )

        policy = AgentPolicy.from_role_spec(role_spec, resolved_tools)
        budget = BudgetTracker.from_role_spec(role_spec)

        return await self._runtime.run(
            agent_id=agent_id,
            execution_id=execution_id,
            role_name=task_node.role,
            objective=task_node.objective,
            policy=policy,
            budget=budget,
            acceptance_criteria=task_node.acceptance_criteria,
        )
