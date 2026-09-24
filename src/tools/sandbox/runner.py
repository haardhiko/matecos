"""
Docker-based ephemeral sandbox for tool execution.

Every container spawned here is:
  - Read-only root filesystem (tmpfs for /tmp)
  - Non-root UID (65534 / nobody)
  - Network disabled unless explicitly allowed
  - Memory + CPU hard-capped
  - Auto-removed on completion or timeout
  - Input via environment / stdin only — no host mounts
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING

import structlog

from src.tools.manifests import ResourceLimits

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class SandboxSettings:
    """Configuration for the SandboxRunner."""

    def __init__(
        self,
        docker_base_url: str = "unix://var/run/docker.sock",
        default_uid: int = 65534,  # nobody
        default_gid: int = 65534,
        network_name: str | None = None,  # name of a Docker network for allowed_network mode
        container_label_prefix: str = "matecos",
    ) -> None:
        """Initialise sandbox settings."""
        self.docker_base_url = docker_base_url
        self.default_uid = default_uid
        self.default_gid = default_gid
        self.network_name = network_name
        self.container_label_prefix = container_label_prefix


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


from pydantic import BaseModel


class SandboxResourceUsage(BaseModel):
    """Aggregated resource usage from a container run."""

    cpu_seconds: float
    memory_mb_peak: float
    disk_mb: float


class SandboxResult(BaseModel):
    """Result of a single ephemeral container execution."""

    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    resource_usage: SandboxResourceUsage
    output_files: list[str] = []


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SandboxUnavailableError(Exception):
    """Raised when the Docker daemon cannot be reached."""


class SandboxTimeoutError(Exception):
    """Raised when a container exceeds its allowed runtime."""


class SandboxSecurityError(Exception):
    """Raised when a security constraint is violated before container launch."""


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class SandboxRunner:
    """
    Runs tool code in isolated Docker containers.

    Each invocation creates a fresh ephemeral container that is automatically
    removed after execution or timeout.  Input is passed via environment
    variables / stdin JSON; output is read from stdout as JSON.
    """

    # Label applied to every container so we can clean up stragglers
    _SESSION_LABEL = "matecos.session"

    def __init__(self, settings: SandboxSettings) -> None:
        """Store settings; Docker client is lazy-initialised via :meth:`initialize`."""
        self._settings = settings
        self._client: object | None = None  # docker.DockerClient at runtime
        self._session_id: str = ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """
        Connect to the Docker daemon.

        Raises :class:`SandboxUnavailableError` if Docker is not reachable.
        """
        try:
            import uuid

            import docker  # type: ignore[import]

            self._session_id = str(uuid.uuid4())
            loop = asyncio.get_event_loop()
            self._client = await loop.run_in_executor(
                None,
                lambda: docker.DockerClient(base_url=self._settings.docker_base_url),
            )
            # Verify the daemon is responsive
            await loop.run_in_executor(None, self._client.ping)  # type: ignore[union-attr]
            logger.info(
                "sandbox_runner.initialized",
                session_id=self._session_id,
                docker_url=self._settings.docker_base_url,
            )
        except ImportError as exc:
            raise SandboxUnavailableError(
                "docker-py is not installed; run 'pip install docker'."
            ) from exc
        except Exception as exc:
            raise SandboxUnavailableError(
                f"Cannot connect to Docker daemon at '{self._settings.docker_base_url}': {exc}"
            ) from exc

    async def health_check(self) -> bool:
        """
        Check if the Docker daemon is reachable.

        Returns True when healthy, False otherwise.
        """
        if self._client is None:
            return False
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._client.ping)  # type: ignore[union-attr]
            return True
        except Exception:
            logger.warning("sandbox_runner.health_check_failed")
            return False

    async def cleanup(self) -> None:
        """
        Remove any stale containers from this session.

        Containers are labelled with the session ID so stragglers can be
        found even after a crash.
        """
        if self._client is None:
            return
        try:
            loop = asyncio.get_event_loop()
            label_filter = {
                "label": [
                    f"{self._SESSION_LABEL}={self._session_id}",
                ]
            }
            containers = await loop.run_in_executor(
                None,
                lambda: self._client.containers.list(  # type: ignore[union-attr]
                    all=True, filters=label_filter
                ),
            )
            for container in containers:
                try:
                    await loop.run_in_executor(None, lambda c=container: c.remove(force=True))
                    logger.info(
                        "sandbox_runner.stale_container_removed",
                        container_id=container.short_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "sandbox_runner.stale_container_remove_failed",
                        container_id=container.short_id,
                        error=str(exc),
                    )
        except Exception as exc:
            logger.error("sandbox_runner.cleanup_failed", error=str(exc))

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def run(
        self,
        image: str,
        command: list[str],
        input_data: dict,
        limits: ResourceLimits,
        env_vars: dict[str, str],
        allowed_network: bool = False,
    ) -> SandboxResult:
        """
        Run *command* in an ephemeral container built from *image*.

        Security guarantees enforced here:
        - ``network_mode='none'`` unless *allowed_network* is True
        - Read-only root filesystem with ``tmpfs`` at ``/tmp``
        - Non-root user (UID/GID from :class:`SandboxSettings`)
        - Hard memory cap (``mem_limit``)
        - Hard CPU cap (``cpu_quota``)
        - ``auto_remove=True`` (container deleted on exit)
        - No host volume mounts; input passed via ``MATECOS_INPUT`` env var as JSON
        - Timeout enforced via :func:`asyncio.wait_for`

        Returns a :class:`SandboxResult` on success.
        Raises :class:`SandboxUnavailableError` if the client is not initialised.
        Raises :class:`SandboxTimeoutError` on timeout.
        """
        if self._client is None:
            raise SandboxUnavailableError(
                "SandboxRunner not initialised; call await runner.initialize() first."
            )

        # Serialise input as JSON and inject via env var (never via file mount)
        serialised_input = json.dumps(input_data)

        # Build environment dict — merge caller env_vars (key names only) with input
        container_env: dict[str, str] = {
            **env_vars,
            "MATECOS_INPUT": serialised_input,
            "MATECOS_SESSION": self._session_id,
        }

        # Security-hardened container configuration
        container_kwargs: dict = {
            "image": image,
            "command": command,
            "environment": container_env,
            "network_mode": "bridge" if allowed_network else "none",
            "read_only": True,
            "tmpfs": {"/tmp": "size=64m,mode=1777"},
            "user": f"{self._settings.default_uid}:{self._settings.default_gid}",
            "mem_limit": f"{limits.max_memory_mb}m",
            "cpu_quota": limits.max_cpu_quota,
            "labels": {
                self._SESSION_LABEL: self._session_id,
                "matecos.managed": "true",
            },
            "detach": True,
            # auto_remove is not used here because we need to read logs/stats first
            "remove": False,
            # Security options
            "security_opt": [f"seccomp={self._get_seccomp_policy()}"],
            "cap_drop": ["ALL"],
        }

        log = logger.bind(image=image, session_id=self._session_id)
        log.info("sandbox_runner.starting_container")

        loop = asyncio.get_event_loop()
        container = None
        start_ns = time.perf_counter_ns()

        try:
            # Create the container (synchronous Docker SDK call, offloaded to executor)
            container = await loop.run_in_executor(
                None,
                lambda: self._client.containers.run(**container_kwargs),  # type: ignore[union-attr]
            )
            log = log.bind(container_id=container.short_id)
            log.info("sandbox_runner.container_started")

            # Wait for the container to finish, honouring timeout
            timeout_sec = float(limits.timeout_seconds)
            try:
                exit_result = await asyncio.wait_for(
                    loop.run_in_executor(None, container.wait),
                    timeout=timeout_sec,
                )
            except TimeoutError:
                log.warning("sandbox_runner.container_timeout")
                await loop.run_in_executor(None, lambda: container.kill())
                raise SandboxTimeoutError(
                    f"Container exceeded timeout of {limits.timeout_seconds}s."
                )

            duration_ms = int((time.perf_counter_ns() - start_ns) / 1_000_000)
            exit_code: int = exit_result.get("StatusCode", -1)

            # Collect stdout/stderr (capped to avoid OOM)
            max_bytes = limits.max_output_kb * 1024
            stdout_raw: bytes = await loop.run_in_executor(
                None,
                lambda: container.logs(stdout=True, stderr=False),
            )
            stderr_raw: bytes = await loop.run_in_executor(
                None,
                lambda: container.logs(stdout=False, stderr=True),
            )
            stdout_str = stdout_raw[:max_bytes].decode("utf-8", errors="replace")
            stderr_str = stderr_raw[:max_bytes].decode("utf-8", errors="replace")

            # Collect resource usage stats (best-effort)
            resource_usage = await self._collect_stats(container, loop)

            log.info(
                "sandbox_runner.container_finished",
                exit_code=exit_code,
                duration_ms=duration_ms,
            )
            return SandboxResult(
                stdout=stdout_str,
                stderr=stderr_str,
                exit_code=exit_code,
                duration_ms=duration_ms,
                resource_usage=resource_usage,
            )

        except SandboxTimeoutError:
            raise
        except Exception as exc:
            duration_ms = int((time.perf_counter_ns() - start_ns) / 1_000_000)
            log.error("sandbox_runner.container_error", error=str(exc))
            raise SandboxUnavailableError(
                f"Container execution failed: {type(exc).__name__}"
            ) from exc
        finally:
            # Always remove the container
            if container is not None:
                try:
                    await loop.run_in_executor(None, lambda: container.remove(force=True))
                except Exception:
                    pass  # best-effort cleanup

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _collect_stats(
        self, container: object, loop: asyncio.AbstractEventLoop
    ) -> SandboxResourceUsage:
        """
        Collect resource usage from a finished container.

        Falls back to zeros on failure (stats may be unavailable for very
        short-lived containers).
        """
        try:
            stats: dict = await loop.run_in_executor(
                None,
                lambda: container.stats(stream=False),  # type: ignore[union-attr]
            )
            # CPU: calculate from cpu_stats delta
            cpu_delta = stats.get("cpu_stats", {}).get("cpu_usage", {}).get(
                "total_usage", 0
            ) - stats.get("precpu_stats", {}).get("cpu_usage", {}).get("total_usage", 0)
            cpu_seconds = cpu_delta / 1e9  # nanoseconds → seconds

            # Memory: usage_in_bytes at peak
            memory_bytes = stats.get("memory_stats", {}).get("max_usage", 0)
            memory_mb = memory_bytes / (1024 * 1024)

            return SandboxResourceUsage(
                cpu_seconds=max(0.0, cpu_seconds),
                memory_mb_peak=max(0.0, memory_mb),
                disk_mb=0.0,  # disk usage is not easily measurable at runtime
            )
        except Exception:
            return SandboxResourceUsage(cpu_seconds=0.0, memory_mb_peak=0.0, disk_mb=0.0)

    def _get_seccomp_policy(self) -> str:
        """
        Return the seccomp profile name/path.

        The 'default' profile blocks the most dangerous syscalls (e.g. ``ptrace``,
        ``reboot``, ``kexec_load``).  Override in settings for stricter profiles.
        """
        return "default"
