"""
test_github_adapter.py
======================
Unit tests for the GitHub adapter components (indexer, query, manifest_builder, pr_adapter).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.tools.github_adapter.indexer import RepoIndex, RepositoryIndexer
from src.tools.github_adapter.manifest_builder import ManifestBuilder
from src.tools.github_adapter.pr_adapter import PullRequest, PullRequestAdapter
from src.tools.github_adapter.query import RepositoryQuery


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """Create a mock repository filesystem structure."""
    repo = tmp_path / "sample_repo"
    repo.mkdir()

    # Python entrypoint
    main_py = repo / "main.py"
    main_py.write_text('if __name__ == "__main__":\n    print("Hello world")\n', encoding="utf-8")

    # Config file
    pyproject = repo / "pyproject.toml"
    pyproject.write_text('[project]\nname = "sample"\nversion = "0.1.0"\n', encoding="utf-8")

    # Nested module
    src_dir = repo / "src"
    src_dir.mkdir()
    util_py = src_dir / "utils.py"
    util_py.write_text("def helper():\n    return 42\n", encoding="utf-8")

    # Dockerfile
    dockerfile = repo / "Dockerfile"
    dockerfile.write_text("FROM python:3.12-slim\nCMD ['python', 'main.py']\n", encoding="utf-8")

    return repo


class TestRepositoryIndexer:
    @pytest.mark.asyncio
    async def test_index_structure(self, sample_repo: Path) -> None:
        indexer = RepositoryIndexer()
        index = await indexer.index(sample_repo)

        assert isinstance(index, RepoIndex)
        assert index.total_files == 4
        assert "Python" in index.languages
        assert index.languages["Python"] == 2
        assert "main.py" in index.entry_points
        assert "pyproject.toml" in index.config_files


class TestRepositoryQuery:
    @pytest.mark.asyncio
    async def test_search_pattern(self, sample_repo: Path) -> None:
        query = RepositoryQuery()
        results = await query.search(sample_repo, pattern=r"def helper")
        assert len(results) == 1
        assert "utils.py" in results[0].file_path
        assert results[0].line_number == 1

    @pytest.mark.asyncio
    async def test_read_file(self, sample_repo: Path) -> None:
        query = RepositoryQuery()
        content = await query.read_file(sample_repo, "main.py")
        assert "Hello world" in content.content
        assert content.line_count == 2

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, sample_repo: Path) -> None:
        query = RepositoryQuery()
        with pytest.raises(FileNotFoundError, match="Path traversal"):
            await query.read_file(sample_repo, "../../etc/passwd")

    @pytest.mark.asyncio
    async def test_list_directory(self, sample_repo: Path) -> None:
        query = RepositoryQuery()
        entries = await query.list_directory(sample_repo, ".")
        names = {e["name"] for e in entries}
        assert "main.py" in names
        assert "src" in names


class TestManifestBuilder:
    @pytest.mark.asyncio
    async def test_build_manifests_from_index(self, sample_repo: Path) -> None:
        indexer = RepositoryIndexer()
        index = await indexer.index(sample_repo)

        builder = ManifestBuilder(owner="test-owner")
        manifests = await builder.build_from_index(index, repo_url="https://github.com/test/repo")

        assert len(manifests) >= 1
        # Should have found python main.py and/or Dockerfile
        tool_ids = {m.tool_id for m in manifests}
        assert any("python" in tid for tid in tool_ids) or any(
            "container" in tid for tid in tool_ids
        )
        for m in manifests:
            assert m.owner == "test-owner"
            assert m.security.network.value == "deny_all"


class TestPullRequestAdapter:
    def test_parse_pr(self) -> None:
        raw_data = {
            "number": 12,
            "title": "Fix security vulnerability",
            "state": "open",
            "html_url": "https://github.com/owner/repo/pull/12",
            "head": {"ref": "patch-1"},
            "base": {"ref": "main"},
            "body": "Fixed CVE-1234",
            "user": {"login": "sec-bot"},
            "labels": [{"name": "security"}],
            "requested_reviewers": [{"login": "alice"}],
        }
        pr = PullRequestAdapter._parse_pr(raw_data)
        assert isinstance(pr, PullRequest)
        assert pr.number == 12
        assert pr.title == "Fix security vulnerability"
        assert pr.labels == ["security"]
        assert pr.reviewers == ["alice"]
