"""API request and response schemas for the request submission endpoint."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AttachmentRef(BaseModel):
    """Reference to a file attachment associated with a request."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(description="Unique identifier for the attachment.")
    type: Literal["csv", "json", "pdf", "txt", "xlsx", "image", "archive"] = Field(
        description="MIME category of the attachment."
    )
    uri: str = Field(description="Secure URI where the attachment can be retrieved.")
    size_bytes: int | None = Field(default=None, ge=0, description="File size in bytes, if known.")
    content_type: str | None = Field(
        default=None, description="MIME type string, e.g. 'application/pdf'."
    )

    @field_validator("uri", mode="before")
    @classmethod
    def uri_must_be_secure(cls, v: str) -> str:
        """Reject URIs that do not use the internal secure:// scheme."""
        if not v.startswith("secure://"):
            raise ValueError(
                "uri must start with 'secure://' — raw external URIs are not permitted"
            )
        return v


class ExecutionConstraints(BaseModel):
    """Caller-supplied execution limits and capability restrictions."""

    model_config = ConfigDict(populate_by_name=True)

    max_cost: float = Field(
        default=10.0,
        ge=0.0,
        le=1000.0,
        description="Maximum total spend in USD for this execution.",
    )
    max_duration_seconds: int = Field(
        default=3600,
        ge=1,
        le=86400,
        description="Wall-clock timeout in seconds (max 24 h).",
    )
    max_agent_iterations: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Maximum LLM reasoning iterations summed across all agents.",
    )
    max_tool_calls: int = Field(
        default=200,
        ge=1,
        le=2000,
        description="Maximum tool invocations summed across all agents.",
    )
    max_agent_depth: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum delegation depth in the agent hierarchy.",
    )
    requires_approval_for_external_actions: bool = Field(
        default=True,
        description="When True, actions with external side-effects require human approval.",
    )
    requires_approval_for_code_execution: bool = Field(
        default=False,
        description="When True, all code execution tool calls require human approval.",
    )
    allowed_tool_capabilities: list[str] | None = Field(
        default=None,
        description="Explicit allow-list of capability tags. None means all capabilities are permitted.",
    )
    denied_tool_capabilities: list[str] = Field(
        default_factory=list,
        description="Capability tags that are explicitly forbidden for this execution.",
    )
    data_sensitivity: Literal["public", "internal", "confidential", "restricted"] = Field(
        default="internal",
        description="Maximum data-sensitivity level permitted for tools accessed in this execution.",
    )


class SubmitRequestBody(BaseModel):
    """Payload for the POST /requests endpoint."""

    model_config = ConfigDict(populate_by_name=True)

    text: str = Field(
        min_length=1,
        max_length=10000,
        description="Natural-language goal statement submitted by the caller. Treated as untrusted data.",
    )
    attachments: list[AttachmentRef] = Field(
        default_factory=list,
        description="Optional file attachments referenced by the request.",
    )
    constraints: ExecutionConstraints = Field(
        default_factory=ExecutionConstraints,
        description="Execution limits and permission constraints for this request.",
    )
    idempotency_key: str | None = Field(
        default=None,
        description="Client-supplied deduplication key. Repeated submissions with the same key return the original response.",
    )

    @field_validator("text", mode="before")
    @classmethod
    def text_is_not_injection(cls, v: str) -> str:
        """Strip surrounding whitespace.

        This is a minimal sanitisation pass only. All injection-risk analysis
        is delegated to the risk engine; the text value must never be treated
        as instructions by any downstream component.
        """
        if isinstance(v, str):
            return v.strip()
        return v


class SubmitRequestResponse(BaseModel):
    """Response returned immediately after a request is accepted."""

    model_config = ConfigDict(populate_by_name=True)

    request_id: str = Field(description="Stable identifier for the submitted request.")
    execution_id: str = Field(description="Identifier for the execution created from this request.")
    status: str = Field(description="Initial lifecycle status, typically 'CREATED'.")
    status_url: str = Field(description="URL the caller can poll for execution progress.")
    created_at: datetime = Field(description="UTC timestamp when the request was accepted.")
    estimated_duration_seconds: int | None = Field(
        default=None,
        ge=0,
        description="Optional estimated wall-clock duration in seconds.",
    )


class TaskStatusItem(BaseModel):
    """Summary of a single task within an execution."""

    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(description="Unique task identifier.")
    title: str = Field(description="Human-readable task title.")
    status: str = Field(description="Current lifecycle status of the task.")
    role: str = Field(description="Agent role responsible for this task.")
    dependencies: list[str] = Field(
        default_factory=list,
        description="task_ids that must complete before this task can start.",
    )
    started_at: datetime | None = Field(
        default=None, description="UTC timestamp when execution began."
    )
    completed_at: datetime | None = Field(
        default=None, description="UTC timestamp when the task finished."
    )
    error: str | None = Field(default=None, description="Error message if the task failed.")


class AgentStatusItem(BaseModel):
    """Runtime statistics for a single agent within an execution."""

    model_config = ConfigDict(populate_by_name=True)

    agent_id: str = Field(description="Unique agent instance identifier.")
    role: str = Field(description="Agent role label.")
    status: str = Field(description="Current lifecycle status of the agent.")
    iterations_used: int = Field(ge=0, description="Number of reasoning iterations consumed.")
    cost_used_usd: float = Field(ge=0.0, description="Estimated spend in USD so far.")


class PendingApproval(BaseModel):
    """A human-approval checkpoint that is blocking further execution."""

    model_config = ConfigDict(populate_by_name=True)

    approval_id: str = Field(description="Unique identifier for this approval request.")
    action_description: str = Field(
        description="Structured, human-readable description of the action awaiting approval."
    )
    risk_level: str = Field(
        description="Risk level assigned by the risk engine (LOW/MEDIUM/HIGH/CRITICAL)."
    )
    requested_at: datetime = Field(description="UTC timestamp when the approval was requested.")


class ExecutionStatusResponse(BaseModel):
    """Full status snapshot of an execution, returned by GET /executions/{id}."""

    model_config = ConfigDict(populate_by_name=True)

    execution_id: str = Field(description="Unique execution identifier.")
    request_id: str = Field(description="The originating request identifier.")
    status: str = Field(description="Current lifecycle status of the execution.")
    goal_text: str = Field(
        description="The normalised goal text derived from the original request."
    )
    created_at: datetime = Field(description="UTC timestamp when the execution was created.")
    updated_at: datetime = Field(description="UTC timestamp of the most recent status change.")
    started_at: datetime | None = Field(
        default=None, description="UTC timestamp when processing began."
    )
    completed_at: datetime | None = Field(
        default=None, description="UTC timestamp when the execution finished."
    )
    tasks: list[TaskStatusItem] = Field(default_factory=list, description="Per-task status items.")
    agents: list[AgentStatusItem] = Field(
        default_factory=list, description="Per-agent runtime statistics."
    )
    result: dict | None = Field(
        default=None,
        description="Structured result payload. Only populated when status is COMPLETED.",
    )
    error: str | None = Field(
        default=None, description="Top-level error message if the execution failed."
    )
    total_cost_usd: float | None = Field(
        default=None, ge=0.0, description="Accumulated spend in USD for the entire execution."
    )
    warnings: list[str] = Field(
        default_factory=list, description="Non-fatal warnings generated during execution."
    )
    pending_approvals: list[PendingApproval] = Field(
        default_factory=list,
        description="Approval checkpoints that are currently blocking execution.",
    )


class ApproveRequest(BaseModel):
    """Body for approving a pending human-approval checkpoint."""

    model_config = ConfigDict(populate_by_name=True)

    notes: str | None = Field(
        default=None, description="Optional reviewer notes recorded in the audit log."
    )


class RejectRequest(BaseModel):
    """Body for rejecting a pending human-approval checkpoint."""

    model_config = ConfigDict(populate_by_name=True)

    reason: str = Field(
        min_length=1,
        description="Mandatory explanation for the rejection, recorded in the audit log.",
    )
