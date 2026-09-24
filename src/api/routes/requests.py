"""Request submission and status endpoints — the request gateway."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import structlog
import ulid
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_user
from src.api.schemas.requests import (
    ApproveRequest,
    ExecutionStatusResponse,
    RejectRequest,
    SubmitRequestBody,
    SubmitRequestResponse,
    TaskStatusItem,
)
from src.api.schemas.common import ErrorDetail

logger: structlog.BoundLogger = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1/requests", tags=["Requests"])

# ---------------------------------------------------------------------------
# Internal stubs — replaced by real infra once wired
# ---------------------------------------------------------------------------

_EXECUTIONS: dict[str, dict[str, Any]] = {}  # in-process store for dev/stub
_IDEMPOTENCY: dict[str, str] = {}            # idempotency_key -> execution_id


def _get_session_stub() -> Any:
    """Stub dependency for AsyncSession; replaced when DB is wired."""
    return None


def _get_settings_stub() -> Any:
    """Stub dependency for AppSettings; replaced when settings module exists."""
    return None


async def _get_session() -> Any:  # noqa: ANN401
    """Resolve an async SQLAlchemy session.

    In production this is replaced by the real ``get_session`` from
    ``src.infrastructure.database``.  Here we attempt the real import and
    fall back to the stub transparently.
    """
    try:
        from src.infrastructure.database import get_session  # type: ignore[import-not-found]
        async for session in get_session():
            yield session
    except ImportError:
        yield _get_session_stub()


async def _get_settings() -> Any:  # noqa: ANN401
    """Resolve the application settings instance."""
    try:
        from src.config import get_settings  # type: ignore[import-not-found]
        return get_settings()
    except ImportError:
        return _get_settings_stub()


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
        title: Short problem type summary.
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


def _assert_ownership(execution: dict[str, Any], user: dict[str, Any]) -> None:
    """Assert that ``user`` owns ``execution``, or is an admin.

    Args:
        execution: Execution record dict.
        user: Current-user dict from :func:`~src.api.dependencies.auth.get_current_user`.

    Raises:
        HTTPException: 404 (not 403) to avoid leaking existence of other
            users' executions.
    """
    is_admin = "admin" in user.get("scopes", [])
    if not is_admin and execution.get("user_id") != user.get("id"):
        raise _problem(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Execution Not Found",
            detail=f"No execution found with id '{execution.get('execution_id')}'",
        )


async def _write_audit_event(
    session: Any,
    execution_id: str,
    event_type: str,
    user_id: str,
    data: dict[str, Any],
) -> None:
    """Persist an audit event for a given execution.

    Falls back to a structured log entry when the DB session is unavailable
    (dev/stub mode).

    Args:
        session: SQLAlchemy async session (may be ``None`` in stub mode).
        execution_id: The parent execution identifier.
        event_type: A short, namespaced event type string, e.g. ``REQUEST_RECEIVED``.
        user_id: Identifier of the acting user.
        data: Arbitrary structured data for the event.
    """
    if session is None:
        logger.info(
            "audit.event",
            execution_id=execution_id,
            event_type=event_type,
            user_id=user_id,
            data=data,
        )
        return

    try:
        from src.infrastructure.audit import write_audit_event  # type: ignore[import-not-found]
        await write_audit_event(session, execution_id, event_type, user_id, data)
    except ImportError:
        logger.info(
            "audit.event",
            execution_id=execution_id,
            event_type=event_type,
            user_id=user_id,
            data=data,
        )


async def _enqueue_execution(execution_id: str, payload: dict[str, Any]) -> None:
    """Enqueue an execution to the orchestration queue.

    Falls back to a log warning if the queue is not yet wired or reachable.

    Args:
        execution_id: The execution identifier to enqueue.
        payload: Structured orchestration payload.
    """
    try:
        from src.config import get_settings
        from src.infrastructure.queue import QueueManager

        settings = get_settings()
        qm = QueueManager(settings.redis.url, settings.redis.max_connections)
        await qm.connect()
        try:
            await qm.enqueue("orchestration", payload)
        finally:
            await qm.close()
    except Exception as exc:
        logger.warning(
            "queue.not_wired",
            execution_id=execution_id,
            error=str(exc),
            note="Execution queued in-process only (stub mode)",
        )


# ---------------------------------------------------------------------------
# POST /v1/requests — submit a new request
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=SubmitRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a new agent request",
)
async def submit_request(
    body: SubmitRequestBody,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> SubmitRequestResponse:
    """Request Gateway — accept, validate, persist and enqueue a new request.

    The ten-step lifecycle:

    1. User is authenticated (via dependency injection).
    2. Idempotency key de-duplication: return the existing execution if the
       same key was already submitted.
    3. Generate a stable ``request_id`` (ULID) and ``execution_id`` (ULID).
    4. Normalise the request into a structured internal representation.
    5. Validate: text length (enforced by schema), attachment count (≤10),
       attachment URI format.
    6. Policy checks: cost budget and duration within configured bounds.
    7. Create an ``Execution`` record in the database with status ``CREATED``.
    8. Write an ``REQUEST_RECEIVED`` audit event.
    9. Enqueue to the orchestration queue.
    10. Return HTTP 202 with a ``status_url``.

    Args:
        body: Validated request payload.
        request: Raw FastAPI request (used for URL building and tracing).
        user: Authenticated user dict.

    Returns:
        :class:`~src.api.schemas.requests.SubmitRequestResponse` with initial
        status and the polling URL.

    Raises:
        HTTPException: 422 for validation failures, 400 for policy violations,
            409 for idempotency conflicts, 500 (masked) for unexpected errors.
    """
    log = logger.bind(user_id=user["id"])

    # ------------------------------------------------------------------
    # Step 2: Idempotency key check
    # ------------------------------------------------------------------
    if body.idempotency_key:
        existing_exec_id = _IDEMPOTENCY.get(body.idempotency_key)
        if existing_exec_id and existing_exec_id in _EXECUTIONS:
            existing = _EXECUTIONS[existing_exec_id]
            log.info(
                "request.idempotency_hit",
                idempotency_key=body.idempotency_key,
                execution_id=existing_exec_id,
            )
            return SubmitRequestResponse(
                request_id=existing["request_id"],
                execution_id=existing_exec_id,
                status=existing["status"],
                status_url=_build_status_url(request, existing_exec_id),
                created_at=existing["created_at"],
            )

    # ------------------------------------------------------------------
    # Step 3: Generate stable IDs
    # ------------------------------------------------------------------
    request_id: str = ulid.new().str
    execution_id: str = ulid.new().str
    log = log.bind(request_id=request_id, execution_id=execution_id)

    # ------------------------------------------------------------------
    # Step 5: Additional validation (schema handles text length)
    # ------------------------------------------------------------------
    if len(body.attachments) > 10:
        raise _problem(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Validation Error",
            detail="A request may include at most 10 attachments.",
            instance=f"/v1/requests#{request_id}",
        )

    for att in body.attachments:
        if not att.uri.startswith("secure://"):
            raise _problem(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                title="Invalid Attachment URI",
                detail=f"Attachment '{att.id}' has an invalid URI scheme; expected 'secure://'.",
            )

    # ------------------------------------------------------------------
    # Step 6: Policy checks
    # ------------------------------------------------------------------
    max_allowed_cost: float = float(os.environ.get("POLICY_MAX_COST_USD", "1000.0"))
    max_allowed_duration: int = int(os.environ.get("POLICY_MAX_DURATION_SECONDS", "86400"))

    if body.constraints.max_cost > max_allowed_cost:
        raise _problem(
            status_code=status.HTTP_400_BAD_REQUEST,
            title="Policy Violation",
            detail=(
                f"Requested max_cost ${body.constraints.max_cost:.2f} exceeds the "
                f"per-request policy ceiling of ${max_allowed_cost:.2f}."
            ),
        )

    if body.constraints.max_duration_seconds > max_allowed_duration:
        raise _problem(
            status_code=status.HTTP_400_BAD_REQUEST,
            title="Policy Violation",
            detail=(
                f"Requested max_duration_seconds {body.constraints.max_duration_seconds} "
                f"exceeds the policy ceiling of {max_allowed_duration}s."
            ),
        )

    # ------------------------------------------------------------------
    # Step 4 / 7: Normalise + create execution record
    # ------------------------------------------------------------------
    now = datetime.now(UTC)
    execution_record: dict[str, Any] = {
        "request_id": request_id,
        "execution_id": execution_id,
        "user_id": user["id"],
        "status": "CREATED",
        "goal_text": body.text,
        "constraints": body.constraints.model_dump(),
        "attachment_count": len(body.attachments),
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "completed_at": None,
        "tasks": [],
        "agents": [],
        "pending_approvals": [],
        "result": None,
        "error": None,
        "total_cost_usd": None,
        "warnings": [],
    }
    _EXECUTIONS[execution_id] = execution_record

    if body.idempotency_key:
        _IDEMPOTENCY[body.idempotency_key] = execution_id

    log.info("request.created", status="CREATED")

    # ------------------------------------------------------------------
    # Step 8: Audit event
    # ------------------------------------------------------------------
    await _write_audit_event(
        session=None,
        execution_id=execution_id,
        event_type="REQUEST_RECEIVED",
        user_id=user["id"],
        data={
            "request_id": request_id,
            "goal_length": len(body.text),
            "attachment_count": len(body.attachments),
            "constraints": body.constraints.model_dump(),
        },
    )

    # ------------------------------------------------------------------
    # Step 9: Enqueue
    # ------------------------------------------------------------------
    await _enqueue_execution(
        execution_id=execution_id,
        payload={
            "execution_id": execution_id,
            "request_id": request_id,
            "user_id": user["id"],
            "goal_text": body.text,
            "constraints": body.constraints.model_dump(),
        },
    )

    # ------------------------------------------------------------------
    # Step 10: Return 202
    # ------------------------------------------------------------------
    return SubmitRequestResponse(
        request_id=request_id,
        execution_id=execution_id,
        status="CREATED",
        status_url=_build_status_url(request, execution_id),
        created_at=now,
    )


def _build_status_url(request: Request, execution_id: str) -> str:
    """Build the absolute polling URL for an execution.

    Args:
        request: The originating FastAPI request.
        execution_id: The execution ULID.

    Returns:
        Absolute URL string, e.g. ``https://api.example.com/v1/requests/01ABC…``.
    """
    return str(request.url_for("get_execution_status", execution_id=execution_id))


# ---------------------------------------------------------------------------
# GET /v1/requests/{execution_id} — status
# ---------------------------------------------------------------------------


@router.get(
    "/{execution_id}",
    response_model=ExecutionStatusResponse,
    summary="Get execution status",
    name="get_execution_status",
)
async def get_execution_status(
    execution_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> ExecutionStatusResponse:
    """Return the current status, task list, agent list and pending approvals.

    Only the owning user (or an admin) may retrieve an execution's status.
    Attempting to access another user's execution returns 404 to prevent
    existence leakage.

    Args:
        execution_id: ULID of the target execution.
        user: Authenticated user dict.

    Returns:
        :class:`~src.api.schemas.requests.ExecutionStatusResponse` snapshot.

    Raises:
        HTTPException: 404 when the execution does not exist or is not owned
            by the caller.
    """
    log = logger.bind(user_id=user["id"], execution_id=execution_id)

    execution = _EXECUTIONS.get(execution_id)
    if execution is None:
        log.warning("request.not_found")
        raise _problem(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Execution Not Found",
            detail=f"No execution found with id '{execution_id}'.",
            instance=f"/v1/requests/{execution_id}",
        )

    _assert_ownership(execution, user)

    return ExecutionStatusResponse(
        execution_id=execution_id,
        request_id=execution["request_id"],
        status=execution["status"],
        goal_text=execution["goal_text"],
        created_at=execution["created_at"],
        updated_at=execution["updated_at"],
        started_at=execution.get("started_at"),
        completed_at=execution.get("completed_at"),
        tasks=[TaskStatusItem(**t) for t in execution.get("tasks", [])],
        agents=execution.get("agents", []),
        result=execution.get("result"),
        error=execution.get("error"),
        total_cost_usd=execution.get("total_cost_usd"),
        warnings=execution.get("warnings", []),
        pending_approvals=execution.get("pending_approvals", []),
    )


# ---------------------------------------------------------------------------
# POST /v1/requests/{execution_id}/cancel
# ---------------------------------------------------------------------------


@router.post(
    "/{execution_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Cancel a running execution",
)
async def cancel_execution(
    execution_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, str]:
    """Request cancellation of an in-progress execution.

    The execution transitions to ``CANCELLATION_REQUESTED``; the orchestrator
    is responsible for performing the actual cancellation asynchronously.

    Args:
        execution_id: ULID of the execution to cancel.
        user: Authenticated user dict.

    Returns:
        Confirmation dict with ``execution_id`` and new ``status``.

    Raises:
        HTTPException: 404 if not found or not owned; 409 if already terminal.
    """
    log = logger.bind(user_id=user["id"], execution_id=execution_id)

    execution = _EXECUTIONS.get(execution_id)
    if execution is None:
        raise _problem(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Execution Not Found",
            detail=f"No execution found with id '{execution_id}'.",
        )

    _assert_ownership(execution, user)

    terminal_statuses = {"COMPLETED", "FAILED", "CANCELLED", "CANCELLATION_REQUESTED"}
    if execution["status"] in terminal_statuses:
        raise _problem(
            status_code=status.HTTP_409_CONFLICT,
            title="Invalid State Transition",
            detail=(
                f"Execution '{execution_id}' is already in terminal state "
                f"'{execution['status']}' and cannot be cancelled."
            ),
        )

    execution["status"] = "CANCELLATION_REQUESTED"
    execution["updated_at"] = datetime.now(UTC)

    await _write_audit_event(
        session=None,
        execution_id=execution_id,
        event_type="CANCELLATION_REQUESTED",
        user_id=user["id"],
        data={"execution_id": execution_id},
    )

    log.info("request.cancel_requested")
    return {"execution_id": execution_id, "status": "CANCELLATION_REQUESTED"}


# ---------------------------------------------------------------------------
# POST /v1/requests/{execution_id}/approve/{approval_id}
# ---------------------------------------------------------------------------


@router.post(
    "/{execution_id}/approve/{approval_id}",
    status_code=status.HTTP_200_OK,
    summary="Approve a pending human-approval checkpoint",
)
async def approve_action(
    execution_id: str,
    approval_id: str,
    body: ApproveRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, str]:
    """Approve a pending human-approval checkpoint within an execution.

    The checkpoint is removed from ``pending_approvals`` and an audit event
    is written. The orchestrator will resume the blocked agent when it
    processes the approval via the queue.

    Args:
        execution_id: ULID of the parent execution.
        approval_id: ULID of the specific approval checkpoint.
        body: Optional reviewer notes.
        user: Authenticated user dict.

    Returns:
        Confirmation dict.

    Raises:
        HTTPException: 404 if execution/approval not found; 403 if not owner.
    """
    log = logger.bind(
        user_id=user["id"],
        execution_id=execution_id,
        approval_id=approval_id,
    )

    execution = _EXECUTIONS.get(execution_id)
    if execution is None:
        raise _problem(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Execution Not Found",
            detail=f"No execution found with id '{execution_id}'.",
        )

    _assert_ownership(execution, user)

    pending = execution.get("pending_approvals", [])
    approval = next((a for a in pending if a.get("approval_id") == approval_id), None)
    if approval is None:
        raise _problem(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Approval Not Found",
            detail=f"No pending approval with id '{approval_id}' in execution '{execution_id}'.",
        )

    execution["pending_approvals"] = [
        a for a in pending if a.get("approval_id") != approval_id
    ]
    execution["updated_at"] = datetime.now(UTC)

    await _write_audit_event(
        session=None,
        execution_id=execution_id,
        event_type="ACTION_APPROVED",
        user_id=user["id"],
        data={
            "approval_id": approval_id,
            "reviewer_notes": body.notes,
        },
    )

    log.info("request.action_approved")
    return {"execution_id": execution_id, "approval_id": approval_id, "result": "approved"}


# ---------------------------------------------------------------------------
# POST /v1/requests/{execution_id}/reject/{approval_id}
# ---------------------------------------------------------------------------


@router.post(
    "/{execution_id}/reject/{approval_id}",
    status_code=status.HTTP_200_OK,
    summary="Reject a pending human-approval checkpoint",
)
async def reject_action(
    execution_id: str,
    approval_id: str,
    body: RejectRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, str]:
    """Reject a pending human-approval checkpoint within an execution.

    The checkpoint is removed and the orchestrator will terminate the blocked
    agent path with a ``REJECTED`` outcome.

    Args:
        execution_id: ULID of the parent execution.
        approval_id: ULID of the specific approval checkpoint.
        body: Mandatory rejection reason for the audit trail.
        user: Authenticated user dict.

    Returns:
        Confirmation dict.

    Raises:
        HTTPException: 404 if execution/approval not found; 403 if not owner.
    """
    log = logger.bind(
        user_id=user["id"],
        execution_id=execution_id,
        approval_id=approval_id,
    )

    execution = _EXECUTIONS.get(execution_id)
    if execution is None:
        raise _problem(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Execution Not Found",
            detail=f"No execution found with id '{execution_id}'.",
        )

    _assert_ownership(execution, user)

    pending = execution.get("pending_approvals", [])
    approval = next((a for a in pending if a.get("approval_id") == approval_id), None)
    if approval is None:
        raise _problem(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Approval Not Found",
            detail=f"No pending approval with id '{approval_id}' in execution '{execution_id}'.",
        )

    execution["pending_approvals"] = [
        a for a in pending if a.get("approval_id") != approval_id
    ]
    execution["updated_at"] = datetime.now(UTC)

    await _write_audit_event(
        session=None,
        execution_id=execution_id,
        event_type="ACTION_REJECTED",
        user_id=user["id"],
        data={
            "approval_id": approval_id,
            "reason": body.reason,
        },
    )

    log.info("request.action_rejected")
    return {"execution_id": execution_id, "approval_id": approval_id, "result": "rejected"}
