"""
episodic_memory.py
==================
Long-term episodic memory — stores completed execution summaries for
cross-execution learning and retrieval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
import ulid

logger = structlog.get_logger(__name__)


@dataclass
class Episode:
    """A complete episode representing one execution's outcome.

    Attributes:
        episode_id: Unique identifier (ULID).
        execution_id: The execution this episode summarises.
        goal: The original user goal.
        outcome: COMPLETED, FAILED, PARTIALLY_COMPLETED.
        key_decisions: List of important decisions made.
        tools_used: List of tool IDs that were invoked.
        total_cost_usd: Total cost incurred.
        total_duration_ms: Total wall-clock time.
        lessons: Lessons learned from this execution.
        failures: Any failures encountered.
        tags: Searchable tags for retrieval.
        created_at: When this episode was recorded.
    """

    episode_id: str = field(default_factory=lambda: ulid.new().str)
    execution_id: str = ""
    goal: str = ""
    outcome: str = "COMPLETED"
    key_decisions: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    total_cost_usd: float = 0.0
    total_duration_ms: int = 0
    lessons: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class EpisodicMemoryStore:
    """Stores and retrieves execution episodes.

    In production, episodes are persisted to the ``episodic_memory``
    PostgreSQL table.  This implementation provides an in-memory store
    with keyword-based retrieval.

    Parameters
    ----------
    retention_days:
        How long to keep episodes before they're eligible for archival.
    """

    def __init__(self, retention_days: int = 90) -> None:
        self._retention_days = retention_days
        self._episodes: dict[str, Episode] = {}
        self._log = logger.bind(component="EpisodicMemoryStore")

    async def store(self, episode: Episode) -> str:
        """Store a new episode.

        Args:
            episode: The episode to store.

        Returns:
            The episode ID.
        """
        self._episodes[episode.episode_id] = episode
        self._log.info(
            "episodic.stored",
            episode_id=episode.episode_id,
            execution_id=episode.execution_id,
            outcome=episode.outcome,
        )
        return episode.episode_id

    async def retrieve_by_execution(self, execution_id: str) -> Episode | None:
        """Retrieve the episode for a specific execution.

        Args:
            execution_id: The execution ULID.

        Returns:
            The ``Episode`` or ``None``.
        """
        for episode in self._episodes.values():
            if episode.execution_id == execution_id:
                return episode
        return None

    async def search(
        self,
        keywords: list[str] | None = None,
        outcome: str | None = None,
        tools_used: list[str] | None = None,
        limit: int = 10,
    ) -> list[Episode]:
        """Search episodes by keywords, outcome, or tools.

        Args:
            keywords: Words to search for in goals and lessons.
            outcome: Filter by outcome status.
            tools_used: Filter by tools that were used.
            limit: Maximum results.

        Returns:
            List of matching episodes, most recent first.
        """
        results: list[Episode] = []

        for episode in sorted(
            self._episodes.values(),
            key=lambda e: e.created_at,
            reverse=True,
        ):
            if outcome and episode.outcome != outcome:
                continue

            if tools_used:
                if not set(tools_used).intersection(episode.tools_used):
                    continue

            if keywords:
                text = f"{episode.goal} {' '.join(episode.lessons)} {' '.join(episode.tags)}"
                text_lower = text.lower()
                if not any(kw.lower() in text_lower for kw in keywords):
                    continue

            results.append(episode)
            if len(results) >= limit:
                break

        self._log.debug("episodic.search", results_count=len(results))
        return results

    async def get_similar_episodes(
        self, goal: str, limit: int = 5
    ) -> list[Episode]:
        """Find episodes with similar goals (keyword-based).

        Args:
            goal: The goal to find similar episodes for.
            limit: Maximum results.

        Returns:
            List of similar episodes.
        """
        # Simple keyword extraction
        words = [w.lower() for w in goal.split() if len(w) > 3]
        return await self.search(keywords=words[:10], limit=limit)

    @property
    def episode_count(self) -> int:
        """Return the total number of stored episodes."""
        return len(self._episodes)
