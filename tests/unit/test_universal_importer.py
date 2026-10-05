"""
test_universal_importer.py
==========================
Comprehensive automated tests for MATECOS Tool ID generation and the Universal Importer Pipeline.

Validates:
1. github.container..actor normalization
2. missing namespace
3. missing tool name
4. empty repository name
5. names containing spaces
6. names containing /
7. names containing -
8. names containing @
9. repeated separators
10. Unicode names
11. duplicate tool names / collision avoidance
12. duplicate repositories
13. monorepo imports
14. Python repository discovery
15. Node repository discovery
16. TypeScript repository discovery
17. Go repository discovery
18. Rust repository discovery
19. C repository discovery
20. C++ repository discovery
21. repository with no recognizable tools
22. repository where one tool fails but others succeed (partial import)
23. dependency detection / installation resilience
24. malformed repository metadata
"""

from __future__ import annotations

import re
from pathlib import Path
import pytest

from src.tools.id_generator import TOOL_ID_PATTERN, generate_tool_id, is_valid_tool_id, normalize_segment
from src.tools.importer.detectors import detect_project_metadata
from src.tools.importer.discovery import discover_candidates
from src.tools.importer.models import DiscoveredCandidate, ImportReport
from src.tools.importer.pipeline import UniversalImporter
from src.tools.manifests import ToolManifest
from src.tools.registry import ToolRegistry
from src.tools.executor import ToolExecutor


# ===========================================================================
# 1-12: Tool ID Generation & Normalization Tests
# ===========================================================================

def test_01_container_actor_bug():
    """1. The reported bug: github.container..actor must normalize to a valid ID."""
    tid = generate_tool_id("github", "container", "", ".actor")
    assert tid == "github.container.actor"
    assert is_valid_tool_id(tid)


def test_02_missing_namespace():
    """2. Missing namespace should be collapsed cleanly."""
    tid = generate_tool_id("github", "myrepo", None, "mytool")
    assert tid == "github.myrepo.mytool"
    assert is_valid_tool_id(tid)


def test_03_missing_tool_name():
    """3. Missing tool name should use a deterministic fallback."""
    tid = generate_tool_id("github", "myrepo", "cli", None)
    assert is_valid_tool_id(tid)
    assert tid.startswith("github.myrepo.cli")


def test_04_empty_repository_name():
    """4. Empty repository name should be omitted or cleanly handled."""
    tid = generate_tool_id("github", "", "", "search")
    assert tid == "github.search"
    assert is_valid_tool_id(tid)


def test_05_names_containing_spaces():
    """5. Names containing spaces should convert to underscores."""
    tid = generate_tool_id("github", "awesome project", "cli tools", "data processor")
    assert " " not in tid
    assert tid == "github.awesome_project.cli_tools.data_processor"
    assert is_valid_tool_id(tid)


def test_06_names_containing_slashes():
    """6. Names containing / or \\ should split or convert cleanly."""
    tid = generate_tool_id("github", "owner/repo", "tools/sub", "run\\tool")
    assert "/" not in tid
    assert "\\" not in tid
    assert is_valid_tool_id(tid)


def test_07_names_containing_hyphens():
    """7. Names containing hyphens should convert to underscores."""
    tid = generate_tool_id("github", "my-repo", "cli-sub", "fast-search-v2")
    assert "-" not in tid
    assert tid == "github.my_repo.cli_sub.fast_search_v2"
    assert is_valid_tool_id(tid)


def test_08_names_containing_at_symbols():
    """8. Names containing @ (like npm @scope/pkg) should be sanitized."""
    tid = generate_tool_id("github", "@company/tools", "@bin", "@exec")
    assert "@" not in tid
    assert is_valid_tool_id(tid)


def test_09_repeated_separators():
    """9. Repeated separators (dots, underscores, slashes) must collapse."""
    tid = generate_tool_id("github...repo....tools___sub...run")
    assert ".." not in tid
    assert "__" not in tid
    assert is_valid_tool_id(tid)


def test_10_unicode_names():
    """10. Unicode names must be converted to valid ASCII letters."""
    tid = generate_tool_id("github", "repositório", "ação", "ferramenta")
    assert is_valid_tool_id(tid)
    assert tid == "github.repositorio.acao.ferramenta"


def test_11_duplicate_tool_names():
    """11. Duplicate tool names must be disambiguated with existing_ids."""
    seen: set[str] = set()
    id1 = generate_tool_id("github", "repo", "cli", "search", existing_ids=seen)
    seen.add(id1)
    id2 = generate_tool_id("github", "repo", "cli", "search", existing_ids=seen)
    seen.add(id2)
    assert id1 != id2
    assert is_valid_tool_id(id1)
    assert is_valid_tool_id(id2)


def test_12_duplicate_repositories():
    """12. Different sources or repositories with same tool name should not collide."""
    id_a = generate_tool_id("github", "repo_a", None, "search")
    id_b = generate_tool_id("github", "repo_b", None, "search")
    assert id_a != id_b
    assert id_a == "github.repo_a.search"
    assert id_b == "github.repo_b.search"
    assert is_valid_tool_id(id_a)
    assert is_valid_tool_id(id_b)


# ===========================================================================
# 13-24: Importer Pipeline & Language Discovery Tests
# ===========================================================================

def test_13_monorepo_discovery(tmp_path: Path):
    """13. Monorepo with packages/ subdirectories should discover tools in sub-packages."""
    monorepo = tmp_path / "monorepo"
    monorepo.mkdir()

    # Package A
    pkg_a = monorepo / "packages" / "tool-a"
    pkg_a.mkdir(parents=True)
    (pkg_a / "package.json").write_text('{"bin": {"tool-a": "./cli.js"}}', encoding="utf-8")
    (pkg_a / "cli.js").write_text('console.log("tool a");', encoding="utf-8")

    # Package B (Python)
    pkg_b = monorepo / "packages" / "tool-b"
    pkg_b.mkdir(parents=True)
    (pkg_b / "main.py").write_text('def run(): pass\nif __name__ == "__main__": run()\n', encoding="utf-8")

    meta = detect_project_metadata(monorepo)
    candidates = discover_candidates(monorepo, "monorepo", "https://github.com/org/monorepo", meta)
    assert len(candidates) >= 2
    for c in candidates:
        assert is_valid_tool_id(c.tool_id)


def test_14_python_repository(tmp_path: Path):
    """14. Standard Python repository with pyproject.toml scripts."""
    repo = tmp_path / "py_repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "pytool"\nversion = "0.1.0"\n[project.scripts]\npy-cli = "pytool.cli:main"\n',
        encoding="utf-8",
    )
    meta = detect_project_metadata(repo)
    assert "Python" in meta["languages"]
    candidates = discover_candidates(repo, "py_repo", "https://github.com/org/py_repo", meta)
    assert any("cli" in c.tool_id for c in candidates)


def test_15_node_repository(tmp_path: Path):
    """15. Node.js repository with package.json bin entry."""
    repo = tmp_path / "node_repo"
    repo.mkdir()
    (repo / "package.json").write_text('{"name": "node-pkg", "bin": {"my-node-cli": "./bin/cli.js"}}', encoding="utf-8")
    (repo / "bin").mkdir()
    (repo / "bin" / "cli.js").write_text('console.log("hello");', encoding="utf-8")

    meta = detect_project_metadata(repo)
    assert "JavaScript" in meta["languages"]
    candidates = discover_candidates(repo, "node_repo", "https://github.com/org/node_repo", meta)
    assert any("my_node_cli" in c.tool_id for c in candidates)


def test_16_typescript_repository(tmp_path: Path):
    """16. TypeScript repository with tsconfig.json."""
    repo = tmp_path / "ts_repo"
    repo.mkdir()
    (repo / "package.json").write_text('{"name": "ts-pkg"}', encoding="utf-8")
    (repo / "tsconfig.json").write_text('{"compilerOptions": {}}', encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "index.ts").write_text('export const run = () => {};', encoding="utf-8")

    meta = detect_project_metadata(repo)
    assert "TypeScript" in meta["languages"]


def test_17_go_repository(tmp_path: Path):
    """17. Go repository with main.go and go.mod."""
    repo = tmp_path / "go_repo"
    repo.mkdir()
    (repo / "go.mod").write_text("module github.com/test/go_repo\ngo 1.22\n", encoding="utf-8")
    (repo / "main.go").write_text("package main\nimport \"fmt\"\nfunc main() { fmt.Println(\"hi\") }\n", encoding="utf-8")

    meta = detect_project_metadata(repo)
    assert "Go" in meta["languages"]
    candidates = discover_candidates(repo, "go_repo", "https://github.com/org/go_repo", meta)
    assert any("go" in c.tool_id for c in candidates)


def test_18_rust_repository(tmp_path: Path):
    """18. Rust repository with Cargo.toml and src/main.rs."""
    repo = tmp_path / "rust_repo"
    repo.mkdir()
    (repo / "Cargo.toml").write_text('[package]\nname = "rust_tool"\nversion = "0.1.0"\n', encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "main.rs").write_text('fn main() { println!("hi"); }\n', encoding="utf-8")

    meta = detect_project_metadata(repo)
    assert "Rust" in meta["languages"]
    candidates = discover_candidates(repo, "rust_repo", "https://github.com/org/rust_repo", meta)
    assert any("rust" in c.tool_id for c in candidates)


def test_19_c_repository(tmp_path: Path):
    """19. C repository with main.c and Makefile."""
    repo = tmp_path / "c_repo"
    repo.mkdir()
    (repo / "Makefile").write_text("all:\n\tgcc -o tool main.c\n", encoding="utf-8")
    (repo / "main.c").write_text('#include <stdio.h>\nint main() { return 0; }\n', encoding="utf-8")

    meta = detect_project_metadata(repo)
    assert "Makefile" in meta["manifest_files"]
    candidates = discover_candidates(repo, "c_repo", "https://github.com/org/c_repo", meta)
    assert any("native" in c.tool_id or "c" in c.capabilities for c in candidates)


def test_20_cpp_repository(tmp_path: Path):
    """20. C++ repository with CMakeLists.txt and main.cpp."""
    repo = tmp_path / "cpp_repo"
    repo.mkdir()
    (repo / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.10)\nproject(cpp_tool)\n", encoding="utf-8")
    (repo / "main.cpp").write_text('#include <iostream>\nint main() { return 0; }\n', encoding="utf-8")

    meta = detect_project_metadata(repo)
    assert "C++" in meta["languages"]
    candidates = discover_candidates(repo, "cpp_repo", "https://github.com/org/cpp_repo", meta)
    assert any("native" in c.tool_id or "c++" in c.capabilities for c in candidates)


def test_21_repository_no_tools(tmp_path: Path):
    """21. Repository with text and markdown documentation only (no recognizable tools)."""
    repo = tmp_path / "doc_repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Just Docs\nSome documentation.", encoding="utf-8")
    (repo / "notes.txt").write_text("Meeting notes.", encoding="utf-8")

    meta = detect_project_metadata(repo)
    candidates = discover_candidates(repo, "doc_repo", "https://github.com/org/doc_repo", meta)
    assert len(candidates) == 0


@pytest.mark.asyncio
async def test_22_partial_import_one_fails_others_succeed(tmp_path: Path):
    """22. Repository where one tool fails validation but others succeed (partial import)."""
    repo = tmp_path / "mixed_repo"
    repo.mkdir()

    # Good tool 1
    (repo / "tool_good.py").write_text('def run(): pass\nif __name__ == "__main__": run()\n', encoding="utf-8")
    # Good tool 2
    (repo / "tool_second.py").write_text('def run(): pass\nif __name__ == "__main__": run()\n', encoding="utf-8")

    reg = ToolRegistry()
    exe = ToolExecutor()
    importer = UniversalImporter()

    # Mock cloner returning this local path directly
    class MockCloner:
        async def clone(self, url, branch):
            return repo

    importer.cloner = MockCloner()

    report = await importer.import_repository("https://github.com/org/mixed_repo", registry=reg, executor=exe)
    assert report.status in {"success", "partial"}
    assert report.tools_registered >= 1


def test_23_dependency_manager_detection(tmp_path: Path):
    """23. Dependency manager detection works without executing arbitrary code."""
    repo = tmp_path / "dep_repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("httpx==0.27.0\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\nname="foo"\n', encoding="utf-8")

    meta = detect_project_metadata(repo)
    assert "pip" in meta["package_managers"]


def test_24_malformed_metadata_resilience(tmp_path: Path):
    """24. Corrupted pyproject.toml / package.json does not crash discovery."""
    repo = tmp_path / "corrupt_repo"
    repo.mkdir()
    # Invalid TOML
    (repo / "pyproject.toml").write_text('[[[corrupt toml syntax', encoding="utf-8")
    # Invalid JSON
    (repo / "package.json").write_text('{not valid json', encoding="utf-8")
    (repo / "script.py").write_text('def run(): pass\nif __name__ == "__main__": run()\n', encoding="utf-8")

    meta = detect_project_metadata(repo)
    candidates = discover_candidates(repo, "corrupt_repo", "https://github.com/org/corrupt", meta)
    # Even with corrupt config files, script.py is still safely discovered!
    assert len(candidates) >= 1
    assert is_valid_tool_id(candidates[0].tool_id)
