"""
clone.py
========
Repository cloning and workspace management.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


class CloneError(Exception):
    """Raised when repository cloning fails."""


class RepositoryCloner:
    """Clones and manages local copies of Git repositories.

    Repositories are cloned into an isolated workspace directory and
    cleaned up after use.

    Parameters
    ----------
    workspace_root:
        Base directory for cloned repositories.
    """

    def __init__(self, workspace_root: str | None = None) -> None:
        self._root = Path(workspace_root or tempfile.mkdtemp(prefix="matecos_repos_"))
        self._root.mkdir(parents=True, exist_ok=True)
        self._cloned: dict[str, Path] = {}
        self._log = logger.bind(component="RepositoryCloner")

    async def clone(
        self,
        repo_url: str,
        ref: str = "main",
        depth: int = 1,
    ) -> Path:
        """Clone a repository to the workspace.

        Args:
            repo_url: Git clone URL.
            ref: Branch, tag, or commit to check out.
            depth: Clone depth (1 for shallow).

        Returns:
            Path to the cloned repository.

        Raises:
            CloneError: If cloning fails.
        """
        import asyncio

        # Derive a safe directory name from the URL
        repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
        dest = self._root / repo_name

        if dest.exists():
            self._log.info("clone.already_exists", repo=repo_name)
            return dest

        cmd = ["git", "clone", "--depth", str(depth), "--branch", ref, repo_url, str(dest)]

        self._log.info("clone.starting", repo_url=repo_url, ref=ref)

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                raise CloneError(
                    f"git clone failed (exit {process.returncode}): {stderr.decode()[:500]}"
                )

            self._cloned[repo_url] = dest
            self._log.info("clone.complete", repo=repo_name, path=str(dest))
            return dest

        except FileNotFoundError:
            raise CloneError("git is not installed or not on PATH.") from None

    def get_path(self, repo_url: str) -> Path | None:
        """Get the local path for a previously cloned repository."""
        return self._cloned.get(repo_url)

    def cleanup(self, repo_url: str) -> None:
        """Remove a cloned repository from disk.

        Args:
            repo_url: The repository URL to clean up.
        """
        path = self._cloned.pop(repo_url, None)
        if path and path.exists():
            shutil.rmtree(path, ignore_errors=True)
            self._log.info("clone.cleaned_up", path=str(path))

    def cleanup_all(self) -> None:
        """Remove all cloned repositories."""
        for url in list(self._cloned.keys()):
            self.cleanup(url)

    @property
    def cloned_repos(self) -> list[str]:
        """Return URLs of all cloned repositories."""
        return list(self._cloned.keys())
