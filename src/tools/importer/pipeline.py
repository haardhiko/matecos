"""
pipeline.py
===========
Universal Tool Import Pipeline for MATECOS.
Coordinates cloning, indexing, language detection, candidate discovery,
manifest generation, strict validation, registration, and execution binding.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import structlog

from src.tools.executor import ToolExecutor
from src.tools.github_adapter.clone import RepositoryCloner
from src.tools.github_adapter.indexer import RepositoryIndexer
from src.tools.id_generator import generate_tool_id, is_valid_tool_id
from src.tools.importer.detectors import detect_project_metadata
from src.tools.importer.discovery import discover_candidates
from src.tools.importer.models import DiscoveredCandidate, ImportReport
from src.tools.manifests import (
    FilesystemPolicy,
    NetworkPolicy,
    ResourceLimits,
    RuntimeConfig,
    SecurityPolicy,
    ToolManifest,
)
from src.tools.registry import ToolExecutionContext, ToolRegistry

logger = structlog.get_logger(__name__)


class UniversalImporter:
    """Universal repository tool importer supporting multiple languages and frameworks."""

    def __init__(
        self,
        cloner: RepositoryCloner | None = None,
        indexer: RepositoryIndexer | None = None,
    ) -> None:
        self.cloner = cloner or RepositoryCloner()
        self.indexer = indexer or RepositoryIndexer()
        self._log = logger.bind(component="UniversalImporter")

    @staticmethod
    def normalize_url(raw_url: str) -> tuple[str, str]:
        """Normalize Git / GitHub URL and extract clean repository name.

        Returns:
            (clean_url, repo_name)
        """
        clean = raw_url.strip()
        if not clean.startswith(("http://", "https://", "git@")):
            if clean.startswith("github.com/"):
                clean = "https://" + clean
            else:
                clean = f"https://github.com/{clean}"

        parsed = urlparse(clean)
        path_parts = [p for p in parsed.path.strip("/").split("/") if p]
        repo_name = path_parts[-1] if path_parts else "repo"
        if repo_name.endswith(".git"):
            repo_name = repo_name[:-4]
        repo_name = re.sub(r"[^a-zA-Z0-9_-]", "_", repo_name) or "repo"
        return clean, repo_name

    def _create_script_runner(self, script_path: Path, invocation_method: str):
        """Create an async execution handler for runnable tools."""
        async def _handler(payload: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
            args = payload.get("args", [])
            if isinstance(args, str):
                args = [args]
            elif not isinstance(args, list):
                args = [str(args)]

            if invocation_method == "node_script":
                cmd = ["node", str(script_path), *[str(a) for a in args]]
            elif invocation_method == "shell_script":
                cmd = ["bash", str(script_path), *[str(a) for a in args]]
            else:
                cmd = [sys.executable, str(script_path), *[str(a) for a in args]]

            def _run_sync() -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    cwd=str(script_path.parent),
                    timeout=30.0,
                )

            try:
                proc = await asyncio.to_thread(_run_sync)
                return {
                    "stdout": proc.stdout[:8000],
                    "stderr": proc.stderr[:4000],
                    "exit_code": proc.returncode,
                    "script": script_path.name,
                }
            except subprocess.TimeoutExpired:
                return {"error": "Execution timed out after 30 seconds", "script": script_path.name}
            except Exception as e:
                return {"error": str(e), "script": script_path.name}

        return _handler

    async def import_repository(
        self,
        url: str,
        branch: str = "main",
        *,
        registry: ToolRegistry | None = None,
        executor: ToolExecutor | None = None,
    ) -> ImportReport:
        """Run the complete universal import pipeline on a repository.

        Stages:
        1. Fetch & Clone
        2. Index
        3. Language & Project Metadata Detection
        4. Candidate Discovery
        5. Normalization & Manifest Generation
        6. Validation
        7. Registration & Execution Binding
        8. Report Construction
        """
        clean_url, repo_name = self.normalize_url(url)
        report = ImportReport(
            repository=repo_name,
            url=clean_url,
            branch=branch,
        )

        if registry is None:
            registry = ToolRegistry()
        if executor is None:
            from src.tools.executor import ToolExecutor
            executor = ToolExecutor()

        # Stage 1: Clone
        try:
            self._log.info("importer.clone_start", url=clean_url, branch=branch)
            repo_path_raw = await self.cloner.clone(clean_url, branch)
            repo_path = Path(repo_path_raw) if not isinstance(repo_path_raw, Path) else repo_path_raw
        except Exception as exc:
            self._log.exception("importer.clone_failed", url=clean_url)
            report.status = "failed"
            report.errors.append({"stage": "clone", "error": f"Failed to clone repository: {exc}"})
            return report

        # Stage 2: Index
        try:
            self._log.info("importer.index_start", repo_path=str(repo_path))
            index_result = await self.indexer.index(repo_path)
            report.total_files = index_result.total_files
            report.languages = dict(index_result.languages)
        except Exception as exc:
            self._log.exception("importer.index_failed", repo_path=str(repo_path))
            report.status = "failed"
            report.errors.append({"stage": "index", "error": f"Failed to index repository: {exc}"})
            return report

        # Stage 3: Language & Project Detection
        try:
            metadata = detect_project_metadata(repo_path)
            report.detected_frameworks = metadata.get("frameworks", [])
            report.dependency_status = {
                "package_managers": metadata.get("package_managers", []),
                "manifest_files": metadata.get("manifest_files", []),
            }
        except Exception as exc:
            self._log.warning("importer.detection_warning", error=str(exc))
            metadata = {"description": f"Repository {repo_name}"}

        # Stage 4: Entry-Point Discovery
        try:
            candidates = discover_candidates(repo_path, repo_name, clean_url, metadata)
            report.tools_discovered = len(candidates)
            self._log.info("importer.discovery_complete", candidates=len(candidates))
        except Exception as exc:
            self._log.exception("importer.discovery_failed")
            report.status = "failed"
            report.errors.append({"stage": "discovery", "error": f"Failed during candidate discovery: {exc}"})
            return report

        # Stage 5, 6, 7: Manifest Generation, Strict Validation & Registration
        for candidate in candidates:
            # Enforce valid Tool ID
            tid = candidate.tool_id
            if not is_valid_tool_id(tid):
                tid = generate_tool_id("github", repo_name, None, candidate.symbol_name)
                candidate.tool_id = tid

            # Build ToolManifest
            try:
                manifest = ToolManifest(
                    tool_id=candidate.tool_id,
                    name=candidate.name or f"{repo_name}: {candidate.symbol_name}",
                    version="0.1.0",
                    description=candidate.description or f"Tool from {repo_name}",
                    capabilities=candidate.capabilities or [f"{repo_name}.{candidate.symbol_name}"],
                    risk_level=candidate.risk_level,
                    runtime=RuntimeConfig(type="builtin" if candidate.is_executable else "subprocess"),
                    owner="github",
                    source_repo=clean_url,
                    input_schema=candidate.input_schema,
                    output_schema=candidate.output_schema,
                    security=SecurityPolicy(
                        network=NetworkPolicy.DENY_ALL,
                        filesystem=FilesystemPolicy.READ_ONLY,
                        scan_passed=False,
                    ),
                )
            except Exception as val_exc:
                self._log.warning("importer.tool_validation_failed", tool_id=candidate.tool_id, error=str(val_exc))
                # Attempt automatic repair of tool ID
                try:
                    repaired_id = generate_tool_id("github", repo_name, "tool", candidate.symbol_name)
                    manifest = ToolManifest(
                        tool_id=repaired_id,
                        name=candidate.name or f"{repo_name}: {candidate.symbol_name}",
                        version="0.1.0",
                        description=candidate.description or f"Tool from {repo_name}",
                        capabilities=candidate.capabilities or [f"{repo_name}.{candidate.symbol_name}"],
                        risk_level=candidate.risk_level,
                        runtime=RuntimeConfig(type="builtin" if candidate.is_executable else "subprocess"),
                        owner="github",
                        source_repo=clean_url,
                        input_schema=candidate.input_schema,
                        output_schema=candidate.output_schema,
                    )
                except Exception as repair_exc:
                    # Record failure and continue with other tools!
                    report.tools_failed += 1
                    report.errors.append({
                        "tool": candidate.symbol_name,
                        "tool_id": candidate.tool_id,
                        "stage": "validation",
                        "error": str(repair_exc),
                    })
                    continue

            # Register in ToolRegistry
            try:
                registry.register(manifest)

                # Bind execution handler in executor
                full_source_path = repo_path / candidate.source_file
                if candidate.is_executable and full_source_path.exists():
                    runner = self._create_script_runner(full_source_path, candidate.invocation_method)
                    executor.register_builtin(manifest.tool_id, runner)
                else:
                    # Provide metadata-informed stub runner for non-executable or containerized tools
                    async def _stub_runner(payload: dict[str, Any], ctx: ToolExecutionContext) -> dict[str, Any]:
                        return {
                            "status": "DISCOVERY_SUPPORT",
                            "tool_id": manifest.tool_id,
                            "notice": "Discovered candidate tool. Direct local execution requires container/runtime setup.",
                            "payload": payload,
                        }
                    executor.register_builtin(manifest.tool_id, _stub_runner)

                report.tools_registered += 1
                report.tools.append({
                    "tool_id": manifest.tool_id,
                    "name": manifest.name,
                    "description": manifest.description,
                    "capabilities": manifest.capabilities,
                    "risk_level": manifest.risk_level,
                    "is_executable": candidate.is_executable,
                    "source_file": candidate.source_file,
                })
            except Exception as reg_exc:
                report.tools_failed += 1
                report.errors.append({
                    "tool": candidate.symbol_name,
                    "tool_id": manifest.tool_id,
                    "stage": "registration",
                    "error": str(reg_exc),
                })

        # Determine overall import status
        if report.tools_registered > 0 and report.tools_failed == 0:
            report.status = "success"
        elif report.tools_registered > 0 and report.tools_failed > 0:
            report.status = "partial"
        else:
            report.status = "failed"

        self._log.info(
            "importer.pipeline_complete",
            repo=repo_name,
            status=report.status,
            discovered=report.tools_discovered,
            registered=report.tools_registered,
            failed=report.tools_failed,
        )

        return report
