"""GitHub tool import pipeline — clone, index, extract, register."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

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
async def import_repo(request: ImportRequest) -> dict:
    """Import tools from a GitHub repository URL via the Universal Importer Pipeline."""
    report = await _importer.import_repository(
        request.url,
        request.branch,
        registry=_registry,
        executor=_executor,
    )

    if report.status == "failed" and not report.tools_registered:
        first_err = report.errors[0]["error"] if report.errors else "No tools could be discovered or registered."
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Import failed: {first_err}",
        )

    # Store metadata for /repos endpoint
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

    # Structured response with backward-compatible aliases
    data = report.to_dict()
    data["repo"] = report.repository
    data["tools_found"] = report.tools_registered
    return data


@router.get("/repos")
async def list_repos() -> list[dict]:
    """List all imported repositories."""
    return list(_imported_repos.values())


@router.delete("/repos/{repo_name}")
async def delete_repo(repo_name: str) -> dict:
    """Remove an imported repository and its tools."""
    if repo_name not in _imported_repos:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_name}' not found.",
        )

    repo_info = _imported_repos.pop(repo_name)
    # Unregister tools
    for t in repo_info.get("tools", []):
        _registry.unregister(t["tool_id"])

    # Cleanup cloned files
    _cloner.cleanup(repo_info.get("url", ""))

    return {"status": "deleted", "repo": repo_name}


@router.get("/tools")
async def list_tools() -> list[dict]:
    """List ALL registered tools (builtins + github-imported)."""
    records = _registry.list_all()
    return [
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
