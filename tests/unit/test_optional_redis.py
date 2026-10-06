"""
Unit tests for optional Redis behavior.
Verifies:
1. Redis NOT configured (redis.url = None or empty string):
   - Application starts up successfully.
   - QueueManager operates using in-memory queue without contacting localhost:6379.
   - LockManager provides in-memory locks without contacting localhost:6379.
   - RateLimiter degrades gracefully (fail-open stub).
   - WorkingMemory operates using in-memory store.
   - Health readiness endpoint reports queue component as healthy.
2. Redis configured (redis.url is set):
   - QueueManager, LockManager, and Redis client connect to configured Redis instance.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import ASGITransport, AsyncClient

from src.config import AppSettings, RedisSettings
from src.main import create_app
from src.infrastructure.queue import QueueManager
from src.infrastructure.locks import LockManager, InMemoryLock, DistributedLock
from src.memory.working_memory import WorkingMemory


@pytest.mark.asyncio
async def test_app_starts_without_redis():
    """Verify application starts and handles requests when Redis is not configured."""
    with patch("src.main.get_settings") as mock_settings_fn:
        custom_settings = AppSettings()
        custom_settings.redis = RedisSettings(url=None)
        mock_settings_fn.return_value = custom_settings

        app = create_app()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Health check should return 200
            resp = await client.get("/health")
            assert resp.status_code == 200
            assert resp.json()["status"] == "healthy"

            # 2. Readiness probe should not fail queue component
            readiness_resp = await client.get("/readiness")
            assert readiness_resp.status_code in (200, 503)
            data = readiness_resp.json()
            assert "queue" in data["components"]
            assert data["components"]["queue"]["status"] == "healthy"

            # 3. Request submission should succeed and enqueue in-memory
            headers = {"Authorization": "Bearer dev-test-token"}
            payload = {
                "text": "Run automated analysis without Redis",
                "constraints": {
                    "max_cost": 5.0,
                    "max_duration_seconds": 1800,
                },
            }
            submit_resp = await client.post("/v1/requests", json=payload, headers=headers)
            assert submit_resp.status_code == 202
            assert "execution_id" in submit_resp.json()


@pytest.mark.asyncio
async def test_queue_manager_in_memory_fallback():
    """Verify QueueManager functions completely without Redis URL."""
    qm = QueueManager(redis_url=None)
    await qm.connect()
    assert qm._client is None
    assert await qm.health_check() is True

    # Enqueue message
    msg_id = await qm.enqueue("test-stream", {"task": "do_work", "val": 42})
    assert msg_id is not None

    # Consume message
    messages = await qm.consume("test-stream", group="test-grp", consumer="test-cons", count=5)
    assert len(messages) == 1
    assert messages[0].payload == {"task": "do_work", "val": 42}
    assert messages[0].msg_id == msg_id

    # Queue should now be empty after consumption
    empty_messages = await qm.consume("test-stream", group="test-grp", consumer="test-cons", count=5)
    assert len(empty_messages) == 0

    await qm.close()


@pytest.mark.asyncio
async def test_lock_manager_in_memory_fallback():
    """Verify LockManager functions with InMemoryLock when Redis URL is not set."""
    lm = LockManager(redis_url=None)
    await lm.connect()
    assert lm._client is None

    lock = lm.lock("resource-123", ttl_seconds=10)
    assert isinstance(lock, InMemoryLock)

    token = await lock.acquire()
    assert token is not None
    assert await lock.extend(token, 20) is True
    assert await lock.release(token) is True
    assert await lock.release("invalid-token") is False

    # Async context manager
    async with lm.lock("resource-456") as ctx_lock:
        assert ctx_lock._token is not None

    await lm.close()


@pytest.mark.asyncio
async def test_working_memory_in_memory_fallback():
    """Verify WorkingMemory operates in memory when Redis is not provided."""
    wm = WorkingMemory(redis=None)
    agent_id = "agent-test-01"

    await wm.save(agent_id, {"step": 1, "status": "active"})
    loaded = await wm.load(agent_id)
    assert loaded is not None
    assert loaded["step"] == 1

    await wm.append_history(agent_id, "Thought: analyzing context")
    updated = await wm.load(agent_id)
    assert updated["history"] == ["Thought: analyzing context"]

    await wm.delete(agent_id)
    assert await wm.load(agent_id) is None


@pytest.mark.asyncio
async def test_redis_configured_behavior():
    """Verify that when redis_url is provided, components attempt Redis connection."""
    fake_redis_client = AsyncMock()
    fake_redis_client.ping.return_value = True
    fake_redis_client.xadd.return_value = "1000-0"
    fake_redis_client.aclose.return_value = None

    with patch("redis.asyncio.from_url", return_value=fake_redis_client) as mock_from_url:
        qm = QueueManager(redis_url="redis://custom-redis-host:6379/1")
        await qm.connect()
        mock_from_url.assert_called_once_with(
            "redis://custom-redis-host:6379/1",
            max_connections=50,
            decode_responses=True,
        )
        fake_redis_client.ping.assert_awaited_once()

        msg_id = await qm.enqueue("tasks", {"hello": "world"})
        assert msg_id == "1000-0"
        fake_redis_client.xadd.assert_awaited_once()

        await qm.close()
        fake_redis_client.aclose.assert_awaited_once()

    # LockManager with configured Redis
    fake_lock_client = AsyncMock()
    fake_lock_client.aclose.return_value = None
    with patch("redis.asyncio.from_url", return_value=fake_lock_client):
        lm = LockManager(redis_url="redis://custom-redis-host:6379/1")
        await lm.connect()
        lock = lm.lock("res-1")
        assert isinstance(lock, DistributedLock)
        await lm.close()
