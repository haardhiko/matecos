"""Tool registry API endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
import ulid
from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.api.dependencies.auth import get_current_user, require_scope
from src.api.schemas.common import ErrorDetail, PaginatedResponse
from src.api.schemas.tools import HealthStatus, ToolManifest, ToolRecord, ToolSearchQuery

logger: structlog.BoundLogger = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1/tools", tags=["Tools"])

def _get_shared_registry():
    """Return the shared ToolRegistry populated by github_tools (all 15 builtins + GitHub imports).

    Lazy import avoids circular dependency at module load time.
    """
    from src.api.routes.github_tools import get_registry  # noqa: PLC0415
    return get_registry()


_TOOL_STORE: dict[str, ToolRecord] = {}  # tool_id -> ToolRecord



def _problem(
    status_code: int,
    title: str,
    detail: str,
    instance: str | None = None,
    problem_type: str = "about:blank",
    **extensions: Any,
) -> HTTPException:
    """Build an RFC 9457-compliant :class:`HTTPException`.

    Args:
        status_code: HTTP status code.
        title: Short problem-type summary.
        detail: Human-readable occurrence-specific explanation.
        instance: URI identifying the specific occurrence.
        problem_type: URI identifying the problem type.
        **extensions: Arbitrary extra fields included in the problem body.

    Returns:
        :class:`HTTPException` with a structured ``detail`` payload.
    """
    body = ErrorDetail(
        type=problem_type,
        title=title,
        status=status_code,
        detail=detail,
        instance=instance,
        extensions=dict(extensions),
    )
    return HTTPException(status_code=status_code, detail=body.model_dump())


_RISK_ORDER: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _risk_within_max(tool_risk: str, max_risk: str) -> bool:
    """Return True when ``tool_risk`` is at or below ``max_risk``.

    Args:
        tool_risk: The tool's risk_level string.
        max_risk: The caller's requested ceiling.

    Returns:
        ``True`` when the tool's risk is within the permitted ceiling.
    """
    return _RISK_ORDER.get(tool_risk, 99) <= _RISK_ORDER.get(max_risk, 99)


# ---------------------------------------------------------------------------
# Converter: internal ToolManifest → API ToolRecord schema
# ---------------------------------------------------------------------------

import ulid as _ulid  # noqa: E402
from datetime import UTC as _UTC, datetime as _dt  # noqa: E402


def _manifest_to_record(rec) -> ToolRecord:
    """Convert an internal registry ToolRecord to the API ToolRecord schema."""
    m = rec.manifest
    now = rec.registered_at

    runtime_type = "builtin"
    runtime_image = None
    if hasattr(m, "runtime") and m.runtime:
        runtime_type = m.runtime.type
        runtime_image = getattr(m.runtime, "image", None)

    from src.api.schemas.tools import ToolLimits  # noqa: PLC0415
    limits_data = {"timeout_seconds": 120, "max_memory_mb": 512,
                   "max_output_kb": 1024, "max_retries": 3, "cost_estimate_usd": 0.001}
    if hasattr(m, "resource_limits") and m.resource_limits:
        rl = m.resource_limits
        limits_data = {
            "timeout_seconds": getattr(rl, "timeout_seconds", 120),
            "max_memory_mb": getattr(rl, "max_memory_mb", 512),
            "max_output_kb": getattr(rl, "max_output_kb", 1024),
            "max_retries": getattr(rl, "max_retries", 3),
            "cost_estimate_usd": getattr(rl, "cost_estimate_usd", 0.001),
        }
    limits = ToolLimits(**limits_data)

    side_effects = "none"
    if hasattr(m, "side_effects") and m.side_effects:
        se = m.side_effects
        side_effects = se.value if hasattr(se, "value") else str(se)

    health = rec.health_status if rec.health_status in ("healthy", "degraded", "unhealthy", "unknown") else "unknown"

    return ToolRecord(
        id=_ulid.new().str,
        tool_id=m.tool_id,
        name=m.name,
        version=m.version,
        description=m.description,
        capabilities=list(m.capabilities),
        input_schema=dict(m.input_schema),
        output_schema=dict(m.output_schema),
        risk_level=m.risk_level,
        side_effects=side_effects,
        permissions=[],
        runtime_type=runtime_type,
        runtime_image=runtime_image,
        limits=limits,
        auth_required=False,
        source_repo=getattr(m, "source_repo", None),
        source_commit=getattr(m, "source_commit", None),
        owner=getattr(m, "owner", "matecos"),
        health_check_url=None,
        metadata={},
        registered_at=now,
        updated_at=now,
        health_status=health,
        success_rate_7d=None,
        avg_latency_ms=rec.avg_latency_ms if rec.avg_latency_ms else None,
        is_available=rec.enabled,
    )


def _all_tools_as_records() -> list[ToolRecord]:
    """Return all tools from the shared registry and _TOOL_STORE as API ToolRecord objects."""
    seen: set[str] = set()
    result: list[ToolRecord] = []

    # 1. Custom stored tools
    for tool_id, rec in _TOOL_STORE.items():
        if "@" not in tool_id and tool_id not in seen:
            seen.add(tool_id)
            result.append(rec)

    # 2. Shared registry tools (15 builtins + GitHub imports)
    registry = _get_shared_registry()
    for rec in registry.list_all():
        if rec.tool_id not in seen:
            seen.add(rec.tool_id)
            try:
                result.append(_manifest_to_record(rec))
            except Exception:
                pass

    return result


# ---------------------------------------------------------------------------
# GET /v1/tools
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=PaginatedResponse[ToolRecord],
    summary="List registered tools",
)
async def list_tools(
    query: ToolSearchQuery = Depends(),
    user: dict[str, Any] = Depends(get_current_user),
) -> PaginatedResponse[ToolRecord]:
    """Return a paginated, filtered list of registered tools.

    Supports filtering by capability tags, maximum risk level, runtime type,
    free-text search, and availability status.

    Args:
        query: Parsed query parameters (injected by FastAPI ``Depends``).
        user: Authenticated user dict (required for auth gate; content unused).

    Returns:
        :class:`~src.api.schemas.common.PaginatedResponse` of
        :class:`~src.api.schemas.tools.ToolRecord` items.
    """
    log = logger.bind(user_id=user["id"])
    tools = _all_tools_as_records()

    # Capability filter — tool must declare ALL requested capabilities
    if query.capabilities:
        tools = [t for t in tools if all(cap in t.capabilities for cap in query.capabilities)]

    # Risk ceiling
    if query.risk_level_max:
        tools = [t for t in tools if _risk_within_max(t.risk_level, query.risk_level_max)]

    # Runtime type
    if query.runtime_type:
        tools = [t for t in tools if t.runtime_type == query.runtime_type]

    # Availability
    if query.available_only:
        tools = [t for t in tools if t.is_available]

    # Free-text filter (simple substring match against name + description)
    if query.text:
        needle = query.text.lower()
        tools = [t for t in tools if needle in t.name.lower() or needle in t.description.lower()]

    total = len(tools)

    # Pagination
    start = (query.page - 1) * query.page_size
    end = start + query.page_size
    page_items = tools[start:end]

    log.debug("tools.list", total=total, page=query.page, page_size=query.page_size)

    return PaginatedResponse(
        items=page_items,
        total=total,
        page=query.page,
        page_size=query.page_size,
        has_next=end < total,
    )


# ---------------------------------------------------------------------------
# GET /v1/tools/{tool_id}
# ---------------------------------------------------------------------------


@router.get(
    "/{tool_id}",
    response_model=ToolRecord,
    summary="Get a single registered tool",
)
async def get_tool(
    tool_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> ToolRecord:
    """Return the full registration record for a specific tool.

    Args:
        tool_id: The globally unique tool identifier.
        user: Authenticated user dict.

    Returns:
        :class:`~src.api.schemas.tools.ToolRecord` for the requested tool.

    Raises:
        HTTPException: 404 when ``tool_id`` is not registered.
    """
    tool = _TOOL_STORE.get(tool_id)
    if tool is not None:
        logger.debug("tools.get", tool_id=tool_id, user_id=user["id"])
        return tool

    registry = _get_shared_registry()
    rec = registry.get(tool_id)
    if rec is not None:
        try:
            record = _manifest_to_record(rec)
            logger.debug("tools.get", tool_id=tool_id, user_id=user["id"])
            return record
        except Exception:
            pass

    raise _problem(
        status_code=status.HTTP_404_NOT_FOUND,
        title="Tool Not Found",
        detail=f"No tool registered with id '{tool_id}'.",
        instance=f"/v1/tools/{tool_id}",
    )


# ---------------------------------------------------------------------------
# POST /v1/tools — register a new tool (admin only)
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=ToolRecord,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new tool (admin)",
)
async def register_tool(
    manifest: ToolManifest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
    _admin: dict[str, Any] = Depends(require_scope("admin")),
) -> ToolRecord:
    """Register a new tool in the registry.

    Requires the ``admin`` scope.  If a tool with the same ``tool_id`` and
    ``version`` is already registered a 409 Conflict is returned.

    Args:
        manifest: Full tool manifest submitted by the caller.
        request: Raw FastAPI request (unused; kept for potential future use).
        user: Authenticated user dict.
        _admin: Scope-check dependency result (enforces admin scope).

    Returns:
        Newly created :class:`~src.api.schemas.tools.ToolRecord`.

    Raises:
        HTTPException: 409 when the tool_id/version already exists.
    """
    log = logger.bind(user_id=user["id"], tool_id=manifest.tool_id, version=manifest.version)

    composite_key = f"{manifest.tool_id}@{manifest.version}"
    if composite_key in _TOOL_STORE:
        raise _problem(
            status_code=status.HTTP_409_CONFLICT,
            title="Tool Already Registered",
            detail=(
                f"Tool '{manifest.tool_id}' version '{manifest.version}' is already registered. "
                "Use a new version string to update."
            ),
            instance=f"/v1/tools/{manifest.tool_id}",
        )

    now = datetime.now(UTC)
    record = ToolRecord(
        **manifest.model_dump(),
        id=ulid.new().str,
        registered_at=now,
        updated_at=now,
        health_status="unknown",
        is_available=True,
    )
    _TOOL_STORE[composite_key] = record
    # Also index by bare tool_id for the most-recent-version lookup
    _TOOL_STORE[manifest.tool_id] = record

    log.info("tools.registered")
    return record


# ---------------------------------------------------------------------------
# DELETE /v1/tools/{tool_id}/{version} — unregister (admin only)
# ---------------------------------------------------------------------------


@router.delete(
    "/{tool_id}/{version}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unregister a tool version (admin)",
)
async def unregister_tool(
    tool_id: str,
    version: str,
    user: dict[str, Any] = Depends(get_current_user),
    _admin: dict[str, Any] = Depends(require_scope("admin")),
) -> None:
    """Remove a specific version of a tool from the registry.

    Requires the ``admin`` scope.  Returns 204 on success; 404 when the
    ``tool_id``/``version`` combination is not found.

    Args:
        tool_id: Tool identifier.
        version: SemVer version string to remove.
        user: Authenticated user dict.
        _admin: Scope-check dependency result.

    Raises:
        HTTPException: 404 when the tool/version is not registered.
    """
    log = logger.bind(user_id=user["id"], tool_id=tool_id, version=version)

    composite_key = f"{tool_id}@{version}"
    if composite_key not in _TOOL_STORE:
        raise _problem(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Tool Version Not Found",
            detail=f"No tool '{tool_id}' version '{version}' is registered.",
            instance=f"/v1/tools/{tool_id}/{version}",
        )

    del _TOOL_STORE[composite_key]

    # Remove the bare-id index if it pointed at this version
    bare_entry = _TOOL_STORE.get(tool_id)
    if bare_entry is not None and bare_entry.version == version:
        del _TOOL_STORE[tool_id]

    log.info("tools.unregistered")


# ---------------------------------------------------------------------------
# GET /v1/tools/{tool_id}/health
# ---------------------------------------------------------------------------


@router.get(
    "/{tool_id}/health",
    response_model=HealthStatus,
    summary="Get the most recently observed health status of a tool",
)
async def get_tool_health(
    tool_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> HealthStatus:
    """Return the most recently cached health status for a tool.

    The health status is updated by the background health-check scheduler.
    This endpoint returns a snapshot — it does not trigger a live probe.

    Args:
        tool_id: Tool identifier.
        user: Authenticated user dict.

    Returns:
        :class:`~src.api.schemas.tools.HealthStatus` snapshot.

    Raises:
        HTTPException: 404 when the tool is not registered.
    """
    tool = _TOOL_STORE.get(tool_id)
    if tool is not None:
        logger.debug("tools.health_check", tool_id=tool_id, user_id=user["id"])
        return HealthStatus(
            tool_id=tool_id,
            status=tool.health_status,
            last_check=tool.updated_at,
            latency_ms=tool.avg_latency_ms,
            error=None if tool.health_status == "healthy" else "Tool reported non-healthy status",
        )

    registry = _get_shared_registry()
    rec = registry.get(tool_id)
    if rec is not None:
        health = rec.health_status if rec.health_status in ("healthy", "degraded", "unhealthy", "unknown") else "healthy"
        logger.debug("tools.health_check", tool_id=tool_id, user_id=user["id"])
        return HealthStatus(
            tool_id=tool_id,
            status=health,
            last_check=rec.registered_at,
            latency_ms=rec.avg_latency_ms if rec.avg_latency_ms else None,
            error=None if health == "healthy" else "Tool reported non-healthy status",
        )

    raise _problem(
        status_code=status.HTTP_404_NOT_FOUND,
        title="Tool Not Found",
        detail=f"No tool registered with id '{tool_id}'.",
        instance=f"/v1/tools/{tool_id}/health",
    )
