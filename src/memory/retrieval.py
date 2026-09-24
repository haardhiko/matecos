"""
retrieval.py
============
Memory retrieval — unified interface for querying across memory stores.
"""

from __future__ import annotations

from typing import Any

import structlog

from src.memory.episodic_memory import Episode, EpisodicMemoryStore
from src.memory.event_store import EventStore
from src.memory.working_memory import WorkingMemory

logger = structlog.get_logger(__name__)


class MemoryRetrieval:
    """Unified retrieval interface across all memory subsystems.

    Provides a single query point that searches working memory, episodic
    memory, and the event store as appropriate.

    Parameters
    ----------
    event_store:
        The append-only event store.
    working_memory:
        The short-lived working memory.
    episodic_memory:
        The long-term episodic memory store.
    """

    def __init__(
        self,
        event_store: EventStore,
        working_memory: WorkingMemory,
        episodic_memory: EpisodicMemoryStore,
    ) -> None:
        self._events = event_store
        self._working = working_memory
        self._episodic = episodic_memory
        self._log = logger.bind(component="MemoryRetrieval")

    async def get_agent_context(self, agent_id: str) -> dict[str, Any]:
        """Retrieve the full context for an active agent.

        Combines working memory with any relevant episodic memories.

        Args:
            agent_id: The agent's ULID.

        Returns:
            Dict with ``working_memory`` and ``relevant_episodes`` keys.
        """
        working = await self._working.load(agent_id) or {}

        # Find relevant past episodes based on current objective
        objective = working.get("objective", "")
        episodes: list[Episode] = []
        if objective:
            episodes = await self._episodic.get_similar_episodes(objective, limit=3)

        return {
            "working_memory": working,
            "relevant_episodes": [
                {
                    "goal": ep.goal,
                    "outcome": ep.outcome,
                    "lessons": ep.lessons,
                    "tools_used": ep.tools_used,
                }
                for ep in episodes
            ],
        }

    async def get_execution_timeline(
        self,
        execution_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get the complete event timeline for an execution.

        Args:
            execution_id: The execution ULID.
            limit: Maximum events.

        Returns:
            List of event dicts in chronological order.
        """
        return await self._events.query_by_execution(
            execution_id=execution_id,
            limit=limit,
        )

    async def search_past_executions(
        self,
        keywords: list[str] | None = None,
        outcome: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search past executions via episodic memory.

        Args:
            keywords: Search terms.
            outcome: Filter by outcome.
            limit: Maximum results.

        Returns:
            List of episode summary dicts.
        """
        episodes = await self._episodic.search(
            keywords=keywords,
            outcome=outcome,
            limit=limit,
        )

        return [
            {
                "episode_id": ep.episode_id,
                "execution_id": ep.execution_id,
                "goal": ep.goal,
                "outcome": ep.outcome,
                "tools_used": ep.tools_used,
                "total_cost_usd": ep.total_cost_usd,
                "lessons": ep.lessons,
                "created_at": ep.created_at.isoformat(),
            }
            for ep in episodes
        ]
