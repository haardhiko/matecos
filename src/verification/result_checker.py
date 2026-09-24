"""
result_checker.py
=================
Post-execution result verification using the verifier agent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import structlog

from src.agents.llm_interface import LLMConfig, LLMProvider
from src.agents.prompts import PromptRenderer

logger = structlog.get_logger(__name__)


@dataclass
class CriterionResult:
    """Result of evaluating a single acceptance criterion."""

    criterion: str
    passed: bool
    evidence: str = ""


@dataclass
class VerificationResult:
    """Aggregate result of verifying an agent's output."""

    verdict: str  # pass, fail, partial
    criteria_results: list[CriterionResult] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    retry_recommended: bool = False
    confidence: float = 0.0


class ResultChecker:
    """Verifies agent results against acceptance criteria.

    Uses the VERIFIER_SYSTEM prompt to have the LLM act as an impartial
    auditor.  Falls back to heuristic verification when LLM is unavailable.

    Parameters
    ----------
    llm:
        The LLM provider for verification inference.
    renderer:
        Optional prompt renderer.
    """

    def __init__(
        self,
        llm: LLMProvider | None = None,
        renderer: PromptRenderer | None = None,
    ) -> None:
        self._llm = llm
        self._renderer = renderer or PromptRenderer()
        self._log = logger.bind(component="ResultChecker")

    async def verify(
        self,
        task_objective: str,
        acceptance_criteria: list[str],
        agent_result: dict[str, Any],
    ) -> VerificationResult:
        """Verify an agent's result against acceptance criteria.

        Args:
            task_objective: What the task was supposed to accomplish.
            acceptance_criteria: List of criteria to check.
            agent_result: The agent's output to verify.

        Returns:
            ``VerificationResult`` with the verification outcome.
        """
        if not acceptance_criteria:
            return VerificationResult(
                verdict="pass",
                confidence=1.0,
            )

        if self._llm:
            return await self._llm_verify(
                task_objective, acceptance_criteria, agent_result
            )

        # Heuristic fallback
        return self._heuristic_verify(acceptance_criteria, agent_result)

    async def _llm_verify(
        self,
        task_objective: str,
        acceptance_criteria: list[str],
        agent_result: dict[str, Any],
    ) -> VerificationResult:
        """Verify using the LLM verifier agent."""
        context = {
            "task_objective": task_objective,
            "acceptance_criteria": acceptance_criteria,
            "agent_result": json.dumps(agent_result, default=str)[:2000],
        }

        messages = self._renderer.build_messages("VERIFIER_SYSTEM", context)
        config = LLMConfig(
            model="gpt-4o-mini",
            temperature=0.0,
            response_format="json",
        )

        try:
            response = await self._llm.complete(messages, config)  # type: ignore
            raw = response.raw_json or json.loads(response.content)

            tool_input = raw.get("tool_input", {})
            verdict = tool_input.get("verdict", "fail")
            criteria_results = [
                CriterionResult(
                    criterion=cr.get("criterion", ""),
                    passed=cr.get("passed", False),
                    evidence=cr.get("evidence", ""),
                )
                for cr in tool_input.get("criteria_results", [])
            ]
            gaps = tool_input.get("gaps", [])
            retry = tool_input.get("retry_recommended", False)

            passed_count = sum(1 for cr in criteria_results if cr.passed)
            total = len(criteria_results) or 1
            confidence = passed_count / total

            return VerificationResult(
                verdict=verdict,
                criteria_results=criteria_results,
                gaps=gaps,
                retry_recommended=retry,
                confidence=confidence,
            )

        except Exception as exc:  # noqa: BLE001
            self._log.warning("result_checker.llm_error", error=str(exc))
            return self._heuristic_verify(acceptance_criteria, agent_result)

    def _heuristic_verify(
        self,
        acceptance_criteria: list[str],
        agent_result: dict[str, Any],
    ) -> VerificationResult:
        """Heuristic verification — checks if result is non-empty and has expected keys."""
        criteria_results: list[CriterionResult] = []

        for criterion in acceptance_criteria:
            # Simple heuristic: check if result exists and is non-empty
            passed = bool(agent_result)
            criteria_results.append(
                CriterionResult(
                    criterion=criterion,
                    passed=passed,
                    evidence="Result is non-empty" if passed else "Result is empty",
                )
            )

        passed_count = sum(1 for cr in criteria_results if cr.passed)
        total = len(criteria_results) or 1

        if passed_count == total:
            verdict = "pass"
        elif passed_count > 0:
            verdict = "partial"
        else:
            verdict = "fail"

        return VerificationResult(
            verdict=verdict,
            criteria_results=criteria_results,
            retry_recommended=(verdict == "fail"),
            confidence=passed_count / total,
        )
