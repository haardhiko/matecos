"""supabase persistent storage and rls

Revision ID: 002_supabase_storage_rls
Revises: 001_initial_schema
Create Date: 2026-10-06 12:45:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "002_supabase_storage_rls"
down_revision: str | None = "001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. user_profiles
    op.create_table(
        "user_profiles",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=256), nullable=True),
        sa.Column("full_name", sa.String(length=256), nullable=True),
        sa.Column("avatar_url", sa.String(length=512), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False, server_default="github"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_user_profiles"),
    )
    op.create_index("ix_user_profiles_email", "user_profiles", ["email"])

    # 2. repositories
    op.create_table(
        "repositories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("url", sa.String(length=512), nullable=False),
        sa.Column("branch", sa.String(length=128), nullable=False, server_default="main"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("total_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("languages", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("frameworks", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_repositories"),
        sa.ForeignKeyConstraint(["user_id"], ["user_profiles.id"], name="fk_repositories_user_id_user_profiles", ondelete="CASCADE"),
    )
    op.create_index("ix_repositories_user_id", "repositories", ["user_id"])
    op.create_index("ix_repositories_name", "repositories", ["name"])

    # 3. tool_imports
    op.create_table(
        "tool_imports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("repository_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("tools_discovered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tools_registered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tools_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dependency_status", sa.String(length=64), nullable=False, server_default="unknown"),
        sa.Column("errors", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tool_imports"),
        sa.ForeignKeyConstraint(["user_id"], ["user_profiles.id"], name="fk_tool_imports_user_id_user_profiles", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], name="fk_tool_imports_repository_id_repositories", ondelete="CASCADE"),
    )
    op.create_index("ix_tool_imports_user_id", "tool_imports", ["user_id"])
    op.create_index("ix_tool_imports_repository_id", "tool_imports", ["repository_id"])
    op.create_index("ix_tool_imports_status", "tool_imports", ["status"])

    # 4. user_tools
    op.create_table(
        "user_tools",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("repository_id", sa.String(length=36), nullable=True),
        sa.Column("tool_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("language", sa.String(length=64), nullable=False, server_default="unknown"),
        sa.Column("entry_point", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("invocation_method", sa.String(length=64), nullable=False, server_default="builtin"),
        sa.Column("risk_level", sa.String(length=16), nullable=False, server_default="low"),
        sa.Column("version", sa.String(length=32), nullable=False, server_default="0.1.0"),
        sa.Column("source_url", sa.String(length=512), nullable=True),
        sa.Column("manifest_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_user_tools"),
        sa.ForeignKeyConstraint(["user_id"], ["user_profiles.id"], name="fk_user_tools_user_id_user_profiles", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], name="fk_user_tools_repository_id_repositories", ondelete="CASCADE"),
    )
    op.create_index("ix_user_tools_user_id", "user_tools", ["user_id"])
    op.create_index("ix_user_tools_tool_id", "user_tools", ["tool_id"])
    op.create_index("ix_user_tools_repository_id", "user_tools", ["repository_id"])

    # Enable Row Level Security (RLS) on user tables
    op.execute("ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE repositories ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE tool_imports ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE user_tools ENABLE ROW LEVEL SECURITY;")

    # RLS Policies using Supabase auth.uid()
    op.execute("""
        CREATE POLICY user_profiles_select_policy ON user_profiles
            FOR SELECT USING (id = auth.uid()::text);
        CREATE POLICY user_profiles_insert_policy ON user_profiles
            FOR INSERT WITH CHECK (id = auth.uid()::text);
        CREATE POLICY user_profiles_update_policy ON user_profiles
            FOR UPDATE USING (id = auth.uid()::text);
    """)

    op.execute("""
        CREATE POLICY repositories_select_policy ON repositories
            FOR SELECT USING (user_id = auth.uid()::text);
        CREATE POLICY repositories_insert_policy ON repositories
            FOR INSERT WITH CHECK (user_id = auth.uid()::text);
        CREATE POLICY repositories_update_policy ON repositories
            FOR UPDATE USING (user_id = auth.uid()::text);
        CREATE POLICY repositories_delete_policy ON repositories
            FOR DELETE USING (user_id = auth.uid()::text);
    """)

    op.execute("""
        CREATE POLICY tool_imports_select_policy ON tool_imports
            FOR SELECT USING (user_id = auth.uid()::text);
        CREATE POLICY tool_imports_insert_policy ON tool_imports
            FOR INSERT WITH CHECK (user_id = auth.uid()::text);
        CREATE POLICY tool_imports_delete_policy ON tool_imports
            FOR DELETE USING (user_id = auth.uid()::text);
    """)

    op.execute("""
        CREATE POLICY user_tools_select_policy ON user_tools
            FOR SELECT USING (user_id = auth.uid()::text);
        CREATE POLICY user_tools_insert_policy ON user_tools
            FOR INSERT WITH CHECK (user_id = auth.uid()::text);
        CREATE POLICY user_tools_update_policy ON user_tools
            FOR UPDATE USING (user_id = auth.uid()::text);
        CREATE POLICY user_tools_delete_policy ON user_tools
            FOR DELETE USING (user_id = auth.uid()::text);
    """)


def downgrade() -> None:
    op.drop_table("user_tools")
    op.drop_table("tool_imports")
    op.drop_table("repositories")
    op.drop_table("user_profiles")
