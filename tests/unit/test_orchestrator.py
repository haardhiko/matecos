"""
test_orchestrator.py
====================
Unit tests for the top-level Orchestrator, Planner, and Scheduler.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from src.agents.llm_interface import MockLLMAdapter
from src.orchestration.orchestrator import Orchestrator, OrchestrationResult
from src.orchestration.planner import Planner, PlanningError
from src.orchestration.scheduler import Scheduler
from src.orchestration.task_graph import TaskGraph, TaskNode
from src.tools.manifests import ResourceLimits, RuntimeConfig, ToolManifest
from src.tools.registry import ToolRegistry


def _create_mock_registry() -> ToolRegistry:
    registry = ToolRegistry()
    manifests = [
        ToolManifest(
            tool_id="math.calculator",
            name="calculator",
            description="Arithmetic calculator",
            version="1.0.0",
            owner="system",
            capabilities=["math.calculate", "calculation"],
            risk_level="low",
            runtime=RuntimeConfig(type="builtin"),
            resource_limits=ResourceLimits(),
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        ),
        ToolManifest(
            tool_id="data.csv.profile",
            name="csv_profile",
            description="Profile tabular data",
            version="1.0.0",
            owner="system",
            capabilities=["data_analysis", "csv_processing"],
            risk_level="low",
            runtime=RuntimeConfig(type="builtin"),
            resource_limits=ResourceLimits(),
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        ),
    ]
    for m in manifests:
        registry.register(m)
    return registry


# ===========================================================================
# Planner Tests
# ===========================================================================


class TestPlanner:
    @pytest.mark.asyncio
    async def test_planner_success(self) -> None:
        plan_response = {
            "decision_type": "complete",
            "tool_input": {
                "task_graph": [
                    {
                        "task_id": "step1",
                        "role": "analyst",
                        "objective": "Analyze input data",
                        "dependencies": [],
                        "acceptance_criteria": ["data summary produced"],
                    },
                    {
                        "task_id": "step2",
                        "role": "writer",
                        "objective": "Summarize analysis into report",
                        "dependencies": ["step1"],
                        "acceptance_criteria": ["markdown report written"],
                    },
                ]
            },
            "reason_summary": "Two-step analysis and report plan.",
            "expected_output": "Decomposed task graph",
            "risk_assessment": "low",
        }
        adapter = MockLLMAdapter(responses=[plan_response])
        planner = Planner(llm=adapter)

        graph = await planner.plan("Analyze dataset and write report")
        assert isinstance(graph, TaskGraph)
        assert graph.node_count == 2
        order = graph.topological_sort()
        assert order == ["step1", "step2"]

    @pytest.mark.asyncio
    async def test_planner_invalid_json(self) -> None:
        class BrokenLLM:
            async def complete(self, messages, config):
                from src.agents.llm_interface import LLMResponse, LLMUsage
                return LLMResponse(
                    content="not json",
                    usage=LLMUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15, cost_usd=0.001),
                    model="mock",
                    finish_reason="stop",
                    raw_json=None,
                )

        planner = Planner(llm=BrokenLLM())
        with pytest.raises(PlanningError, match="not valid JSON"):
            await planner.plan("Fail goal")

    @pytest.mark.asyncio
    async def test_planner_unknown_role(self) -> None:
        bad_role_plan = {
            "decision_type": "complete",
            "tool_input": {
                "task_graph": [
                    {
                        "task_id": "step1",
                        "role": "super_wizard",
                        "objective": "Cast magic",
                    }
                ]
            },
            "reason_summary": "bad role",
        }
        adapter = MockLLMAdapter(responses=[bad_role_plan])
        planner = Planner(llm=adapter)
        with pytest.raises(PlanningError, match="Unknown role"):
            await planner.plan("Goal with bad role")


# ===========================================================================
# Scheduler Tests
# ===========================================================================


class TestScheduler:
    @pytest.mark.asyncio
    async def test_scheduler_executes_graph(self) -> None:
        registry = _create_mock_registry()
        agent_response = {
            "decision_type": "complete",
            "reason_summary": "Task finished successfully.",
            "tool_input": {"summary": "Done"},
            "risk_assessment": "low",
        }
        adapter = MockLLMAdapter(responses=[agent_response])

        from src.agents.runtime import AgentRuntime
        runtime = AgentRuntime(llm=adapter)
        scheduler = Scheduler(runtime=runtime, tool_registry=registry, max_concurrent=2)

        graph = TaskGraph()
        graph.add_node(TaskNode(task_id="t1", role="analyst", objective="Process data"))
        graph.add_node(TaskNode(task_id="t2", role="writer", objective="Draft report", dependencies=["t1"]))

        results = await scheduler.execute_graph("exec-101", graph, user_id="user-1")
        assert len(results) == 2
        assert results["t1"].status == "COMPLETED"
        assert results["t2"].status == "COMPLETED"
        assert graph.is_complete is True


# ===========================================================================
# Orchestrator Full Flow Tests
# ===========================================================================


class TestOrchestrator:
    @pytest.mark.asyncio
    async def test_full_orchestration_cycle(self) -> None:
        registry = _create_mock_registry()

        plan_response = {
            "decision_type": "complete",
            "tool_input": {
                "task_graph": [
                    {
                        "task_id": "task_1",
                        "role": "analyst",
                        "objective": "Calculate totals",
                        "dependencies": [],
                    }
                ]
            },
            "reason_summary": "Single task calculation plan.",
        }
        agent_response = {
            "decision_type": "complete",
            "reason_summary": "Calculated totals successfully.",
            "tool_input": {"summary": "Total is 42"},
            "risk_assessment": "low",
        }

        adapter = MockLLMAdapter(responses=[plan_response, agent_response])
        orchestrator = Orchestrator(llm=adapter, tool_registry=registry)

        result = await orchestrator.execute(
            execution_id="exec-full-1",
            goal="Calculate total metrics for Q1",
            user_id="user-42",
        )

        assert isinstance(result, OrchestrationResult)
        assert result.status == "COMPLETED"
        assert result.execution_id == "exec-full-1"
        assert result.result is not None
        assert "tasks" in result.result
        assert "task_1" in result.result["tasks"]
        assert result.result["tasks"]["task_1"]["status"] == "COMPLETED"
        assert result.total_duration_ms >= 0
