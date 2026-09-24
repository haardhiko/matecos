"""OpenTelemetry + structlog observability setup for MATECOS."""
from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from typing import Any

import structlog
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# ── Context Variables ─────────────────────────────────────────────────────────

REQUEST_ID_CTX: ContextVar[str] = ContextVar("request_id", default="")
EXECUTION_ID_CTX: ContextVar[str] = ContextVar("execution_id", default="")
AGENT_ID_CTX: ContextVar[str] = ContextVar("agent_id", default="")
TASK_ID_CTX: ContextVar[str] = ContextVar("task_id", default="")

# ── Structlog processors ──────────────────────────────────────────────────────


def inject_trace_context(
    logger: Any, method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Inject OpenTelemetry trace context into every log record."""
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    else:
        event_dict["trace_id"] = ""
        event_dict["span_id"] = ""

    event_dict["request_id"] = REQUEST_ID_CTX.get("")
    event_dict["execution_id"] = EXECUTION_ID_CTX.get("")
    event_dict["agent_id"] = AGENT_ID_CTX.get("")
    event_dict["task_id"] = TASK_ID_CTX.get("")
    return event_dict


def drop_private_reasoning(
    logger: Any, method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Strip any field that could leak private chain-of-thought."""
    for key in list(event_dict.keys()):
        if key in {"reasoning", "chain_of_thought", "cot", "thinking", "scratch_pad"}:
            del event_dict[key]
    return event_dict


# ── Setup ─────────────────────────────────────────────────────────────────────


def configure_telemetry(service_name: str, otlp_endpoint: str, enabled: bool = True) -> None:
    """
    Configure OpenTelemetry TracerProvider, MeterProvider, and structlog.

    Call once at application startup.
    """
    resource = Resource.create({"service.name": service_name, "service.version": "0.1.0"})

    # ── Tracing ──────────────────────────────────────────────────────────────
    if enabled:
        span_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        tracer_provider = TracerProvider(
            resource=resource,
            sampler=ParentBased(root=ALWAYS_ON),
        )
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    else:
        tracer_provider = TracerProvider(resource=resource)

    trace.set_tracer_provider(tracer_provider)

    # ── Metrics ───────────────────────────────────────────────────────────────
    if enabled:
        metric_exporter = OTLPMetricExporter(endpoint=otlp_endpoint, insecure=True)
        reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=30_000)
        meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    else:
        meter_provider = MeterProvider(resource=resource)

    metrics.set_meter_provider(meter_provider)

    # ── structlog ─────────────────────────────────────────────────────────────
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            inject_trace_context,
            drop_private_reasoning,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_tracer(name: str) -> trace.Tracer:
    """Return an OTEL Tracer for the given component name."""
    return trace.get_tracer(name)


def get_meter(name: str) -> metrics.Meter:
    """Return an OTEL Meter for the given component name."""
    return metrics.get_meter(name)


def get_logger(name: str) -> structlog.BoundLogger:
    """Return a structlog BoundLogger bound to the given component name."""
    return structlog.get_logger(name)


# ── FastAPI Middleware ─────────────────────────────────────────────────────────


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """
    Read X-Request-ID from request headers (or generate one),
    set REQUEST_ID_CTX context variable, and propagate to response.
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:16]}"
        token = REQUEST_ID_CTX.set(request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            REQUEST_ID_CTX.reset(token)
