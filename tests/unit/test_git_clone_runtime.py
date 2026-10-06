"""
Tests for OrchaDeck Git Clone runtime, executable resolution, environment sanitization,
error classification, and connectivity preflight checks.
"""

from pathlib import Path
import pytest

from src.tools.github_adapter.clone import (
    resolve_git_executable,
    get_git_subprocess_env,
    sanitize_env_for_logging,
    sanitize_command_for_logging,
    classify_git_error,
    GitDNSResolutionError,
    GitNetworkError,
    GitAuthenticationError,
    GitRepositoryNotFoundError,
    RepositoryCloner,
)


def test_resolve_git_executable_finds_system_git():
    git_path = resolve_git_executable()
    assert git_path is not None
    assert git_path.exists()
    assert str(git_path).lower().endswith("git.exe") or str(git_path).lower().endswith("git")


def test_resolve_git_executable_custom_env(monkeypatch, tmp_path):
    dummy_git = tmp_path / "git.exe"
    dummy_git.write_text("dummy", encoding="utf-8")
    monkeypatch.setenv("ORCHADECK_GIT_PATH", str(dummy_git))
    resolved = resolve_git_executable()
    assert str(resolved).lower() == str(dummy_git.resolve()).lower()


def test_get_git_subprocess_env_filters_hermes(monkeypatch):
    test_path = r"C:\Windows\System32;C:\Users\haard\AppData\Local\hermes\git\bin;C:\Program Files\Git\cmd"
    monkeypatch.setenv("PATH", test_path)
    git_path = Path(r"C:\Program Files\Git\cmd\git.exe")
    env = get_git_subprocess_env(git_path)
    assert "hermes\\git" not in env.get("PATH", "").lower()
    assert env.get("GIT_TERMINAL_PROMPT") == "0"


def test_sanitize_command_for_logging():
    cmd = ["git", "clone", "https://x-access-token:ghp_secretToken123456@github.com/org/repo.git", "dest"]
    sanitized = sanitize_command_for_logging(cmd)
    assert "ghp_secretToken123456" not in sanitized[2]
    assert "https://x-access-token:[REDACTED]@github.com/org/repo.git" in sanitized[2]


def test_sanitize_env_for_logging():
    env = {"GIT_TOKEN": "secret", "PATH": "some_path", "SYSTEMROOT": "C:\\Windows"}
    sanitized = sanitize_env_for_logging(env)
    assert "GIT_TOKEN" not in sanitized
    assert "PATH" in sanitized


def test_classify_git_error_dns():
    err = classify_git_error(
        stderr="fatal: unable to access 'https://github.com/...': Could not resolve host: github.com",
        stdout="",
        exit_code=128,
        command=["git", "ls-remote"],
        git_executable=r"C:\Program Files\Git\cmd\git.exe",
    )
    assert isinstance(err, GitDNSResolutionError)
    assert err.error_type == "dns_resolution_failed"


def test_classify_git_error_auth():
    err = classify_git_error(
        stderr="fatal: Authentication failed for 'https://github.com/private/repo.git'",
        stdout="",
        exit_code=128,
        command=["git", "ls-remote"],
        git_executable=r"C:\Program Files\Git\cmd\git.exe",
    )
    assert isinstance(err, GitAuthenticationError)
    assert err.error_type == "authentication_failed"


def test_classify_git_error_not_found():
    err = classify_git_error(
        stderr="remote: Repository not found.\nfatal: repository '...' not found",
        stdout="",
        exit_code=128,
        command=["git", "ls-remote"],
        git_executable=r"C:\Program Files\Git\cmd\git.exe",
    )
    assert isinstance(err, GitRepositoryNotFoundError)
    assert err.error_type == "repository_not_found"


def test_classify_git_error_network():
    err = classify_git_error(
        stderr="fatal: unable to access 'https://...': Failed to connect to github.com port 443: Timed out",
        stdout="",
        exit_code=128,
        command=["git", "ls-remote"],
        git_executable=r"C:\Program Files\Git\cmd\git.exe",
    )
    assert isinstance(err, GitNetworkError)
    assert err.error_type == "network_error"


@pytest.mark.asyncio
async def test_preflight_invalid_host():
    cloner = RepositoryCloner()
    with pytest.raises(GitDNSResolutionError):
        await cloner.preflight_check("https://nonexistent-domain-xyz-12345.com/org/repo.git")


@pytest.mark.asyncio
async def test_public_repo_clone_integration(tmp_path):
    """Integration test verifying clone of a public repo with curloptResolve."""
    cloner = RepositoryCloner(workspace_root=str(tmp_path))
    repo_path = await cloner.clone(
        repo_url="https://github.com/bottlepy/bottle.git",
        ref="master",
        depth=1,
    )
    assert repo_path.exists()
    assert (repo_path / "bottle.py").exists()
