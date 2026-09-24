"""Distributed locking via Redis (Redlock pattern)."""
from __future__ import annotations

import secrets
import time
from types import TracebackType
from typing import Any

import redis.asyncio as aioredis

from src.infrastructure.telemetry import get_logger

logger = get_logger(__name__)

# Lua scripts for atomic operations
_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

_EXTEND_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("pexpire", KEYS[1], ARGV[2])
else
    return 0
end
"""


class LockNotAcquiredError(Exception):
    """Raised when a lock cannot be acquired within the configured timeout."""


class DistributedLock:
    """
    A single distributed lock backed by Redis.

    Implements a simplified Redlock using a single Redis node.
    For true multi-node Redlock, extend to multiple Redis instances.
    """

    def __init__(
        self,
        client: aioredis.Redis,
        key: str,
        ttl_seconds: int = 30,
        retry_count: int = 3,
        retry_delay_ms: int = 100,
    ) -> None:
        self._client = client
        self._key = f"lock:{key}"
        self._ttl_ms = ttl_seconds * 1000
        self._retry_count = retry_count
        self._retry_delay = retry_delay_ms / 1000.0
        self._token: str | None = None

    async def acquire(self) -> str | None:
        """
        Attempt to acquire the lock.

        Returns:
            Token string if acquired, None if not acquired after retries.
        """
        token = secrets.token_hex(16)
        for attempt in range(self._retry_count):
            acquired = await self._client.set(
                self._key, token, px=self._ttl_ms, nx=True
            )
            if acquired:
                self._token = token
                logger.debug("lock.acquired", key=self._key, attempt=attempt)
                return token
            if attempt < self._retry_count - 1:
                await _async_sleep(self._retry_delay)
        logger.warning("lock.failed_to_acquire", key=self._key, retries=self._retry_count)
        return None

    async def release(self, token: str) -> bool:
        """
        Release the lock if the token matches.

        Returns:
            True if released, False if token mismatch (lock already expired or stolen).
        """
        result: Any = await self._client.eval(_RELEASE_SCRIPT, 1, self._key, token)
        released = bool(result)
        if released:
            self._token = None
            logger.debug("lock.released", key=self._key)
        else:
            logger.warning("lock.release_failed", key=self._key, reason="token_mismatch_or_expired")
        return released

    async def extend(self, token: str, ttl_seconds: int) -> bool:
        """Extend lock TTL if the token matches. Returns True on success."""
        result: Any = await self._client.eval(
            _EXTEND_SCRIPT, 1, self._key, token, str(ttl_seconds * 1000)
        )
        return bool(result)

    async def __aenter__(self) -> "DistributedLock":
        token = await self.acquire()
        if token is None:
            raise LockNotAcquiredError(f"Could not acquire lock: {self._key}")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._token:
            await self.release(self._token)


async def _async_sleep(seconds: float) -> None:
    import asyncio
    await asyncio.sleep(seconds)


class LockManager:
    """Factory for DistributedLock instances sharing a single Redis connection."""

    def __init__(self, redis_url: str, max_connections: int = 10) -> None:
        self._url = redis_url
        self._max_connections = max_connections
        self._client: aioredis.Redis | None = None

    async def connect(self) -> None:
        self._client = aioredis.from_url(
            self._url,
            max_connections=self._max_connections,
            decode_responses=True,
        )

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()

    def lock(
        self,
        key: str,
        ttl_seconds: int = 30,
        retry_count: int = 3,
        retry_delay_ms: int = 100,
    ) -> DistributedLock:
        """Create a DistributedLock for the given key."""
        if self._client is None:
            raise RuntimeError("LockManager not connected. Call connect() first.")
        return DistributedLock(
            self._client, key, ttl_seconds, retry_count, retry_delay_ms
        )
