"""Schemas for agent records and decision records."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AgentRole(str, Enum):
    """Enumeration of recognised agent specialisation roles."""

    PLANNER = "planner"
    DATA_ANALYSIS = "data_analysis"
    RESEARCH = "research"
    CODE_EXECUTION = "code_execution"
    DOCUMENT_GENERATION = "document_generation"
    REPOSITORY_OPERATOR = "repository_operator"
    VERIFICATION = "verification"
    RISK_REVIEW = "risk_review"
    SYNTHESIS = "synthesis"
    ORCHESTRATOR = "orchestrator"


class PermissionScope(BaseModel):
    """Capability allow/deny lists and data-access constraints for a single agent."""

    model_config = ConfigDict(populate_by_name=True)

    allowed_capabilities: list[str] = Field(
        default_factory=list,
        description="Capability tags the agent is explicitly permitted to use.",
    )
    denied_capabilities: list[str] = Field(
        default_factory=list,
        description="Capability tags explicitly forbidden, even if present in the allow list.",
    )
    data_access_level: Literal["public", "internal", "confidential", "restricted"] = Field(
        default="internal",
        description="Maximum data-sensitivity level the agent may access.",
    )
    can_spawn_agents: bool = Field(
        default=False,
        description="When True, the agent may delegate tasks to sub-agents.",
    )
    max_delegation_depth: int = Field(
        default=3, ge=0,
        description="Maximum number of recursive delegation hops permitted from this agent.",
    )
    can_approve_own_actions: bool = Field(
        default=False,
        description="When True, the agent may self-approve its own risk checkpoints (high-trust roles only).",
    )


class BudgetSpec(BaseModel):
    """Resource budget allocated to a single agent task."""

    model_config = ConfigDict(populate_by_name=True)

    max_iterations: int = Field(
        default=50, ge=1,
        description="Maximum number of LLM reasoning iterations.",
    )
    max_time_seconds: int = Field(
        default=3600, ge=1,
        description="Wall-clock timeout in seconds.",
    )
    max_cost_usd: float = Field(
        default=5.0, ge=0.0,
        description="Maximum spend in USD, including LLM and tool costs.",
    )
    max_tool_calls: int = Field(
        default=100, ge=1,
        description="Maximum number of tool invocations permitted.",
    )


class DecisionRecord(BaseModel):
    """Structured record of a single agent decision step.

    This schema intentionally excludes raw chain-of-thought. Only structured,
    length-bounded summaries are stored, ensuring no internal reasoning is
    ever persisted or surfaced to callers.
    """

    model_config = ConfigDict(populate_by_name=True)

    agent_id: str = Field(description="Identifier of the agent that made this decision.")
    step_id: str = Field(description="Unique identifier for this decision step within the agent's run.")
    decision_type: Literal[
        "tool_call", "delegate", "complete", "fail", "request_approval", "wait"
    ] = Field(description="Category of decision taken by the agent.")
    goal: str = Field(description="The objective the agent was pursuing at decision time.")
    selected_tool: str | None = Field(
        default=None,
        description="tool_id of the tool selected when decision_type is 'tool_call'.",
    )
    selected_agent_role: str | None = Field(
        default=None,
        description="Target agent role when decision_type is 'delegate'.",
    )
    reason_summary: str = Field(
        max_length=500,
        description="Concise, structured rationale for the decision (max 500 chars). Raw CoT is never stored here.",
    )
    expected_output: str | None = Field(
        default=None,
        description="Brief description of the expected outcome of this decision.",
    )
    risk_level: Literal["low", "medium", "high", "critical"] = Field(
        description="Risk classification of this decision as assessed by the risk engine."
    )
    requires_approval: bool = Field(
        description="True when this decision must be approved by a human before it is executed."
    )
    timestamp: datetime = Field(description="UTC timestamp when the decision was made.")
    execution_id: str = Field(description="Identifier of the parent execution.")
    task_id: str | None = Field(
        default=None, description="Identifier of the task this decision belongs to, if applicable."
    )

    @field_validator("reason_summary", mode="before")
    @classmethod
    def no_cot_leak(cls, v: str) -> str:
        """Enforce the 500-character ceiling to prevent raw chain-of-thought leakage."""
        if isinstance(v, str) and len(v) > 500:
            raise ValueError("reason_summary must be <= 500 chars (no raw CoT allowed)")
        return v


class AgentTask(BaseModel):
    """Task specification dispatched to an agent by the orchestrator or a parent agent."""

    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(description="Unique task identifier, typically a ULID.")
    execution_id: str = Field(description="Parent execution this task belongs to.")
    role: AgentRole = Field(description="Agent specialisation role that should execute this task.")
    objective: str = Field(description="Clear, structured statement of what the agent must accomplish.")
    context: dict = Field(
        default_factory=dict,
        description="Structured context data passed to the agent. Must not contain raw user input.",
    )
    allowed_tools: list[str] = Field(
        default_factory=list,
        description="Explicit allow-list of tool_ids. Empty list means the role's default tool set applies.",
    )
    permission_scope: PermissionScope = Field(
        default_factory=PermissionScope,
        description="Capability and data-access restrictions for this task's agent.",
    )
    budget: BudgetSpec = Field(
        default_factory=BudgetSpec,
        description="Resource budget allocated to this task.",
    )
    dependencies: list[str] = Field(
        default_factory=list,
        description="task_ids that must reach a terminal state before this task may begin.",
    )
    acceptance_criteria: list[str] = Field(
        default_factory=list,
        description="Structured, verifiable criteria that define successful task completion.",
    )
    parent_agent_id: str | None = Field(
        default=None,
        description="agent_id of the agent that spawned this task, or None if created by the orchestrator.",
    )


class AgentResult(BaseModel):
    """Final outcome record produced when an agent task reaches a terminal state."""

    model_config = ConfigDict(populate_by_name=True)

    agent_id: str = Field(description="Identifier of the agent instance that ran this task.")
    task_id: str = Field(description="Task identifier this result corresponds to.")
    execution_id: str = Field(description="Parent execution identifier.")
    status: str = Field(
        description="Terminal status of the agent run (e.g. COMPLETED, FAILED, CANCELLED)."
    )
    result: dict | None = Field(
        default=None,
        description="Structured output produced by the agent. None when the task did not complete successfully.",
    )
    error: str | None = Field(
        default=None,
        description="Error message if the agent failed. Must not contain raw chain-of-thought.",
    )
    iterations_used: int = Field(ge=0, description="Total LLM reasoning iterations consumed.")
    cost_usd: float = Field(ge=0.0, description="Total spend in USD for this agent run.")
    tokens_used: int = Field(ge=0, description="Total LLM tokens consumed (prompt + completion).")
    tool_calls_made: int = Field(ge=0, description="Total tool invocations made during the run.")
    decision_records: list[DecisionRecord] = Field(
        default_factory=list,
        description="Ordered list of structured decision records. Raw chain-of-thought is never stored.",
    )
    completed_at: datetime = Field(description="UTC timestamp when the agent reached a terminal state.")
