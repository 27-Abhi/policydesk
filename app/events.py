"""In-process async event bus for real-time HITL dispute SSE notifications."""

import asyncio
import threading
from typing import Any


class DisputeEventBus:
    """Thread-safe event bus bridging sync compliance review calls and async SSE streams."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any]]]]] = {}

    async def subscribe(self, thread_id: str) -> asyncio.Queue[dict[str, Any]]:
        """Register an asyncio Queue subscriber for a dispute thread."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=16)
        with self._lock:
            self._subscribers.setdefault(thread_id, []).append((loop, queue))
        return queue

    def unsubscribe(self, thread_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove a closed SSE subscriber queue."""
        with self._lock:
            subs = self._subscribers.get(thread_id, [])
            remaining = [(lp, q) for lp, q in subs if q is not queue]
            if remaining:
                self._subscribers[thread_id] = remaining
            else:
                self._subscribers.pop(thread_id, None)

    def publish(self, thread_id: str, payload: dict[str, Any]) -> None:
        """Push a dispute resolution event to all active SSE subscribers for thread_id."""
        with self._lock:
            subs = list(self._subscribers.get(thread_id, []))

        for loop, queue in subs:
            try:
                if loop.is_closed():
                    continue
                loop.call_soon_threadsafe(self._put_nonblocking, queue, payload)
            except RuntimeError:
                continue

    @staticmethod
    def _put_nonblocking(queue: asyncio.Queue[dict[str, Any]], payload: dict[str, Any]) -> None:
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            pass


_EVENT_BUS = DisputeEventBus()


def get_dispute_event_bus() -> DisputeEventBus:
    """Return the process-wide dispute SSE event bus."""
    return _EVENT_BUS
