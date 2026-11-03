"""Fan-out of live market data to WebSocket subscribers.

Each subscriber has a bounded queue. Publishing never blocks: when a slow client's queue is
full, its oldest message is dropped (and counted), so one slow client can neither stall the
simulation nor grow memory without bound.
"""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(eq=False)
class Subscriber:
    queue: asyncio.Queue[str]
    symbols: frozenset[str] | None  # None: every symbol.
    dropped: int = 0
    closed: bool = field(default=False)

    def wants(self, symbol: str | None) -> bool:
        return symbol is None or self.symbols is None or symbol in self.symbols


class StreamHub:
    def __init__(self, queue_size: int) -> None:
        self._queue_size = queue_size
        self._subscribers: set[Subscriber] = set()

    def subscribe(self, symbols: frozenset[str] | None) -> Subscriber:
        subscriber = Subscriber(asyncio.Queue(maxsize=self._queue_size), symbols)
        self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.discard(subscriber)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, message: dict[str, Any], symbol: str | None = None) -> None:
        """Queues ``message`` for every subscriber interested in ``symbol`` (None: everyone)."""
        if not self._subscribers:
            return
        text = json.dumps(message, separators=(",", ":"))  # Serialized once for all clients.
        for subscriber in self._subscribers:
            if not subscriber.wants(symbol):
                continue
            if subscriber.queue.full():
                subscriber.queue.get_nowait()
                subscriber.dropped += 1
            subscriber.queue.put_nowait(text)

    def close(self) -> None:
        """Tells every subscriber the stream has ended."""
        self.publish({"type": "closed"})
        for subscriber in self._subscribers:
            subscriber.closed = True
