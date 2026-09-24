"""
retention.py
============
Memory retention policies — archival and cleanup of old data.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class RetentionPolicy:
    """Manages retention and cleanup of old memory data.

    Enforces configurable TTLs for working memory, episodic memory,
    and the event store.

    Parameters
    ----------
    working_memory_ttl_hours:
        TTL for working memory entries (Redis).
    episodic_retention_days:
        How long to keep episodic memory entries.
    event_archive_days:
        How long before events are eligible for archival.
    """

    def __init__(
        self,
        working_memory_ttl_hours: int = 24,
        episodic_retention_days: int = 90,
        event_archive_days: int = 365,
    ) -> None:
        self._working_ttl = working_memory_ttl_hours
        self._episodic_retention = episodic_retention_days
        self._event_archive = event_archive_days
        self._log = logger.bind(component="RetentionPolicy")

    @property
    def working_memory_ttl_hours(self) -> int:
        """TTL for working memory entries."""
        return self._working_ttl

    @property
    def episodic_cutoff(self) -> datetime:
        """Cutoff datetime for episodic memory retention."""
        return datetime.now(UTC) - timedelta(days=self._episodic_retention)

    @property
    def event_archive_cutoff(self) -> datetime:
        """Cutoff datetime for event archival."""
        return datetime.now(UTC) - timedelta(days=self._event_archive)

    async def cleanup_episodic(
        self, episodic_store: Any
    ) -> int:
        """Remove episodes older than the retention period.

        Args:
            episodic_store: The ``EpisodicMemoryStore`` instance.

        Returns:
            Number of episodes removed.
        """
        cutoff = self.episodic_cutoff
        to_remove: list[str] = []

        for eid, episode in episodic_store._episodes.items():
            if episode.created_at < cutoff:
                to_remove.append(eid)

        for eid in to_remove:
            del episodic_store._episodes[eid]

        if to_remove:
            self._log.info(
                "retention.episodic_cleanup",
                removed_count=len(to_remove),
                cutoff=cutoff.isoformat(),
            )

        return len(to_remove)

    async def cleanup_events(
        self, session: Any = None
    ) -> int:
        """Archive events older than the archive period.

        In production, this moves old events to a cold-storage table
        or external archive.  The in-memory implementation is a no-op.

        Args:
            session: Optional DB session for production archival.

        Returns:
            Number of events archived.
        """
        self._log.info(
            "retention.event_archive_check",
            archive_cutoff=self.event_archive_cutoff.isoformat(),
        )
        # Full implementation requires DB session — stub for now
        return 0
