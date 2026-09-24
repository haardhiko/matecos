"""SQLAlchemy ORM models for MATECOS — all tables in one module."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database import Base

# ── Enums ─────────────────────────────────────────────────────────────────────


class ExecutionStatusEnum(str, enum.Enum):
    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    PLANNING = "PLANNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    RUNNING = "RUNNING"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


class AgentStatusEnum(str, enum.Enum):
    CREATED = "CREATED"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_FOR_DEPENDENCY = "WAITING_FOR_DEPENDENCY"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    RETRYING = "RETRYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ToolInvocationStatusEnum(str, enum.Enum):
    REQUESTED = "REQUESTED"
    RISK_CHECKING = "RISK_CHECKING"
    APPROVED = "APPROVED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    BLOCKED = "BLOCKED"


class RiskLevelEnum(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class EventTypeEnum(str, enum.Enum):
    REQUEST_RECEIVED = "REQUEST_RECEIVED"
    VALIDATION_STARTED = "VALIDATION_STARTED"
    VALIDATION_PASSED = "VALIDATION_PASSED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    PLAN_CREATED = "PLAN_CREATED"
    PLAN_REVISION = "PLAN_REVISION"
    AGENT_SPAWNED = "AGENT_SPAWNED"
    AGENT_COMPLETED = "AGENT_COMPLETED"
    AGENT_FAILED = "AGENT_FAILED"
    AGENT_CANCELLED = "AGENT_CANCELLED"
    TOOL_SELECTED = "TOOL_SELECTED"
    RISK_ASSESSED = "RISK_ASSESSED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    TOOL_INVOKED = "TOOL_INVOKED"
    TOOL_COMPLETED = "TOOL_COMPLETED"
    TOOL_FAILED = "TOOL_FAILED"
    TOOL_TIMED_OUT = "TOOL_TIMED_OUT"
    VALIDATION_RESULT = "VALIDATION_RESULT"
    RETRY_ATTEMPT = "RETRY_ATTEMPT"
    FINAL_SYNTHESIS = "FINAL_SYNTHESIS"
    VERIFICATION_STARTED = "VERIFICATION_STARTED"
    VERIFICATION_COMPLETED = "VERIFICATION_COMPLETED"
    EXECUTION_COMPLETED = "EXECUTION_COMPLETED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    EXECUTION_CANCELLED = "EXECUTION_CANCELLED"
    EXECUTION_TIMED_OUT = "EXECUTION_TIMED_OUT"
    STATE_TRANSITION = "STATE_TRANSITION"
    ERROR = "ERROR"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    DECISION_RECORD = "DECISION_RECORD"


# ── ORM Models ────────────────────────────────────────────────────────────────


class Execution(Base):
    """Top-level execution record corresponding to a user request."""

    __tablename__ = "executions"
    __table_args__ = (
        Index("ix_executions_user_id", "user_id"),
        Index("ix_executions_status", "status"),
        Index("ix_executions_created_at", "created_at"),
        Index("ix_executions_request_id", "request_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(26), nullable=False, unique=True)
    user_id: Mapped[str] = mapped_column(String(256), nullable=False)
    goal_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ExecutionStatusEnum] = mapped_column(
        Enum(ExecutionStatusEnum, name="execution_status"),
        nullable=False,
        default=ExecutionStatusEnum.CREATED,
    )
    plan_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(256), nullable=True, unique=True)
    total_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    constraints: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, name="metadata"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    tasks: Mapped[list[Task]] = relationship(
        "Task", back_populates="execution", cascade="all, delete-orphan"
    )
    agents: Mapped[list[AgentRecord]] = relationship(
        "AgentRecord", back_populates="execution", cascade="all, delete-orphan"
    )
    audit_events: Mapped[list[AuditEvent]] = relationship(
        "AuditEvent", back_populates="execution", cascade="all, delete-orphan"
    )
    approval_requests: Mapped[list[ApprovalRequest]] = relationship(
        "ApprovalRequest", back_populates="execution", cascade="all, delete-orphan"
    )


class Task(Base):
    """A single task node within an execution plan (task graph)."""

    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_execution_id", "execution_id"),
        Index("ix_tasks_status", "status"),
        Index("ix_tasks_assigned_agent_id", "assigned_agent_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    execution_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("executions.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    dependencies: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    required_capabilities: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    acceptance_criteria: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    assigned_agent_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, name="metadata"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    execution: Mapped[Execution] = relationship("Execution", back_populates="tasks")


class AgentRecord(Base):
    """Persistent record of an agent instance lifecycle."""

    __tablename__ = "agents"
    __table_args__ = (
        Index("ix_agents_execution_id", "execution_id"),
        Index("ix_agents_status", "status"),
        Index("ix_agents_role", "role"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    execution_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("executions.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    parent_agent_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[AgentStatusEnum] = mapped_column(
        Enum(AgentStatusEnum, name="agent_status"), nullable=False, default=AgentStatusEnum.CREATED
    )
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_tools: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    permission_scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    iteration_budget: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    iterations_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    time_budget_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=3600)
    cost_budget_usd: Mapped[float] = mapped_column(Float, nullable=False, default=5.0)
    cost_used_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_calls_made: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_records: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    execution: Mapped[Execution] = relationship("Execution", back_populates="agents")
    tool_invocations: Mapped[list[ToolInvocation]] = relationship(
        "ToolInvocation", back_populates="agent"
    )


class ToolInvocation(Base):
    """Record of a single tool invocation."""

    __tablename__ = "tool_invocations"
    __table_args__ = (
        Index("ix_tool_invocations_execution_id", "execution_id"),
        Index("ix_tool_invocations_agent_id", "agent_id"),
        Index("ix_tool_invocations_tool_id", "tool_id"),
        Index("ix_tool_invocations_status", "status"),
        Index("ix_tool_invocations_idempotency_key", "idempotency_key"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    execution_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("executions.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    tool_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[ToolInvocationStatusEnum] = mapped_column(
        Enum(ToolInvocationStatusEnum, name="tool_invocation_status"),
        nullable=False,
        default=ToolInvocationStatusEnum.REQUESTED,
    )
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    risk_level: Mapped[RiskLevelEnum] = mapped_column(
        Enum(RiskLevelEnum, name="risk_level"), nullable=False, default=RiskLevelEnum.LOW
    )
    risk_assessment_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    resource_usage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, name="metadata"
    )

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    agent: Mapped[AgentRecord] = relationship("AgentRecord", back_populates="tool_invocations")


class AuditEvent(Base):
    """
    Append-only audit event log.

    IMPORTANT: This table must never be updated or deleted from application code.
    It is the immutable source of truth for all state transitions and decisions.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_execution_id", "execution_id"),
        Index("ix_audit_events_agent_id", "agent_id"),
        Index("ix_audit_events_event_type", "event_type"),
        Index("ix_audit_events_created_at", "created_at"),
        Index("ix_audit_events_parent_event_id", "parent_event_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    execution_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("executions.id", ondelete="CASCADE"), nullable=True
    )
    task_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    tool_invocation_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    parent_event_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("audit_events.id"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_type: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # user | agent | system | tool
    actor_id: Mapped[str] = mapped_column(String(256), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    sensitivity: Mapped[str] = mapped_column(
        String(32), nullable=False, default="internal"
    )  # public | internal | sensitive
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    span_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    request_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    execution: Mapped[Execution | None] = relationship("Execution", back_populates="audit_events")


class ApprovalRequest(Base):
    """Pending human approval request for high-risk actions."""

    __tablename__ = "approval_requests"
    __table_args__ = (
        Index("ix_approval_requests_execution_id", "execution_id"),
        Index("ix_approval_requests_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    execution_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("executions.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    action_description: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    risk_assessment_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )  # pending | approved | rejected
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    execution: Mapped[Execution] = relationship("Execution", back_populates="approval_requests")


class ToolRecord(Base):
    """Registered tool manifest record."""

    __tablename__ = "tool_records"
    __table_args__ = (
        Index("ix_tool_records_is_available", "is_available"),
        Index("ix_tool_records_health_status", "health_status"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)  # "{tool_id}:{version}"
    tool_id: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    requires_sandbox: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=120)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    cost_estimate_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.001)
    health_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    source_repo: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner: Mapped[str] = mapped_column(String(256), nullable=False, default="system")
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class EpisodicMemory(Base):
    """Summary of a completed execution for episodic memory retrieval."""

    __tablename__ = "episodic_memories"
    __table_args__ = (
        Index("ix_episodic_memories_user_id", "user_id"),
        Index("ix_episodic_memories_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    execution_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("executions.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    user_id: Mapped[str] = mapped_column(String(256), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    key_decisions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    tools_used: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    result_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    failures: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    verification_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unverified"
    )
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    lessons: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    sensitivity: Mapped[str] = mapped_column(String(32), nullable=False, default="internal")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkingMemorySnapshot(Base):
    """Point-in-time snapshot of agent working memory (with TTL)."""

    __tablename__ = "working_memory_snapshots"
    __table_args__ = (
        Index("ix_working_memory_execution_id", "execution_id"),
        Index("ix_working_memory_agent_id", "agent_id"),
        Index("ix_working_memory_expires_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    execution_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("executions.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    snapshot_type: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # context | state | result
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
