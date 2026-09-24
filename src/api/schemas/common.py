"""Common Pydantic base schemas shared across the API."""

from __future__ import annotations

from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

T = TypeVar("T")


class TimestampMixin(BaseModel):
    """Mixin that adds timezone-aware created_at and updated_at timestamps."""

    model_config = ConfigDict(populate_by_name=True)

    created_at: datetime = Field(description="UTC timestamp when the record was created.")
    updated_at: datetime = Field(description="UTC timestamp when the record was last updated.")


class PaginationParams(BaseModel):
    """Query parameters for paginated list endpoints."""

    model_config = ConfigDict(populate_by_name=True)

    page: int = Field(default=1, ge=1, description="1-based page number.")
    page_size: int = Field(
        default=20, ge=1, le=100, description="Number of items per page (max 100)."
    )

    @field_validator("page_size", mode="before")
    @classmethod
    def cap_page_size(cls, v: int) -> int:
        """Enforce a hard ceiling of 100 items per page."""
        if isinstance(v, int) and v > 100:
            raise ValueError("page_size must not exceed 100")
        return v


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic paginated response envelope."""

    model_config = ConfigDict(populate_by_name=True)

    items: list[T] = Field(description="Items in the current page.")
    total: int = Field(ge=0, description="Total number of matching records.")
    page: int = Field(ge=1, description="Current page number (1-based).")
    page_size: int = Field(ge=1, le=100, description="Number of items per page.")
    has_next: bool = Field(description="True when additional pages are available.")


class ErrorDetail(BaseModel):
    """RFC 9457 Problem Details object for HTTP error responses."""

    model_config = ConfigDict(populate_by_name=True)

    type: str = Field(
        default="about:blank",
        description="A URI reference that identifies the problem type.",
    )
    title: str = Field(description="Short, human-readable summary of the problem type.")
    status: int = Field(ge=100, le=599, description="HTTP status code.")
    detail: str = Field(description="Human-readable explanation specific to this occurrence.")
    instance: str | None = Field(
        default=None,
        description="A URI reference that identifies the specific occurrence of the problem.",
    )
    extensions: dict = Field(
        default_factory=dict,
        description="Additional members that carry problem-specific diagnostic information.",
    )


class ComponentHealth(BaseModel):
    """Health status report for a single system component."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(description="Component identifier.")
    status: str = Field(description="One of: healthy, degraded, unhealthy.")
    latency_ms: float | None = Field(
        default=None, ge=0.0, description="Round-trip latency in milliseconds, if applicable."
    )
    error: str | None = Field(
        default=None, description="Error message if the component is not healthy."
    )


class HealthResponse(BaseModel):
    """Top-level system health response."""

    model_config = ConfigDict(populate_by_name=True)

    status: Literal["healthy", "degraded", "unhealthy"] = Field(
        description="Aggregate health status of the system."
    )
    version: str = Field(description="Application version string.")
    timestamp: datetime = Field(description="UTC timestamp when the health check was performed.")
    components: dict[str, ComponentHealth] = Field(
        default_factory=dict,
        description="Per-component health details, keyed by component name.",
    )


class RequestIdMixin(BaseModel):
    """Mixin that carries distributed tracing identifiers."""

    model_config = ConfigDict(populate_by_name=True)

    request_id: str = Field(description="Unique identifier for this HTTP request.")
    trace_id: str | None = Field(
        default=None, description="Distributed trace identifier for cross-service correlation."
    )
