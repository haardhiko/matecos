"""
discovery.py
============
Multi-language entry point discovery and candidate tool extraction for MATECOS.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from src.tools.id_generator import generate_tool_id
from src.tools.importer.models import DiscoveredCandidate

# Common directories to skip during scanning
IGNORED_DIR_NAMES = {
    ".git",
    ".github",
    ".vscode",
    ".idea",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "target",
    "tests",
    "test",
    "testing",
    "spec",
    "docs",
    "documentation",
    "site-packages",
    "tmp",
}


def _is_test_file(path: Path) -> bool:
    """Return True if path appears to be a test file or test directory."""
    for part in path.parts:
        if part.lower() in {"tests", "test", "testing", "spec", "specs", "__tests__"}:
            return True
    name = path.name.lower()
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith("_test.go")
        or name.endswith(".test.js")
        or name.endswith(".test.ts")
        or name.endswith(".spec.js")
        or name.endswith(".spec.ts")
    )


def discover_candidates(
    repo_path: Path,
    repo_name: str,
    repo_url: str,
    metadata: dict[str, Any],
) -> list[DiscoveredCandidate]:
    """Scan the repository and discover all potential tool entry points across languages.

    Args:
        repo_path: Path to the cloned repository.
        repo_name: Clean repository name.
        repo_url: Source repository URL.
        metadata: Output from detect_project_metadata.

    Returns:
        List of DiscoveredCandidate objects with unique, normalized tool IDs.
    """
    candidates: list[DiscoveredCandidate] = []
    seen_ids: set[str] = set()
    repo_slug = re.sub(r"[^a-zA-Z0-9_]", "_", repo_name.lower()).strip("_") or "repo"
    description = metadata.get("description", "")

    # -----------------------------------------------------------------------
    # 1. Python declared CLI entry points (pyproject.toml across repo/packages)
    # -----------------------------------------------------------------------
    for pyproject_file in repo_path.glob("**/pyproject.toml"):
        if any(ign in pyproject_file.parts for ign in IGNORED_DIR_NAMES):
            continue
        try:
            data = tomllib.loads(pyproject_file.read_text(encoding="utf-8", errors="ignore"))
            scripts = data.get("project", {}).get("scripts", {})
            if not scripts:
                scripts = data.get("tool", {}).get("poetry", {}).get("scripts", {})
            pkg_rel = str(pyproject_file.relative_to(repo_path)).replace("\\", "/")
            for script_name, target in scripts.items():
                tid = generate_tool_id("github", repo_slug, "cli", script_name, existing_ids=seen_ids)
                seen_ids.add(tid)
                candidates.append(
                    DiscoveredCandidate(
                        source_file=pkg_rel,
                        symbol_name=str(target),
                        language="Python",
                        framework="cli",
                        confidence=0.95,
                        invocation_method="cli_command",
                        tool_id=tid,
                        name=f"{repo_name}: {script_name}",
                        description=f"CLI entrypoint '{script_name}' -> {target}. {description[:120]}",
                        capabilities=[f"{repo_slug}.{script_name}", "cli", "python"],
                        input_schema={
                            "type": "object",
                            "properties": {
                                "args": {"type": "array", "items": {"type": "string"}, "description": "Command line arguments"}
                            },
                        },
                        output_schema={
                            "type": "object",
                            "properties": {
                                "stdout": {"type": "string"},
                                "stderr": {"type": "string"},
                                "exit_code": {"type": "integer"},
                            },
                        },
                        is_executable=True,
                    )
                )
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # 2. Node.js / TypeScript package.json bin (across repo/packages)
    # -----------------------------------------------------------------------
    for pkg_file in repo_path.glob("**/package.json"):
        if any(ign in pkg_file.parts for ign in IGNORED_DIR_NAMES):
            continue
        try:
            pkg_data = json.loads(pkg_file.read_text(encoding="utf-8", errors="ignore"))
            bin_data = pkg_data.get("bin", {})
            if isinstance(bin_data, str):
                bin_name = pkg_data.get("name", repo_name)
                bin_data = {bin_name: bin_data}

            pkg_parent = pkg_file.parent.relative_to(repo_path)
            for bin_name, bin_rel in bin_data.items():
                target_rel = str(pkg_parent / bin_rel).replace("\\", "/") if str(pkg_parent) != "." else str(bin_rel).replace("\\", "/")
                tid = generate_tool_id("github", repo_slug, "bin", bin_name, existing_ids=seen_ids)
                seen_ids.add(tid)
                candidates.append(
                    DiscoveredCandidate(
                        source_file=target_rel,
                        symbol_name=bin_name,
                        language="JavaScript" if not target_rel.endswith(".ts") else "TypeScript",
                        framework="node",
                        confidence=0.90,
                        invocation_method="node_script",
                        tool_id=tid,
                        name=f"{repo_name}: {bin_name}",
                        description=f"Node.js binary '{bin_name}' from {target_rel}. {description[:120]}",
                        capabilities=[f"{repo_slug}.{bin_name}", "nodejs", "cli"],
                        input_schema={"type": "object", "properties": {"args": {"type": "array", "items": {"type": "string"}}}},
                        output_schema={"type": "object", "properties": {"stdout": {"type": "string"}, "exit_code": {"type": "integer"}}},
                        is_executable=True,
                    )
                )
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # 3. Docker / Container definitions (Dockerfile, .actor/Dockerfile)
    # -----------------------------------------------------------------------
    for dpath in repo_path.glob("**/Dockerfile"):
        if any(ign in dpath.parts for ign in IGNORED_DIR_NAMES):
            continue
        rel = dpath.relative_to(repo_path)
        # Handle Apify actor or directory-named container
        p_name = rel.parent.name
        container_tag = p_name if p_name and p_name != "." else "container"
        tid = generate_tool_id("github", repo_slug, "container", container_tag, existing_ids=seen_ids)
        seen_ids.add(tid)
        candidates.append(
            DiscoveredCandidate(
                source_file=str(rel).replace("\\", "/"),
                symbol_name=container_tag,
                language="Dockerfile",
                framework="docker",
                confidence=0.85,
                invocation_method="container",
                tool_id=tid,
                name=f"{repo_name} Container: {container_tag}",
                description=f"Containerized tool from {rel}. {description[:120]}",
                capabilities=[f"{repo_slug}.{container_tag}", "container"],
                risk_level="high",
                is_executable=True,
            )
        )

    # -----------------------------------------------------------------------
    # 4. Go entry points (main.go or cmd/*/main.go)
    # -----------------------------------------------------------------------
    for gpath in repo_path.glob("**/main.go"):
        if any(ign in gpath.parts for ign in IGNORED_DIR_NAMES) or _is_test_file(gpath):
            continue
        rel = gpath.relative_to(repo_path)
        p_name = rel.parent.name
        tool_sub = p_name if p_name and p_name != "." else "main"
        tid = generate_tool_id("github", repo_slug, "go", tool_sub, existing_ids=seen_ids)
        seen_ids.add(tid)
        candidates.append(
            DiscoveredCandidate(
                source_file=str(rel).replace("\\", "/"),
                symbol_name="main",
                language="Go",
                framework="go",
                confidence=0.85,
                invocation_method="native_binary",
                tool_id=tid,
                name=f"{repo_name} Go: {tool_sub}",
                description=f"Go entry point from {rel}. {description[:120]}",
                capabilities=[f"{repo_slug}.{tool_sub}", "go"],
                is_executable=False,  # Compiled language discovery
                metadata={"requires_build": True, "compiler": "go"},
            )
        )

    # -----------------------------------------------------------------------
    # 5. Rust entry points (src/main.rs, src/bin/*.rs)
    # -----------------------------------------------------------------------
    for rpath in list(repo_path.glob("**/main.rs")) + list(repo_path.glob("**/bin/*.rs")):
        if any(ign in rpath.parts for ign in IGNORED_DIR_NAMES) or _is_test_file(rpath):
            continue
        rel = rpath.relative_to(repo_path)
        tool_sub = rpath.stem
        tid = generate_tool_id("github", repo_slug, "rust", tool_sub, existing_ids=seen_ids)
        seen_ids.add(tid)
        candidates.append(
            DiscoveredCandidate(
                source_file=str(rel).replace("\\", "/"),
                symbol_name=tool_sub,
                language="Rust",
                framework="cargo",
                confidence=0.85,
                invocation_method="native_binary",
                tool_id=tid,
                name=f"{repo_name} Rust: {tool_sub}",
                description=f"Rust binary entrypoint from {rel}. {description[:120]}",
                capabilities=[f"{repo_slug}.{tool_sub}", "rust"],
                is_executable=False,  # Compiled language discovery
                metadata={"requires_build": True, "compiler": "cargo"},
            )
        )

    # -----------------------------------------------------------------------
    # 6. C / C++ entry points (main.c, main.cpp)
    # -----------------------------------------------------------------------
    for cpath in list(repo_path.glob("**/main.c")) + list(repo_path.glob("**/main.cpp")):
        if any(ign in cpath.parts for ign in IGNORED_DIR_NAMES) or _is_test_file(cpath):
            continue
        rel = cpath.relative_to(repo_path)
        tool_sub = cpath.stem
        lang = "C" if cpath.suffix == ".c" else "C++"
        tid = generate_tool_id("github", repo_slug, "native", tool_sub, existing_ids=seen_ids)
        seen_ids.add(tid)
        candidates.append(
            DiscoveredCandidate(
                source_file=str(rel).replace("\\", "/"),
                symbol_name=tool_sub,
                language=lang,
                framework="native",
                confidence=0.80,
                invocation_method="native_binary",
                tool_id=tid,
                name=f"{repo_name} {lang}: {tool_sub}",
                description=f"{lang} source entry point from {rel}. {description[:120]}",
                capabilities=[f"{repo_slug}.{tool_sub}", lang.lower()],
                is_executable=False,
                metadata={"requires_build": True, "compiler": "gcc/clang"},
            )
        )

    # -----------------------------------------------------------------------
    # 7. Shell scripts (*.sh)
    # -----------------------------------------------------------------------
    for shpath in repo_path.glob("**/*.sh"):
        if any(ign in shpath.parts for ign in IGNORED_DIR_NAMES) or _is_test_file(shpath):
            continue
        rel = shpath.relative_to(repo_path)
        tool_sub = shpath.stem
        tid = generate_tool_id("github", repo_slug, "shell", tool_sub, existing_ids=seen_ids)
        seen_ids.add(tid)
        candidates.append(
            DiscoveredCandidate(
                source_file=str(rel).replace("\\", "/"),
                symbol_name=tool_sub,
                language="Shell",
                framework="bash",
                confidence=0.80,
                invocation_method="shell_script",
                tool_id=tid,
                name=f"{repo_name} Shell: {tool_sub}",
                description=f"Shell script from {rel}. {description[:120]}",
                capabilities=[f"{repo_slug}.{tool_sub}", "shell"],
                input_schema={"type": "object", "properties": {"args": {"type": "array", "items": {"type": "string"}}}},
                output_schema={"type": "object", "properties": {"stdout": {"type": "string"}, "exit_code": {"type": "integer"}}},
                is_executable=True,
            )
        )

    # -----------------------------------------------------------------------
    # 8. Python script inspection (functions & __main__)
    # -----------------------------------------------------------------------
    py_files = [
        f for f in repo_path.glob("**/*.py")
        if not any(ign in f.parts for ign in IGNORED_DIR_NAMES) and not _is_test_file(f)
    ]

    _pattern = re.compile(
        r"def\s+(main|run|handler|execute|process|cli|start|serve)\s*\(|if\s+__name__\s*==\s*['\"]__main__['\"]|click\.command|argparse\.ArgumentParser",
        re.IGNORECASE,
    )

    for py_file in py_files[:75]:  # Safety cap for large repositories
        rel = py_file.relative_to(repo_path)
        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            if _pattern.search(content):
                stem = py_file.stem
                tid = generate_tool_id("github", repo_slug, stem, existing_ids=seen_ids)
                seen_ids.add(tid)
                candidates.append(
                    DiscoveredCandidate(
                        source_file=str(rel).replace("\\", "/"),
                        symbol_name=stem,
                        language="Python",
                        framework="python",
                        confidence=0.80,
                        invocation_method="python_script",
                        tool_id=tid,
                        name=f"{repo_name}: {stem}",
                        description=f"Python tool from {rel.name}. {description[:120]}",
                        capabilities=[f"{repo_slug}.{stem}", "python"],
                        input_schema={"type": "object", "properties": {"args": {"type": "array", "items": {"type": "string"}}}},
                        output_schema={"type": "object", "properties": {"stdout": {"type": "string"}, "exit_code": {"type": "integer"}}},
                        is_executable=True,
                    )
                )
        except OSError:
            continue

    # -----------------------------------------------------------------------
    # 9. Fallback if no tools found but Python files exist
    # -----------------------------------------------------------------------
    if not candidates and py_files:
        fallback = py_files[0]
        rel = fallback.relative_to(repo_path)
        stem = fallback.stem
        tid = generate_tool_id("github", repo_slug, stem, existing_ids=seen_ids)
        seen_ids.add(tid)
        candidates.append(
            DiscoveredCandidate(
                source_file=str(rel).replace("\\", "/"),
                symbol_name=stem,
                language="Python",
                framework="python",
                confidence=0.50,
                invocation_method="python_script",
                tool_id=tid,
                name=f"{repo_name}: {stem}",
                description=f"Primary module tool from {rel.name}. {description[:120]}",
                capabilities=[f"{repo_slug}.{stem}", "python"],
                input_schema={"type": "object", "properties": {"args": {"type": "array", "items": {"type": "string"}}}},
                output_schema={"type": "object", "properties": {"stdout": {"type": "string"}, "exit_code": {"type": "integer"}}},
                is_executable=True,
            )
        )

    return candidates
