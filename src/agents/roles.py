"""
roles.py
========
Agent role specifications and the role registry.

Each role defines the capabilities, budget defaults, allowed tools,
and behavioural constraints for an agent specialisation.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentRoleSpec:
    """Immutable specification for an agent role.

    Attributes:
        name: Unique role identifier (matches ``AgentRole`` enum values).
        display_name: Human-readable name.
        description: What this role does.
        default_model: LLM model to use unless overridden.
        default_temperature: Sampling temperature for this role.
        max_iterations: Maximum ReAct loop iterations.
        max_tool_calls: Maximum tool invocations per execution.
        max_cost_usd: Budget ceiling for this role.
        allowed_tool_patterns: Glob patterns for permitted tools.
        denied_tool_patterns: Glob patterns for explicitly denied tools.
        capabilities: Capabilities this role can provide.
        can_delegate: Whether this role can spawn sub-agents.
        requires_human_approval: If True, every action requires human sign-off.
        prompt_template: Name of the prompt template in the registry.
    """

    name: str
    display_name: str
    description: str
    default_model: str = "gpt-4o-mini"
    default_temperature: float = 0.1
    max_iterations: int = 30
    max_tool_calls: int = 50
    max_cost_usd: float = 5.0
    allowed_tool_patterns: list[str] = field(default_factory=lambda: ["*"])
    denied_tool_patterns: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    can_delegate: bool = False
    requires_human_approval: bool = False
    prompt_template: str = "REACT_AGENT_SYSTEM"


# ---------------------------------------------------------------------------
# Role definitions
# ---------------------------------------------------------------------------

ROLE_REGISTRY: dict[str, AgentRoleSpec] = {
    "planner": AgentRoleSpec(
        name="planner",
        display_name="Planner",
        description="Decomposes goals into a task DAG and assigns roles.",
        default_model="gpt-4o",
        default_temperature=0.2,
        max_iterations=5,
        max_tool_calls=5,
        max_cost_usd=1.0,
        allowed_tool_patterns=[],
        capabilities=["planning", "decomposition", "task_assignment"],
        can_delegate=True,
        prompt_template="PLANNER_SYSTEM",
    ),
    "researcher": AgentRoleSpec(
        name="researcher",
        display_name="Researcher",
        description="Searches, retrieves, and synthesises information from web and documents.",
        default_temperature=0.3,
        max_iterations=40,
        max_tool_calls=80,
        max_cost_usd=3.0,
        allowed_tool_patterns=["web.*", "data.csv.*", "document.*"],
        capabilities=["web_search", "document_reading", "data_retrieval", "summarisation"],
    ),
    "analyst": AgentRoleSpec(
        name="analyst",
        display_name="Data Analyst",
        description="Analyses structured data, computes statistics, and builds visualisations.",
        default_temperature=0.1,
        max_iterations=30,
        max_tool_calls=60,
        max_cost_usd=5.0,
        allowed_tool_patterns=["data.*", "math.*", "code.*"],
        capabilities=["data_analysis", "statistics", "visualisation", "csv_processing"],
    ),
    "coder": AgentRoleSpec(
        name="coder",
        display_name="Code Engineer",
        description="Writes, reviews, and executes code in a sandboxed environment.",
        default_temperature=0.0,
        max_iterations=50,
        max_tool_calls=100,
        max_cost_usd=8.0,
        allowed_tool_patterns=["code.*", "data.*", "math.*"],
        capabilities=["code_generation", "code_review", "code_execution", "debugging"],
    ),
    "writer": AgentRoleSpec(
        name="writer",
        display_name="Content Writer",
        description="Produces structured documents, reports, and summaries.",
        default_temperature=0.5,
        max_iterations=20,
        max_tool_calls=20,
        max_cost_usd=2.0,
        allowed_tool_patterns=["document.*", "web.*"],
        capabilities=["writing", "editing", "formatting", "report_generation"],
    ),
    "verifier": AgentRoleSpec(
        name="verifier",
        display_name="Verifier",
        description="Validates agent outputs against acceptance criteria.",
        default_temperature=0.0,
        max_iterations=5,
        max_tool_calls=10,
        max_cost_usd=1.0,
        allowed_tool_patterns=[],
        capabilities=["verification", "validation", "quality_assurance"],
        prompt_template="VERIFIER_SYSTEM",
    ),
    "risk_assessor": AgentRoleSpec(
        name="risk_assessor",
        display_name="Risk Assessor",
        description="Assesses the risk of proposed actions and tool invocations.",
        default_temperature=0.0,
        max_iterations=3,
        max_tool_calls=5,
        max_cost_usd=0.5,
        allowed_tool_patterns=[],
        capabilities=["risk_assessment", "security_review", "policy_enforcement"],
        prompt_template="RISK_REVIEW_SYSTEM",
    ),
    "orchestrator": AgentRoleSpec(
        name="orchestrator",
        display_name="Orchestrator",
        description="Coordinates multi-agent workflows and manages task dependencies.",
        default_model="gpt-4o",
        default_temperature=0.1,
        max_iterations=100,
        max_tool_calls=200,
        max_cost_usd=10.0,
        allowed_tool_patterns=["*"],
        capabilities=["orchestration", "coordination", "monitoring"],
        can_delegate=True,
    ),
    "repo_analyst": AgentRoleSpec(
        name="repo_analyst",
        display_name="Repository Analyst",
        description="Analyses GitHub repositories: code structure, dependencies, and documentation.",
        default_temperature=0.1,
        max_iterations=30,
        max_tool_calls=60,
        max_cost_usd=5.0,
        allowed_tool_patterns=["github.*", "code.*", "data.*"],
        capabilities=["repo_analysis", "code_review", "dependency_analysis", "documentation"],
    ),
    "executor": AgentRoleSpec(
        name="executor",
        display_name="Task Executor",
        description="General-purpose agent that executes individual tasks using available tools.",
        default_temperature=0.1,
        max_iterations=30,
        max_tool_calls=50,
        max_cost_usd=5.0,
        allowed_tool_patterns=["*"],
        capabilities=["task_execution", "tool_usage"],
    ),
}


def get_role_spec(role_name: str) -> AgentRoleSpec:
    """Look up a role specification by name.

    Args:
        role_name: The role identifier (case-insensitive).

    Returns:
        The matching ``AgentRoleSpec``.

    Raises:
        KeyError: If no role with that name exists.
    """
    key = role_name.lower()
    if key not in ROLE_REGISTRY:
        raise KeyError(
            f"Unknown agent role '{role_name}'. Available roles: {sorted(ROLE_REGISTRY.keys())}"
        )
    return ROLE_REGISTRY[key]
