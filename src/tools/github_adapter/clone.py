"""
clone.py
========
Repository cloning and workspace management for OrchaDeck.

Robust Git execution engine:
- Explicit resolution of system Git (prefers C:\\Program Files\\Git\\cmd\\git.exe on Windows)
- Complete environment inheritance (preserves SystemRoot, PATH, proxy, and SSL settings)
- DNS preflight check and automated http.curloptResolve injection (fixes Windows MSYS AAAA negative cache)
- Preflight connectivity check (git ls-remote) before clone
- Structured error classification (DNS, Network, Auth, 404, Not Installed, Subprocess)
- Safe command sanitization and secret-redacted logging
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Structured Clone Exceptions
# ---------------------------------------------------------------------------


class CloneError(Exception):
    """Base exception for repository cloning and preflight failures."""

    def __init__(
        self,
        message: str,
        *,
        error_type: str = "clone_failed",
        command: list[str] | None = None,
        stdout: str = "",
        stderr: str = "",
        exit_code: int | None = None,
        git_executable: str = "",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_type = error_type
        self.command = command or []
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.git_executable = git_executable

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_type": self.error_type,
            "message": self.message,
            "exit_code": self.exit_code,
            "stderr": self.stderr,
            "git_executable": self.git_executable,
        }


class GitNotInstalledError(CloneError):
    """Git executable could not be found or executed."""

    def __init__(self, message: str = "Git is not installed or not accessible.", **kwargs: Any) -> None:
        super().__init__(message, error_type="git_not_installed", **kwargs)


class GitDNSResolutionError(CloneError):
    """DNS resolution failed (e.g., host could not be resolved)."""

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(message, error_type="dns_resolution_failed", **kwargs)


class GitNetworkError(CloneError):
    """Network connection failure (timeout, unreachable, SSL handshake)."""

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(message, error_type="network_error", **kwargs)


class GitAuthenticationError(CloneError):
    """Authentication or authorization failure (HTTP 401/403, invalid token/key)."""

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(message, error_type="authentication_failed", **kwargs)


class GitRepositoryNotFoundError(CloneError):
    """Target repository or branch does not exist (HTTP 404)."""

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(message, error_type="repository_not_found", **kwargs)


class GitSubprocessCloneError(CloneError):
    """General git clone subprocess execution failure."""

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(message, error_type="clone_failed", **kwargs)


# ---------------------------------------------------------------------------
# Utility & Environment Helpers
# ---------------------------------------------------------------------------


def resolve_git_executable(custom_path: str | Path | None = None) -> Path:
    """Resolve the Git executable explicitly, preferring system Git on Windows.

    Resolution order:
    1. Explicit custom_path argument if provided.
    2. Environment variable: ORCHADECK_GIT_PATH or GIT_EXECUTABLE.
    3. On Windows:
       a. C:\\Program Files\\Git\\cmd\\git.exe
       b. %ProgramFiles%\\Git\\cmd\\git.exe
       c. %ProgramFiles(x86)%\\Git\\cmd\\git.exe
       d. shutil.which("git") (excluding embedded hermes/bin/git.exe)
    4. On Unix:
       a. shutil.which("git")
       b. Standard paths: /usr/bin/git, /usr/local/bin/git, /opt/homebrew/bin/git

    Raises:
        GitNotInstalledError: If no working Git executable can be found.
    """
    candidates: list[Path] = []

    # 1. Custom parameter
    if custom_path:
        candidates.append(Path(custom_path))

    # 2. Environment variables
    for env_var in ("ORCHADECK_GIT_PATH", "GIT_EXECUTABLE"):
        val = os.environ.get(env_var, "").strip()
        if val:
            candidates.append(Path(val))

    # 3. Platform-specific preferred locations
    if sys.platform == "win32":
        # Windows Requirement 6: prefer working system Git installation
        candidates.append(Path(r"C:\Program Files\Git\cmd\git.exe"))
        pf = os.environ.get("ProgramFiles")
        if pf:
            candidates.append(Path(pf) / "Git" / "cmd" / "git.exe")
        pfx86 = os.environ.get("ProgramFiles(x86)")
        if pfx86:
            candidates.append(Path(pfx86) / "Git" / "cmd" / "git.exe")

        # Fallback to which
        which_git = shutil.which("git")
        if which_git:
            cand = Path(which_git)
            # Avoid embedded hermes/bin/git.exe unless no system git exists
            if "hermes" not in str(cand).lower() or not any(c.exists() for c in candidates):
                candidates.append(cand)
    else:
        which_git = shutil.which("git")
        if which_git:
            candidates.append(Path(which_git))
        for p in ("/usr/bin/git", "/usr/local/bin/git", "/opt/homebrew/bin/git"):
            candidates.append(Path(p))

    # Check candidates in order
    for candidate in candidates:
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate.resolve()
        except OSError:
            continue

    raise GitNotInstalledError(
        f"Git executable not found. Checked locations: {[str(c) for c in candidates]}"
    )


def get_git_subprocess_env(git_executable: Path) -> dict[str, str]:
    """Construct an effective environment dictionary for Git subprocess execution.

    Preserves critical Windows system variables (SystemRoot, COMSPEC, TEMP),
    retains proxy/network/SSL variables, prepends Git cmd directory to PATH,
    and disables interactive credential prompts.
    """
    env = os.environ.copy()

    # 1. Critical Windows system environment variables
    if sys.platform == "win32":
        sys_root = env.get("SystemRoot") or env.get("SYSTEMROOT") or r"C:\Windows"
        env["SystemRoot"] = sys_root
        env["SYSTEMROOT"] = sys_root
        env["windir"] = sys_root
        if "ComSpec" not in env and "COMSPEC" not in env:
            env["COMSPEC"] = os.path.join(sys_root, "system32", "cmd.exe")
        if "TEMP" not in env and "TMP" not in env:
            env["TEMP"] = os.path.join(sys_root, "Temp")
            env["TMP"] = env["TEMP"]

    # 2. Prepend Git directory to PATH and filter out other embedded Git installations
    git_dir = str(git_executable.parent)
    current_path = env.get("PATH", "")
    filtered_parts: list[str] = []
    for part in current_path.split(os.pathsep):
        part_clean = part.strip()
        if not part_clean:
            continue
        # Avoid conflicting embedded/portable Git installations (e.g. hermes\git)
        if "hermes" in part_clean.lower() and "git" in part_clean.lower():
            continue
        filtered_parts.append(part_clean)

    if git_dir.lower() not in [p.lower() for p in filtered_parts]:
        filtered_parts.insert(0, git_dir)

    env["PATH"] = os.pathsep.join(filtered_parts)

    # 3. Prevent hanging on interactive prompts
    env["GIT_TERMINAL_PROMPT"] = "0"

    return env


def sanitize_env_for_logging(env: dict[str, str]) -> dict[str, str]:
    """Return a sanitized copy of relevant environment variables for safe logging."""
    sensitive_patterns = (
        "token", "secret", "password", "key", "auth", "credential",
        "private", "cert", "jwt", "bearer", "api_key", "apikey",
    )
    relevant_keys = {
        "PATH", "SystemRoot", "SYSTEMROOT", "COMSPEC", "windir", "TEMP", "TMP",
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "all_proxy", "no_proxy",
        "SSL_CERT_FILE", "SSL_CERT_DIR", "GIT_SSL_CAINFO", "GIT_SSL_NO_VERIFY",
        "GIT_TERMINAL_PROMPT", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    }
    sanitized: dict[str, str] = {}
    for k, v in env.items():
        if k in relevant_keys:
            if any(pat in k.lower() for pat in sensitive_patterns):
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = v
    return sanitized


def sanitize_command_for_logging(command: list[str]) -> list[str]:
    """Sanitize URLs containing embedded credentials in command strings."""
    sanitized = []
    for arg in command:
        if "@github.com" in arg or "@gitlab.com" in arg or "@bitbucket.org" in arg:
            sanitized.append(re.sub(r"://([^:]+):([^@]+)@", r"://\1:[REDACTED]@", arg))
        else:
            sanitized.append(arg)
    return sanitized


def get_git_resolve_args(repo_url: str) -> list[str]:
    """Resolve target hostname via Python socket and return `-c http.curloptResolve=...`.

    Bypasses Windows MSYS cURL dual-stack DNS resolution issues (where negative AAAA caching
    causes subsequent calls to report 'Could not resolve host: github.com').
    """
    try:
        parsed = urlparse(repo_url)
        hostname = parsed.hostname
        if not hostname:
            return []
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addr_info = socket.getaddrinfo(hostname, port, family=socket.AF_INET, proto=socket.IPPROTO_TCP)
        if addr_info:
            ip = addr_info[0][4][0]
            return ["-c", f"http.curloptResolve={hostname}:{port}:{ip}"]
    except Exception:
        pass
    return []


def classify_git_error(
    stderr: str,
    stdout: str,
    exit_code: int | None,
    command: list[str],
    git_executable: str,
) -> CloneError:
    """Classify Git error output into specific structured exception types."""
    combined = f"{stderr}\n{stdout}".strip()
    combined_lower = combined.lower()

    kwargs = {
        "command": sanitize_command_for_logging(command),
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "git_executable": git_executable,
    }

    # 1. DNS Resolution Failure
    if any(m in combined_lower for m in [
        "could not resolve host",
        "unable to resolve",
        "name or service not known",
        "nodename nor servname provided",
        "temporary failure in name resolution",
    ]):
        return GitDNSResolutionError(
            f"Git DNS resolution failed: {stderr or combined}",
            **kwargs,
        )

    # 2. Authentication / Permission Failure
    if any(m in combined_lower for m in [
        "authentication failed",
        "permission denied (publickey)",
        "could not read username",
        "terminal prompts disabled",
        "invalid username or password",
        "http 401",
        "http 403",
        "access denied",
    ]):
        return GitAuthenticationError(
            f"Git authentication failed: {stderr or combined}",
            **kwargs,
        )

    # 3. Repository or Branch Not Found
    if any(m in combined_lower for m in [
        "repository not found",
        "does not exist",
        "remote branch", "not found in upstream",
        "http 404",
    ]):
        return GitRepositoryNotFoundError(
            f"Git repository or ref not found: {stderr or combined}",
            **kwargs,
        )

    # 4. Network / Connectivity / SSL Handshake Failure
    if any(m in combined_lower for m in [
        "failed to connect",
        "connection timed out",
        "connection refused",
        "network is unreachable",
        "ssl certificate problem",
        "server certificate verification failed",
        "gnutls_handshake failed",
        "recv failure",
        "connection reset by peer",
        "operation timed out",
    ]):
        return GitNetworkError(
            f"Git network connection failed: {stderr or combined}",
            **kwargs,
        )

    # 5. Generic Clone / Subprocess Failure
    return GitSubprocessCloneError(
        f"Git subprocess failed with code {exit_code}: {stderr or combined}",
        **kwargs,
    )


def _remove_readonly(func, path, exc_info):
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
            subprocess.run(["cmd", "/c", "rd", "/s", "/q", str(path)], capture_output=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# RepositoryCloner Implementation
# ---------------------------------------------------------------------------


class RepositoryCloner:
    """Clones and manages local copies of Git repositories.

    Repositories are cloned into an isolated workspace directory and
    cleaned up after use.
    """

    def __init__(
        self,
        workspace_root: str | None = None,
        git_executable: str | Path | None = None,
    ) -> None:
        self._root = Path(workspace_root or tempfile.mkdtemp(prefix="matecos_repos_"))
        self._root.mkdir(parents=True, exist_ok=True)
        self._cloned: dict[str, Path] = {}
        self._custom_git_path = Path(git_executable) if git_executable else None
        self._log = logger.bind(component="RepositoryCloner")

    def get_git_executable(self) -> Path:
        """Resolve the effective Git executable."""
        return resolve_git_executable(self._custom_git_path)

    async def preflight_check(self, repo_url: str, timeout: float = 15.0) -> None:
        """Run preflight check verifying Git execution, host DNS, network, and repo access.

        Runs `git ls-remote --heads <repo_url>` before creating destination directories.

        Raises:
            CloneError subclass on failure.
        """
        git_exec = self.get_git_executable()
        env = get_git_subprocess_env(git_exec)

        # Preflight proactive DNS check
        parsed = urlparse(repo_url)
        if parsed.hostname:
            try:
                socket.getaddrinfo(parsed.hostname, parsed.port or 443)
            except socket.gaierror as gai:
                raise GitDNSResolutionError(
                    f"Git DNS resolution failed: Could not resolve host '{parsed.hostname}': {gai}",
                    command=[str(git_exec), "ls-remote", repo_url],
                    git_executable=str(git_exec),
                ) from gai

        resolve_args = get_git_resolve_args(repo_url)
        cmd = [str(git_exec), *resolve_args, "ls-remote", "--heads", repo_url]

        safe_cmd = sanitize_command_for_logging(cmd)
        self._log.info(
            "git.preflight_check",
            git_executable=str(git_exec),
            repo_url=repo_url,
            effective_path=env.get("PATH", "")[:200],
            sanitized_env=sanitize_env_for_logging(env),
        )

        def _run_sync() -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                timeout=timeout,
            )

        try:
            proc = await asyncio.to_thread(_run_sync)
        except subprocess.TimeoutExpired as texc:
            raise GitNetworkError(
                f"Git preflight connectivity check timed out after {timeout}s",
                command=safe_cmd,
                git_executable=str(git_exec),
            ) from texc
        except FileNotFoundError as fnf:
            raise GitNotInstalledError(
                f"Git executable '{git_exec}' not found or not executable",
                command=safe_cmd,
                git_executable=str(git_exec),
            ) from fnf

        out_text = proc.stdout.decode(errors="replace").strip()
        err_text = proc.stderr.decode(errors="replace").strip()

        if proc.returncode != 0:
            self._log.warning(
                "git.preflight_failed",
                exit_code=proc.returncode,
                stderr=err_text,
                git_executable=str(git_exec),
            )
            raise classify_git_error(
                stderr=err_text,
                stdout=out_text,
                exit_code=proc.returncode,
                command=cmd,
                git_executable=str(git_exec),
            )

        self._log.info("git.preflight_success", repo_url=repo_url)

    async def clone(
        self,
        repo_url: str,
        ref: str = "main",
        depth: int = 1,
        timeout: float = 120.0,
    ) -> Path:
        """Clone a repository to the workspace.

        Args:
            repo_url: Git clone URL.
            ref: Branch, tag, or commit to check out.
            depth: Clone depth (1 for shallow).
            timeout: Subprocess timeout in seconds.

        Returns:
            Path to the cloned repository.

        Raises:
            CloneError subclass: If preflight or cloning fails.
        """
        git_exec = self.get_git_executable()
        env = get_git_subprocess_env(git_exec)

        # Log effective Git executable and environment (excluding secrets)
        self._log.info(
            "git.effective_runtime",
            git_executable=str(git_exec),
            effective_path=env.get("PATH", "")[:200],
            sanitized_env=sanitize_env_for_logging(env),
        )

        # Derive safe directory name from URL
        repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
        dest = self._root / repo_name

        if dest.exists():
            if (dest / ".git").exists():
                self._log.info("clone.already_exists", repo=repo_name, path=str(dest))
                return dest
            else:
                _safe_rmtree(dest)

        # Stage 0: Run preflight connectivity check
        await self.preflight_check(repo_url, timeout=min(20.0, timeout))

        self._log.info("clone.starting", repo_url=repo_url, ref=ref, git_executable=str(git_exec))

        resolve_args = get_git_resolve_args(repo_url)

        attempts: list[list[str]] = []
        if ref and ref.lower() not in ["head", "default", ""]:
            attempts.append([
                str(git_exec), *resolve_args, "clone", "--depth", str(depth), "--branch", ref, repo_url, str(dest)
            ])
        attempts.append([
            str(git_exec), *resolve_args, "clone", "--depth", str(depth), repo_url, str(dest)
        ])

        def _run_git_subprocess(command: list[str]) -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                timeout=timeout,
            )

        last_error_exc: CloneError | None = None

        for attempt_idx, cmd in enumerate(attempts):
            _safe_rmtree(dest)
            safe_cmd = sanitize_command_for_logging(cmd)
            self._log.info("clone.attempt_command", cmd=safe_cmd, attempt=attempt_idx + 1)

            try:
                proc = await asyncio.to_thread(_run_git_subprocess, cmd)
            except subprocess.TimeoutExpired as texc:
                last_error_exc = GitNetworkError(
                    f"git clone timed out after {timeout}s",
                    command=safe_cmd,
                    git_executable=str(git_exec),
                )
                if attempt_idx < len(attempts) - 1:
                    await asyncio.sleep(1.0)
                continue
            except FileNotFoundError as fnf:
                raise GitNotInstalledError(
                    f"Git executable '{git_exec}' not found",
                    command=safe_cmd,
                    git_executable=str(git_exec),
                ) from fnf

            out_text = proc.stdout.decode(errors="replace").strip()
            err_text = proc.stderr.decode(errors="replace").strip()

            if proc.returncode == 0:
                self._cloned[repo_url] = dest
                self._log.info(
                    "clone.complete",
                    repo=repo_name,
                    path=str(dest),
                    git_executable=str(git_exec),
                )
                return dest
            else:
                last_error_exc = classify_git_error(
                    stderr=err_text,
                    stdout=out_text,
                    exit_code=proc.returncode,
                    command=cmd,
                    git_executable=str(git_exec),
                )
                self._log.warning(
                    "clone.attempt_failed",
                    cmd=safe_cmd,
                    error_type=last_error_exc.error_type,
                    exit_code=proc.returncode,
                    stderr=err_text,
                )
                if attempt_idx < len(attempts) - 1:
                    await asyncio.sleep(1.0)

        if last_error_exc:
            raise last_error_exc

        raise GitSubprocessCloneError(
            f"git clone failed for {repo_url}",
            command=sanitize_command_for_logging(attempts[0]),
            git_executable=str(git_exec),
        )

    def get_path(self, repo_url: str) -> Path | None:
        """Get the local path for a previously cloned repository."""
        return self._cloned.get(repo_url)

    def cleanup(self, repo_url: str) -> None:
        """Remove a cloned repository from disk."""
        path = self._cloned.pop(repo_url, None)
        if path and path.exists():
            _safe_rmtree(path)
            self._log.info("clone.cleaned_up", path=str(path))

    def cleanup_all(self) -> None:
        """Remove all cloned repositories."""
        for url in list(self._cloned.keys()):
            self.cleanup(url)

    @property
    def cloned_repos(self) -> list[str]:
        """Return URLs of all cloned repositories."""
        return list(self._cloned.keys())
