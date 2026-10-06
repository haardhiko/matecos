"""
Supabase Database Client & Repository Service for OrchaDeck.
Handles persistent storage of user profiles, repositories, tool imports, and tools
with complete user isolation and robust error handling.
"""

from __future__ import annotations

import uuid
from datetime import datetime, UTC
from typing import Any

import structlog
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database import get_session
from src.infrastructure.models import (
    UserProfile,
    RepositoryRecord,
    ToolImportRecord,
    UserToolRecord,
)

logger = structlog.get_logger(__name__)


class SupabaseStorageService:
    """Service interacting with Supabase PostgreSQL tables."""

    @staticmethod
    async def ensure_user_profile(
        user: dict[str, Any],
        session: AsyncSession | None = None,
    ) -> UserProfile:
        """Upsert user profile record from authenticated JWT token user."""
        user_id = str(user.get("id"))
        email = user.get("email")
        full_name = user.get("full_name")
        avatar_url = user.get("avatar_url")
        provider = user.get("provider", "github")

        async def _do(s: AsyncSession) -> UserProfile:
            stmt = select(UserProfile).where(UserProfile.id == user_id)
            res = await s.execute(stmt)
            profile = res.scalar_one_or_none()
            if not profile:
                profile = UserProfile(
                    id=user_id,
                    email=email,
                    full_name=full_name,
                    avatar_url=avatar_url,
                    provider=provider,
                )
                s.add(profile)
                await s.flush()
            else:
                if email and profile.email != email:
                    profile.email = email
                if full_name and profile.full_name != full_name:
                    profile.full_name = full_name
                if avatar_url and profile.avatar_url != avatar_url:
                    profile.avatar_url = avatar_url
                await s.flush()
            return profile

        if session is not None:
            return await _do(session)

        async with get_session() as s:
            return await _do(s)

    @staticmethod
    async def record_import_start(
        user_id: str,
        repo_name: str,
        repo_url: str,
        branch: str,
    ) -> tuple[str, str]:
        """Create or update repository record with status='processing' and record an import entry.

        Returns:
            Tuple of (repository_id, import_id).
        """
        async with get_session() as s:
            # Upsert repository
            stmt = select(RepositoryRecord).where(
                RepositoryRecord.user_id == user_id,
                RepositoryRecord.name == repo_name,
            )
            res = await s.execute(stmt)
            repo = res.scalar_one_or_none()
            now = datetime.now(UTC)

            if not repo:
                repo_id = str(uuid.uuid4())
                repo = RepositoryRecord(
                    id=repo_id,
                    user_id=user_id,
                    name=repo_name,
                    url=repo_url,
                    branch=branch,
                    status="processing",
                    created_at=now,
                    updated_at=now,
                )
                s.add(repo)
            else:
                repo.url = repo_url
                repo.branch = branch
                repo.status = "processing"
                repo.updated_at = now
                repo_id = repo.id

            await s.flush()

            import_id = str(uuid.uuid4())
            import_record = ToolImportRecord(
                id=import_id,
                user_id=user_id,
                repository_id=repo_id,
                status="processing",
                created_at=now,
            )
            s.add(import_record)
            await s.flush()
            return repo_id, import_id

    @staticmethod
    async def record_import_complete(
        user_id: str,
        repo_id: str,
        import_id: str,
        report_data: dict[str, Any],
        registered_manifests: list[dict[str, Any]],
    ) -> None:
        """Persist successful or partial import results, updating repository, import run, and user_tools."""
        async with get_session() as s:
            # 1. Update Repository
            stmt_repo = select(RepositoryRecord).where(
                RepositoryRecord.id == repo_id,
                RepositoryRecord.user_id == user_id,
            )
            res_repo = await s.execute(stmt_repo)
            repo = res_repo.scalar_one_or_none()
            if repo:
                repo.status = report_data.get("status", "success")
                repo.total_files = report_data.get("total_files", 0)
                repo.languages = report_data.get("languages", [])
                repo.frameworks = report_data.get("detected_frameworks", [])
                repo.updated_at = datetime.now(UTC)

            # 2. Update Tool Import
            stmt_imp = select(ToolImportRecord).where(
                ToolImportRecord.id == import_id,
                ToolImportRecord.user_id == user_id,
            )
            res_imp = await s.execute(stmt_imp)
            imp = res_imp.scalar_one_or_none()
            if imp:
                imp.status = report_data.get("status", "success")
                imp.tools_discovered = report_data.get("tools_discovered", 0)
                imp.tools_registered = report_data.get("tools_registered", 0)
                imp.tools_failed = report_data.get("tools_failed", 0)
                imp.dependency_status = report_data.get("dependency_status", "unknown")
                imp.errors = report_data.get("errors", [])
                imp.duration_ms = report_data.get("duration_ms", 0)

            # 3. Clean up previously registered tools for this repo under this user
            await s.execute(
                delete(UserToolRecord).where(
                    UserToolRecord.repository_id == repo_id,
                    UserToolRecord.user_id == user_id,
                )
            )

            # 4. Insert extracted tools into user_tools
            for t in registered_manifests:
                tool_uid = str(uuid.uuid4())
                ut = UserToolRecord(
                    id=tool_uid,
                    user_id=user_id,
                    repository_id=repo_id,
                    tool_id=t["tool_id"],
                    name=t.get("name", t["tool_id"]),
                    description=t.get("description", ""),
                    capabilities=t.get("capabilities", []),
                    language=t.get("language", "unknown"),
                    entry_point=t.get("entry_point", ""),
                    invocation_method=t.get("invocation_method", "builtin"),
                    risk_level=t.get("risk_level", "low"),
                    version=t.get("version", "0.1.0"),
                    source_url=t.get("source_url") or report_data.get("url"),
                    manifest_json=t.get("manifest_json", {}),
                    is_active=True,
                )
                s.add(ut)

            await s.flush()

    @staticmethod
    async def record_import_failed(
        user_id: str,
        repo_id: str,
        import_id: str,
        error_message: str,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        """Preserve full error telemetry when import completely fails."""
        async with get_session() as s:
            stmt_repo = select(RepositoryRecord).where(
                RepositoryRecord.id == repo_id,
                RepositoryRecord.user_id == user_id,
            )
            res_repo = await s.execute(stmt_repo)
            repo = res_repo.scalar_one_or_none()
            if repo:
                repo.status = "failed"
                repo.updated_at = datetime.now(UTC)

            stmt_imp = select(ToolImportRecord).where(
                ToolImportRecord.id == import_id,
                ToolImportRecord.user_id == user_id,
            )
            res_imp = await s.execute(stmt_imp)
            imp = res_imp.scalar_one_or_none()
            if imp:
                imp.status = "failed"
                imp.errors = errors or [{"error": error_message, "stage": "import"}]
            await s.flush()

    @staticmethod
    async def list_user_repos(user_id: str) -> list[dict[str, Any]]:
        """List repositories owned by the user, including extracted tools and import state."""
        async with get_session() as s:
            stmt = select(RepositoryRecord).where(
                RepositoryRecord.user_id == user_id
            ).order_by(RepositoryRecord.created_at.desc())
            res = await s.execute(stmt)
            repos = res.scalars().all()

            results: list[dict[str, Any]] = []
            for r in repos:
                # Fetch tools for each repo
                tools_stmt = select(UserToolRecord).where(
                    UserToolRecord.repository_id == r.id,
                    UserToolRecord.user_id == user_id,
                )
                t_res = await s.execute(tools_stmt)
                tools = t_res.scalars().all()

                results.append({
                    "id": r.id,
                    "name": r.name,
                    "url": r.url,
                    "branch": r.branch,
                    "status": r.status,
                    "total_files": r.total_files,
                    "languages": r.languages,
                    "frameworks": r.frameworks,
                    "tools": [
                        {
                            "tool_id": t.tool_id,
                            "name": t.name,
                            "description": t.description,
                            "capabilities": t.capabilities,
                            "language": t.language,
                            "risk_level": t.risk_level,
                        }
                        for t in tools
                    ],
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                })
            return results

    @staticmethod
    async def delete_user_repo(user_id: str, repo_name: str) -> tuple[bool, str, list[str]]:
        """Delete repository record owned by user and return (success, repo_url, deleted_tool_ids)."""
        async with get_session() as s:
            stmt = select(RepositoryRecord).where(
                RepositoryRecord.user_id == user_id,
                RepositoryRecord.name == repo_name,
            )
            res = await s.execute(stmt)
            repo = res.scalar_one_or_none()
            if not repo:
                return False, "", []

            repo_url = repo.url

            # Get tool IDs
            tools_stmt = select(UserToolRecord.tool_id).where(
                UserToolRecord.repository_id == repo.id,
                UserToolRecord.user_id == user_id,
            )
            t_res = await s.execute(tools_stmt)
            tool_ids = list(t_res.scalars().all())

            # Delete repository (cascades imports and tools)
            await s.delete(repo)
            await s.flush()
            return True, repo_url, tool_ids

    @staticmethod
    async def list_user_tools(user_id: str) -> list[dict[str, Any]]:
        """List active tools owned by user."""
        async with get_session() as s:
            stmt = select(UserToolRecord).where(
                UserToolRecord.user_id == user_id,
                UserToolRecord.is_active == True,  # noqa: E712
            ).order_by(UserToolRecord.created_at.desc())
            res = await s.execute(stmt)
            tools = res.scalars().all()
            return [
                {
                    "tool_id": t.tool_id,
                    "name": t.name,
                    "description": t.description,
                    "capabilities": t.capabilities,
                    "risk_level": t.risk_level,
                    "version": t.version,
                    "runtime_type": t.invocation_method,
                    "source": t.source_url or "github",
                    "language": t.language,
                }
                for t in tools
            ]
