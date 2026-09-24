"""
test_memory.py
==============
Tests for the memory subsystem: event store, working memory, episodic memory.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.memory.episodic_memory import Episode, EpisodicMemoryStore
from src.memory.event_store import EventStore
from src.memory.retention import RetentionPolicy
from src.memory.working_memory import WorkingMemory

# ===========================================================================
# Event Store
# ===========================================================================


class TestEventStore:
    @pytest.mark.asyncio
    async def test_append_in_memory(self) -> None:
        store = EventStore()
        event_id = await store.append(
            execution_id="exec-1",
            event_type="REQUEST_RECEIVED",
            actor_id="user-1",
            data={"key": "value"},
        )
        assert event_id
        assert store.in_memory_count == 1

    @pytest.mark.asyncio
    async def test_query_by_execution(self) -> None:
        store = EventStore()
        await store.append("exec-1", "REQUEST_RECEIVED", "user-1")
        await store.append("exec-1", "TASK_STARTED", "system")
        await store.append("exec-2", "REQUEST_RECEIVED", "user-2")

        events = await store.query_by_execution("exec-1")
        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_query_by_event_type(self) -> None:
        store = EventStore()
        await store.append("exec-1", "REQUEST_RECEIVED", "user-1")
        await store.append("exec-1", "TASK_STARTED", "system")

        events = await store.query_by_execution("exec-1", event_type="TASK_STARTED")
        assert len(events) == 1
        assert events[0]["event_type"] == "TASK_STARTED"


# ===========================================================================
# Working Memory
# ===========================================================================


class TestWorkingMemory:
    @pytest.mark.asyncio
    async def test_save_and_load(self) -> None:
        wm = WorkingMemory()
        await wm.save("agent-1", {"objective": "test", "history": []})
        data = await wm.load("agent-1")
        assert data is not None
        assert data["objective"] == "test"

    @pytest.mark.asyncio
    async def test_load_nonexistent(self) -> None:
        wm = WorkingMemory()
        data = await wm.load("nonexistent")
        assert data is None

    @pytest.mark.asyncio
    async def test_delete(self) -> None:
        wm = WorkingMemory()
        await wm.save("agent-1", {"data": "test"})
        await wm.delete("agent-1")
        data = await wm.load("agent-1")
        assert data is None

    @pytest.mark.asyncio
    async def test_append_history(self) -> None:
        wm = WorkingMemory()
        await wm.save("agent-1", {"history": []})
        await wm.append_history("agent-1", "Step 1: Did something")
        await wm.append_history("agent-1", "Step 2: Did something else")

        data = await wm.load("agent-1")
        assert data is not None
        assert len(data["history"]) == 2

    @pytest.mark.asyncio
    async def test_append_history_rolling_window(self) -> None:
        wm = WorkingMemory()
        await wm.save("agent-1", {"history": []})
        for i in range(60):
            await wm.append_history("agent-1", f"Step {i}", max_entries=50)

        data = await wm.load("agent-1")
        assert data is not None
        assert len(data["history"]) == 50


# ===========================================================================
# Episodic Memory
# ===========================================================================


class TestEpisodicMemory:
    @pytest.mark.asyncio
    async def test_store_and_retrieve(self) -> None:
        store = EpisodicMemoryStore()
        episode = Episode(
            execution_id="exec-1",
            goal="Analyse sales data",
            outcome="COMPLETED",
            tools_used=["data.csv.profile", "math.calculator"],
        )
        eid = await store.store(episode)
        assert eid

        retrieved = await store.retrieve_by_execution("exec-1")
        assert retrieved is not None
        assert retrieved.goal == "Analyse sales data"

    @pytest.mark.asyncio
    async def test_search_by_keywords(self) -> None:
        store = EpisodicMemoryStore()
        await store.store(
            Episode(
                execution_id="exec-1",
                goal="Analyse sales data for Q4",
                outcome="COMPLETED",
            )
        )
        await store.store(
            Episode(
                execution_id="exec-2",
                goal="Write a report about marketing",
                outcome="COMPLETED",
            )
        )

        results = await store.search(keywords=["sales"])
        assert len(results) == 1
        assert results[0].execution_id == "exec-1"

    @pytest.mark.asyncio
    async def test_search_by_outcome(self) -> None:
        store = EpisodicMemoryStore()
        await store.store(Episode(execution_id="e1", goal="A", outcome="COMPLETED"))
        await store.store(Episode(execution_id="e2", goal="B", outcome="FAILED"))

        results = await store.search(outcome="FAILED")
        assert len(results) == 1
        assert results[0].outcome == "FAILED"

    @pytest.mark.asyncio
    async def test_search_by_tools(self) -> None:
        store = EpisodicMemoryStore()
        await store.store(
            Episode(
                execution_id="e1",
                goal="A",
                tools_used=["web.search"],
            )
        )
        await store.store(
            Episode(
                execution_id="e2",
                goal="B",
                tools_used=["math.calculator"],
            )
        )

        results = await store.search(tools_used=["web.search"])
        assert len(results) == 1


# ===========================================================================
# Retention Policy
# ===========================================================================


class TestRetentionPolicy:
    def test_cutoff_dates(self) -> None:
        policy = RetentionPolicy(
            episodic_retention_days=30,
            event_archive_days=365,
        )
        now = datetime.now(UTC)

        episodic_cutoff = policy.episodic_cutoff
        assert (now - episodic_cutoff).days <= 31

        event_cutoff = policy.event_archive_cutoff
        assert (now - event_cutoff).days <= 366

    @pytest.mark.asyncio
    async def test_cleanup_episodic(self) -> None:
        store = EpisodicMemoryStore()
        old_episode = Episode(
            execution_id="old",
            goal="Old task",
            created_at=datetime.now(UTC) - timedelta(days=100),
        )
        new_episode = Episode(
            execution_id="new",
            goal="New task",
        )
        await store.store(old_episode)
        await store.store(new_episode)

        policy = RetentionPolicy(episodic_retention_days=30)
        removed = await policy.cleanup_episodic(store)
        assert removed == 1
        assert store.episode_count == 1
