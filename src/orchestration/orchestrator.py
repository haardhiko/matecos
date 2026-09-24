"""
orchestrator.py
===============
Main orchestrator — the top-level execution engine.

The ``Orchestrator`` ties together planner, scheduler, risk engine,
verification, and memory to execute a full request lifecycle:
1. Validate the request.
2. Plan (decompose the goal into a task graph).
3. Optionally await human approval of the plan.
4. Schedule and execute the task graph.
5. Verify the aggregate result.
6. Record episodic memory.
7. Return the verified result.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import structlog
import ulid

from src.agents.llm_interface import LLMProvider
from src.agents.prompts import PromptRenderer
from src.agents.runtime import AgentResult, AgentRuntime
from src.orchestration.planner import Planner, PlanningError
from src.orchestration.scheduler import Scheduler
from src.orchestration.state_machine import (
    ExecutionStatus,
    StateMachine,
    create_execution_state_machine,
)
from src.orchestration.task_graph import TaskGraph
from src.tools.registry import ToolRegistry

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Orchestration result
# ---------------------------------------------------------------------------


@dataclass
class OrchestrationResult:
    """Final result of a complete orchestration run.

    Attributes:
        execution_id: The execution ULID.
        status: Terminal execution status.
        result: Aggregated result from all tasks.
        task_results: Per-task results keyed by task_id.
        plan: The task graph that was executed.
        total_cost_usd: Total cost across all agents.
        total_duration_ms: Wall-clock duration in milliseconds.
        error: Error message if the orchestration failed.
    """

    execution_id: str
    status: str
    result: dict[str, Any] | None = None
    task_results: dict[str, AgentResult] = field(default_factory=dict)
    plan: TaskGraph | None = None
    total_cost_usd: float = 0.0
    total_duration_ms: int = 0
    error: str | None = None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class Orchestrator:
    """Top-level execution engine that coordinates the full request lifecycle.

    Parameters
    ----------
    llm:
        The LLM provider for planner and agent inference.
    tool_registry:
        The tool registry for tool discovery and execution.
    max_concurrent_agents:
        Maximum number of simultaneously running agents.
    """

    def __init__(
        self,
        llm: LLMProvider,
        tool_registry: ToolRegistry,
        max_concurrent_agents: int = 10,
        risk_checker: Any = None,
        tool_executor: Any = None,
    ) -> None:
        self._llm = llm
        self._registry = tool_registry
        self._renderer = PromptRenderer()
        self._planner = Planner(llm=llm, renderer=self._renderer)
        self._runtime = AgentRuntime(
            llm=llm,
            prompt_renderer=self._renderer,
            tool_executor=tool_executor,
            risk_checker=risk_checker,
        )
        self._scheduler = Scheduler(
            runtime=self._runtime,
            tool_registry=tool_registry,
            max_concurrent=max_concurrent_agents,
        )
        self._log = logger.bind(component="Orchestrator")

    async def execute(
        self,
        execution_id: str,
        goal: str,
        user_id: str = "",
        constraints: dict[str, Any] | None = None,
    ) -> OrchestrationResult:
        """Execute a full request lifecycle.

        Args:
            execution_id: Unique execution identifier (ULID).
            goal: The user's natural-language goal.
            user_id: Originating user ID.
            constraints: Optional execution constraints.

        Returns:
            ``OrchestrationResult`` with the final outcome.
        """
        start_ns = time.perf_counter_ns()
        fsm = create_execution_state_machine(execution_id=execution_id)
        log = self._log.bind(execution_id=execution_id)
        log.info("orchestrator.execute_start", goal_length=len(goal))

        try:
            # Phase 1: Validate
            await fsm.transition(ExecutionStatus.VALIDATING, "start_validation")
            log.info("orchestrator.validating")
            # (Input validation already done at the API layer)

            # Phase 2: Plan
            await fsm.transition(ExecutionStatus.PLANNING, "validation_passed")
            log.info("orchestrator.planning")

            try:
                graph = await self._planner.plan(goal)
            except PlanningError as exc:
                await fsm.transition(ExecutionStatus.FAILED, "planning_failed")
                return OrchestrationResult(
                    execution_id=execution_id,
                    status="FAILED",
                    error=f"Planning failed: {exc}",
                    total_duration_ms=self._elapsed_ms(start_ns),
                )

            log.info("orchestrator.plan_ready", task_count=graph.node_count)

            # Phase 3: Execute
            await fsm.transition(ExecutionStatus.RUNNING, "plan_approved_auto")
            log.info("orchestrator.running")

            task_results = await self._scheduler.execute_graph(
                execution_id=execution_id,
                graph=graph,
                user_id=user_id,
            )

            # Phase 4: Verify
            await fsm.transition(ExecutionStatus.VERIFYING, "all_tasks_complete")
            log.info("orchestrator.verifying")

            # Aggregate results
            total_cost = sum(r.total_cost_usd for r in task_results.values())
            all_completed = all(
                r.status == "COMPLETED" for r in task_results.values()
            )

            if all_completed:
                aggregated_result = self._aggregate_results(task_results)
                await fsm.transition(ExecutionStatus.COMPLETED, "verification_passed")
                final_status = "COMPLETED"
            elif any(r.status == "COMPLETED" for r in task_results.values()):
                aggregated_result = self._aggregate_results(task_results)
                await fsm.transition(ExecutionStatus.PARTIALLY_COMPLETED, "partial_completion")
                final_status = "PARTIALLY_COMPLETED"
            else:
                aggregated_result = None
                await fsm.transition(ExecutionStatus.FAILED, "verification_failed")
                final_status = "FAILED"

            duration_ms = self._elapsed_ms(start_ns)

            log.info(
                "orchestrator.execute_complete",
                status=final_status,
                cost_usd=round(total_cost, 4),
                duration_ms=duration_ms,
            )

            return OrchestrationResult(
                execution_id=execution_id,
                status=final_status,
                result=aggregated_result,
                task_results=task_results,
                plan=graph,
                total_cost_usd=round(total_cost, 4),
                total_duration_ms=duration_ms,
            )

        except Exception as exc:  # noqa: BLE001
            log.exception("orchestrator.unexpected_error")
            try:
                fail_triggers = {
                    ExecutionStatus.VALIDATING: "validation_failed",
                    ExecutionStatus.PLANNING: "planning_failed",
                    ExecutionStatus.RUNNING: "runtime_error",
                    ExecutionStatus.VERIFYING: "verification_failed",
                    ExecutionStatus.PARTIALLY_COMPLETED: "irrecoverable_failure",
                }
                trig = fail_triggers.get(fsm.state)
                if trig:
                    await fsm.transition(ExecutionStatus.FAILED, trig)
            except Exception:
                pass

            return OrchestrationResult(
                execution_id=execution_id,
                status="FAILED",
                error=f"Orchestration error: {type(exc).__name__}: {exc}",
                total_duration_ms=self._elapsed_ms(start_ns),
            )

    def _aggregate_results(
        self, task_results: dict[str, AgentResult]
    ) -> dict[str, Any]:
        """Aggregate individual task results into a single result dict."""
        aggregated: dict[str, Any] = {
            "tasks": {},
            "summary": [],
        }
        for tid, result in task_results.items():
            aggregated["tasks"][tid] = {
                "role": result.role,
                "status": result.status,
                "result": result.result,
                "error": result.error,
            }
            if result.status == "COMPLETED" and result.result:
                summary = result.result.get("summary", "")
                if summary:
                    aggregated["summary"].append(f"[{result.role}] {summary}")

        return aggregated

    @staticmethod
    def _elapsed_ms(start_ns: int) -> int:
        """Calculate elapsed milliseconds since start_ns."""
        return int((time.perf_counter_ns() - start_ns) / 1_000_000)
