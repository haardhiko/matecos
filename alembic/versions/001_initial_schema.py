"""initial schema

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-09-22 17:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # executions
    op.create_table(
        "executions",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("request_id", sa.String(length=26), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("goal_text", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "CREATED",
                "VALIDATING",
                "PLANNING",
                "AWAITING_APPROVAL",
                "RUNNING",
                "PARTIALLY_COMPLETED",
                "VERIFYING",
                "COMPLETED",
                "FAILED",
                "CANCELLED",
                "TIMED_OUT",
                name="execution_status",
            ),
            nullable=False,
        ),
        sa.Column("plan_id", sa.String(length=26), nullable=True),
        sa.Column("idempotency_key", sa.String(length=256), nullable=True),
        sa.Column("total_cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("constraints", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_executions_user_id", "executions", ["user_id"])
    op.create_index("ix_executions_status", "executions", ["status"])
    op.create_index("ix_executions_created_at", "executions", ["created_at"])
    op.create_index("ix_executions_request_id", "executions", ["request_id"])

    # tasks
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("execution_id", sa.String(length=26), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("dependencies", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("required_capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("acceptance_criteria", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("assigned_agent_id", sa.String(length=26), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["execution_id"], ["executions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasks_execution_id", "tasks", ["execution_id"])
    op.create_index("ix_tasks_status", "tasks", ["status"])

    # agents
    op.create_table(
        "agents",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("execution_id", sa.String(length=26), nullable=False),
        sa.Column("task_id", sa.String(length=26), nullable=True),
        sa.Column("parent_agent_id", sa.String(length=26), nullable=True),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "CREATED",
                "READY",
                "RUNNING",
                "WAITING_FOR_DEPENDENCY",
                "WAITING_FOR_APPROVAL",
                "RETRYING",
                "COMPLETED",
                "FAILED",
                "CANCELLED",
                name="agent_status",
            ),
            nullable=False,
        ),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("allowed_tools", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("permission_scope", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("iteration_budget", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("iterations_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("time_budget_seconds", sa.Integer(), nullable=False, server_default="3600"),
        sa.Column("cost_budget_usd", sa.Float(), nullable=False, server_default="5.0"),
        sa.Column("cost_used_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_calls_made", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("decision_records", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["execution_id"], ["executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["parent_agent_id"], ["agents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agents_execution_id", "agents", ["execution_id"])
    op.create_index("ix_agents_status", "agents", ["status"])
    op.create_index("ix_agents_role", "agents", ["role"])

    # tool_invocations
    op.create_table(
        "tool_invocations",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("execution_id", sa.String(length=26), nullable=False),
        sa.Column("agent_id", sa.String(length=26), nullable=False),
        sa.Column("tool_id", sa.String(length=128), nullable=False),
        sa.Column("tool_version", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "REQUESTED",
                "RISK_CHECKING",
                "APPROVED",
                "RUNNING",
                "SUCCEEDED",
                "FAILED",
                "TIMED_OUT",
                "BLOCKED",
                name="tool_invocation_status",
            ),
            nullable=False,
        ),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "risk_level",
            sa.Enum("LOW", "MEDIUM", "HIGH", "CRITICAL", name="risk_level"),
            nullable=False,
        ),
        sa.Column("risk_assessment_id", sa.String(length=26), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("idempotency_key", sa.String(length=256), nullable=True),
        sa.Column("resource_usage", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["execution_id"], ["executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tool_invocations_execution_id", "tool_invocations", ["execution_id"])
    op.create_index("ix_tool_invocations_agent_id", "tool_invocations", ["agent_id"])

    # audit_events (append-only)
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("execution_id", sa.String(length=26), nullable=True),
        sa.Column("task_id", sa.String(length=26), nullable=True),
        sa.Column("agent_id", sa.String(length=26), nullable=True),
        sa.Column("tool_invocation_id", sa.String(length=26), nullable=True),
        sa.Column("parent_event_id", sa.String(length=26), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=256), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sensitivity", sa.String(length=32), nullable=False, server_default="internal"),
        sa.Column("trace_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("span_id", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("request_id", sa.String(length=32), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["execution_id"], ["executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_event_id"], ["audit_events.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_execution_id", "audit_events", ["execution_id"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])

    # approval_requests
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("execution_id", sa.String(length=26), nullable=False),
        sa.Column("agent_id", sa.String(length=26), nullable=True),
        sa.Column("action_description", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("risk_assessment_id", sa.String(length=26), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=256), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["execution_id"], ["executions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # tool_records
    op.create_table(
        "tool_records",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("tool_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("input_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("requires_sandbox", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("cost_estimate_usd", sa.Float(), nullable=False, server_default="0.001"),
        sa.Column("health_status", sa.String(length=16), nullable=False, server_default="unknown"),
        sa.Column("source_repo", sa.String(length=512), nullable=True),
        sa.Column("source_commit", sa.String(length=64), nullable=True),
        sa.Column("owner", sa.String(length=256), nullable=False, server_default="system"),
        sa.Column("is_available", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("manifest_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "registered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # episodic_memories
    op.create_table(
        "episodic_memories",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("execution_id", sa.String(length=26), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("key_decisions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tools_used", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("failures", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "verification_status", sa.String(length=32), nullable=False, server_default="unverified"
        ),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("lessons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sensitivity", sa.String(length=32), nullable=False, server_default="internal"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["execution_id"], ["executions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("execution_id"),
    )

    # working_memory_snapshots
    op.create_table(
        "working_memory_snapshots",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("execution_id", sa.String(length=26), nullable=False),
        sa.Column("agent_id", sa.String(length=26), nullable=True),
        sa.Column("snapshot_type", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["execution_id"], ["executions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("working_memory_snapshots")
    op.drop_table("episodic_memories")
    op.drop_table("tool_records")
    op.drop_table("approval_requests")
    op.drop_table("audit_events")
    op.drop_table("tool_invocations")
    op.drop_table("agents")
    op.drop_table("tasks")
    op.drop_table("executions")
    op.execute("DROP TYPE IF EXISTS execution_status")
    op.execute("DROP TYPE IF EXISTS agent_status")
    op.execute("DROP TYPE IF EXISTS tool_invocation_status")
    op.execute("DROP TYPE IF EXISTS risk_level")
