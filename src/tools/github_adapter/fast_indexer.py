"""
fast_indexer.py
===============
High-performance parallel filesystem indexing for repository ingestion.

Features:
- Native Rust extension hook (`matecos_native`) with seamless fallback.
- Multi-threaded traversal using `os.scandir` batches across CPU cores.
- Optimized hash sets and pre-computed extension lookups to minimize GIL contention.
"""

from __future__ import annotations

import concurrent.futures
import os
from pathlib import Path
from typing import Any

import structlog

from src.tools.github_adapter.indexer import (
    _CONFIG_FILES,
    _DEP_FILES,
    _ENTRY_POINTS,
    _EXT_LANG_MAP,
    _IGNORE_DIRS,
    FileInfo,
    RepoIndex,
)

logger = structlog.get_logger(__name__)

# Check for compiled Rust native extension
try:
    import matecos_native  # type: ignore[import-not-found]
    _NATIVE_RUST_AVAILABLE = True
except ImportError:
    _NATIVE_RUST_AVAILABLE = False


class FastRepositoryIndexer:
    """Accelerated repository indexer providing parallel filesystem traversal."""

    MAX_FILES = 50_000

    def __init__(self, max_workers: int = 4) -> None:
        self.max_workers = max_workers
        self._log = logger.bind(component="FastRepositoryIndexer")

    @property
    def is_rust_native(self) -> bool:
        """Returns True if the compiled Rust extension is active."""
        return _NATIVE_RUST_AVAILABLE

    async def index(self, repo_path: Path) -> RepoIndex:
        """Asynchronously index a repository using parallel file traversal."""
        import asyncio

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._index_sync, repo_path)

    def _index_sync(self, repo_path: Path) -> RepoIndex:
        """Synchronously scan the repository directory tree with multi-threaded optimizations."""
        repo_path_resolved = repo_path.resolve()
        idx = RepoIndex(repo_path=str(repo_path_resolved))

        # Check native Rust path if available
        if _NATIVE_RUST_AVAILABLE:
            try:
                rust_res = matecos_native.fast_index_repo(str(repo_path_resolved))
                self._log.debug("fast_indexer.rust_scan_complete", files=len(rust_res.files))
                # Map rust_res to RepoIndex
                idx.total_files = rust_res.total_files
                idx.total_size_bytes = rust_res.total_bytes
                # populate file structures
                for p, sz, ext, lang in rust_res.files:
                    idx.files.append(FileInfo(path=p, size_bytes=sz, extension=ext, language=lang))
                return idx
            except Exception as e:
                self._log.warning("fast_indexer.rust_scan_fallback", error=str(e))

        # High-performance parallel Python directory walker
        # 1. Discover top-level non-ignored subdirectories
        subdirs: list[str] = []
        root_files: list[os.DirEntry] = []

        try:
            with os.scandir(repo_path_resolved) as it:
                for entry in it:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name not in _IGNORE_DIRS and not entry.name.startswith("."):
                            subdirs.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        root_files.append(entry)
        except OSError:
            return idx

        file_infos: list[FileInfo] = []

        # Process root files first
        for entry in root_files:
            try:
                st = entry.stat()
                rel_path = str(Path(entry.path).relative_to(repo_path_resolved))
                ext = Path(entry.name).suffix.lower()
                lang = _EXT_LANG_MAP.get(ext, "")
                file_infos.append(
                    FileInfo(
                        path=rel_path,
                        size_bytes=st.st_size,
                        extension=ext,
                        language=lang,
                    )
                )
            except OSError:
                continue

        # 2. Parallel scan across subdirectories using ThreadPoolExecutor
        def scan_subtree(root_dir: str) -> list[FileInfo]:
            collected: list[FileInfo] = []
            for cur_root, dirs, files in os.walk(root_dir):
                # Prune ignored directories in-place
                dirs[:] = [d for d in dirs if d not in _IGNORE_DIRS and not d.startswith(".")]

                for fname in files:
                    full_p = os.path.join(cur_root, fname)
                    try:
                        sz = os.path.getsize(full_p)
                        rel = os.path.relpath(full_p, repo_path_resolved)
                        ext = os.path.splitext(fname)[1].lower()
                        lang = _EXT_LANG_MAP.get(ext, "")
                        collected.append(FileInfo(path=rel, size_bytes=sz, extension=ext, language=lang))
                    except OSError:
                        continue
            return collected

        if subdirs:
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = [executor.submit(scan_subtree, d) for d in subdirs]
                for fut in concurrent.futures.as_completed(futures):
                    try:
                        file_infos.extend(fut.result())
                    except Exception as e:
                        self._log.warning("fast_indexer.worker_error", error=str(e))

        # 3. Aggregate statistics
        for finfo in file_infos:
            idx.files.append(finfo)
            idx.total_files += 1
            idx.total_size_bytes += finfo.size_bytes

            ext = finfo.extension
            lang = finfo.language
            fname = os.path.basename(finfo.path)

            if ext:
                idx.file_types[ext] = idx.file_types.get(ext, 0) + 1
            if lang:
                idx.languages[lang] = idx.languages.get(lang, 0) + 1

            if fname in _ENTRY_POINTS:
                idx.entry_points.append(finfo.path)
            if fname in _CONFIG_FILES:
                idx.config_files.append(finfo.path)
            if fname in _DEP_FILES:
                idx.dependency_files.append(finfo.path)

        self._log.info(
            "fast_indexer.complete",
            total_files=idx.total_files,
            total_bytes=idx.total_size_bytes,
            languages=len(idx.languages),
        )
        return idx
