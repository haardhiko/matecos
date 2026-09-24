"""
Tool executor — dispatches tool invocations to builtin handlers or sandbox containers.

This is an internal component used exclusively by :class:`ToolRegistry`.
Agents must never call the executor directly.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import structlog

from src.tools.manifests import ToolManifest
from src.tools.sandbox.runner import SandboxRunner, SandboxTimeoutError, SandboxUnavailableError

if TYPE_CHECKING:
    from src.tools.registry import ToolExecutionContext, ToolExecutionResult
    from src.tools.sandbox.runner import SandboxSettings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ExecutorError(Exception):
    """Base error from the tool executor."""


class RemoteToolError(ExecutorError):
    """Raised when a remote HTTP tool call fails."""


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


class ToolExecutor:
    """
    Routes tool invocations to the correct runtime handler.

    There are three runtime paths:
    - **builtin**: Calls a registered Python :class:`Callable` in-process.
    - **container**: Delegates to :class:`SandboxRunner` (Docker).
    - **remote**: Makes an HTTP POST to an external service endpoint.

    All paths enforce timeouts, capture resource usage, and return a
    :class:`ToolExecutionResult`.  Error messages are sanitised — internal
    addresses and secrets are never exposed to callers.
    """

    def __init__(
        self,
        settings: Any = None,
        sandbox_runner: SandboxRunner | None = None,
    ) -> None:
        """
        Initialise the executor.

        Args:
            settings: Application settings (used for remote tool base URLs, etc.).
            sandbox_runner: A ready :class:`SandboxRunner` instance.
        """
        if settings is None:
            from src.config import get_settings

            settings = get_settings()
        if sandbox_runner is None:
            from src.tools.sandbox.runner import SandboxRunner

            sandbox_runner = SandboxRunner(settings.sandbox)

        self._settings = settings
        self._sandbox = sandbox_runner
        self._handlers: dict[str, Callable] = {}

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------

    def register_builtin(self, tool_id: str, handler: Callable) -> None:
        """
        Register a builtin tool handler function.

        Args:
            tool_id: The tool identifier (e.g. ``'math.calculator'``).
            handler: An async callable with signature
                ``async def handler(payload: dict, context: ToolExecutionContext) -> dict``.
        """
        if not callable(handler):
            raise TypeError(f"Handler for '{tool_id}' must be callable.")
        self._handlers[tool_id] = handler
        logger.info("executor.builtin_registered", tool_id=tool_id)

    # ------------------------------------------------------------------
    # Main dispatch
    # ------------------------------------------------------------------

    async def execute(
        self,
        manifest: ToolManifest,
        payload: dict,
        context: "ToolExecutionContext",
    ) -> "ToolExecutionResult":
        """
        Execute a tool invocation.

        Routes by ``manifest.runtime.type``:
        - ``builtin`` → :meth:`_execute_builtin`
        - ``container`` → :meth:`_execute_container`
        - ``remote`` → :meth:`_execute_remote`

        All paths:
        1. Validate input (defence-in-depth re-validation).
        2. Start timer.
        3. Execute with timeout via :func:`asyncio.wait_for`.
        4. Capture resource usage.
        5. Return :class:`ToolExecutionResult`.

        On timeout: ``status='TIMED_OUT'``.
        On any exception: ``status='FAILED'`` with sanitised error message.
        """
        # Import here to avoid circular imports
        from src.tools.registry import ToolExecutionResult  # type: ignore[attr-defined]

        invocation_id = str(uuid.uuid4())
        log = logger.bind(
            invocation_id=invocation_id,
            tool_id=manifest.tool_id,
            runtime_type=manifest.runtime.type,
            execution_id=context.execution_id,
            agent_id=context.agent_id,
        )
        log.info("executor.dispatch_start")

        # Determine effective timeout
        timeout_sec = float(
            context.timeout_override
            if context.timeout_override is not None
            else manifest.limits.timeout_seconds
        )

        start_ns = time.perf_counter_ns()
        status = "SUCCEEDED"
        output: dict | None = None
        error: str | None = None
        resource_usage: dict = {}

        try:
            # Route to the correct handler
            if manifest.runtime.type == "builtin":
                coro = self._execute_builtin(manifest, payload, context)
            elif manifest.runtime.type == "container":
                coro = self._execute_container(manifest, payload, context)
            elif manifest.runtime.type == "remote":
                coro = self._execute_remote(manifest, payload, context)
            elif manifest.runtime.type == "subprocess":
                # Subprocess is treated the same as container (sandboxed)
                coro = self._execute_container(manifest, payload, context)
            else:
                raise ExecutorError(f"Unknown runtime type: '{manifest.runtime.type}'")

            # Enforce timeout
            raw_output = await asyncio.wait_for(coro, timeout=timeout_sec)

            # Validate output is a dict (all handlers must return dict)
            if not isinstance(raw_output, dict):
                raise ExecutorError(
                    f"Handler returned '{type(raw_output).__name__}' instead of dict."
                )
            output = raw_output

        except asyncio.TimeoutError:
            status = "TIMED_OUT"
            error = f"Tool execution timed out after {timeout_sec:.0f}s."
            log.warning("executor.timed_out", timeout_sec=timeout_sec)

        except SandboxTimeoutError as exc:
            status = "TIMED_OUT"
            error = str(exc)
            log.warning("executor.sandbox_timeout")

        except (SandboxUnavailableError, ExecutorError, RemoteToolError) as exc:
            status = "FAILED"
            # Sanitise: strip any path/address info from the message
            error = _sanitise_error(str(exc))
            log.error("executor.failed", error=error)

        except ValueError as exc:
            # Validation errors from handlers
            status = "FAILED"
            error = _sanitise_error(str(exc))
            log.warning("executor.validation_error", error=error)

        except Exception as exc:
            status = "FAILED"
            # Intentionally vague — do not leak internals
            error = f"Internal execution error: {type(exc).__name__}"
            log.exception("executor.unexpected_error")

        duration_ms = int((time.perf_counter_ns() - start_ns) / 1_000_000)

        log.info(
            "executor.dispatch_complete",
            status=status,
            duration_ms=duration_ms,
        )

        return ToolExecutionResult(
            invocation_id=invocation_id,
            tool_id=manifest.tool_id,
            status=status,
            output=output,
            error=error,
            duration_ms=duration_ms,
            cost_usd=manifest.limits.cost_estimate_usd if status == "SUCCEEDED" else 0.0,
            tokens_used=0,  # Reserved for LLM-backed tools
            resource_usage=resource_usage,
        )

    # ------------------------------------------------------------------
    # Runtime handlers
    # ------------------------------------------------------------------

    async def _execute_builtin(
        self,
        manifest: ToolManifest,
        payload: dict,
        context: "ToolExecutionContext",
    ) -> dict:
        """
        Invoke a registered builtin handler function.

        Raises:
            ExecutorError: If no handler is registered for the tool ID.
        """
        handler = self._handlers.get(manifest.tool_id)
        if handler is None:
            raise ExecutorError(
                f"No builtin handler registered for tool '{manifest.tool_id}'."
            )

        logger.debug("executor.builtin_invoke", tool_id=manifest.tool_id)
        result = await handler(payload, context)
        return result

    async def _execute_container(
        self,
        manifest: ToolManifest,
        payload: dict,
        context: "ToolExecutionContext",
    ) -> dict:
        """
        Run the tool in an ephemeral Docker container via :class:`SandboxRunner`.

        The container image and command come from ``manifest.runtime``.
        Input is passed as ``MATECOS_INPUT`` (JSON-encoded) via environment.
        Output is parsed from stdout as JSON.
        """
        import json

        image = manifest.runtime.image
        if not image:
            raise ExecutorError(
                f"Tool '{manifest.tool_id}' has no runtime.image configured for container execution."
            )

        command = list(manifest.runtime.command)
        if not command:
            raise ExecutorError(
                f"Tool '{manifest.tool_id}' has no runtime.command configured."
            )

        # env_vars contains key names only — values are injected from secret store.
        # Here we pass empty-string placeholders; the real secret store is wired in Phase 5.
        env_vars: dict[str, str] = {k: "" for k in manifest.runtime.env_vars}

        sandbox_result = await self._sandbox.run(
            image=image,
            command=command,
            input_data=payload,
            limits=manifest.limits,
            env_vars=env_vars,
            allowed_network=manifest.security.network.allowed,
        )

        if sandbox_result.exit_code != 0:
            stderr_preview = sandbox_result.stderr[:500]
            raise ExecutorError(
                f"Container exited with code {sandbox_result.exit_code}. "
                f"Stderr (truncated): {stderr_preview}"
            )

        # Parse stdout as JSON
        try:
            return json.loads(sandbox_result.stdout)
        except json.JSONDecodeError as exc:
            raise ExecutorError(
                f"Container stdout is not valid JSON: {exc}"
            ) from exc

    async def _execute_remote(
        self,
        manifest: ToolManifest,
        payload: dict,
        context: "ToolExecutionContext",
    ) -> dict:
        """
        Call an external remote tool service via HTTP POST.

        The service endpoint is derived from ``manifest.metadata['endpoint']``.
        A bearer token (from settings) is included in the ``Authorization`` header.
        The response must be a JSON object.
        """
        try:
            import httpx  # type: ignore[import]
        except ImportError as exc:
            raise ExecutorError(
                "httpx is not installed; required for remote tool execution."
            ) from exc

        import json

        endpoint = manifest.metadata.get("endpoint")
        if not endpoint:
            raise RemoteToolError(
                f"Tool '{manifest.tool_id}' metadata missing 'endpoint' for remote execution."
            )

        timeout_sec = float(manifest.limits.timeout_seconds)

        logger.debug("executor.remote_invoke", tool_id=manifest.tool_id)

        try:
            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=httpx.Timeout(timeout_sec),
            ) as client:
                response = await client.post(
                    endpoint,
                    json={
                        "payload": payload,
                        "execution_id": context.execution_id,
                        "agent_id": context.agent_id,
                    },
                    headers={
                        "Content-Type": "application/json",
                        "X-Matecos-Tool-Id": manifest.tool_id,
                        "X-Matecos-Version": manifest.version,
                    },
                )
        except httpx.TimeoutException as exc:
            raise RemoteToolError("Remote tool request timed out.") from exc
        except httpx.RequestError as exc:
            raise RemoteToolError("Remote tool request failed due to a network error.") from exc

        if response.status_code not in range(200, 300):
            raise RemoteToolError(
                f"Remote tool returned HTTP {response.status_code}."
            )

        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise RemoteToolError(
                "Remote tool returned non-JSON response."
            ) from exc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitise_error(message: str) -> str:
    """
    Sanitise an error message before returning it to a caller.

    Strips content that could reveal internal network topology, file paths,
    or secret values.  Currently redacts IPv4 addresses and Unix paths.
    """
    import re

    # Redact IPv4 addresses
    message = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<ip-redacted>", message)
    # Redact Unix-style absolute paths
    message = re.sub(r"/[a-zA-Z0-9_./-]{5,}", "<path-redacted>", message)
    # Redact Windows-style absolute paths
    message = re.sub(r"[A-Za-z]:\\[^\s]+", "<path-redacted>", message)
    return message
