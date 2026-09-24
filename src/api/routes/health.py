"""Health check and readiness endpoints."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Response, status
from opentelemetry import metrics

from src.api.schemas.common import ComponentHealth, HealthResponse

logger: structlog.BoundLogger = structlog.get_logger(__name__)

router = APIRouter(tags=["Health"])

# ---------------------------------------------------------------------------
# OpenTelemetry metrics instruments
# ---------------------------------------------------------------------------
_meter = metrics.get_meter("matecos.health")
_request_counter = _meter.create_counter(
    "matecos.requests.total",
    description="Total HTTP requests handled",
    unit="1",
)
_error_counter = _meter.create_counter(
    "matecos.errors.total",
    description="Total HTTP errors returned",
    unit="1",
)

# Simple in-process counters for the /metrics text endpoint
_COUNTERS: dict[str, int] = {
    "requests_total": 0,
    "errors_total": 0,
}

# Application version — injected from package metadata at startup
_APP_VERSION: str = "0.1.0"


# ---------------------------------------------------------------------------
# Liveness
# ---------------------------------------------------------------------------


@router.get(
    "/health",
    summary="Liveness probe",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
)
async def liveness() -> HealthResponse:
    """Return a lightweight liveness response.

    Always returns HTTP 200 with ``status='healthy'``.  Kubernetes liveness
    probes should target this endpoint; if it fails the pod will be
    restarted.

    Returns:
        :class:`~src.api.schemas.common.HealthResponse` with aggregate
        status ``healthy``.
    """
    _COUNTERS["requests_total"] += 1
    _request_counter.add(1, {"endpoint": "/health"})
    return HealthResponse(
        status="healthy",
        version=_APP_VERSION,
        timestamp=datetime.now(UTC),
        components={},
    )


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


@router.get(
    "/readiness",
    summary="Readiness probe",
    status_code=status.HTTP_200_OK,
)
async def readiness(response: Response) -> HealthResponse:
    """Return a readiness response by probing the database and message queue.

    Checks:
    - **Database** — executes ``SELECT 1`` via
      :func:`~src.infrastructure.database.check_db_health`.
    - **Queue / Redis** — issues a ``PING`` via
      :meth:`~src.infrastructure.queue.QueueManager.health_check`.

    Returns HTTP 200 when all checks pass, HTTP 503 when any check fails.

    Returns:
        :class:`~src.api.schemas.common.HealthResponse` with per-component
        details.
    """
    _COUNTERS["requests_total"] += 1
    _request_counter.add(1, {"endpoint": "/readiness"})

    components: dict[str, ComponentHealth] = {}
    overall_healthy = True

    # --- Database probe ---
    db_component = await _probe_database()
    components["database"] = db_component
    if db_component.status != "healthy":
        overall_healthy = False

    # --- Queue/Redis probe ---
    queue_component = await _probe_queue()
    components["queue"] = queue_component
    if queue_component.status != "healthy":
        overall_healthy = False

    aggregate: str
    if overall_healthy:
        aggregate = "healthy"
    elif any(c.status == "unhealthy" for c in components.values()):
        aggregate = "unhealthy"
    else:
        aggregate = "degraded"

    if not overall_healthy:
        _COUNTERS["errors_total"] += 1
        _error_counter.add(1, {"endpoint": "/readiness"})
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status=aggregate,  # type: ignore[arg-type]
        version=_APP_VERSION,
        timestamp=datetime.now(UTC),
        components=components,
    )


async def _probe_database() -> ComponentHealth:
    """Execute a lightweight ``SELECT 1`` against the database.

    Returns:
        :class:`~src.api.schemas.common.ComponentHealth` for the database.
    """
    start = time.monotonic()
    try:
        # Import here to avoid circular imports at module load time.
        from src.infrastructure.database import check_db_health  # type: ignore[import-not-found]

        await check_db_health()
        latency_ms = (time.monotonic() - start) * 1000
        return ComponentHealth(name="database", status="healthy", latency_ms=latency_ms)
    except ImportError:
        # Infrastructure layer not yet wired — treat as degraded in early dev.
        return ComponentHealth(
            name="database",
            status="degraded",
            error="infrastructure.database module not available",
        )
    except Exception as exc:  # noqa: BLE001
        latency_ms = (time.monotonic() - start) * 1000
        logger.error("health.database_probe_failed", error=str(exc))
        return ComponentHealth(
            name="database",
            status="unhealthy",
            latency_ms=latency_ms,
            error="Database health check failed",
        )


async def _probe_queue() -> ComponentHealth:
    """Issue a PING to the Redis/queue backend.

    Returns:
        :class:`~src.api.schemas.common.ComponentHealth` for the queue.
    """
    start = time.monotonic()
    try:
        from src.infrastructure.queue import QueueManager  # type: ignore[import-not-found]

        await QueueManager.health_check()
        latency_ms = (time.monotonic() - start) * 1000
        return ComponentHealth(name="queue", status="healthy", latency_ms=latency_ms)
    except ImportError:
        return ComponentHealth(
            name="queue",
            status="degraded",
            error="infrastructure.queue module not available",
        )
    except Exception as exc:  # noqa: BLE001
        latency_ms = (time.monotonic() - start) * 1000
        logger.error("health.queue_probe_failed", error=str(exc))
        return ComponentHealth(
            name="queue",
            status="unhealthy",
            latency_ms=latency_ms,
            error="Queue health check failed",
        )


# ---------------------------------------------------------------------------
# Prometheus-compatible metrics text
# ---------------------------------------------------------------------------


@router.get(
    "/metrics",
    summary="Prometheus text metrics",
    response_class=Response,
    include_in_schema=False,
)
async def prometheus_metrics() -> Response:
    """Return a minimal Prometheus text-format metrics snapshot.

    Exposes two counters:
    - ``matecos_requests_total`` — cumulative HTTP requests handled.
    - ``matecos_errors_total`` — cumulative HTTP errors returned.

    This is a lightweight supplement to the full OTel pipeline; for
    production Prometheus scraping wire the official
    ``opentelemetry-exporter-prometheus`` exporter instead.

    Returns:
        Plain-text Prometheus exposition format response.
    """
    lines: list[str] = [
        "# HELP matecos_requests_total Total HTTP requests handled by MATECOS.",
        "# TYPE matecos_requests_total counter",
        f"matecos_requests_total {_COUNTERS['requests_total']}",
        "# HELP matecos_errors_total Total HTTP errors returned by MATECOS.",
        "# TYPE matecos_errors_total counter",
        f"matecos_errors_total {_COUNTERS['errors_total']}",
        "",
    ]
    return Response(
        content="\n".join(lines),
        media_type="text/plain; version=0.0.4; charset=utf-8",
        status_code=status.HTTP_200_OK,
    )
