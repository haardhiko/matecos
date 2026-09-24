"""
isolation.py
============
Execution isolation — container and process isolation for tool execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class IsolationPolicy:
    """Defines the isolation requirements for a tool execution.

    Attributes:
        network_disabled: If True, ``--network none`` (hard invariant).
        readonly_rootfs: If True, mount rootfs as read-only.
        max_memory_mb: Memory limit for the container.
        max_cpu_percent: CPU usage cap (percentage).
        max_pids: Maximum number of processes.
        tmpfs_size_mb: Size of the tmpfs mount.
        timeout_seconds: Execution timeout.
        drop_capabilities: Linux capabilities to drop.
    """

    network_disabled: bool = True  # HARD INVARIANT — always True for sandboxed tools
    readonly_rootfs: bool = True
    max_memory_mb: int = 2048
    max_cpu_percent: int = 50
    max_pids: int = 256
    tmpfs_size_mb: int = 512
    timeout_seconds: int = 300
    drop_capabilities: list[str] = field(
        default_factory=lambda: [
            "ALL",  # Drop all, then selectively add if needed
        ]
    )


# Pre-defined isolation profiles
STRICT_ISOLATION = IsolationPolicy(
    network_disabled=True,
    readonly_rootfs=True,
    max_memory_mb=1024,
    max_cpu_percent=25,
    max_pids=128,
    tmpfs_size_mb=256,
    timeout_seconds=120,
)

STANDARD_ISOLATION = IsolationPolicy(
    network_disabled=True,
    readonly_rootfs=True,
    max_memory_mb=2048,
    max_cpu_percent=50,
    max_pids=256,
    tmpfs_size_mb=512,
    timeout_seconds=300,
)

RELAXED_ISOLATION = IsolationPolicy(
    network_disabled=True,  # Never change this — hard invariant
    readonly_rootfs=False,
    max_memory_mb=4096,
    max_cpu_percent=75,
    max_pids=512,
    tmpfs_size_mb=1024,
    timeout_seconds=600,
)


class IsolationManager:
    """Manages execution isolation for tool invocations.

    Ensures all sandboxed tool executions run with ``--network none``
    as an absolute invariant that cannot be overridden.
    """

    def __init__(self, default_policy: IsolationPolicy | None = None) -> None:
        self._default = default_policy or STANDARD_ISOLATION
        self._log = logger.bind(component="IsolationManager")

    def get_policy(self, tool_id: str, risk_level: str = "LOW") -> IsolationPolicy:
        """Get the isolation policy for a tool based on its risk level.

        Args:
            tool_id: The tool identifier.
            risk_level: The assessed risk level.

        Returns:
            An ``IsolationPolicy`` appropriate for the risk level.
        """
        risk = risk_level.upper()
        if risk == "CRITICAL":
            self._log.info("isolation.strict", tool_id=tool_id)
            return STRICT_ISOLATION
        if risk == "HIGH":
            self._log.info("isolation.strict", tool_id=tool_id)
            return STRICT_ISOLATION
        if risk == "MEDIUM":
            return self._default
        return RELAXED_ISOLATION

    def build_docker_args(self, policy: IsolationPolicy) -> list[str]:
        """Build Docker CLI arguments from an isolation policy.

        The ``--network none`` flag is ALWAYS included regardless of
        the policy setting. This is a security invariant.

        Args:
            policy: The isolation policy.

        Returns:
            List of Docker CLI argument strings.
        """
        args: list[str] = [
            "--network", "none",  # HARD INVARIANT — always enforced
            "--memory", f"{policy.max_memory_mb}m",
            "--pids-limit", str(policy.max_pids),
            "--cpus", str(policy.max_cpu_percent / 100),
            "--tmpfs", f"/tmp:{policy.tmpfs_size_mb}m",
        ]

        if policy.readonly_rootfs:
            args.extend(["--read-only"])

        for cap in policy.drop_capabilities:
            args.extend(["--cap-drop", cap])

        # Security opts
        args.extend([
            "--security-opt", "no-new-privileges",
            "--user", "65534:65534",  # nobody:nogroup
        ])

        return args
