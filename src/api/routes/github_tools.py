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
from src.tools.github_adapter.manifest_builder import ManifestBuilder, normalize_tool_segment
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
    branch: str = ""


class ExecuteRequest(BaseModel):
    payload: dict


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/import")
async def import_repo(request: ImportRequest) -> dict:
    """Import tools from a GitHub repository URL."""
    # Accept a repository URL or a GitHub tree/blob link, then clone the repo root.
    raw_url = request.url.strip()
    selected_branch = request.branch.strip()
    if not raw_url.startswith("http://") and not raw_url.startswith("https://") and not raw_url.startswith("git@"):
        if raw_url.startswith("github.com/"):
            raw_url = "https://" + raw_url
        else:
            raw_url = f"https://github.com/{raw_url}"
    parsed = urlparse(raw_url)
    if not parsed.netloc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Git/GitHub URL provided.",
        )

    path_parts = [p for p in parsed.path.strip("/").split("/") if p]
    if parsed.hostname and parsed.hostname.lower() == "github.com":
        if len(path_parts) < 2:
            raise HTTPException(status_code=400, detail="Use a GitHub link that includes an owner and repository name.")
        if len(path_parts) > 2 and path_parts[2] in {"tree", "blob"}:
            if len(path_parts) < 4:
                raise HTTPException(status_code=400, detail="The GitHub link is missing its branch or file path.")
            selected_branch = selected_branch or path_parts[3]
        raw_url = f"https://github.com/{path_parts[0]}/{path_parts[1]}"
    clean_url = raw_url
    selected_branch = selected_branch or "main"

    # Extract repo name
    path_parts = [p for p in urlparse(clean_url).path.strip("/").split("/") if p]
    repo_name = path_parts[-1] if path_parts else "repo"
    if repo_name.endswith(".git"):
        repo_name = repo_name[:-4]
    repo_name = re.sub(r"[^a-zA-Z0-9_-]", "_", repo_name) or "repo"

    try:
        # Clone (async)
        repo_path = await _cloner.clone(clean_url, selected_branch)

        # Index (async)
        index_result = await _indexer.index(repo_path)

        # Build manifests (async)
        manifests = await _builder.build_from_index(index_result, clean_url)

        # Additional scanning: detect Python packages and README
        repo_dir = Path(repo_path) if not isinstance(repo_path, Path) else repo_path
        extra_caps: list[str] = []
        description = f"Imported from {clean_url}"

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

        # Helper to create runnable python script handlers
        def _make_script_handler(s_path: Path):
            async def _handler(payload: dict, context: ToolExecutionContext) -> dict:
                import asyncio
                import subprocess
                import sys

                args = payload.get("args", [])
                if isinstance(args, str):
                    args = [args]
                elif not isinstance(args, list):
                    args = [str(args)]
                try:
                    proc = await asyncio.to_thread(
                        subprocess.run,
                        [sys.executable, str(s_path), *[str(a) for a in args]],
                        capture_output=True,
                        check=False,
                        cwd=str(s_path.parent),
                        timeout=30.0,
                    )
                    return {
                        "stdout": proc.stdout.decode(errors="replace")[:4000],
                        "stderr": proc.stderr.decode(errors="replace")[:2000],
                        "exit_code": proc.returncode,
                        "script": s_path.name
                    }
                except subprocess.TimeoutExpired:
                    return {"error": "The script exceeded its 30-second time limit.", "script": s_path.name}
                except Exception as e:
                    return {"error": str(e), "script": s_path.name}
            return _handler

        # Scan for additional Python scripts that look like tools
        py_files = list(repo_dir.rglob("*.py"))
        tool_candidates = [
            path
            for path in py_files
            if not any(
                part.lower() in {"test", "tests", "docs", "examples", ".venv", "venv", "site-packages"}
                for part in path.relative_to(repo_dir).parts[:-1]
            )
            and not path.stem.lower().startswith("test_")
        ]
        _tool_patterns = re.compile(
            r"def\s+(main|run|handler|execute|process|cli)\s*\(|if\s+__name__\s*==\s*['\"]__main__['\"]", re.IGNORECASE
        )
        tool_script_map: dict[str, Path] = {}

        for py_file in tool_candidates[:50]:  # safety cap
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                if _tool_patterns.search(content):
                    relative_script = py_file.relative_to(repo_dir).with_suffix("").as_posix()
                    name = normalize_tool_segment(relative_script.replace("/", "_"), "script")
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
                            runtime=RuntimeConfig(type="builtin"),
                            owner="github",
                            source_repo=clean_url,
                        )
                        manifests.append(m)
                        tool_script_map[tid] = py_file
            except OSError:
                continue

        # If still no tools discovered, register top-level python file as fallback tool
        if not manifests and tool_candidates:
            fallback_py = tool_candidates[0]
            relative_script = fallback_py.relative_to(repo_dir).with_suffix("").as_posix()
            name = normalize_tool_segment(relative_script.replace("/", "_"), "script")
            tid = f"github.python.{name}"
            m = ToolManifest(
                tool_id=tid,
                name=f"Python: {name}",
                version="0.1.0",
                description=description[:200],
                capabilities=[f"python.{name}", "python"],
                input_schema={
                    "type": "object",
                    "properties": {
                        "args": {"type": "array", "items": {"type": "string"}}
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
                runtime=RuntimeConfig(type="builtin"),
                owner="github",
                source_repo=clean_url,
            )
            manifests.append(m)
            tool_script_map[tid] = fallback_py

        # Register all discovered tools and bind runtime handlers
        tools_found: list[dict[str, Any]] = []
        for manifest in manifests:
            _registry.register(manifest)
            if manifest.tool_id in tool_script_map:
                _executor.register_builtin(manifest.tool_id, _make_script_handler(tool_script_map[manifest.tool_id]))
            elif manifest.runtime.type == "builtin" and manifest.tool_id not in _executor._handlers:
                # default stub handler for safety
                async def _generic_handler(payload: dict, context: ToolExecutionContext) -> dict:
                    return {"result": "Tool executed successfully", "input": payload}
                _executor.register_builtin(manifest.tool_id, _generic_handler)

            tools_found.append(
                {
                    "tool_id": manifest.tool_id,
                    "name": manifest.name,
                    "description": manifest.description[:200],
                    "capabilities": manifest.capabilities,
                    "risk_level": manifest.risk_level,
                }
            )

        # Use the README to name and describe script tools in plain language.
        # The model may only annotate script paths found in this checkout.
        readme_for_llm = ""
        readme_path = repo_dir / "README.md"
        if readme_path.is_file():
            try:
                readme_for_llm = readme_path.read_text(encoding="utf-8", errors="ignore")[:12000]
            except OSError:
                pass
        candidate_paths = [
            {"path": str(path.relative_to(repo_dir)).replace("\\", "/"), "name": path.stem}
            for path in tool_script_map.values()
        ]
        from src.services.llm_service import llm_service

        descriptions = await llm_service.describe_repository_tools(clean_url, readme_for_llm, candidate_paths)
        for manifest in manifests:
            script_path = tool_script_map.get(manifest.tool_id)
            if script_path is None:
                continue
            relative_path = str(script_path.relative_to(repo_dir)).replace("\\", "/")
            info = descriptions.get(relative_path)
            if not info:
                continue
            capabilities = list(dict.fromkeys(
                [*manifest.capabilities, *[item.strip() for item in info["capabilities"].split(",") if item.strip()]]
            ))[:12]
            updated_manifest = manifest.model_copy(update={
                "name": info["name"],
                "description": info["description"],
                "capabilities": capabilities,
            })
            _registry.unregister(manifest.tool_id)
            _registry.register(updated_manifest)
            for item in tools_found:
                if item["tool_id"] == manifest.tool_id:
                    item.update({"name": info["name"], "description": info["description"], "capabilities": capabilities})

        # Store metadata
        _imported_repos[repo_name] = {
            "name": repo_name,
            "url": clean_url,
            "branch": selected_branch,
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
        reason = str(exc).strip() or f"{type(exc).__name__}. Check the server log for details."
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Import failed: {reason}",
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
