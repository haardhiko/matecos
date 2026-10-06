"""GitHub tool import pipeline — clone, index, extract, register."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from src.api.dependencies.auth import get_current_user
from src.infrastructure.supabase_service import SupabaseStorageService

from src.tools.executor import ToolExecutor
from src.tools.github_adapter.clone import RepositoryCloner
from src.tools.github_adapter.indexer import RepositoryIndexer
from src.tools.github_adapter.manifest_builder import ManifestBuilder
from src.tools.importer import UniversalImporter
from src.tools.manifests import (
    ResourceLimits,
    RuntimeConfig,
    SecurityPolicy,
    ToolManifest,
)
from src.tools.registry import ToolExecutionContext, ToolRegistry

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1/github", tags=["GitHub Tools"])

# ---------------------------------------------------------------------------
# Global singletons (persist across requests)
# ---------------------------------------------------------------------------

_cloner = RepositoryCloner()
_indexer = RepositoryIndexer()
_builder = ManifestBuilder()
_registry = ToolRegistry()
_executor = ToolExecutor()
_importer = UniversalImporter(cloner=_cloner, indexer=_indexer)

# Track imported repos
_imported_repos: dict[str, dict[str, Any]] = {}

# Register 3 builtin tools at module load
from src.tools.builtins.calculator import (  # noqa: E402
    calculator_handler,
    get_manifest as calc_manifest,
)
from src.tools.builtins.csv_profile import (  # noqa: E402
    csv_profile_handler,
    get_manifest as csv_manifest,
)

from src.tools.builtins.json_format import handler as json_format_handler, JSON_FORMAT_MANIFEST
from src.tools.builtins.base64_tool import handler as base64_tool_handler, BASE64_TOOL_MANIFEST
from src.tools.builtins.hash_tool import handler as hash_tool_handler, HASH_TOOL_MANIFEST
from src.tools.builtins.uuid_tool import handler as uuid_tool_handler, UUID_TOOL_MANIFEST
from src.tools.builtins.regex_tool import handler as regex_tool_handler, REGEX_TOOL_MANIFEST
from src.tools.builtins.timestamp_tool import handler as timestamp_tool_handler, TIMESTAMP_TOOL_MANIFEST
from src.tools.builtins.word_count import handler as word_count_handler, WORD_COUNT_MANIFEST
from src.tools.builtins.url_parse import handler as url_parse_handler, URL_PARSE_MANIFEST
from src.tools.builtins.password_gen import handler as password_gen_handler, PASSWORD_GEN_MANIFEST
from src.tools.builtins.text_diff import handler as text_diff_handler, TEXT_DIFF_MANIFEST
from src.tools.builtins.encode_tool import handler as encode_tool_handler, ENCODE_TOOL_MANIFEST
from src.tools.builtins.unit_convert import handler as unit_convert_handler, UNIT_CONVERT_MANIFEST

from src.tools.builtins.http_fetch import (  # noqa: E402
    get_manifest as http_manifest,
    http_fetch_handler,
)

_registry.register(calc_manifest())
_registry.register(http_manifest())
_registry.register(csv_manifest())

_registry.register(JSON_FORMAT_MANIFEST)
_registry.register(BASE64_TOOL_MANIFEST)
_registry.register(HASH_TOOL_MANIFEST)
_registry.register(UUID_TOOL_MANIFEST)
_registry.register(REGEX_TOOL_MANIFEST)
_registry.register(TIMESTAMP_TOOL_MANIFEST)
_registry.register(WORD_COUNT_MANIFEST)
_registry.register(URL_PARSE_MANIFEST)
_registry.register(PASSWORD_GEN_MANIFEST)
_registry.register(TEXT_DIFF_MANIFEST)
_registry.register(ENCODE_TOOL_MANIFEST)
_registry.register(UNIT_CONVERT_MANIFEST)


_executor.register_builtin("math.calculator", calculator_handler)
_executor.register_builtin("web.http_fetch", http_fetch_handler)
_executor.register_builtin("data.csv.profile", csv_profile_handler)

_executor.register_builtin("text.json.format", json_format_handler)
_executor.register_builtin("text.base64.codec", base64_tool_handler)
_executor.register_builtin("crypto.hash.gen", hash_tool_handler)
_executor.register_builtin("util.uuid.gen", uuid_tool_handler)
_executor.register_builtin("text.regex.test", regex_tool_handler)
_executor.register_builtin("util.timestamp.convert", timestamp_tool_handler)
_executor.register_builtin("text.word.count", word_count_handler)
_executor.register_builtin("text.url.parse", url_parse_handler)
_executor.register_builtin("util.password.gen", password_gen_handler)
_executor.register_builtin("text.diff.compare", text_diff_handler)
_executor.register_builtin("text.encode.convert", encode_tool_handler)
_executor.register_builtin("data.convert.units", unit_convert_handler)



def get_registry() -> ToolRegistry:
    """Return the shared tool registry."""
    return _registry


def get_executor() -> ToolExecutor:
    """Return the shared tool executor."""
    return _executor


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ImportRequest(BaseModel):
    url: str
    branch: str = "main"


class ExecuteRequest(BaseModel):
    payload: dict


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/import")
async def import_repo(
    request: ImportRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict:
    """Import tools from a GitHub repository URL via the Universal Importer Pipeline.

    Ensures the user profile exists, creates a repository record and import run,
    executes discovery, registers tools, and persists the full results in Supabase.
    """
    user_id = str(current_user["id"])
    parsed_path = urlparse(request.url).path.rstrip("/")
    repo_name_guess = parsed_path.split("/")[-1].removesuffix(".git") if parsed_path else "repo"

    # 1. Ensure user profile in database
    try:
        await SupabaseStorageService.ensure_user_profile(current_user)
    except Exception as exc:
        logger.warning("supabase.profile_sync_failed", error=str(exc))

    # 2. Record initial import state in DB
    db_repo_id: str | None = None
    db_import_id: str | None = None
    try:
        db_repo_id, db_import_id = await SupabaseStorageService.record_import_start(
            user_id=user_id,
            repo_name=repo_name_guess,
            repo_url=request.url,
            branch=request.branch,
        )
    except Exception as exc:
        logger.warning("supabase.record_start_failed", error=str(exc))

    # 3. Execute Universal Importer pipeline
    try:
        report = await _importer.import_repository(
            request.url,
            request.branch,
            registry=_registry,
            executor=_executor,
        )
    except Exception as exc:
        if db_repo_id and db_import_id:
            try:
                await SupabaseStorageService.record_import_failed(
                    user_id=user_id,
                    repo_id=db_repo_id,
                    import_id=db_import_id,
                    error_message=str(exc),
                )
            except Exception:
                pass
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Import failed: {exc}",
        )

    # 4. Handle failed import outcome
    if report.status == "failed" and not report.tools_registered:
        first_err_dict = report.errors[0] if report.errors else {}
        first_err = first_err_dict.get("error", "No tools could be discovered or registered.")
        err_type = first_err_dict.get("error_type")
        prefix = f"[{err_type}] " if err_type else ""

        if db_repo_id and db_import_id:
            try:
                await SupabaseStorageService.record_import_failed(
                    user_id=user_id,
                    repo_id=db_repo_id,
                    import_id=db_import_id,
                    error_message=first_err,
                    errors=report.errors,
                )
            except Exception as exc:
                logger.warning("supabase.record_failed_state_error", error=str(exc))

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Import failed: {prefix}{first_err}",
        )

    # 5. Persist success/partial status & tools into Supabase
    if db_repo_id and db_import_id:
        try:
            await SupabaseStorageService.record_import_complete(
                user_id=user_id,
                repo_id=db_repo_id,
                import_id=db_import_id,
                report_data=report.to_dict(),
                registered_manifests=report.tools,
            )
        except Exception as exc:
            logger.warning("supabase.record_complete_failed", error=str(exc))

    # Store in memory cache for backward compatibility & local fast lookups
    _imported_repos[report.repository] = {
        "name": report.repository,
        "url": report.url,
        "branch": report.branch,
        "status": report.status,
        "tools": report.tools,
        "total_files": report.total_files,
        "languages": report.languages,
        "detected_frameworks": report.detected_frameworks,
        "dependency_status": report.dependency_status,
        "tools_discovered": report.tools_discovered,
        "tools_registered": report.tools_registered,
        "tools_failed": report.tools_failed,
        "errors": report.errors,
    }

    data = report.to_dict()
    data["repo"] = report.repository
    data["tools_found"] = report.tools_registered
    return data


@router.get("/repos")
async def list_repos(
    current_user: dict[str, Any] = Depends(get_current_user),
) -> list[dict]:
    """List all imported repositories owned by the authenticated user."""
    user_id = str(current_user["id"])
    try:
        user_repos = await SupabaseStorageService.list_user_repos(user_id)
        if user_repos:
            return user_repos
    except Exception as exc:
        logger.warning("supabase.list_repos_failed", error=str(exc))

    # Fallback to local memory cache
    return list(_imported_repos.values())


@router.delete("/repos/{repo_name}")
async def delete_repo(
    repo_name: str,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict:
    """Remove an imported repository and its tools for the authenticated user."""
    user_id = str(current_user["id"])

    # 1. Delete from Supabase DB
    repo_url = ""
    deleted_tool_ids: list[str] = []
    try:
        deleted, repo_url, deleted_tool_ids = await SupabaseStorageService.delete_user_repo(user_id, repo_name)
    except Exception as exc:
        logger.warning("supabase.delete_repo_failed", error=str(exc))

    # 2. Delete from in-memory cache if present
    repo_info = _imported_repos.pop(repo_name, None)
    if repo_info:
        for t in repo_info.get("tools", []):
            _registry.unregister(t["tool_id"])
        _cloner.cleanup(repo_info.get("url", ""))

    for tid in deleted_tool_ids:
        _registry.unregister(tid)
    if repo_url:
        _cloner.cleanup(repo_url)

    return {"status": "deleted", "repo": repo_name}


@router.get("/tools")
async def list_tools(
    current_user: dict[str, Any] = Depends(get_current_user),
) -> list[dict]:
    """List ALL registered tools (builtins + user-specific github-imported tools)."""
    user_id = str(current_user["id"])
    records = _registry.list_all()

    builtin_tools = [
        {
            "tool_id": r.tool_id,
            "name": r.manifest.name,
            "description": r.manifest.description,
            "capabilities": r.manifest.capabilities,
            "risk_level": r.manifest.risk_level,
            "version": r.manifest.version,
            "runtime_type": r.manifest.runtime.type,
            "source": r.manifest.source_repo or "builtin",
            "health_status": r.health_status,
            "invocation_count": r.invocation_count,
        }
        for r in records
    ]

    # Query persistent tools owned by this user
    try:
        user_tools = await SupabaseStorageService.list_user_tools(user_id)
        existing_tids = {t["tool_id"] for t in builtin_tools}
        for ut in user_tools:
            if ut["tool_id"] not in existing_tids:
                builtin_tools.append({
                    "tool_id": ut["tool_id"],
                    "name": ut["name"],
                    "description": ut["description"],
                    "capabilities": ut["capabilities"],
                    "risk_level": ut["risk_level"],
                    "version": ut["version"],
                    "runtime_type": ut["runtime_type"],
                    "source": ut["source"],
                    "health_status": "healthy",
                    "invocation_count": 0,
                })
    except Exception as exc:
        logger.warning("supabase.list_tools_failed", error=str(exc))

    return builtin_tools


@router.post("/tools/{tool_id}/execute")
async def execute_tool(tool_id: str, request: ExecuteRequest) -> dict:
    """Execute a specific tool directly by its ID."""
    record = _registry.get(tool_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool '{tool_id}' not found.",
        )

    context = ToolExecutionContext(
        execution_id=f"direct-{int(time.time() * 1000)}",
        agent_id="direct-api",
        user_id="dashboard-user",
    )

    result = await _executor.execute(record.manifest, request.payload, context)

    return {
        "invocation_id": result.invocation_id,
        "tool_id": result.tool_id,
        "status": result.status,
        "output": result.output,
        "error": result.error,
        "duration_ms": result.duration_ms,
    }
