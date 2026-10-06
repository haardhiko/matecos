"""Redis Streams-based queue with consumer groups and dead-letter handling."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import redis.asyncio as aioredis

from src.infrastructure.telemetry import get_logger

logger = get_logger(__name__)

DLQ_SUFFIX = ":dlq"


@dataclass
class QueueMessage:
    """A message received from a Redis Stream."""

    stream: str
    msg_id: str
    payload: dict[str, Any]
    delivered_count: int = 1


class QueueManager:
    """
    Redis Streams-based async queue manager.

    Supports consumer groups, dead-letter queues, and health checking.
    """

    def __init__(self, redis_url: str | None = None, max_connections: int = 50) -> None:
        self._url = redis_url
        self._max_connections = max_connections
        self._client: aioredis.Redis | None = None
        # In-memory stream storage when Redis is not configured
        self._memory_streams: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        self._memory_counter: int = 0

    async def connect(self) -> None:
        """Establish connection pool to Redis if configured."""
        if not self._url:
            logger.info("queue.redis_not_configured_using_in_memory")
            return
        self._client = aioredis.from_url(
            self._url,
            max_connections=self._max_connections,
            decode_responses=True,
        )
        await self._client.ping()
        logger.info("queue.connected", url=self._url)

    async def close(self) -> None:
        """Close all connections."""
        if self._client:
            await self._client.aclose()
            self._client = None

    def _require_client(self) -> aioredis.Redis | None:
        return self._client

    async def enqueue(
        self,
        stream: str,
        payload: dict[str, Any],
        msg_id: str | None = None,
        max_len: int = 10_000,
    ) -> str:
        """
        Add a message to a Redis Stream (or in-memory fallback).

        Args:
            stream: Stream key name.
            payload: Serializable dict to enqueue.
            msg_id: Optional explicit message ID.
            max_len: Approximate max stream length.

        Returns:
            Message ID string.
        """
        client = self._require_client()
        if client is None:
            self._memory_counter += 1
            generated_id = msg_id or f"{int(time.time() * 1000)}-{self._memory_counter}"
            if stream not in self._memory_streams:
                self._memory_streams[stream] = []
            self._memory_streams[stream].append((generated_id, dict(payload)))
            if len(self._memory_streams[stream]) > max_len:
                self._memory_streams[stream].pop(0)
            return generated_id

        serialized = {k: json.dumps(v) if not isinstance(v, str) else v for k, v in payload.items()}
        result: str = await client.xadd(
            stream,
            serialized,
            id=msg_id or "*",
            maxlen=max_len,
            approximate=True,
        )
        return result

    async def consume(
        self,
        stream: str,
        group: str,
        consumer: str,
        count: int = 10,
        block_ms: int = 1000,
    ) -> list[QueueMessage]:
        """
        Read undelivered messages from a consumer group (or in-memory stream).
        """
        client = self._require_client()
        if client is None:
            items = self._memory_streams.get(stream, [])
            pulled = items[:count]
            # remove pulled items from memory queue
            self._memory_streams[stream] = items[count:]
            return [QueueMessage(stream=stream, msg_id=mid, payload=p) for mid, p in pulled]

        await self.create_group(stream, group)

        try:
            raw = await client.xreadgroup(
                groupname=group,
                consumername=consumer,
                streams={stream: ">"},
                count=count,
                block=block_ms,
            )
        except aioredis.ResponseError as e:
            logger.warning("queue.consume_error", error=str(e))
            return []

        messages: list[QueueMessage] = []
        if raw:
            for _stream, entries in raw:
                for msg_id, fields in entries:
                    payload = {k: self._try_parse_json(v) for k, v in fields.items()}
                    messages.append(QueueMessage(stream=stream, msg_id=msg_id, payload=payload))
        return messages

    async def ack(self, stream: str, group: str, msg_id: str) -> None:
        """Acknowledge a message."""
        client = self._require_client()
        if client is None:
            return
        await client.xack(stream, group, msg_id)

    async def create_group(self, stream: str, group: str) -> None:
        """Create a consumer group if it doesn't already exist."""
        client = self._require_client()
        if client is None:
            return
        try:
            await client.xgroup_create(stream, group, id="$", mkstream=True)
        except aioredis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    async def dead_letter(
        self, stream: str, msg_id: str, payload: dict[str, Any], reason: str
    ) -> None:
        """Move a failed message to the DLQ stream with failure metadata."""
        dlq_payload = {
            **payload,
            "original_stream": stream,
            "original_id": msg_id,
            "failure_reason": reason,
        }
        await self.enqueue(f"{stream}{DLQ_SUFFIX}", dlq_payload)
        logger.warning("queue.dead_lettered", stream=stream, msg_id=msg_id, reason=reason)

    async def health_check(self) -> bool:
        """Verify queue backend connectivity (returns True if healthy or in-memory)."""
        client = self._require_client()
        if client is None:
            return True
        try:
            return await client.ping()
        except Exception:
            return False

    @staticmethod
    def _try_parse_json(value: str) -> Any:
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
