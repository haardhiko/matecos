"""
query.py
========
Repository content querying — search files, read content, and extract symbols.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class SearchResult:
    """A single file search result."""

    file_path: str
    line_number: int
    line_content: str
    match_start: int = 0
    match_end: int = 0


@dataclass
class FileContent:
    """Content of a single file."""

    path: str
    content: str
    size_bytes: int
    line_count: int
    truncated: bool = False


class RepositoryQuery:
    """Query interface for cloned repositories.

    Provides search, file reading, and basic symbol extraction.

    Parameters
    ----------
    max_file_size:
        Maximum file size to read (bytes).
    """

    MAX_SEARCH_RESULTS = 100
    MAX_FILE_SIZE = 1_024 * 1_024  # 1 MB

    _IGNORE_DIRS = {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        "dist",
        "build",
    }

    def __init__(self, max_file_size: int | None = None) -> None:
        self._max_file_size = max_file_size or self.MAX_FILE_SIZE
        self._log = logger.bind(component="RepositoryQuery")

    async def search(
        self,
        repo_path: Path,
        pattern: str,
        file_pattern: str | None = None,
        case_sensitive: bool = False,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Search for a pattern across repository files.

        Args:
            repo_path: Path to the repository.
            pattern: Regex pattern to search for.
            file_pattern: Optional glob to filter files.
            case_sensitive: Whether the search is case-sensitive.
            max_results: Maximum results to return.

        Returns:
            List of ``SearchResult`` objects.
        """
        max_res = max_results or self.MAX_SEARCH_RESULTS
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            compiled = re.compile(pattern, flags)
        except re.error as exc:
            self._log.warning("query.invalid_pattern", pattern=pattern, error=str(exc))
            return []

        results: list[SearchResult] = []

        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in self._IGNORE_DIRS]

            for name in files:
                if len(results) >= max_res:
                    break

                filepath = Path(root) / name
                rel_path = str(filepath.relative_to(repo_path))

                if file_pattern:
                    if not filepath.match(file_pattern):
                        continue

                try:
                    size = filepath.stat().st_size
                    if size > self._max_file_size:
                        continue

                    content = filepath.read_text(encoding="utf-8", errors="ignore")
                    for line_num, line in enumerate(content.splitlines(), 1):
                        match = compiled.search(line)
                        if match:
                            results.append(
                                SearchResult(
                                    file_path=rel_path,
                                    line_number=line_num,
                                    line_content=line[:500],
                                    match_start=match.start(),
                                    match_end=match.end(),
                                )
                            )
                            if len(results) >= max_res:
                                break
                except (OSError, UnicodeDecodeError):
                    continue

        self._log.debug("query.search_complete", results=len(results))
        return results

    async def read_file(
        self,
        repo_path: Path,
        file_path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> FileContent:
        """Read a file from the repository.

        Args:
            repo_path: Path to the repository.
            file_path: Relative path within the repository.
            start_line: Optional start line (1-indexed).
            end_line: Optional end line (1-indexed, inclusive).

        Returns:
            ``FileContent`` with the file's contents.

        Raises:
            FileNotFoundError: If the file doesn't exist.
        """
        full_path = repo_path / file_path

        # Security: prevent path traversal
        try:
            full_path.resolve().relative_to(repo_path.resolve())
        except ValueError:
            raise FileNotFoundError(f"Path traversal detected: {file_path}") from None

        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        size = full_path.stat().st_size
        truncated = size > self._max_file_size

        content = full_path.read_text(
            encoding="utf-8",
            errors="ignore",
        )

        if truncated:
            content = content[: self._max_file_size]

        lines = content.splitlines()
        if start_line or end_line:
            s = (start_line or 1) - 1
            e = end_line or len(lines)
            lines = lines[s:e]
            content = "\n".join(lines)

        return FileContent(
            path=file_path,
            content=content,
            size_bytes=size,
            line_count=len(lines),
            truncated=truncated,
        )

    async def list_directory(
        self,
        repo_path: Path,
        dir_path: str = ".",
    ) -> list[dict[str, Any]]:
        """List contents of a directory in the repository.

        Args:
            repo_path: Path to the repository.
            dir_path: Relative directory path.

        Returns:
            List of entry dicts with name, type, and size.
        """
        full_path = repo_path / dir_path

        # Security: prevent path traversal
        try:
            full_path.resolve().relative_to(repo_path.resolve())
        except ValueError:
            raise FileNotFoundError(f"Path traversal detected: {dir_path}") from None

        if not full_path.is_dir():
            raise FileNotFoundError(f"Directory not found: {dir_path}")

        entries: list[dict[str, Any]] = []
        for entry in sorted(full_path.iterdir()):
            if entry.name in self._IGNORE_DIRS:
                continue
            entries.append(
                {
                    "name": entry.name,
                    "type": "directory" if entry.is_dir() else "file",
                    "size_bytes": entry.stat().st_size if entry.is_file() else None,
                }
            )

        return entries
