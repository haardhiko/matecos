"""
test_verification.py
====================
Unit tests for input validation, result checking, and provenance tracking.
"""

from __future__ import annotations

import pytest

from src.agents.llm_interface import MockLLMAdapter
from src.verification.provenance import ProvenanceTracker
from src.verification.result_checker import ResultChecker, VerificationResult
from src.verification.validators import InputValidator, ValidationError


class TestInputValidator:
    def test_validate_goal_success(self) -> None:
        validator = InputValidator()
        goal = "Analyze the customer churn dataset and generate findings."
        validated = validator.validate_goal(goal)
        assert validated == goal

    def test_validate_goal_empty(self) -> None:
        validator = InputValidator()
        with pytest.raises(ValidationError, match="cannot be empty"):
            validator.validate_goal("   ")

    def test_validate_goal_too_short(self) -> None:
        validator = InputValidator()
        with pytest.raises(ValidationError, match="at least 10 characters"):
            validator.validate_goal("Short")

    def test_validate_tool_input_schema(self) -> None:
        validator = InputValidator()
        schema = {
            "type": "object",
            "required": ["expression"],
            "properties": {
                "expression": {"type": "string"},
                "precision": {"type": "integer"},
            },
        }

        # Valid input
        errors = validator.validate_tool_input("math.calculator", {"expression": "2+2"}, schema)
        assert errors == []

        # Missing required
        errors = validator.validate_tool_input("math.calculator", {}, schema)
        assert len(errors) == 1
        assert "Missing required field: 'expression'" in errors[0]

        # Wrong type
        errors = validator.validate_tool_input(
            "math.calculator", {"expression": "2+2", "precision": "not-an-int"}, schema
        )
        assert len(errors) == 1
        assert "expected type 'integer'" in errors[0]


class TestResultChecker:
    @pytest.mark.asyncio
    async def test_heuristic_verification(self) -> None:
        checker = ResultChecker(llm=None)
        res = await checker.verify(
            task_objective="Extract columns",
            acceptance_criteria=["Columns must be listed"],
            agent_result={"columns": ["age", "income", "churn"]},
        )
        assert isinstance(res, VerificationResult)
        assert res.verdict == "pass"
        assert res.confidence == 1.0

    @pytest.mark.asyncio
    async def test_empty_result_fails_heuristic(self) -> None:
        checker = ResultChecker(llm=None)
        res = await checker.verify(
            task_objective="Extract columns",
            acceptance_criteria=["Columns must be listed"],
            agent_result={},
        )
        assert res.verdict == "fail"
        assert res.retry_recommended is True

    @pytest.mark.asyncio
    async def test_llm_verification(self) -> None:
        llm_resp = {
            "decision_type": "complete",
            "tool_input": {
                "verdict": "pass",
                "criteria_results": [
                    {
                        "criterion": "Summary exists",
                        "passed": True,
                        "evidence": "Found summary field",
                    }
                ],
                "gaps": [],
                "retry_recommended": False,
            },
            "reason_summary": "All criteria met.",
        }
        adapter = MockLLMAdapter(responses=[llm_resp])
        checker = ResultChecker(llm=adapter)

        res = await checker.verify(
            task_objective="Provide summary",
            acceptance_criteria=["Summary exists"],
            agent_result={"summary": "Detailed report summary."},
        )
        assert res.verdict == "pass"
        assert len(res.criteria_results) == 1
        assert res.criteria_results[0].passed is True


class TestProvenanceTracker:
    def test_record_and_get_by_execution(self) -> None:
        tracker = ProvenanceTracker()
        rec_id = tracker.record(
            output_id="out-1",
            output_type="task_result",
            source_ids=["src-raw-data"],
            agent_id="agent-1",
            execution_id="exec-42",
        )
        assert rec_id
        records = tracker.get_by_execution("exec-42")
        assert len(records) == 1
        assert records[0].output_id == "out-1"

    def test_lineage_traversal(self) -> None:
        tracker = ProvenanceTracker()
        # raw -> processed -> final
        tracker.record(output_id="proc-1", output_type="intermediate", source_ids=["raw-1"])
        tracker.record(output_id="final-1", output_type="result", source_ids=["proc-1"])

        lineage = tracker.get_lineage("final-1")
        output_ids = [r.output_id for r in lineage]
        assert "final-1" in output_ids
        assert "proc-1" in output_ids
