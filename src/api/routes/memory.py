"""Memory and audit trail endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_config

from src.api.dependencies.auth import get_current_user
from src.api.schemas.common import ErrorDetail

logger: structlog.BoundLogger = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1/memory", tags=["Memory"])

# ---------------------------------------------------------------------------
# Response schemas defined here to avoid cluttering the shared schemas file
# ---------------------------------------------------------------------------


class AuditEventResponse(BaseModel):
    """A single audit event associated with an execution.

    Only events at ``public`` or ``internal`` sensitivity levels are returned
    by this endpoint.  Events at ``sensitive`` or higher are never exposed via
    the API.
    """

    model_config = model_config(populate_by_name=True)

    event_id: str = Field(description="Unique identifier for this audit event (ULID).")
    execution_id: str = Field(description="Parent execution this event belongs to.")
    event_type: str = Field(description="Short, namespaced event type string, e.g. 'REQUEST_RECEIVED'.")
    user_id: str = Field(description="Identifier of the actor who triggered this event.")
    timestamp: datetime = Field(description="UTC timestamp when the event was recorded.")
    sensitivity: Literal["public", "internal"] = Field(
        description="Sensitivity level of this event. Only public/internal events are returned."
    )
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured event payload. Never contains raw user input or private reasoning.",
    )


class TimelineEntry(BaseModel):
    """A single entry in the reconstructed execution timeline."""

    model_config = model_config(populate_by_name=True)

    sequence: int = Field(description="Monotonically increasing sequence number within the execution.")
    timestamp: datetime = Field(description="UTC timestamp of the event.")
    event_type: str = Field(description="Event category.")
    actor: str = Field(description="Who triggered this event: user, agent role, or system.")
    description: str = Field(description="Human-readable description of what occurred.")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured metadata for this timeline entry. No raw CoT or private reasoning.",
    )


# ---------------------------------------------------------------------------
# In-process audit store stub
# ---------------------------------------------------------------------------

# Populated by other routes / orchestration layer; keyed by execution_id.
_AUDIT_STORE: dict[str, list[dict[str, Any]]] = {}

# Shadow reference to the request store from the requests route.
# In production this is DB-backed; here we import lazily to avoid circularity.
_EXECUTION_REF: dict[str, dict[str, Any]] | None = None


def _get_execution_store() -> dict[str, dict[str, Any]]:
    """Return the in-process execution store from the requests module.

    Returns:
        The shared ``_EXECUTIONS`` dict from the requests module.
    """
    global _EXECUTION_REF
    if _EXECUTION_REF is None:
        try:
            from src.api.routes.requests import _EXECUTIONS  # type: ignore[import-not-found]
            _EXECUTION_REF = _EXECUTIONS
        except ImportError:
            _EXECUTION_REF = {}
    return _EXECUTION_REF


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
        **extensions: Arbitrary extra key-value extensions.

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


def _assert_execution_visibility(
    execution_id: str,
    user: dict[str, Any],
) -> dict[str, Any]:
    """Assert the user may access the given execution.

    Args:
        execution_id: ULID of the execution to check.
        user: Current-user dict.

    Returns:
        The execution record dict.

    Raises:
        HTTPException: 404 when the execution does not exist or the user is
            not permitted to see it.
    """
    store = _get_execution_store()
    execution = store.get(execution_id)
    if execution is None:
        raise _problem(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Execution Not Found",
            detail=f"No execution found with id '{execution_id}'.",
            instance=f"/v1/memory/{execution_id}/events",
        )
    is_admin = "admin" in user.get("scopes", [])
    if not is_admin and execution.get("user_id") != user.get("id"):
        # Return 404 — do not reveal existence of other users' executions
        raise _problem(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Execution Not Found",
            detail=f"No execution found with id '{execution_id}'.",
            instance=f"/v1/memory/{execution_id}",
        )
    return execution


# ---------------------------------------------------------------------------
# GET /v1/memory/{execution_id}/events
# ---------------------------------------------------------------------------


@router.get(
    "/{execution_id}/events",
    response_model=list[AuditEventResponse],
    summary="Get audit events for an execution",
)
async def get_execution_events(
    execution_id: str,
    event_type: str | None = Query(default=None, description="Filter by event type."),
    limit: int = Query(default=50, ge=1, le=500, description="Maximum number of events to return."),
    offset: int = Query(default=0, ge=0, description="Number of events to skip."),
    user: dict[str, Any] = Depends(get_current_user),
) -> list[AuditEventResponse]:
    """Return audit events for an execution owned by the authenticated user.

    Events are filtered by sensitivity: only ``public`` and ``internal``
    events are returned.  Events classified as ``sensitive`` or higher are
    **never** surfaced via the API regardless of the caller's role.

    Args:
        execution_id: ULID of the target execution.
        event_type: Optional filter to return only events of a specific type.
        limit: Maximum number of events to return (1–500).
        offset: Number of events to skip (for manual pagination).
        user: Authenticated user dict.

    Returns:
        Ordered list of :class:`AuditEventResponse` records (oldest first).

    Raises:
        HTTPException: 404 when the execution does not exist or is not owned
            by the caller.
    """
    log = logger.bind(user_id=user["id"], execution_id=execution_id)

    _assert_execution_visibility(execution_id, user)

    raw_events: list[dict[str, Any]] = _AUDIT_STORE.get(execution_id, [])

    # Filter by sensitivity — only public/internal events are API-visible
    safe_events = [
        e for e in raw_events
        if e.get("sensitivity", "internal") in ("public", "internal")
    ]

    # Optional event type filter
    if event_type is not None:
        safe_events = [e for e in safe_events if e.get("event_type") == event_type]

    # Apply offset + limit
    paged = safe_events[offset: offset + limit]

    log.debug("memory.events", total=len(safe_events), returned=len(paged))

    return [
        AuditEventResponse(
            event_id=e.get("event_id", "unknown"),
            execution_id=execution_id,
            event_type=e.get("event_type", "UNKNOWN"),
            user_id=e.get("user_id", ""),
            timestamp=e.get("timestamp", datetime.now(UTC)),
            sensitivity=e.get("sensitivity", "internal"),
            data=e.get("data", {}),
        )
        for e in paged
    ]


# ---------------------------------------------------------------------------
# GET /v1/memory/{execution_id}/timeline
# ---------------------------------------------------------------------------


@router.get(
    "/{execution_id}/timeline",
    response_model=list[TimelineEntry],
    summary="Get reconstructed execution timeline (operator view)",
)
async def get_execution_timeline(
    execution_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> list[TimelineEntry]:
    """Return a chronologically reconstructed timeline of an execution.

    The timeline is assembled from audit events and agent decision records.
    It is designed for operator-level observability: entries describe *what*
    happened (tool calls, delegations, approvals) but **never** include
    private reasoning, raw chain-of-thought, or sensitive input data.

    Args:
        execution_id: ULID of the target execution.
        user: Authenticated user dict.

    Returns:
        Ordered list of :class:`TimelineEntry` records (oldest first).

    Raises:
        HTTPException: 404 when the execution does not exist or is not owned
            by the caller.
    """
    log = logger.bind(user_id=user["id"], execution_id=execution_id)

    execution = _assert_execution_visibility(execution_id, user)

    raw_events: list[dict[str, Any]] = _AUDIT_STORE.get(execution_id, [])

    # Keep only non-sensitive events for the timeline
    safe_events = [
        e for e in raw_events
        if e.get("sensitivity", "internal") in ("public", "internal")
    ]

    entries: list[TimelineEntry] = []
    seq = 1

    # Synthesise a timeline entry for the execution creation
    entries.append(
        TimelineEntry(
            sequence=seq,
            timestamp=execution.get("created_at", datetime.now(UTC)),
            event_type="EXECUTION_CREATED",
            actor=execution.get("user_id", "user"),
            description="Execution created and queued for orchestration.",
            metadata={"request_id": execution.get("request_id", "")},
        )
    )
    seq += 1

    # One entry per audit event (structured, no CoT)
    for event in sorted(safe_events, key=lambda e: e.get("timestamp", datetime.min)):
        event_type: str = event.get("event_type", "UNKNOWN")

        # Map event types to human-readable descriptions
        description = _describe_event(event_type, event.get("data", {}))

        # Determine the actor from the event
        actor = _determine_actor(event)

        entries.append(
            TimelineEntry(
                sequence=seq,
                timestamp=event.get("timestamp", datetime.now(UTC)),
                event_type=event_type,
                actor=actor,
                description=description,
                metadata={
                    k: v for k, v in event.get("data", {}).items()
                    # Strip any fields that might inadvertently carry raw input
                    if k not in ("goal_text", "raw_input", "chain_of_thought", "reasoning")
                },
            )
        )
        seq += 1

    log.debug("memory.timeline", entry_count=len(entries))
    return entries


# ---------------------------------------------------------------------------
# Timeline helpers — no CoT, no raw input
# ---------------------------------------------------------------------------


def _describe_event(event_type: str, data: dict[str, Any]) -> str:
    """Convert an event type to a short human-readable description.

    Args:
        event_type: Namespaced event type string.
        data: Structured event data dict (sanitised).

    Returns:
        A short, operator-friendly description string.
    """
    descriptions: dict[str, str] = {
        "REQUEST_RECEIVED": "User request received and validated.",
        "CANCELLATION_REQUESTED": "Execution cancellation requested by user.",
        "ACTION_APPROVED": f"Approval checkpoint approved (id: {data.get('approval_id', '?')}).",
        "ACTION_REJECTED": f"Approval checkpoint rejected (id: {data.get('approval_id', '?')}).",
        "AGENT_SPAWNED": f"Agent spawned with role '{data.get('role', 'unknown')}'.",
        "AGENT_COMPLETED": f"Agent completed with status '{data.get('status', 'unknown')}'.",
        "TOOL_CALLED": f"Tool '{data.get('tool_id', 'unknown')}' invoked.",
        "TOOL_RESULT": f"Tool '{data.get('tool_id', 'unknown')}' returned a result.",
        "TASK_STARTED": f"Task '{data.get('task_id', '?')}' started.",
        "TASK_COMPLETED": f"Task '{data.get('task_id', '?')}' completed.",
        "APPROVAL_REQUESTED": "Human approval checkpoint raised.",
        "RISK_ASSESSMENT": f"Risk assessment result: {data.get('decision', 'unknown')}.",
    }
    return descriptions.get(event_type, f"Event: {event_type}.")


def _determine_actor(event: dict[str, Any]) -> str:
    """Derive a display-safe actor label from an event record.

    Args:
        event: Raw event dict.

    Returns:
        A short actor string such as ``'system'``, ``'user'``, or a role label.
    """
    data = event.get("data", {})
    if "agent_role" in data:
        return f"agent:{data['agent_role']}"
    if event.get("user_id"):
        user_id: str = event["user_id"]
        if user_id.startswith("agent:"):
            return user_id
        return "user"
    return "system"
