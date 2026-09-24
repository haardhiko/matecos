"""
runtime.py
==========
Agent runtime — the controlled ReAct loop.

``AgentRuntime`` executes a single agent's lifecycle:
1. Build prompt from role, objective, and history.
2. Call LLM for next action decision.
3. Parse and validate the structured decision.
4. Execute the decided action (tool call, delegate, complete, fail).
5. Record results and loop.

The runtime never leaks chain-of-thought, enforces budget limits at every
iteration, and calls the risk engine before every tool invocation.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from src.agents.llm_interface import LLMConfig, LLMProvider, LLMResponse
from src.agents.policies import AgentPolicy, BudgetExhaustedError, BudgetTracker
from src.agents.prompts import PromptRenderer
from src.agents.roles import get_role_spec

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Action decision model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionDecision:
    """Parsed and validated decision from an LLM response.

    Attributes:
        decision_type: One of tool_call, delegate, complete, fail,
            request_approval, wait.
        tool_id: Tool identifier (when decision_type='tool_call').
        tool_input: Parameters for the tool call.
        reason_summary: Concise rationale (max 500 chars).
        expected_output: Brief description of expected result.
        risk_assessment: Self-assessed risk level.
    """

    decision_type: str
    tool_id: str | None = None
    tool_input: dict[str, Any] | None = None
    reason_summary: str = ""
    expected_output: str | None = None
    risk_assessment: str = "low"


class ActionParseError(Exception):
    """Raised when the LLM response cannot be parsed into an ActionDecision."""


# ---------------------------------------------------------------------------
# Decision record (audit-safe, no CoT)
# ---------------------------------------------------------------------------


@dataclass
class DecisionRecord:
    """An audit-safe record of one agent decision.

    Contains only the structured decision and outcome —
    never raw LLM reasoning or chain-of-thought.
    """

    iteration: int
    decision: ActionDecision
    outcome: str = ""  # "success", "failed", "blocked", etc.
    tool_result_summary: str = ""  # Max 500 chars
    cost_usd: float = 0.0
    tokens_used: int = 0
    duration_ms: int = 0


# ---------------------------------------------------------------------------
# AgentRuntime
# ---------------------------------------------------------------------------


class AgentRuntime:
    """Executes a single agent's ReAct loop.

    The runtime is stateless across executions — all mutable state lives in
    the ``AgentContext`` passed to :meth:`run`.

    Parameters
    ----------
    llm:
        The LLM provider to use for inference.
    prompt_renderer:
        Template renderer for building prompts.
    tool_executor:
        Callback that executes a tool invocation. Signature:
        ``async def execute(tool_id, tool_input, context) -> dict``.
    risk_checker:
        Callback for pre-invocation risk assessment. Signature:
        ``async def check(tool_id, tool_input, context) -> dict``.
        Must return ``{"decision": "allow"|"deny"|"require_human_approval", ...}``.
    """

    VALID_DECISION_TYPES = frozenset(
        {"tool_call", "delegate", "complete", "fail", "request_approval", "wait"}
    )

    def __init__(
        self,
        llm: LLMProvider,
        prompt_renderer: PromptRenderer | None = None,
        tool_executor: Any = None,
        risk_checker: Any = None,
    ) -> None:
        self._llm = llm
        self._renderer = prompt_renderer or PromptRenderer()
        self._tool_executor = tool_executor
        self._risk_checker = risk_checker
        self._log = logger.bind(component="AgentRuntime")

    async def run(
        self,
        agent_id: str,
        execution_id: str,
        role_name: str,
        objective: str,
        policy: AgentPolicy,
        budget: BudgetTracker,
        acceptance_criteria: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> AgentResult:
        """Execute the ReAct loop for a single agent.

        Args:
            agent_id: Unique agent identifier (ULID).
            execution_id: Parent execution identifier.
            role_name: Agent role name (looked up in the role registry).
            objective: The task objective for this agent.
            policy: The agent's execution policy.
            budget: Budget tracker (pre-configured with limits).
            acceptance_criteria: Optional list of acceptance criteria.
            context: Optional execution context dict.

        Returns:
            ``AgentResult`` with the final outcome.
        """
        role_spec = get_role_spec(role_name)
        llm_config = LLMConfig(
            model=role_spec.default_model,
            temperature=role_spec.default_temperature,
        )

        log = self._log.bind(
            agent_id=agent_id,
            execution_id=execution_id,
            role=role_name,
        )
        log.info("agent.run_start", objective=objective[:200])

        history: list[str] = []
        decision_records: list[DecisionRecord] = []
        last_tool_result: str | None = None
        final_result: dict[str, Any] | None = None
        final_status = "FAILED"
        error_message: str | None = None

        try:
            for iteration in range(1, role_spec.max_iterations + 1):
                budget.record_iteration()

                # Build prompt
                allowed_tools = list(policy.allowed_tools) if policy.allowed_tools else []
                prompt_context = {
                    "role": role_spec.display_name,
                    "objective": objective,
                    "allowed_tools": allowed_tools,
                    "budget_remaining": budget.remaining,
                    "history": history[-10:],  # Last 10 entries
                    "last_tool_result": last_tool_result,
                    "acceptance_criteria": acceptance_criteria or [],
                }

                messages = self._renderer.build_messages(role_spec.prompt_template, prompt_context)

                # Call LLM
                start_ns = time.perf_counter_ns()
                llm_response = await self._llm.complete(messages, llm_config)
                duration_ms = int((time.perf_counter_ns() - start_ns) / 1_000_000)

                budget.record_cost(llm_response.usage.cost_usd)

                # Parse decision
                try:
                    decision = self._parse_decision(llm_response)
                except ActionParseError as exc:
                    log.warning(
                        "agent.parse_error",
                        iteration=iteration,
                        error=str(exc),
                    )
                    history.append(f"[PARSE_ERROR] {exc}")
                    decision_records.append(
                        DecisionRecord(
                            iteration=iteration,
                            decision=ActionDecision(decision_type="fail", reason_summary=str(exc)),
                            outcome="parse_error",
                            duration_ms=duration_ms,
                            cost_usd=llm_response.usage.cost_usd,
                            tokens_used=llm_response.usage.total_tokens,
                        )
                    )
                    continue

                log.info(
                    "agent.decision",
                    iteration=iteration,
                    decision_type=decision.decision_type,
                    tool_id=decision.tool_id,
                )

                # Handle terminal decisions
                if decision.decision_type == "complete":
                    final_result = decision.tool_input or {"summary": decision.reason_summary}
                    final_status = "COMPLETED"
                    decision_records.append(
                        DecisionRecord(
                            iteration=iteration,
                            decision=decision,
                            outcome="completed",
                            duration_ms=duration_ms,
                            cost_usd=llm_response.usage.cost_usd,
                            tokens_used=llm_response.usage.total_tokens,
                        )
                    )
                    break

                if decision.decision_type == "fail":
                    error_message = decision.reason_summary
                    final_status = "FAILED"
                    decision_records.append(
                        DecisionRecord(
                            iteration=iteration,
                            decision=decision,
                            outcome="failed",
                            duration_ms=duration_ms,
                            cost_usd=llm_response.usage.cost_usd,
                            tokens_used=llm_response.usage.total_tokens,
                        )
                    )
                    break

                if decision.decision_type == "request_approval":
                    final_status = "WAITING_FOR_APPROVAL"
                    decision_records.append(
                        DecisionRecord(
                            iteration=iteration,
                            decision=decision,
                            outcome="awaiting_approval",
                            duration_ms=duration_ms,
                            cost_usd=llm_response.usage.cost_usd,
                            tokens_used=llm_response.usage.total_tokens,
                        )
                    )
                    break

                if decision.decision_type == "wait":
                    history.append("[WAIT] Agent decided to wait for dependencies.")
                    decision_records.append(
                        DecisionRecord(
                            iteration=iteration,
                            decision=decision,
                            outcome="waiting",
                            duration_ms=duration_ms,
                            cost_usd=llm_response.usage.cost_usd,
                            tokens_used=llm_response.usage.total_tokens,
                        )
                    )
                    continue

                # Handle tool_call
                if decision.decision_type == "tool_call" and decision.tool_id:
                    if not policy.is_tool_allowed(decision.tool_id):
                        history.append(
                            f"[BLOCKED] Tool '{decision.tool_id}' is not allowed by policy."
                        )
                        decision_records.append(
                            DecisionRecord(
                                iteration=iteration,
                                decision=decision,
                                outcome="blocked_by_policy",
                                duration_ms=duration_ms,
                                cost_usd=llm_response.usage.cost_usd,
                                tokens_used=llm_response.usage.total_tokens,
                            )
                        )
                        continue

                    # Risk check (non-bypassable)
                    if self._risk_checker:
                        risk_result = await self._risk_checker(
                            decision.tool_id,
                            decision.tool_input or {},
                            {"agent_id": agent_id, "execution_id": execution_id},
                        )
                        risk_decision = risk_result.get("decision", "allow")
                        if risk_decision == "deny":
                            history.append(
                                f"[RISK_DENIED] Tool '{decision.tool_id}' denied by risk engine."
                            )
                            decision_records.append(
                                DecisionRecord(
                                    iteration=iteration,
                                    decision=decision,
                                    outcome="risk_denied",
                                    duration_ms=duration_ms,
                                    cost_usd=llm_response.usage.cost_usd,
                                    tokens_used=llm_response.usage.total_tokens,
                                )
                            )
                            continue
                        if risk_decision == "require_human_approval":
                            final_status = "WAITING_FOR_APPROVAL"
                            decision_records.append(
                                DecisionRecord(
                                    iteration=iteration,
                                    decision=decision,
                                    outcome="awaiting_risk_approval",
                                    duration_ms=duration_ms,
                                    cost_usd=llm_response.usage.cost_usd,
                                    tokens_used=llm_response.usage.total_tokens,
                                )
                            )
                            break

                    # Execute tool
                    budget.record_tool_call()
                    tool_result: dict[str, Any] = {}
                    tool_outcome = "success"

                    if self._tool_executor:
                        try:
                            tool_result = await self._tool_executor(
                                decision.tool_id,
                                decision.tool_input or {},
                                {"agent_id": agent_id, "execution_id": execution_id},
                            )
                        except Exception as exc:  # noqa: BLE001
                            tool_result = {"error": str(exc)}
                            tool_outcome = "tool_failed"
                            log.warning(
                                "agent.tool_failed",
                                tool_id=decision.tool_id,
                                error=str(exc),
                            )
                    else:
                        tool_result = {"note": "No tool executor configured (stub mode)"}

                    # Truncate result for history (max 500 chars)
                    result_str = json.dumps(tool_result)
                    last_tool_result = result_str[:500]
                    history.append(
                        f"[TOOL_CALL] {decision.tool_id} -> {tool_outcome}: {result_str[:200]}"
                    )

                    decision_records.append(
                        DecisionRecord(
                            iteration=iteration,
                            decision=decision,
                            outcome=tool_outcome,
                            tool_result_summary=result_str[:500],
                            duration_ms=duration_ms,
                            cost_usd=llm_response.usage.cost_usd,
                            tokens_used=llm_response.usage.total_tokens,
                        )
                    )
                    continue

                # Handle delegate
                if decision.decision_type == "delegate":
                    if not policy.can_delegate:
                        history.append("[BLOCKED] Delegation not allowed by policy.")
                        decision_records.append(
                            DecisionRecord(
                                iteration=iteration,
                                decision=decision,
                                outcome="delegation_blocked",
                                duration_ms=duration_ms,
                                cost_usd=llm_response.usage.cost_usd,
                                tokens_used=llm_response.usage.total_tokens,
                            )
                        )
                        continue

                    # Delegation is handled by the orchestrator — signal it
                    final_status = "DELEGATING"
                    final_result = decision.tool_input
                    decision_records.append(
                        DecisionRecord(
                            iteration=iteration,
                            decision=decision,
                            outcome="delegating",
                            duration_ms=duration_ms,
                            cost_usd=llm_response.usage.cost_usd,
                            tokens_used=llm_response.usage.total_tokens,
                        )
                    )
                    break

            else:
                # Loop exhausted without terminal decision
                final_status = "FAILED"
                error_message = f"Agent exhausted max iterations ({role_spec.max_iterations})"
                log.warning("agent.max_iterations_reached")

        except BudgetExhaustedError as exc:
            final_status = "FAILED"
            error_message = str(exc)
            log.warning("agent.budget_exhausted", dimension=exc.dimension)

        except Exception as exc:  # noqa: BLE001
            final_status = "FAILED"
            error_message = f"Unexpected runtime error: {type(exc).__name__}"
            log.exception("agent.unexpected_error")

        log.info(
            "agent.run_complete",
            status=final_status,
            iterations=budget.iterations,
            cost_usd=round(budget.cost_usd, 4),
            tool_calls=budget.tool_calls,
        )

        return AgentResult(
            agent_id=agent_id,
            execution_id=execution_id,
            role=role_name,
            status=final_status,
            result=final_result,
            error=error_message,
            decision_records=decision_records,
            total_iterations=budget.iterations,
            total_cost_usd=round(budget.cost_usd, 4),
            total_tool_calls=budget.tool_calls,
        )

    def _parse_decision(self, response: LLMResponse) -> ActionDecision:
        """Parse an LLM response into an ActionDecision.

        Args:
            response: The raw LLM response.

        Returns:
            A validated ``ActionDecision``.

        Raises:
            ActionParseError: If the response is not valid JSON or
                contains invalid fields.
        """
        raw_json = response.raw_json
        if raw_json is None:
            # Try parsing the content directly
            try:
                raw_json = json.loads(response.content)
            except json.JSONDecodeError as exc:
                raise ActionParseError(
                    f"LLM response is not valid JSON: {response.content[:200]}"
                ) from exc

        decision_type = raw_json.get("decision_type")
        if decision_type not in self.VALID_DECISION_TYPES:
            raise ActionParseError(
                f"Invalid decision_type: '{decision_type}'. "
                f"Valid types: {sorted(self.VALID_DECISION_TYPES)}"
            )

        reason_summary = str(raw_json.get("reason_summary", ""))[:500]

        return ActionDecision(
            decision_type=decision_type,
            tool_id=raw_json.get("tool_id"),
            tool_input=raw_json.get("tool_input"),
            reason_summary=reason_summary,
            expected_output=raw_json.get("expected_output"),
            risk_assessment=raw_json.get("risk_assessment", "low"),
        )


# ---------------------------------------------------------------------------
# Agent result
# ---------------------------------------------------------------------------


@dataclass
class AgentResult:
    """Final result of an agent execution.

    Attributes:
        agent_id: The agent's ULID.
        execution_id: The parent execution's ULID.
        role: The role this agent executed as.
        status: Terminal status (COMPLETED, FAILED, WAITING_FOR_APPROVAL, etc.)
        result: The structured result dict (when COMPLETED).
        error: Error message (when FAILED).
        decision_records: Audit trail of all decisions made.
        total_iterations: How many ReAct iterations were executed.
        total_cost_usd: Total cost incurred.
        total_tool_calls: Total tool invocations.
    """

    agent_id: str
    execution_id: str
    role: str
    status: str
    result: dict[str, Any] | None = None
    error: str | None = None
    decision_records: list[DecisionRecord] = field(default_factory=list)
    total_iterations: int = 0
    total_cost_usd: float = 0.0
    total_tool_calls: int = 0
