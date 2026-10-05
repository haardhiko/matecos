"""
detectors.py
============
Detects project types, languages, package managers, and dependencies across repositories.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def detect_project_metadata(repo_path: Path) -> dict[str, Any]:
    """Inspect repository files and detect languages, package managers, and frameworks.

    Args:
        repo_path: Root path to the cloned repository.

    Returns:
        Dictionary of detected project characteristics.
    """
    detected_languages: set[str] = set()
    detected_package_managers: list[str] = []
    detected_frameworks: set[str] = set()
    manifest_files: list[str] = []

    # 1. Python detection
    has_pyproject = (repo_path / "pyproject.toml").exists()
    has_setup_py = (repo_path / "setup.py").exists()
    has_setup_cfg = (repo_path / "setup.cfg").exists()
    has_requirements = (repo_path / "requirements.txt").exists()
    has_pipfile = (repo_path / "Pipfile").exists()
    has_poetry = (repo_path / "poetry.lock").exists()

    if has_pyproject or has_setup_py or has_setup_cfg or has_requirements or has_pipfile or has_poetry:
        detected_languages.add("Python")
        if has_poetry:
            detected_package_managers.append("poetry")
        elif (repo_path / "uv.lock").exists():
            detected_package_managers.append("uv")
        elif has_pipfile:
            detected_package_managers.append("pipenv")
        else:
            detected_package_managers.append("pip")

        if has_pyproject:
            manifest_files.append("pyproject.toml")
        if has_setup_py:
            manifest_files.append("setup.py")
        if has_requirements:
            manifest_files.append("requirements.txt")

    # 2. Node / JavaScript / TypeScript detection
    has_pkg_json = (repo_path / "package.json").exists()
    if has_pkg_json:
        detected_languages.add("JavaScript")
        manifest_files.append("package.json")
        if (repo_path / "tsconfig.json").exists():
            detected_languages.add("TypeScript")
            manifest_files.append("tsconfig.json")

        if (repo_path / "pnpm-lock.yaml").exists():
            detected_package_managers.append("pnpm")
        elif (repo_path / "yarn.lock").exists():
            detected_package_managers.append("yarn")
        else:
            detected_package_managers.append("npm")

    # 3. Go detection
    if (repo_path / "go.mod").exists():
        detected_languages.add("Go")
        detected_package_managers.append("go")
        manifest_files.append("go.mod")

    # 4. Rust detection
    if (repo_path / "Cargo.toml").exists():
        detected_languages.add("Rust")
        detected_package_managers.append("cargo")
        manifest_files.append("Cargo.toml")

    # 5. Java / Kotlin detection
    if (repo_path / "pom.xml").exists():
        detected_languages.add("Java")
        detected_package_managers.append("maven")
        manifest_files.append("pom.xml")
    if (repo_path / "build.gradle").exists() or (repo_path / "build.gradle.kts").exists():
        detected_languages.add("Java")
        detected_package_managers.append("gradle")
        manifest_files.append("build.gradle")

    # 6. C / C++ detection
    if (repo_path / "CMakeLists.txt").exists():
        detected_languages.add("C++")
        detected_package_managers.append("cmake")
        manifest_files.append("CMakeLists.txt")
    if (repo_path / "Makefile").exists() and "C++" not in detected_languages:
        manifest_files.append("Makefile")

    # 7. Container / Docker detection
    has_dockerfile = (
        (repo_path / "Dockerfile").exists()
        or (repo_path / ".actor" / "Dockerfile").exists()
        or any(repo_path.glob("**/Dockerfile"))
    )
    if has_dockerfile:
        detected_frameworks.add("docker")

    # 8. Description extraction from README
    description = ""
    for rname in ["README.md", "README.rst", "README.txt", "readme.md"]:
        rpath = repo_path / rname
        if rpath.exists():
            try:
                raw_readme = rpath.read_text(encoding="utf-8", errors="ignore")
                description = raw_readme[:500].strip()
                break
            except OSError:
                pass

    return {
        "languages": sorted(detected_languages),
        "package_managers": detected_package_managers,
        "frameworks": sorted(detected_frameworks),
        "manifest_files": manifest_files,
        "description": description or f"Repository at {repo_path.name}",
        "has_container": has_dockerfile,
    }
