"""API schemas for tool registry endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ToolLimits(BaseModel):
    """Resource limits applied when executing a tool."""

    model_config = ConfigDict(populate_by_name=True)

    timeout_seconds: int = Field(
        default=120, ge=1, le=3600,
        description="Maximum wall-clock time allowed for a single tool invocation.",
    )
    max_memory_mb: int = Field(
        default=512, ge=64, le=8192,
        description="Memory ceiling in megabytes for the tool's runtime environment.",
    )
    max_output_kb: int = Field(
        default=1024, ge=1, le=102400,
        description="Maximum size of the tool's output payload in kilobytes.",
    )
    max_retries: int = Field(
        default=3, ge=0, le=10,
        description="Number of automatic retries on transient failure.",
    )
    cost_estimate_usd: float = Field(
        default=0.001, ge=0.0,
        description="Estimated monetary cost per invocation in USD.",
    )


class ToolManifest(BaseModel):
    """Public tool registration manifest submitted when registering a tool."""

    model_config = ConfigDict(populate_by_name=True)

    tool_id: str = Field(
        pattern=r"^[a-z][a-z0-9._-]{2,63}$",
        description="Globally unique tool identifier. Must match ^[a-z][a-z0-9._-]{2,63}$.",
    )
    name: str = Field(description="Human-readable display name of the tool.")
    version: str = Field(description="SemVer-formatted version string, e.g. '1.2.3'.")
    description: str = Field(description="Plain-language description of what the tool does.")
    capabilities: list[str] = Field(
        min_length=1,
        description="Capability tags that describe what the tool can do, e.g. ['web_search', 'read_only'].",
    )
    input_schema: dict = Field(description="JSON Schema object describing accepted input parameters.")
    output_schema: dict = Field(description="JSON Schema object describing the tool's output structure.")
    risk_level: Literal["low", "medium", "high", "critical"] = Field(
        description="Risk classification assigned by the tool owner."
    )
    side_effects: Literal[
        "none", "read_only", "write", "external_communication", "irreversible"
    ] = Field(description="Worst-case side-effect category for a single invocation.")
    permissions: list[str] = Field(
        default_factory=list,
        description="Platform permission strings required by this tool.",
    )
    runtime_type: Literal["builtin", "container", "subprocess", "remote"] = Field(
        description="Execution model used to invoke the tool."
    )
    runtime_image: str | None = Field(
        default=None,
        description="OCI image reference when runtime_type is 'container'.",
    )
    limits: ToolLimits = Field(description="Resource limits applied during tool execution.")
    auth_required: bool = Field(
        default=False,
        description="True when the tool requires caller authentication credentials.",
    )
    source_repo: str | None = Field(
        default=None, description="URL of the source repository, for auditing purposes."
    )
    source_commit: str | None = Field(
        default=None, description="Git commit SHA pinned at registration time."
    )
    owner: str = Field(description="Team or individual responsible for this tool.")
    health_check_url: str | None = Field(
        default=None,
        description="URL polled periodically to determine the tool's health status.",
    )
    metadata: dict = Field(
        default_factory=dict,
        description="Arbitrary key-value metadata for extension without schema changes.",
    )


class ToolRecord(ToolManifest):
    """Persisted tool record returned by the registry, extending the manifest with runtime data."""

    id: str = Field(description="Internal ULID assigned by the registry at registration time.")
    registered_at: datetime = Field(description="UTC timestamp when the tool was first registered.")
    updated_at: datetime = Field(description="UTC timestamp of the most recent manifest update.")
    health_status: Literal["healthy", "degraded", "unhealthy", "unknown"] = Field(
        description="Most recently observed health status."
    )
    success_rate_7d: float | None = Field(
        default=None, ge=0.0, le=1.0,
        description="Fraction of successful invocations over the past 7 days (0.0–1.0).",
    )
    avg_latency_ms: float | None = Field(
        default=None, ge=0.0,
        description="Rolling average invocation latency in milliseconds over the past 7 days.",
    )
    is_available: bool = Field(
        default=True,
        description="False when the tool has been administratively disabled or is unhealthy.",
    )


class ToolSearchQuery(BaseModel):
    """Query parameters for the tool search endpoint."""

    model_config = ConfigDict(populate_by_name=True)

    capabilities: list[str] | None = Field(
        default=None,
        description="Filter to tools that declare all of the listed capability tags.",
    )
    risk_level_max: Literal["low", "medium", "high", "critical"] | None = Field(
        default=None,
        description="Exclude tools with a risk_level higher than this threshold.",
    )
    runtime_type: str | None = Field(
        default=None, description="Filter by runtime execution model."
    )
    text: str | None = Field(
        default=None,
        description="Free-text query matched against the tool name and description (fuzzy).",
    )
    available_only: bool = Field(
        default=True,
        description="When True, exclude tools that are disabled or unhealthy.",
    )
    page: int = Field(default=1, ge=1, description="1-based page number.")
    page_size: int = Field(default=20, ge=1, le=100, description="Items per page (max 100).")


class ValidationResult(BaseModel):
    """Result of validating a tool manifest or invocation payload."""

    model_config = ConfigDict(populate_by_name=True)

    valid: bool = Field(description="True when no blocking errors were found.")
    errors: list[str] = Field(
        default_factory=list,
        description="Blocking validation errors that must be resolved.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-blocking issues that the caller should be aware of.",
    )


class HealthStatus(BaseModel):
    """Most recently observed health status for a registered tool."""

    model_config = ConfigDict(populate_by_name=True)

    tool_id: str = Field(description="Tool identifier this status report belongs to.")
    status: Literal["healthy", "degraded", "unhealthy", "unknown"] = Field(
        description="Current health classification."
    )
    last_check: datetime | None = Field(
        default=None, description="UTC timestamp of the most recent health probe."
    )
    latency_ms: float | None = Field(
        default=None, ge=0.0,
        description="Round-trip latency measured during the last health probe.",
    )
    error: str | None = Field(
        default=None,
        description="Error detail from the last health probe, if the tool is not healthy.",
    )
