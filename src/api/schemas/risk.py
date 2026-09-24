"""Schemas for risk engine decisions."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RiskLevel(str, Enum):
    """Ordinal risk classification used throughout the risk engine."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RiskDecisionType(str, Enum):
    """Possible outcomes of a risk assessment."""

    ALLOW = "allow"
    ALLOW_WITH_LIMITS = "allow_with_limits"
    REQUIRE_HUMAN_APPROVAL = "require_human_approval"
    DENY = "deny"


class ProposedAction(BaseModel):
    """Description of an action submitted to the risk engine for assessment.

    ``input_data`` must contain only a sanitised summary — raw user-supplied
    data must never be forwarded to the risk engine.
    """

    model_config = ConfigDict(populate_by_name=True)

    action_type: Literal[
        "tool_invocation",
        "agent_spawn",
        "data_access",
        "external_communication",
        "code_execution",
        "file_write",
        "network_request",
    ] = Field(description="Category of the proposed action.")
    tool_id: str | None = Field(
        default=None,
        description="tool_id when action_type is 'tool_invocation'.",
    )
    agent_role: str | None = Field(
        default=None,
        description="Target agent role when action_type is 'agent_spawn'.",
    )
    input_data: dict = Field(
        default_factory=dict,
        description="Sanitised structural summary of the action's input. Must not contain raw user data.",
    )
    requesting_agent_id: str = Field(
        description="agent_id of the agent requesting permission to perform this action."
    )
    execution_id: str = Field(description="Parent execution context for this action.")
    task_id: str | None = Field(
        default=None,
        description="task_id of the task that triggered this action, if applicable.",
    )
    delegation_depth: int = Field(
        default=0, ge=0,
        description="Number of delegation hops from the root orchestrator to the requesting agent.",
    )


class RiskFactor(BaseModel):
    """A single contributing factor in a risk assessment."""

    model_config = ConfigDict(populate_by_name=True)

    factor: str = Field(description="Short identifier for this risk factor, e.g. 'external_network_access'.")
    weight: float = Field(
        ge=0.0, le=1.0,
        description="Relative contribution of this factor to the overall risk score (0.0–1.0).",
    )
    description: str = Field(description="Human-readable explanation of why this factor applies.")


class RiskDecision(BaseModel):
    """Full output of a risk assessment, including the decision and supporting evidence."""

    model_config = ConfigDict(populate_by_name=True)

    risk_assessment_id: str = Field(description="Unique identifier for this risk assessment record.")
    execution_id: str = Field(description="Parent execution context.")
    action: str = Field(description="Normalised string representation of the assessed action.")
    tool_id: str | None = Field(
        default=None,
        description="tool_id when the assessed action involves a tool invocation.",
    )
    risk_level: RiskLevel = Field(description="Aggregate risk classification for the assessed action.")
    factors: list[RiskFactor] = Field(
        default_factory=list,
        description="Ordered list of risk factors that contributed to the decision.",
    )
    decision: RiskDecisionType = Field(
        description="The engine's disposition: allow, allow_with_limits, require_human_approval, or deny."
    )
    required_controls: list[str] = Field(
        default_factory=list,
        description="Control identifiers that must be active when decision is 'allow_with_limits'.",
    )
    denial_reason: str | None = Field(
        default=None,
        description="Mandatory explanation when decision is 'deny'.",
    )
    approval_request_id: str | None = Field(
        default=None,
        description="Identifier of the created approval request when decision is 'require_human_approval'.",
    )
    assessed_at: datetime = Field(description="UTC timestamp when the assessment was completed.")
    expires_at: datetime | None = Field(
        default=None,
        description="UTC timestamp after which this decision must be re-evaluated. None means no expiry.",
    )
    is_deterministic: bool = Field(
        default=True,
        description="False when an LLM was used during the assessment, indicating non-deterministic output.",
    )


class RiskContext(BaseModel):
    """Contextual metadata passed to the risk engine alongside a proposed action."""

    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(description="Identifier of the user who initiated the top-level execution.")
    execution_id: str = Field(description="Identifier of the active execution.")
    agent_id: str = Field(description="Identifier of the agent requesting the risk assessment.")
    delegation_depth: int = Field(
        default=0, ge=0,
        description="Delegation depth of the requesting agent within the execution hierarchy.",
    )
    previous_risk_decisions: list[str] = Field(
        default_factory=list,
        description="risk_assessment_ids of earlier decisions made within this execution.",
    )
    accumulated_risk_score: float = Field(
        default=0.0, ge=0.0,
        description="Running total of risk scores from all prior decisions in this execution.",
    )
    data_sensitivity: str = Field(
        default="internal",
        description="Effective data-sensitivity level of the current execution context.",
    )
    requires_audit: bool = Field(
        default=True,
        description="When True, all risk decisions in this context are persisted to the audit log.",
    )
