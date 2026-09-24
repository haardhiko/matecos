"""
working_memory.py
=================
Short-lived working memory for active agent executions.

Working memory is stored in Redis with a configurable TTL and provides
fast access to the current state of an agent's reasoning context.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_REDIS_PREFIX = "matecos:working_memory:"


class WorkingMemory:
    """Redis-backed working memory for active agent executions.

    Stores the current context, history, and intermediate results for
    an agent during its ReAct loop execution.

    Parameters
    ----------
    redis:
        An async Redis client instance.
    ttl_hours:
        Time-to-live for working memory entries.
    """

    def __init__(self, redis: Any = None, ttl_hours: int = 24) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_hours * 3600
        self._log = logger.bind(component="WorkingMemory")
        # In-memory fallback for testing
        self._memory_store: dict[str, dict[str, Any]] = {}

    def _key(self, agent_id: str) -> str:
        return f"{_REDIS_PREFIX}{agent_id}"

    async def save(
        self,
        agent_id: str,
        data: dict[str, Any],
    ) -> None:
        """Save or update working memory for an agent.

        Args:
            agent_id: The agent's ULID.
            data: The working memory snapshot.
        """
        data["updated_at"] = datetime.now(UTC).isoformat()

        if self._redis:
            key = self._key(agent_id)
            await self._redis.setex(key, self._ttl_seconds, json.dumps(data))
        else:
            self._memory_store[agent_id] = data

        self._log.debug("working_memory.saved", agent_id=agent_id)

    async def load(self, agent_id: str) -> dict[str, Any] | None:
        """Load working memory for an agent.

        Args:
            agent_id: The agent's ULID.

        Returns:
            The working memory dict or None if not found / expired.
        """
        if self._redis:
            key = self._key(agent_id)
            raw = await self._redis.get(key)
            if raw:
                return json.loads(raw)
            return None

        return self._memory_store.get(agent_id)

    async def delete(self, agent_id: str) -> None:
        """Delete working memory for an agent.

        Args:
            agent_id: The agent's ULID.
        """
        if self._redis:
            key = self._key(agent_id)
            await self._redis.delete(key)
        else:
            self._memory_store.pop(agent_id, None)

        self._log.debug("working_memory.deleted", agent_id=agent_id)

    async def append_history(
        self,
        agent_id: str,
        entry: str,
        max_entries: int = 50,
    ) -> None:
        """Append an entry to the agent's working history.

        Maintains a rolling window of ``max_entries``.

        Args:
            agent_id: The agent's ULID.
            entry: The history entry to append.
            max_entries: Maximum history length.
        """
        data = await self.load(agent_id) or {}
        history: list[str] = data.get("history", [])
        history.append(entry)

        # Trim to max_entries
        if len(history) > max_entries:
            history = history[-max_entries:]

        data["history"] = history
        await self.save(agent_id, data)
