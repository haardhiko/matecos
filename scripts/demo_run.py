"""
demo_run.py
===========
Demonstration script showing the end-to-end MATECOS flow:
1. Initialize Tool Registry with builtin tools.
2. Initialize Mock / configured LLM.
3. Instantiate Orchestrator with FSM, Risk Engine, and Task Graph.
4. Execute a sample multi-agent goal.
5. Print the full execution breakdown, task results, and cost accounting.
"""

from __future__ import annotations

import asyncio
import json
import ulid

from scripts.seed_tools import build_default_registry
from src.agents.llm_interface import MockLLMAdapter
from src.orchestration.orchestrator import Orchestrator
from src.risk.engine import RiskEngine


async def run_demo() -> None:
    print("=" * 70)
    print("MATECOS — Multi-Agent Tool Ecosystem Demonstration")
    print("=" * 70)

    # 1. Registry & Executor
    print("\n[1/4] Initializing Tool Registry...")
    registry, executor = build_default_registry()
    print(f"      Loaded {registry.tool_count} static tools.")

    # 2. Risk Engine
    print("\n[2/4] Initializing Risk Engine...")
    risk_engine = RiskEngine()
    print("      Active classifiers: prompt_injection, data_exfiltration, privilege_escalation, sandbox_escape, content_safety, output_integrity.")

    # 3. LLM & Orchestrator
    print("\n[3/4] Initializing Orchestrator with mock plan & responses...")
    plan_resp = {
        "decision_type": "complete",
        "tool_input": {
            "task_graph": [
                {
                    "task_id": "step_extract",
                    "role": "analyst",
                    "objective": "Profile and analyze incoming data points",
                    "dependencies": [],
                    "acceptance_criteria": ["data columns profiled"],
                },
                {
                    "task_id": "step_summarize",
                    "role": "writer",
                    "objective": "Produce a concise executive summary",
                    "dependencies": ["step_extract"],
                    "acceptance_criteria": ["executive summary provided"],
                },
            ]
        },
        "reason_summary": "Two-phase decomposition: quantitative analysis followed by executive reporting.",
    }

    analyst_resp = {
        "decision_type": "complete",
        "reason_summary": "Data columns identified: revenue, profit_margin. Average margin: 23.4%",
        "tool_input": {
            "summary": "Revenue grew by 18% YoY. Average profit margin is 23.4%.",
            "metrics": {"growth_yoy": 0.18, "margin": 0.234},
        },
        "risk_assessment": "low",
    }

    writer_resp = {
        "decision_type": "complete",
        "reason_summary": "Executive briefing document prepared successfully.",
        "tool_input": {
            "summary": "EXECUTIVE BRIEFING: Healthy financial trajectory with 18% YoY growth and strong 23.4% margins.",
        },
        "risk_assessment": "low",
    }

    mock_llm = MockLLMAdapter(responses=[plan_resp, analyst_resp, writer_resp])

    orchestrator = Orchestrator(
        llm=mock_llm,
        tool_registry=registry,
        risk_checker=lambda tid, tin, ctx: risk_engine.assess(tid, tin, ctx).__dict__,
        tool_executor=executor.execute,
    )

    # 4. Execute Goal
    goal = "Analyze quarterly revenue growth and produce an executive briefing."
    exec_id = ulid.new().str
    print(f"\n[4/4] Executing Request (Execution ID: {exec_id})...")
    print(f"      Goal: '{goal}'\n")

    result = await orchestrator.execute(
        execution_id=exec_id,
        goal=goal,
        user_id="demo-user-01",
    )

    print("-" * 70)
    print(f"EXECUTION STATUS : {result.status}")
    print(f"TOTAL DURATION   : {result.total_duration_ms} ms")
    print(f"TOTAL COST (USD) : ${result.total_cost_usd:.4f}")
    print("-" * 70)
    print("TASK BREAKDOWN:")
    for task_id, task_res in result.task_results.items():
        print(f"  * Task [{task_id}] -> Role: {task_res.role}, Status: {task_res.status}, Iterations: {task_res.total_iterations}")
        if task_res.result:
            print(f"    Summary: {task_res.result.get('summary')}")

    print("\nFINAL AGGREGATED OUTPUT:")
    print(json.dumps(result.result, indent=2))
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_demo())
