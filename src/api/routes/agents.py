"""Agent status endpoints."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status

from src.api.dependencies.auth import get_current_user
from src.api.schemas.agents import AgentStatusItem, DecisionRecord
from src.api.schemas.common import ErrorDetail, PaginatedResponse

logger: structlog.BoundLogger = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1/agents", tags=["Agents"])

# ---------------------------------------------------------------------------
# In-process store stubs (replaced by DB queries when infra is wired)
# ---------------------------------------------------------------------------

# These are populated by the orchestration layer during execution.
_AGENT_STORE: dict[str, dict[str, Any]] = {}   # agent_id -> agent record
_DECISION_STORE: dict[str, list[dict[str, Any]]] = {}  # agent_id -> decisions


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


def _assert_agent_visibility(agent: dict[str, Any], user: dict[str, Any]) -> None:
    """Assert the user may view ``agent``.

    Users may see only agents belonging to their own executions.  Admins may
    see all.  Returns 404 (not 403) on denial to prevent existence leakage.

    Args:
        agent: Agent record dict.
        user: Current-user dict from :func:`~src.api.dependencies.auth.get_current_user`.

    Raises:
        HTTPException: 404 when the user is not permitted to see this agent.
    """
    is_admin = "admin" in user.get("scopes", [])
    if not is_admin and agent.get("user_id") != user.get("id"):
        raise _problem(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Agent Not Found",
            detail=f"No agent found with id '{agent.get('agent_id')}'.",
        )


# ---------------------------------------------------------------------------
# GET /v1/agents
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=PaginatedResponse[AgentStatusItem],
    summary="List agent instances",
)
async def list_agents(
    execution_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
    user: dict[str, Any] = Depends(get_current_user),
) -> PaginatedResponse[AgentStatusItem]:
    """Return a paginated list of agent instances visible to the caller.

    Optionally filtered by ``execution_id``.  Non-admin users see only agents
    that belong to their own executions.

    Args:
        execution_id: Optional filter — only return agents for this execution.
        page: 1-based page number.
        page_size: Number of items per page (1–100).
        user: Authenticated user dict.

    Returns:
        :class:`~src.api.schemas.common.PaginatedResponse` of
        :class:`~src.api.schemas.agents.AgentStatusItem` records.
    """
    page_size = max(1, min(page_size, 100))
    is_admin = "admin" in user.get("scopes", [])
    log = logger.bind(user_id=user["id"], execution_id=execution_id)

    agents = list(_AGENT_STORE.values())

    # Visibility filter
    if not is_admin:
        agents = [a for a in agents if a.get("user_id") == user["id"]]

    # Execution filter
    if execution_id is not None:
        agents = [a for a in agents if a.get("execution_id") == execution_id]

    total = len(agents)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = agents[start:end]

    log.debug("agents.list", total=total, page=page)

    items: list[AgentStatusItem] = [
        AgentStatusItem(
            agent_id=a["agent_id"],
            role=a["role"],
            status=a["status"],
            iterations_used=a.get("iterations_used", 0),
            cost_used_usd=a.get("cost_used_usd", 0.0),
        )
        for a in page_items
    ]

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        has_next=end < total,
    )


# ---------------------------------------------------------------------------
# GET /v1/agents/{agent_id}
# ---------------------------------------------------------------------------


@router.get(
    "/{agent_id}",
    response_model=AgentStatusItem,
    summary="Get a single agent instance",
)
async def get_agent(
    agent_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> AgentStatusItem:
    """Return runtime statistics for a specific agent instance.

    Args:
        agent_id: Unique agent instance identifier (ULID).
        user: Authenticated user dict.

    Returns:
        :class:`~src.api.schemas.agents.AgentStatusItem` for the agent.

    Raises:
        HTTPException: 404 when the agent does not exist or is not visible
            to the caller.
    """
    agent = _AGENT_STORE.get(agent_id)
    if agent is None:
        raise _problem(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Agent Not Found",
            detail=f"No agent found with id '{agent_id}'.",
            instance=f"/v1/agents/{agent_id}",
        )

    _assert_agent_visibility(agent, user)

    logger.debug("agents.get", agent_id=agent_id, user_id=user["id"])
    return AgentStatusItem(
        agent_id=agent["agent_id"],
        role=agent["role"],
        status=agent["status"],
        iterations_used=agent.get("iterations_used", 0),
        cost_used_usd=agent.get("cost_used_usd", 0.0),
    )


# ---------------------------------------------------------------------------
# GET /v1/agents/{agent_id}/decisions
# ---------------------------------------------------------------------------


@router.get(
    "/{agent_id}/decisions",
    response_model=list[DecisionRecord],
    summary="Get structured decision records for an agent",
)
async def get_agent_decisions(
    agent_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> list[DecisionRecord]:
    """Return the structured decision records produced by a specific agent.

    Decision records contain only sanitised, length-bounded summaries.
    Raw chain-of-thought is **never** stored or returned — this is enforced
    by :class:`~src.api.schemas.agents.DecisionRecord` at the schema level
    (``reason_summary`` max 500 chars) and at the agent layer.

    Args:
        agent_id: Unique agent instance identifier (ULID).
        user: Authenticated user dict.

    Returns:
        Ordered list of :class:`~src.api.schemas.agents.DecisionRecord`
        instances.  Empty list when no decisions have been recorded yet.

    Raises:
        HTTPException: 404 when the agent does not exist or is not visible
            to the caller.
    """
    agent = _AGENT_STORE.get(agent_id)
    if agent is None:
        raise _problem(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Agent Not Found",
            detail=f"No agent found with id '{agent_id}'.",
            instance=f"/v1/agents/{agent_id}/decisions",
        )

    _assert_agent_visibility(agent, user)

    raw_decisions = _DECISION_STORE.get(agent_id, [])

    logger.debug(
        "agents.decisions",
        agent_id=agent_id,
        user_id=user["id"],
        count=len(raw_decisions),
    )

    # Deserialise from dict store; schema validator enforces CoT guardrails.
    return [DecisionRecord(**d) for d in raw_decisions]
