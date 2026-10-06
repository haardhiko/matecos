"""
Unit and integration tests for Supabase Authentication, JWT validation,
and Supabase Storage Service (persistence, user isolation, RLS compatibility).
"""

import jwt
import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from src.api.dependencies.auth import get_current_user
from src.infrastructure.models import (
    Base,
    UserProfile,
    RepositoryRecord,
    ToolImportRecord,
    UserToolRecord,
)
from src.infrastructure.supabase_service import SupabaseStorageService


@pytest.mark.asyncio
async def test_get_current_user_dev_token():
    # Dev tokens should resolve cleanly
    user = await get_current_user(token="dev-test-token")
    assert "id" in user
    assert user.get("is_dev") is True


@pytest.mark.asyncio
async def test_get_current_user_supabase_jwt():
    secret = "super-secret-jwt-key-for-test-32b"
    now = int(time.time())
    payload = {
        "sub": "b2f67936-3a78-4395-93df-f9ba3c467ee9",
        "email": "user@example.com",
        "aud": "authenticated",
        "role": "authenticated",
        "exp": now + 3600,
        "user_metadata": {
            "user_name": "octocat",
            "avatar_url": "https://github.com/octocat.png",
            "full_name": "The Octocat",
        },
        "app_metadata": {
            "provider": "github"
        }
    }
    token = jwt.encode(payload, secret, algorithm="HS256")

    with patch("src.config.get_settings") as mock_get_settings:
        mock_settings = MagicMock()
        mock_settings.app_env = "production"
        mock_settings.supabase.jwt_secret = secret
        mock_settings.supabase.url = "https://test.supabase.co"
        mock_get_settings.return_value = mock_settings

        user = await get_current_user(token=token)
        assert user["id"] == "b2f67936-3a78-4395-93df-f9ba3c467ee9"
        assert user["email"] == "user@example.com"
        assert user["avatar_url"] == "https://github.com/octocat.png"
        assert user["full_name"] == "The Octocat"


@pytest.mark.asyncio
async def test_get_current_user_expired_jwt():
    secret = "super-secret-jwt-key-for-test-32b"
    past = int(time.time()) - 3600
    payload = {
        "sub": "b2f67936-3a78-4395-93df-f9ba3c467ee9",
        "email": "user@example.com",
        "exp": past,
    }
    token = jwt.encode(payload, secret, algorithm="HS256")

    with patch("src.config.get_settings") as mock_get_settings:
        mock_settings = MagicMock()
        mock_settings.app_env = "production"
        mock_settings.supabase.jwt_secret = secret
        mock_settings.supabase.url = "https://test.supabase.co"
        mock_get_settings.return_value = mock_settings

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(token=token)
        assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_supabase_storage_service_sqlite_memory():
    # Test full persistence and user isolation using an in-memory SQLite async engine
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    target_tables = [
        UserProfile.__table__,
        RepositoryRecord.__table__,
        ToolImportRecord.__table__,
        UserToolRecord.__table__,
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=target_tables))

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def mock_get_session():
        async with session_factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    with patch("src.infrastructure.supabase_service.get_session", mock_get_session):
        # 1. Profile sync for user 1
        user1_claims = {
            "id": "user-uuid-1",
            "email": "u1@example.com",
            "username": "u1github",
            "name": "User One",
            "avatar_url": "https://u1.png"
        }
        profile1 = await SupabaseStorageService.ensure_user_profile(user1_claims)
        assert profile1.id == "user-uuid-1"

        # Profile sync for user 2
        user2_claims = {
            "id": "user-uuid-2",
            "email": "u2@example.com",
            "username": "u2github",
            "name": "User Two",
            "avatar_url": "https://u2.png"
        }
        profile2 = await SupabaseStorageService.ensure_user_profile(user2_claims)
        assert profile2.id == "user-uuid-2"

        # 2. Record import start for user 1
        repo_id_1, import_id_1 = await SupabaseStorageService.record_import_start(
            user_id="user-uuid-1",
            repo_name="psf/requests",
            repo_url="https://github.com/psf/requests",
            branch="main",
        )
        assert repo_id_1 is not None
        assert import_id_1 is not None

        # 3. Complete import with tools for user 1
        manifests = [
            {
                "tool_id": "github.psf.requests.get",
                "name": "get",
                "description": "Send GET request",
                "version": "1.0.0",
                "language": "python",
                "entry_point": "requests.api:get",
                "source_url": "https://github.com/psf/requests",
                "manifest_json": {"type": "object"}
            },
            {
                "tool_id": "github.psf.requests.post",
                "name": "post",
                "description": "Send POST request",
                "version": "1.0.0",
                "language": "python",
                "entry_point": "requests.api:post",
                "source_url": "https://github.com/psf/requests",
                "manifest_json": {"type": "object"}
            }
        ]
        report_data = {
            "status": "success",
            "total_files": 45,
            "languages": ["python"],
            "detected_frameworks": [],
            "tools_discovered": 2,
            "tools_registered": 2,
            "tools_failed": 0,
            "dependency_status": "satisfied",
            "duration_ms": 1200,
        }
        await SupabaseStorageService.record_import_complete(
            user_id="user-uuid-1",
            repo_id=repo_id_1,
            import_id=import_id_1,
            report_data=report_data,
            registered_manifests=manifests,
        )

        # 4. Record failed import for user 2
        repo_id_2, import_id_2 = await SupabaseStorageService.record_import_start(
            user_id="user-uuid-2",
            repo_name="broken/broken-repo",
            repo_url="https://github.com/broken/broken-repo",
            branch="main",
        )
        await SupabaseStorageService.record_import_failed(
            user_id="user-uuid-2",
            repo_id=repo_id_2,
            import_id=import_id_2,
            error_message="Clone failed: DNS resolution error",
        )

        # 5. User isolation verification:
        user1_repos = await SupabaseStorageService.list_user_repos("user-uuid-1")
        assert len(user1_repos) == 1
        assert user1_repos[0]["name"] == "psf/requests"
        assert user1_repos[0]["status"] == "success"

        user1_tools = await SupabaseStorageService.list_user_tools("user-uuid-1")
        assert len(user1_tools) == 2
        tool_ids = [t["tool_id"] for t in user1_tools]
        assert "github.psf.requests.get" in tool_ids

        # User 2 should only see user 2's repos and tools
        user2_repos = await SupabaseStorageService.list_user_repos("user-uuid-2")
        assert len(user2_repos) == 1
        assert user2_repos[0]["name"] == "broken/broken-repo"
        assert user2_repos[0]["status"] == "failed"

        user2_tools = await SupabaseStorageService.list_user_tools("user-uuid-2")
        assert len(user2_tools) == 0

        # 6. Deletion test
        deleted, repo_url, deleted_tool_ids = await SupabaseStorageService.delete_user_repo("user-uuid-1", "psf/requests")
        assert deleted is True
        assert len(deleted_tool_ids) == 2
        user1_repos_after = await SupabaseStorageService.list_user_repos("user-uuid-1")
        assert len(user1_repos_after) == 0

    await engine.dispose()
