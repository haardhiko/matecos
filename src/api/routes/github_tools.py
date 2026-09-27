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
from src.tools.builtins.http_fetch import (  # noqa: E402
    get_manifest as http_manifest,
    http_fetch_handler,
)

_registry.register(calc_manifest())
_registry.register(http_manifest())
_registry.register(csv_manifest())

_executor.register_builtin("math.calculator", calculator_handler)
_executor.register_builtin("web.http_fetch", http_fetch_handler)
_executor.register_builtin("data.csv.profile", csv_profile_handler)


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
    """Import tools from a GitHub repository URL."""
    # Validate URL
    parsed = urlparse(request.url)
    if not parsed.scheme or not parsed.netloc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Git/GitHub URL provided.",
        )

    # Extract repo name
    repo_name = parsed.path.strip("/").split("/")[-1]
    if repo_name.endswith(".git"):
        repo_name = repo_name[:-4]
    if not repo_name:
        repo_name = "unknown_repo"

    try:
        # Clone (async)
        repo_path = await _cloner.clone(request.url, request.branch)

        # Index (async)
        index_result = await _indexer.index(repo_path)

        # Build manifests (async)
        manifests = await _builder.build_from_index(index_result, request.url)

        # Additional scanning: detect Python packages and README
        repo_dir = Path(repo_path) if not isinstance(repo_path, Path) else repo_path
        extra_caps: list[str] = []
        description = f"Imported from {request.url}"

        if (repo_dir / "README.md").exists():
            try:
                readme_text = (repo_dir / "README.md").read_text(
                    encoding="utf-8", errors="ignore"
                )
                description = readme_text[:500].strip()
            except OSError:
                pass

        if (repo_dir / "requirements.txt").exists() or (
            repo_dir / "pyproject.toml"
        ).exists():
            extra_caps.append("python")

        # Scan for additional Python scripts that look like tools
        py_files = list(repo_dir.rglob("*.py"))
        _tool_patterns = re.compile(
            r"def\s+(main|run|handler|execute|process)\s*\(", re.IGNORECASE
        )
        for py_file in py_files[:50]:  # safety cap
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                if _tool_patterns.search(content):
                    rel = str(py_file.relative_to(repo_dir))
                    name = py_file.stem
                    # Check it wasn't already picked up by ManifestBuilder
                    tid = f"github.python.{name}"
                    if not any(m.tool_id == tid for m in manifests):
                        m = ToolManifest(
                            tool_id=tid,
                            name=f"Python: {name}",
                            version="0.1.0",
                            description=description[:200],
                            capabilities=[f"python.{name}"] + extra_caps,
                            input_schema={
                                "type": "object",
                                "properties": {
                                    "args": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    }
                                },
                            },
                            output_schema={
                                "type": "object",
                                "properties": {
                                    "stdout": {"type": "string"},
                                    "exit_code": {"type": "integer"},
                                },
                            },
                            risk_level="medium",
                            runtime=RuntimeConfig(
                                type="container", image="python:3.12-slim"
                            ),
                            owner="github",
                            source_repo=request.url,
                        )
                        manifests.append(m)
            except OSError:
                continue

        # Register all discovered tools
        tools_found: list[dict[str, Any]] = []
        for manifest in manifests:
            _registry.register(manifest)
            tools_found.append(
                {
                    "tool_id": manifest.tool_id,
                    "name": manifest.name,
                    "description": manifest.description[:200],
                    "capabilities": manifest.capabilities,
                    "risk_level": manifest.risk_level,
                }
            )

        # Store metadata
        _imported_repos[repo_name] = {
            "name": repo_name,
            "url": request.url,
            "branch": request.branch,
            "tools": tools_found,
            "total_files": index_result.total_files,
            "languages": dict(index_result.languages),
        }

        logger.info(
            "github.import_complete",
            repo=repo_name,
            tools_found=len(tools_found),
        )

        return {
            "repo": repo_name,
            "tools_found": len(tools_found),
            "tools": tools_found,
            "languages": dict(index_result.languages),
            "total_files": index_result.total_files,
        }

    except Exception as exc:
        logger.exception("github.import_failed", url=request.url)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Import failed: {exc}",
        ) from exc


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
