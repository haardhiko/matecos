"""
engine.py
=========
Risk engine — non-bypassable risk assessment before and after every tool call.

The ``RiskEngine`` runs all classifiers, aggregates scores, maps to risk
levels, and produces a risk decision.  It is deterministic and rule-based;
LLM assessment is used only as a fallback for edge cases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from src.risk.classifiers import ALL_CLASSIFIERS, ClassifierResult

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Risk policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RiskPolicy:
    """Configurable thresholds for risk decisions.

    Attributes:
        low_threshold: Max aggregated score for LOW risk.
        medium_threshold: Max aggregated score for MEDIUM risk.
        high_threshold: Max aggregated score for HIGH risk.
        auto_approve_low: Auto-approve LOW risk actions.
        auto_approve_medium: Auto-approve MEDIUM risk actions.
        deny_critical: Automatically deny CRITICAL risk actions.
    """

    low_threshold: float = 0.2
    medium_threshold: float = 0.5
    high_threshold: float = 0.8
    auto_approve_low: bool = True
    auto_approve_medium: bool = False
    deny_critical: bool = True


DEFAULT_POLICY = RiskPolicy()
CONSERVATIVE_POLICY = RiskPolicy(
    low_threshold=0.15,
    medium_threshold=0.4,
    high_threshold=0.7,
    auto_approve_low=True,
    auto_approve_medium=False,
    deny_critical=True,
)
PERMISSIVE_POLICY = RiskPolicy(
    low_threshold=0.3,
    medium_threshold=0.6,
    high_threshold=0.9,
    auto_approve_low=True,
    auto_approve_medium=True,
    deny_critical=True,
)


# ---------------------------------------------------------------------------
# Risk decision
# ---------------------------------------------------------------------------


@dataclass
class RiskDecision:
    """Result of a risk assessment.

    Attributes:
        risk_level: LOW, MEDIUM, HIGH, or CRITICAL.
        decision: allow, allow_with_limits, require_human_approval, or deny.
        aggregate_score: Weighted sum of classifier scores.
        classifier_results: Individual classifier outputs.
        required_controls: Controls to apply if allowed.
        denial_reason: Reason for denial (when decision='deny').
    """

    risk_level: str
    decision: str
    aggregate_score: float
    classifier_results: list[ClassifierResult] = field(default_factory=list)
    required_controls: list[str] = field(default_factory=list)
    denial_reason: str | None = None


# ---------------------------------------------------------------------------
# RiskEngine
# ---------------------------------------------------------------------------


class RiskEngine:
    """Non-bypassable risk assessment engine.

    Called before AND after every tool invocation.  Pre-invocation
    assessment determines whether the tool may execute; post-invocation
    assessment validates the output.

    Parameters
    ----------
    policy:
        The risk policy to apply.  Defaults to ``DEFAULT_POLICY``.
    """

    # Classifier weights (must sum to 1.0)
    _WEIGHTS = {
        "prompt_injection": 0.25,
        "data_exfiltration": 0.20,
        "privilege_escalation": 0.20,
        "sandbox_escape": 0.15,
        "content_safety": 0.10,
        "output_integrity": 0.10,
    }

    def __init__(self, policy: RiskPolicy | None = None) -> None:
        self._policy = policy or DEFAULT_POLICY
        self._log = logger.bind(component="RiskEngine")

    def assess(
        self,
        tool_id: str,
        tool_input: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> RiskDecision:
        """Assess the risk of a tool invocation.

        Runs all classifiers, computes a weighted aggregate score,
        maps to a risk level, and produces a decision based on the
        configured policy.

        Args:
            tool_id: The tool identifier.
            tool_input: The tool's input parameters.
            context: Execution context (agent_id, execution_id, etc.).

        Returns:
            A ``RiskDecision`` with the assessment result.
        """
        ctx = context or {}
        results: list[ClassifierResult] = []

        for classifier in ALL_CLASSIFIERS:
            try:
                result = classifier(tool_id, tool_input, ctx)
                results.append(result)
            except Exception as exc:  # noqa: BLE001
                self._log.warning(
                    "risk.classifier_error",
                    classifier=classifier.__name__,
                    error=str(exc),
                )
                # Treat error as medium risk
                results.append(
                    ClassifierResult(
                        classifier=classifier.__name__,
                        score=0.5,
                        reason=f"Classifier error: {type(exc).__name__}",
                    )
                )

        # Compute weighted aggregate
        aggregate = 0.0
        for result in results:
            weight = self._WEIGHTS.get(result.classifier, 0.1)
            aggregate += result.score * weight

        aggregate = min(1.0, aggregate)

        # Map to risk level
        risk_level = self._score_to_level(aggregate)

        # Map to decision
        decision, controls, denial_reason = self._level_to_decision(risk_level, aggregate, tool_id)

        self._log.info(
            "risk.assessed",
            tool_id=tool_id,
            aggregate_score=round(aggregate, 4),
            risk_level=risk_level,
            decision=decision,
        )

        return RiskDecision(
            risk_level=risk_level,
            decision=decision,
            aggregate_score=round(aggregate, 4),
            classifier_results=results,
            required_controls=controls,
            denial_reason=denial_reason,
        )

    def assess_output(
        self,
        tool_id: str,
        tool_output: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> RiskDecision:
        """Assess the risk of a tool's output (post-invocation).

        Same logic as :meth:`assess` but run on the output data.
        """
        return self.assess(tool_id, tool_output, context)

    def _score_to_level(self, score: float) -> str:
        """Map an aggregate score to a risk level."""
        if score <= self._policy.low_threshold:
            return "LOW"
        if score <= self._policy.medium_threshold:
            return "MEDIUM"
        if score <= self._policy.high_threshold:
            return "HIGH"
        return "CRITICAL"

    def _level_to_decision(
        self, level: str, score: float, tool_id: str
    ) -> tuple[str, list[str], str | None]:
        """Map a risk level to a decision, controls, and optional denial reason."""
        if level == "CRITICAL" and self._policy.deny_critical:
            return "deny", [], f"Tool '{tool_id}' assessed as CRITICAL risk (score={score:.2f})."

        if level == "HIGH":
            return "require_human_approval", ["enhanced_logging", "output_sanitisation"], None

        if level == "MEDIUM":
            if self._policy.auto_approve_medium:
                return "allow_with_limits", ["enhanced_logging"], None
            return "require_human_approval", ["enhanced_logging"], None

        if level == "LOW":
            if self._policy.auto_approve_low:
                return "allow", [], None
            return "allow_with_limits", [], None

        return "allow", [], None
