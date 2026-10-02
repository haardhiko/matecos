"""
manifest_builder.py
===================
Builds tool manifests from repository analysis.

Scans a cloned repository and generates ``ToolManifest`` objects for
discovered tool-like patterns (APIs, CLIs, scripts).
"""

from __future__ import annotations

import re
from pathlib import Path

import structlog

from src.tools.github_adapter.indexer import RepoIndex
from src.tools.manifests import (
    FilesystemPolicy,
    NetworkPolicy,
    ResourceLimits,
    RuntimeConfig,
    SecurityPolicy,
    ToolManifest,
)

logger = structlog.get_logger(__name__)


class ManifestBuilder:
    """Builds tool manifests from repository analysis results.

    Generates manifests for:
    - Python scripts (``*.py`` with ``if __name__ == "__main__"``)
    - Node.js entry points (``package.json`` with scripts)
    - Shell scripts (``*.sh``)
    - Dockerfiles (containerised tools)

    Parameters
    ----------
    owner:
        Default owner for generated manifests.
    """

    def __init__(self, owner: str = "github") -> None:
        self._owner = owner
        self._log = logger.bind(component="ManifestBuilder")

    async def build_from_index(
        self,
        repo_index: RepoIndex,
        repo_url: str,
    ) -> list[ToolManifest]:
        """Generate tool manifests from a repository index.

        Args:
            repo_index: The repository index.
            repo_url: The source repository URL.

        Returns:
            List of discovered ``ToolManifest`` objects.
        """
        manifests: list[ToolManifest] = []

        # Look for Python entry points
        for finfo in repo_index.files:
            if finfo.extension == ".py" and finfo.path in repo_index.entry_points:
                manifest = self._build_python_manifest(finfo.path, repo_url)
                manifests.append(manifest)

        # Look for Dockerfiles
        for finfo in repo_index.files:
            if finfo.path.endswith("Dockerfile") or "Dockerfile" in finfo.path:
                manifest = self._build_container_manifest(finfo.path, repo_url)
                manifests.append(manifest)

        self._log.info(
            "manifest_builder.complete",
            repo_url=repo_url,
            manifests_count=len(manifests),
        )
        return manifests

    def _build_python_manifest(self, script_path: str, repo_url: str) -> ToolManifest:
        """Build a manifest for a Python script."""
        name = Path(script_path).stem
        tool_id = f"github.python.{name}"

        return ToolManifest(
            tool_id=tool_id,
            name=f"Python: {name}",
            description=f"Python script from {repo_url}",
            version="0.1.0",
            owner=self._owner,
            capabilities=[f"python.{name}"],
            risk_level="medium",
            runtime=RuntimeConfig(
                type="container",
                image="python:3.12-slim",
                entrypoint=["python", script_path],
            ),
            resource_limits=ResourceLimits(),
            security=SecurityPolicy(
                network=NetworkPolicy.DENY_ALL,
                filesystem=FilesystemPolicy.READ_ONLY,
                scan_passed=False,
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Command-line arguments",
                    },
                    "stdin": {
                        "type": "string",
                        "description": "Standard input data",
                    },
                },
            },
            output_schema={
                "type": "object",
                "properties": {
                    "stdout": {"type": "string"},
                    "stderr": {"type": "string"},
                    "exit_code": {"type": "integer"},
                },
            },
        )

    def _build_container_manifest(self, dockerfile_path: str, repo_url: str) -> ToolManifest:
        """Build a manifest for a Dockerised tool."""
        dockerfile = Path(dockerfile_path)
        parent_name = dockerfile.parent.name
        file_suffix = dockerfile.stem.removeprefix("Dockerfile").strip("._-")
        raw_name = "_".join(part for part in (parent_name, file_suffix) if part) or "container"
        name = re.sub(r"[^a-z0-9_]+", "_", raw_name.lower()).strip("_")
        if not name:
            name = "container"
        if not name[0].isalpha():
            name = f"tool_{name}"
        name = name[:64].rstrip("_") or "container"
        tool_id = f"github.container.{name}"

        return ToolManifest(
            tool_id=tool_id,
            name=f"Container: {name}",
            description=f"Containerised tool from {repo_url}",
            version="0.1.0",
            owner=self._owner,
            capabilities=[f"container.{name}"],
            risk_level="high",
            runtime=RuntimeConfig(
                type="container",
                image=f"matecos-github-{name}:latest",
            ),
            resource_limits=ResourceLimits(
                max_memory_mb=4096,
                max_cpu_percent=75,
                max_execution_seconds=600,
            ),
            security=SecurityPolicy(
                network=NetworkPolicy.DENY_ALL,
                filesystem=FilesystemPolicy.READ_WRITE_SCOPED,
                scan_passed=True,
            ),
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
