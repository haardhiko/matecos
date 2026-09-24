"""
test_e2e.py
===========
End-to-end flow test:
User request -> Request gateway -> Planner (DAG) -> Scheduler -> Agent runtime -> Risk check -> Result verification
"""

from __future__ import annotations

import pytest

from src.agents.llm_interface import MockLLMAdapter
from src.orchestration.orchestrator import Orchestrator
from src.tools.manifests import ResourceLimits, RuntimeConfig, ToolManifest
from src.tools.registry import ToolRegistry


@pytest.fixture
def mock_ecosystem():
    """Set up the complete tool ecosystem with mock LLM responses."""
    registry = ToolRegistry()
    registry.register(
        ToolManifest(
            tool_id="math.calculator",
            name="calculator",
            description="Arithmetic calculator",
            version="1.0.0",
            owner="system",
            capabilities=["calculation", "math.calculate"],
            risk_level="low",
            runtime=RuntimeConfig(type="builtin"),
            resource_limits=ResourceLimits(),
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
    )

    plan_response = {
        "decision_type": "complete",
        "tool_input": {
            "task_graph": [
                {
                    "task_id": "step_fetch",
                    "role": "researcher",
                    "objective": "Gather historical market data",
                    "dependencies": [],
                    "acceptance_criteria": ["data collected"],
                },
                {
                    "task_id": "step_compute",
                    "role": "analyst",
                    "objective": "Calculate variance and risk metrics",
                    "dependencies": ["step_fetch"],
                    "acceptance_criteria": ["metrics computed"],
                },
            ]
        },
        "reason_summary": "Two-phase data gather and metric computation.",
    }

    agent_step1_response = {
        "decision_type": "complete",
        "reason_summary": "Gathered data points: 100, 105, 102",
        "tool_input": {"data": [100, 105, 102]},
        "risk_assessment": "low",
    }

    agent_step2_response = {
        "decision_type": "complete",
        "reason_summary": "Variance computed: 4.22",
        "tool_input": {"variance": 4.22, "summary": "Low volatility observed"},
        "risk_assessment": "low",
    }

    llm = MockLLMAdapter(responses=[plan_response, agent_step1_response, agent_step2_response])
    orchestrator = Orchestrator(llm=llm, tool_registry=registry)

    return {"registry": registry, "orchestrator": orchestrator, "llm": llm}


class TestEndToEndFlow:
    @pytest.mark.asyncio
    async def test_complete_multi_agent_pipeline(self, mock_ecosystem):
        orchestrator = mock_ecosystem["orchestrator"]

        result = await orchestrator.execute(
            execution_id="e2e-exec-001",
            goal="Analyze volatility for Q3 market data",
            user_id="analyst-user",
        )

        assert result.status == "COMPLETED"
        assert result.execution_id == "e2e-exec-001"
        assert result.result is not None
        assert "tasks" in result.result

        tasks = result.result["tasks"]
        assert "step_fetch" in tasks
        assert "step_compute" in tasks
        assert tasks["step_fetch"]["status"] == "COMPLETED"
        assert tasks["step_compute"]["status"] == "COMPLETED"
        assert len(result.result["summary"]) > 0
