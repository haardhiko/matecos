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


def _remove_readonly(func, path, exc_info):
    import os, stat
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def _safe_rmtree(path: Path) -> None:
    if not path.exists():
        return
    try:
        shutil.rmtree(path, onerror=_remove_readonly)
    except Exception:
        pass
    if path.exists():
        try:
            import subprocess
            subprocess.run(["cmd", "/c", "rd", "/s", "/q", str(path)], capture_output=True)
        except Exception:
            pass


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
            if (dest / ".git").exists():
                self._log.info("clone.already_exists", repo=repo_name)
                return dest
            else:
                _safe_rmtree(dest)

        self._log.info("clone.starting", repo_url=repo_url, ref=ref)

        attempts = []
        if ref and ref.lower() not in ["head", "default", ""]:
            attempts.append(["git", "clone", "--depth", str(depth), "--branch", ref, repo_url, str(dest)])
        attempts.append(["git", "clone", "--depth", str(depth), repo_url, str(dest)])

        import subprocess

        def _run_git(command: list[str]) -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        last_error = ""
        for cmd in attempts:
            _safe_rmtree(dest)
            try:
                proc = await asyncio.to_thread(_run_git, cmd)
                if proc.returncode == 0:
                    self._cloned[repo_url] = dest
                    self._log.info("clone.complete", repo=repo_name, path=str(dest))
                    return dest
                else:
                    err_text = proc.stderr.decode(errors="replace").strip()
                    out_text = proc.stdout.decode(errors="replace").strip()
                    last_error = err_text or out_text or f"Process exited with code {proc.returncode}"
                    self._log.warning("clone.attempt_failed", cmd=cmd, error=last_error)
            except FileNotFoundError:
                raise CloneError("git is not installed or not on PATH.") from None

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
