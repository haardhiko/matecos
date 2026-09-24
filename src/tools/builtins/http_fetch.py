"""
Builtin tool: HTTP fetch with SSRF guard and domain allowlist.

Tool ID: ``web.http_fetch``

Fetches a URL via HTTP/HTTPS with strict security controls:
- SSRF protection: blocks private/link-local/loopback/metadata IP ranges
- Domain allowlist: configurable per-deployment; defaults to deny-all
- Response size cap: 1 MB
- No redirect following (prevents open-redirect abuse)
- Sanitised error messages (no internal addresses exposed)
"""

from __future__ import annotations

import ipaddress
import socket
import time
import urllib.parse
from typing import TYPE_CHECKING

import structlog

from src.tools.manifests import (
    NetworkPolicy,
    ResourceLimits,
    RuntimeConfig,
    SecurityPolicy,
    SideEffects,
    ToolManifest,
)

if TYPE_CHECKING:
    from src.tools.registry import ToolExecutionContext

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_RESPONSE_BYTES: int = 1 * 1024 * 1024  # 1 MB
ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})
DEFAULT_TIMEOUT_SECONDS: int = 30

# Cloud metadata endpoints that must always be blocked regardless of IP checks
BLOCKED_METADATA_HOSTNAMES: frozenset[str] = frozenset(
    {
        "169.254.169.254",  # AWS / GCP / Azure IMDS
        "metadata.google.internal",  # GCP metadata
        "metadata.internal",
        "instance-data",
        "169.254.170.23",  # ECS task metadata
    }
)

# Blocked private + link-local + loopback IP networks (RFC 1918, RFC 3927, etc.)
_BLOCKED_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local (AWS metadata lives here)
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),  # shared address space (RFC 6598)
    ipaddress.ip_network("192.0.0.0/24"),  # IETF protocol assignments
    ipaddress.ip_network("198.18.0.0/15"),  # benchmarking
    ipaddress.ip_network("198.51.100.0/24"),  # documentation
    ipaddress.ip_network("203.0.113.0/24"),  # documentation
    ipaddress.ip_network("240.0.0.0/4"),  # reserved
    # IPv6
    ipaddress.ip_network("::1/128"),  # loopback
    ipaddress.ip_network("fc00::/7"),  # unique local
    ipaddress.ip_network("fe80::/10"),  # link-local
    ipaddress.ip_network("::ffff:0:0/96"),  # IPv4-mapped
]

# Default allowed domains (empty = deny all in conservative mode)
DEFAULT_ALLOWED_DOMAINS: list[str] = []


# ---------------------------------------------------------------------------
# SSRF guard
# ---------------------------------------------------------------------------


class SSRFError(ValueError):
    """Raised when a URL fails the SSRF safety check."""


def _check_ssrf(url: str, allowed_domains: list[str]) -> None:
    """
    Validate that *url* is safe to fetch.

    Checks performed (in order):
    1. Scheme must be http or https.
    2. Host must not be a blocked metadata hostname.
    3. If a domain allowlist is configured (non-empty), the host must match.
    4. All resolved IP addresses must not fall within blocked networks.

    Raises:
        SSRFError: On any security violation.  Error messages are deliberately
            vague — they do not reveal internal network topology.
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        raise SSRFError("Invalid URL.")

    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise SSRFError(f"URL scheme '{scheme}' is not allowed; use http or https.")

    host = parsed.hostname or ""
    if not host:
        raise SSRFError("URL must have a non-empty host.")

    # Strip port from host (already done by urlparse.hostname)
    host_lower = host.lower()

    # Check blocked metadata hostnames
    if host_lower in BLOCKED_METADATA_HOSTNAMES:
        raise SSRFError("Access to this host is not permitted.")

    # Domain allowlist check (empty list = deny all)
    if allowed_domains:
        if not any(
            host_lower == d.lower() or host_lower.endswith("." + d.lower()) for d in allowed_domains
        ):
            raise SSRFError("Host is not in the allowed domain list.")
    # If allowed_domains is empty, we're in deny-all mode — raise
    else:
        raise SSRFError(
            "No allowed domains are configured; HTTP fetch is disabled in conservative mode."
        )

    # DNS resolution check — resolve and validate each address
    try:
        addr_infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise SSRFError("Could not resolve host.") from exc

    for _family, _type, _proto, _canonname, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            raise SSRFError("Invalid IP address from DNS resolution.")

        for blocked_net in _BLOCKED_NETWORKS:
            if ip_obj in blocked_net:
                raise SSRFError("Access to this host is not permitted.")


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


async def http_fetch_handler(
    payload: dict,
    context: ToolExecutionContext,
) -> dict:
    """
    Fetch a URL and return the response.

    Input payload keys:
    - ``url`` (str, required): The URL to fetch.
    - ``method`` (str, optional, default ``GET``): HTTP method.
    - ``headers`` (dict, optional): Additional request headers.
    - ``body`` (str | None, optional): Request body for POST/PUT.
    - ``timeout`` (int, optional, default 30): Request timeout in seconds.
    - ``allowed_domains`` (list[str], optional): Override the domain allowlist
      for this call (must be pre-authorised by the calling agent's policy).

    Security controls applied:
    1. SSRF guard validates the URL before any network I/O.
    2. ``follow_redirects=False`` prevents open-redirect exploitation.
    3. Response body is hard-capped at 1 MB.
    4. Sanitised error messages (no internal IP addresses exposed).

    Returns a dict with: ``status_code``, ``headers``, ``body``,
    ``content_type``, ``latency_ms``.
    """
    try:
        import httpx  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError("httpx is not installed; run 'pip install httpx'.") from exc

    log = logger.bind(
        tool_id="web.http_fetch",
        execution_id=context.execution_id,
        agent_id=context.agent_id,
    )

    url: str = payload["url"]
    method: str = str(payload.get("method", "GET")).upper()
    extra_headers: dict = dict(payload.get("headers", {}))
    body: str | None = payload.get("body")
    timeout_sec: int = int(payload.get("timeout", DEFAULT_TIMEOUT_SECONDS))
    timeout_sec = max(1, min(timeout_sec, 60))  # clamp 1–60 s

    # Allowlist: from payload override (must be policy-gated upstream) or default
    allowed_domains: list[str] = list(payload.get("allowed_domains", DEFAULT_ALLOWED_DOMAINS))

    log.info("http_fetch.start", url=url, method=method)

    # Security: SSRF check before any network I/O
    try:
        _check_ssrf(url, allowed_domains)
    except SSRFError as exc:
        log.warning("http_fetch.ssrf_blocked", reason=str(exc))
        raise ValueError(f"URL blocked by security policy: {exc}") from exc

    # Sanitise method (only standard HTTP methods)
    _ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
    if method not in _ALLOWED_METHODS:
        raise ValueError(f"HTTP method '{method}' is not allowed.")

    # Build safe headers (no host header injection, no hop-by-hop headers)
    _BLOCKED_HEADERS = {"host", "connection", "transfer-encoding", "upgrade", "proxy-connection"}
    safe_headers: dict[str, str] = {
        k: v for k, v in extra_headers.items() if k.lower() not in _BLOCKED_HEADERS
    }

    start_ns = time.perf_counter_ns()
    try:
        async with httpx.AsyncClient(
            follow_redirects=False,  # No redirect following — prevents open redirect
            timeout=httpx.Timeout(timeout_sec),
        ) as client:
            request_kwargs: dict = {
                "method": method,
                "url": url,
                "headers": safe_headers,
            }
            if body is not None:
                request_kwargs["content"] = body.encode("utf-8")

            response = await client.request(**request_kwargs)

            # Read response body with size cap
            body_bytes = await response.aread()
            if len(body_bytes) > MAX_RESPONSE_BYTES:
                body_bytes = body_bytes[:MAX_RESPONSE_BYTES]
                log.warning(
                    "http_fetch.response_truncated",
                    original_size=len(body_bytes),
                    cap=MAX_RESPONSE_BYTES,
                )

    except httpx.TimeoutException as exc:
        raise TimeoutError(f"HTTP request timed out after {timeout_sec}s.") from exc
    except httpx.RequestError as exc:
        # Sanitise: do not expose internal addresses
        raise RuntimeError("HTTP request failed due to a network error.") from exc

    latency_ms = int((time.perf_counter_ns() - start_ns) / 1_000_000)

    content_type = response.headers.get("content-type", "")

    # Decode body safely
    charset = "utf-8"
    if "charset=" in content_type:
        parts = content_type.split("charset=")
        charset = parts[-1].strip().split(";")[0].strip() or "utf-8"

    try:
        body_str = body_bytes.decode(charset, errors="replace")
    except LookupError:
        body_str = body_bytes.decode("utf-8", errors="replace")

    # Return response headers (filtered — no hop-by-hop)
    _HOP_BY_HOP = {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
    response_headers = {
        k.lower(): v for k, v in response.headers.items() if k.lower() not in _HOP_BY_HOP
    }

    log.info(
        "http_fetch.complete",
        status_code=response.status_code,
        latency_ms=latency_ms,
        body_len=len(body_str),
    )

    return {
        "status_code": response.status_code,
        "headers": response_headers,
        "body": body_str,
        "content_type": content_type,
        "latency_ms": latency_ms,
    }


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def get_manifest() -> ToolManifest:
    """Return the :class:`ToolManifest` for the ``web.http_fetch`` tool."""
    return ToolManifest(
        tool_id="web.http_fetch",
        name="HTTP Fetch Tool",
        version="1.0.0",
        description=(
            "Fetches a URL via HTTP/HTTPS with SSRF protection and domain allowlist. "
            "Responses are capped at 1 MB and redirects are not followed."
        ),
        capabilities=["http", "web_request", "data_fetch"],
        input_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["url"],
            "additionalProperties": False,
            "properties": {
                "url": {
                    "type": "string",
                    "format": "uri",
                    "description": "The URL to fetch (must be http or https).",
                },
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
                    "default": "GET",
                },
                "headers": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Additional HTTP request headers.",
                    "default": {},
                },
                "body": {
                    "type": ["string", "null"],
                    "description": "Request body (for POST/PUT/PATCH).",
                    "default": None,
                },
                "timeout": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 60,
                    "default": 30,
                    "description": "Request timeout in seconds.",
                },
                "allowed_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Domain allowlist override (requires policy authorisation).",
                    "default": [],
                },
            },
        },
        output_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["status_code", "headers", "body", "content_type", "latency_ms"],
            "properties": {
                "status_code": {"type": "integer", "minimum": 100, "maximum": 599},
                "headers": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "body": {"type": "string"},
                "content_type": {"type": "string"},
                "latency_ms": {"type": "integer", "minimum": 0},
            },
        },
        risk_level="medium",
        side_effects=SideEffects.EXTERNAL_COMMUNICATION,
        permissions=["network:http"],
        runtime=RuntimeConfig(type="builtin"),
        limits=ResourceLimits(
            timeout_seconds=60,
            max_memory_mb=128,
            max_output_kb=1024,
        ),
        security=SecurityPolicy(
            requires_scan=True,
            scan_passed=False,
            network=NetworkPolicy(
                allowed=True,
                allowed_domains=[],  # configured per-deployment
                requires_proxy=True,
            ),
        ),
    )


HTTP_FETCH_MANIFEST = get_manifest()
fetch_url = http_fetch_handler
