"""
clone.py
========
Repository cloning and workspace management.
"""

from __future__ import annotations

import shutil
import tempfile
import hashlib
import subprocess
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
        cache_key = f"{repo_url}#{ref}"
        suffix = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:10]
        dest = self._root / f"{repo_name}-{suffix}"

        if dest.exists():
            if (dest / ".git").exists():
                self._cloned[repo_url] = dest
                self._log.info("clone.already_exists", repo=repo_name)
                return dest
            else:
                shutil.rmtree(dest, ignore_errors=True)

        self._log.info("clone.starting", repo_url=repo_url, ref=ref)

        # First attempt: if ref is specified and not empty, try with --branch
        # If that fails (e.g. branch main doesn't exist, but master does), retry without --branch
        attempts = []
        if ref and ref.lower() not in ["head", "default", ""]:
            attempts.append(["git", "clone", "--depth", str(depth), "--branch", ref, repo_url, str(dest)])
        attempts.append(["git", "clone", "--depth", str(depth), repo_url, str(dest)])

        last_error = ""
        for cmd in attempts:
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            try:
                process = await asyncio.to_thread(
                    subprocess.run,
                    cmd,
                    capture_output=True,
                    check=False,
                    timeout=120,
                )
                if process.returncode == 0:
                    self._cloned[repo_url] = dest
                    self._log.info("clone.complete", repo=repo_name, path=str(dest))
                    return dest
                else:
                    last_error = process.stderr.decode(errors="replace")[:500]
                    self._log.warning("clone.attempt_failed", cmd=cmd, error=last_error)
            except FileNotFoundError:
                raise CloneError("git is not installed or not on PATH.") from None
            except subprocess.TimeoutExpired:
                raise CloneError("git clone timed out after 120 seconds.") from None

        raise CloneError(f"git clone failed: {last_error}")

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
