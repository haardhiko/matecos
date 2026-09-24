"""ToolManifest Pydantic model — canonical internal representation of a MATECOS tool."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class RuntimeConfig(BaseModel):
    """Describes how and where a tool runs."""

    model_config = {"frozen": True}

    type: Literal["builtin", "container", "subprocess", "remote"]
    image: str | None = None
    dockerfile_path: str | None = None
    command: list[str] = Field(default_factory=list)
    entrypoint: list[str] = Field(default_factory=list)
    # Key names only — values are injected at runtime from secret store
    env_vars: dict[str, str] = Field(default_factory=dict)
    working_dir: str | None = None

    @model_validator(mode="after")
    def _validate_container_requires_image(self) -> "RuntimeConfig":
        """Container runtime must have either an image or a dockerfile_path."""
        if self.type == "container" and not self.image and not self.dockerfile_path:
            raise ValueError(
                "RuntimeConfig with type='container' requires 'image' or 'dockerfile_path'."
            )
        return self


class ResourceLimits(BaseModel):
    """Hard resource caps enforced by the executor / sandbox."""

    model_config = {"frozen": True}

    timeout_seconds: int = 120
    max_memory_mb: int = 512
    max_cpu_quota: int = 50_000  # 50% of one CPU in Docker units (1e5 == 100%)
    max_cpu_percent: int | None = None
    max_execution_seconds: int | None = None
    max_disk_mb: int = 512
    max_output_kb: int = 1024
    max_retries: int = 3
    cost_estimate_usd: float = 0.001

    @field_validator("timeout_seconds")
    @classmethod
    def _timeout_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("timeout_seconds must be > 0")
        return v

    @field_validator("max_memory_mb")
    @classmethod
    def _memory_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_memory_mb must be > 0")
        return v


class SideEffects(str, Enum):
    """Describes what persistent side-effects a tool may produce."""

    NONE = "none"
    READ_ONLY = "read_only"
    WRITE = "write"
    EXTERNAL_COMMUNICATION = "external_communication"
    IRREVERSIBLE = "irreversible"


class NetworkPolicy(BaseModel):
    """Network egress policy for a tool."""

    model_config = {"frozen": True}

    allowed: bool = False
    allowed_domains: list[str] = Field(default_factory=list)
    requires_proxy: bool = True

    DENY_ALL: ClassVar[NetworkPolicy]
    ALLOW_ALL: ClassVar[NetworkPolicy]

    @property
    def value(self) -> str:
        return "allow_all" if self.allowed else "deny_all"


NetworkPolicy.DENY_ALL = NetworkPolicy(allowed=False, requires_proxy=True)
NetworkPolicy.ALLOW_ALL = NetworkPolicy(allowed=True, allowed_domains=["*"], requires_proxy=False)


class FilesystemPolicy(BaseModel):
    """Filesystem access policy for a tool."""

    model_config = {"frozen": True}

    allowed_read_paths: list[str] = Field(default_factory=list)
    allowed_write_paths: list[str] = Field(default_factory=lambda: ["/output"])
    # MUST always be False for untrusted tools
    host_secret_access: bool = False

    READ_ONLY: ClassVar[FilesystemPolicy]
    READ_WRITE_SCOPED: ClassVar[FilesystemPolicy]
    DENY_ALL: ClassVar[FilesystemPolicy]

    @property
    def value(self) -> str:
        if self.allowed_write_paths:
            return "read_write_scoped"
        return "read_only"

    @field_validator("host_secret_access")
    @classmethod
    def _no_secret_access(cls, v: bool) -> bool:
        if v:
            raise ValueError(
                "host_secret_access must be False; tools may not access host secrets directly."
            )
        return v


FilesystemPolicy.READ_ONLY = FilesystemPolicy(
    allowed_read_paths=["/"], allowed_write_paths=[], host_secret_access=False
)
FilesystemPolicy.READ_WRITE_SCOPED = FilesystemPolicy(
    allowed_read_paths=["/"], allowed_write_paths=["/output"], host_secret_access=False
)
FilesystemPolicy.DENY_ALL = FilesystemPolicy(
    allowed_read_paths=[], allowed_write_paths=[], host_secret_access=False
)


class SecurityPolicy(BaseModel):
    """Security constraints and audit metadata for a tool."""

    model_config = {"frozen": True}

    requires_scan: bool = True
    requires_human_approval: bool = False
    scan_passed: bool = False
    scan_date: datetime | None = None
    # MUST always be False — tools never run as root
    run_as_root: bool = False
    seccomp_profile: str = "default"
    network: NetworkPolicy = Field(default_factory=NetworkPolicy)
    filesystem: FilesystemPolicy = Field(default_factory=FilesystemPolicy)

    @field_validator("run_as_root")
    @classmethod
    def _no_root(cls, v: bool) -> bool:
        if v:
            raise ValueError("run_as_root must be False; tools may never run as root.")
        return v


class ToolManifest(BaseModel):
    """
    Canonical internal representation of a MATECOS tool.

    This is the single source of truth for tool metadata used throughout the
    registry, executor, selector, and audit subsystems.
    """

    model_config = {"frozen": True}

    # Identity
    tool_id: str  # e.g. 'data.csv.profile'
    name: str
    version: str  # semver: MAJOR.MINOR.PATCH
    description: str

    # Capability declaration
    capabilities: list[str]
    input_schema: dict  # JSON Schema draft-2020-12 object
    output_schema: dict  # JSON Schema draft-2020-12 object

    # Risk + side-effect classification
    risk_level: Literal["low", "medium", "high", "critical"]
    side_effects: SideEffects = SideEffects.NONE
    permissions: list[str] = Field(default_factory=list)

    # Execution
    runtime: RuntimeConfig
    limits: ResourceLimits = Field(default_factory=ResourceLimits)
    resource_limits: ResourceLimits | None = None
    security: SecurityPolicy = Field(default_factory=SecurityPolicy)

    # Provenance
    source_repo: str | None = None
    source_commit: str | None = None
    owner: str = "system"

    # Operations
    health_check_command: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _remap_resource_limits(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "resource_limits" in data and "limits" not in data:
                data["limits"] = data.pop("resource_limits")
        return data

    @field_validator("tool_id")
    @classmethod
    def _validate_tool_id(cls, v: str) -> str:
        import re

        pattern = r"^[a-z][a-z0-9]*(\.[a-z][a-z0-9_]*)+$"
        if not re.fullmatch(pattern, v):
            raise ValueError(
                f"tool_id '{v}' is invalid; must match pattern '{pattern}' (e.g. 'data.csv.profile')."
            )
        return v

    @field_validator("version")
    @classmethod
    def _validate_semver(cls, v: str) -> str:
        import re

        # Strict semver: MAJOR.MINOR.PATCH with optional pre-release and build metadata
        pattern = (
            r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
            r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
            r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
            r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
        )
        if not re.fullmatch(pattern, v):
            raise ValueError(
                f"version '{v}' is not valid semver (e.g. '1.0.0', '2.3.1-alpha.1')."
            )
        return v

    @model_validator(mode="after")
    def _high_risk_requires_scan(self) -> "ToolManifest":
        """High and critical risk tools must have passed a security scan."""
        if self.risk_level in ("high", "critical") and not self.security.scan_passed:
            raise ValueError(
                f"High-risk and critical tools must pass security scanning (risk_level='{self.risk_level}', require security.scan_passed=True)."
            )
        return self
