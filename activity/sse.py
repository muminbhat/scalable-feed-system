from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, DefaultDict


def format_sse(*, data: Any, event: str = "notification", event_id: int | None = None) -> str:
    """
    Format a Server-Sent Event message.
    """

    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    if event:
        lines.append(f"event: {event}")
    # SSE supports multi-line data; we avoid newlines by using compact JSON.
    lines.append(f"data: {payload}")
    return "\n".join(lines) + "\n\n"


@dataclass(frozen=True)
class Subscriber:
    user_id: int
    queue: "asyncio.Queue[dict[str, Any]]"


class NotificationBroker:
    """
    In-memory pub/sub for SSE notifications.

    Notes:
    - Works for a single-process deployment (assignment scope).
    - In production, swap this with Redis/NATS pubsub so multiple workers can publish.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._subs: DefaultDict[int, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)

    async def subscribe(self, user_id: int, *, max_queue_size: int = 200) -> Subscriber:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_queue_size)
        async with self._lock:
            self._subs[user_id].add(q)
        return Subscriber(user_id=user_id, queue=q)

    async def unsubscribe(self, sub: Subscriber) -> None:
        async with self._lock:
            queues = self._subs.get(sub.user_id)
            if not queues:
                return
            queues.discard(sub.queue)
            if not queues:
                self._subs.pop(sub.user_id, None)

    async def publish(self, user_id: int, message: dict[str, Any]) -> None:
        async with self._lock:
            queues = list(self._subs.get(user_id, ()))
        if not queues:
            return

        for q in queues:
            # Backpressure strategy: if a client is too slow, drop the oldest.
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                # If still full, drop this message (polling can catch up).
                continue

    async def any_subscribers(self, user_ids: list[int]) -> bool:
        async with self._lock:
            return any(bool(self._subs.get(uid)) for uid in user_ids)


broker = NotificationBroker()

