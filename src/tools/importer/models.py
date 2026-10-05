"""
models.py
=========
Data models for the MATECOS Universal Tool Importer Pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class DiscoveredCandidate:
    """A discovered candidate tool before manifest construction and registration."""

    source_file: str
    symbol_name: str
    language: str
    framework: str = "generic"
    confidence: float = 0.8
    invocation_method: str = "python_script"  # python_script, cli_command, node_script, shell_script, container, native_binary, library_symbol
    dependencies: list[str] = field(default_factory=list)
    tool_id: str = ""
    name: str = ""
    description: str = ""
    capabilities: list[str] = field(default_factory=list)
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    risk_level: str = "medium"
    is_executable: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolImportError:
    """Record of a tool or step that failed during import."""

    candidate_id: str
    stage: str  # discovery, normalization, validation, registration, dependencies
    error: str
    file_path: str = ""


@dataclass
class ImportReport:
    """Structured report returned by the Universal Importer Pipeline."""

    repository: str
    url: str
    branch: str = "main"
    status: Literal["success", "partial", "failed"] = "success"
    tools_discovered: int = 0
    tools_registered: int = 0
    tools_failed: int = 0
    tools: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    languages: dict[str, int] = field(default_factory=dict)
    detected_frameworks: list[str] = field(default_factory=list)
    dependency_status: dict[str, Any] = field(default_factory=dict)
    total_files: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "url": self.url,
            "branch": self.branch,
            "status": self.status,
            "tools_discovered": self.tools_discovered,
            "tools_registered": self.tools_registered,
            "tools_failed": self.tools_failed,
            "tools": self.tools,
            "errors": self.errors,
            "languages": self.languages,
            "detected_frameworks": self.detected_frameworks,
            "dependency_status": self.dependency_status,
            "total_files": self.total_files,
        }
