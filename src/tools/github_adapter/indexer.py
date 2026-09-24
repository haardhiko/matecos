"""
indexer.py
==========
Repository indexing — analyses file structure, languages, and dependencies.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class FileInfo:
    """Information about a single file in the repository."""

    path: str
    size_bytes: int
    extension: str
    language: str = ""


@dataclass
class RepoIndex:
    """Index of a repository's contents.

    Attributes:
        repo_path: Path to the repository.
        total_files: Total number of files.
        total_size_bytes: Total size in bytes.
        languages: Language distribution (language → file count).
        file_types: Extension distribution.
        files: List of file information.
        entry_points: Detected entry point files.
        config_files: Detected configuration files.
        dependency_files: Detected dependency/package files.
    """

    repo_path: str = ""
    total_files: int = 0
    total_size_bytes: int = 0
    languages: dict[str, int] = field(default_factory=dict)
    file_types: dict[str, int] = field(default_factory=dict)
    files: list[FileInfo] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    config_files: list[str] = field(default_factory=list)
    dependency_files: list[str] = field(default_factory=list)


# Extension → language mapping
_EXT_LANG_MAP: dict[str, str] = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".jsx": "JavaScript",
    ".tsx": "TypeScript",
    ".java": "Java",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".cpp": "C++",
    ".c": "C",
    ".h": "C",
    ".cs": "C#",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".sh": "Shell",
    ".sql": "SQL",
    ".r": "R",
    ".md": "Markdown",
    ".yml": "YAML",
    ".yaml": "YAML",
    ".json": "JSON",
    ".toml": "TOML",
    ".html": "HTML",
    ".css": "CSS",
}

_ENTRY_POINTS = {"main.py", "app.py", "index.js", "main.go", "main.rs", "Main.java"}
_CONFIG_FILES = {
    "pyproject.toml", "setup.py", "setup.cfg",
    "package.json", "tsconfig.json",
    "Makefile", "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    ".github", "Cargo.toml", "go.mod",
}
_DEP_FILES = {
    "requirements.txt", "Pipfile", "poetry.lock", "Pipfile.lock",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "Cargo.lock", "go.sum", "Gemfile.lock",
}

_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".tox", "dist", "build",
    ".egg-info", "target", ".idea", ".vscode",
}


class RepositoryIndexer:
    """Indexes a repository's file structure and content."""

    MAX_FILES = 10_000  # Safety limit

    def __init__(self) -> None:
        self._log = logger.bind(component="RepositoryIndexer")

    async def index(self, repo_path: Path) -> RepoIndex:
        """Build an index of the repository.

        Args:
            repo_path: Path to the cloned repository.

        Returns:
            A ``RepoIndex`` with the analysis results.
        """
        idx = RepoIndex(repo_path=str(repo_path))
        file_count = 0

        for root, dirs, files in os.walk(repo_path):
            # Prune ignored directories
            dirs[:] = [d for d in dirs if d not in _IGNORE_DIRS]

            for name in files:
                if file_count >= self.MAX_FILES:
                    self._log.warning("indexer.max_files_reached")
                    break

                filepath = Path(root) / name
                rel_path = str(filepath.relative_to(repo_path))

                try:
                    size = filepath.stat().st_size
                except OSError:
                    continue

                ext = filepath.suffix.lower()
                lang = _EXT_LANG_MAP.get(ext, "")

                file_info = FileInfo(
                    path=rel_path,
                    size_bytes=size,
                    extension=ext,
                    language=lang,
                )
                idx.files.append(file_info)
                idx.total_files += 1
                idx.total_size_bytes += size
                file_count += 1

                # Track distributions
                if ext:
                    idx.file_types[ext] = idx.file_types.get(ext, 0) + 1
                if lang:
                    idx.languages[lang] = idx.languages.get(lang, 0) + 1

                # Detect special files
                if name in _ENTRY_POINTS:
                    idx.entry_points.append(rel_path)
                if name in _CONFIG_FILES:
                    idx.config_files.append(rel_path)
                if name in _DEP_FILES:
                    idx.dependency_files.append(rel_path)

        self._log.info(
            "indexer.complete",
            total_files=idx.total_files,
            languages=len(idx.languages),
            repo_path=str(repo_path),
        )
        return idx
