"""
event_store.py
==============
Append-only event store for all MATECOS lifecycle events.

Events are persisted to the ``audit_events`` table via the ORM model.
The store supports querying by execution, event type, and time range.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
import ulid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.models import AuditEvent
from src.infrastructure.models import EventTypeEnum as EventType

logger = structlog.get_logger(__name__)


class EventStore:
    """Append-only event store backed by the ``audit_events`` table.

    All writes are append-only — no UPDATE or DELETE operations are
    ever issued against this table.

    Parameters
    ----------
    session_factory:
        Callable that returns an ``AsyncSession`` context manager.
    """

    def __init__(self, session_factory: Any = None) -> None:
        self._session_factory = session_factory
        self._log = logger.bind(component="EventStore")
        # Fallback in-memory store for testing/dev
        self._memory_store: list[dict[str, Any]] = []

    async def append(
        self,
        execution_id: str,
        event_type: str | EventType,
        actor_id: str,
        data: dict[str, Any] | None = None,
        session: AsyncSession | None = None,
    ) -> str:
        """Append a new event to the store.

        Args:
            execution_id: The parent execution ULID.
            event_type: Event type string or EventType enum.
            actor_id: Who triggered the event (user ID, agent ID, or "system").
            data: Arbitrary structured event data.
            session: Optional session to use (bypasses session_factory).

        Returns:
            The ULID of the new event.
        """
        event_id = ulid.new().str
        now = datetime.now(UTC)

        event_type_str = event_type.value if isinstance(event_type, EventType) else event_type

        if session:
            event = AuditEvent(
                id=event_id,
                execution_id=execution_id,
                event_type=event_type_str,
                actor_id=actor_id,
                data=data or {},
                created_at=now,
            )
            session.add(event)
            self._log.info(
                "event.appended",
                event_id=event_id,
                execution_id=execution_id,
                event_type=event_type_str,
            )
        else:
            # In-memory fallback
            record = {
                "id": event_id,
                "execution_id": execution_id,
                "event_type": event_type_str,
                "actor_id": actor_id,
                "data": data or {},
                "created_at": now,
            }
            self._memory_store.append(record)
            self._log.info(
                "event.appended_in_memory",
                event_id=event_id,
                event_type=event_type_str,
            )

        return event_id

    async def query_by_execution(
        self,
        execution_id: str,
        event_type: str | None = None,
        limit: int = 100,
        session: AsyncSession | None = None,
    ) -> list[dict[str, Any]]:
        """Query events for a specific execution.

        Args:
            execution_id: The execution ULID to filter by.
            event_type: Optional event type filter.
            limit: Maximum number of events to return.
            session: Optional session.

        Returns:
            List of event dicts ordered by created_at ascending.
        """
        if session:
            stmt = (
                select(AuditEvent)
                .where(AuditEvent.execution_id == execution_id)
                .order_by(AuditEvent.created_at.asc())
                .limit(limit)
            )
            if event_type:
                stmt = stmt.where(AuditEvent.event_type == event_type)

            result = await session.execute(stmt)
            events = result.scalars().all()
            return [
                {
                    "id": e.id,
                    "execution_id": e.execution_id,
                    "event_type": e.event_type,
                    "actor_id": e.actor_id,
                    "data": e.data,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in events
            ]

        # In-memory fallback
        filtered = [e for e in self._memory_store if e["execution_id"] == execution_id]
        if event_type:
            filtered = [e for e in filtered if e["event_type"] == event_type]
        return filtered[:limit]

    async def query_by_time_range(
        self,
        start: datetime,
        end: datetime,
        event_type: str | None = None,
        limit: int = 100,
        session: AsyncSession | None = None,
    ) -> list[dict[str, Any]]:
        """Query events within a time range.

        Args:
            start: Range start (inclusive).
            end: Range end (inclusive).
            event_type: Optional event type filter.
            limit: Maximum number of events.
            session: Optional session.

        Returns:
            List of event dicts.
        """
        if session:
            stmt = (
                select(AuditEvent)
                .where(AuditEvent.created_at >= start)
                .where(AuditEvent.created_at <= end)
                .order_by(AuditEvent.created_at.asc())
                .limit(limit)
            )
            if event_type:
                stmt = stmt.where(AuditEvent.event_type == event_type)

            result = await session.execute(stmt)
            events = result.scalars().all()
            return [
                {
                    "id": e.id,
                    "execution_id": e.execution_id,
                    "event_type": e.event_type,
                    "actor_id": e.actor_id,
                    "data": e.data,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in events
            ]

        # In-memory fallback
        filtered = [e for e in self._memory_store if start <= e["created_at"] <= end]
        if event_type:
            filtered = [e for e in filtered if e["event_type"] == event_type]
        return filtered[:limit]

    @property
    def in_memory_count(self) -> int:
        """Return the count of in-memory events (for testing)."""
        return len(self._memory_store)
