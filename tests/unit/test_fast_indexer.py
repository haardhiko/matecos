"""Unit tests for FastRepositoryIndexer."""

import pytest
from pathlib import Path

from src.tools.github_adapter.fast_indexer import FastRepositoryIndexer


@pytest.mark.asyncio
async def test_fast_repository_indexer(tmp_path: Path):
    # Setup mock repository tree
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hello')")
    (tmp_path / "src" / "utils.js").write_text("console.log('hi')")
    (tmp_path / "requirements.txt").write_text("fastapi==0.110.0")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/master")

    indexer = FastRepositoryIndexer(max_workers=2)
    idx = await indexer.index(tmp_path)

    assert idx.total_files == 3  # main.py, utils.js, requirements.txt (.git ignored)
    assert idx.total_size_bytes > 0
    assert "Python" in idx.languages
    assert "JavaScript" in idx.languages
    assert any("requirements.txt" in f for f in idx.dependency_files)
    assert any("main.py" in f for f in idx.entry_points)
