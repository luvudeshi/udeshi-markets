"""Fan-out of live market data to WebSocket subscribers.

Each subscriber has a bounded queue, and publishing never blocks, so one slow client can
neither stall the simulation nor grow memory without bound. A client is never left with a
silent gap, though. Updates are published one batch per tick, and when a subscriber's queue
cannot take a whole batch, its queued messages are discarded and replaced by a single fresh
``snapshot`` of the current state. The client then rebuilds its view from that snapshot, which
already includes everything it missed. The same happens to every subscriber after a reset.
"""

import asyncio
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

Message = dict[str, Any]
SnapshotFactory = Callable[[frozenset[str] | None], Message]


def encode(message: Message) -> str:
    return json.dumps(message, separators=(",", ":"))


@dataclass(eq=False)
class Subscriber:
    queue: asyncio.Queue[str]
    symbols: frozenset[str] | None  # None: every symbol.
    resyncs: int = 0  # Times this subscriber fell behind and was sent a fresh snapshot.
    closed: bool = field(default=False)

    def wants(self, symbol: str | None) -> bool:
        return symbol is None or self.symbols is None or symbol in self.symbols


class StreamHub:
    def __init__(self, queue_size: int, snapshot: SnapshotFactory) -> None:
        self._queue_size = queue_size
        self._snapshot = snapshot
        self._subscribers: set[Subscriber] = set()

    def subscribe(self, symbols: frozenset[str] | None) -> Subscriber:
        """Registers a subscriber whose queue starts with a snapshot of the current state, so
        every later update follows it in order."""
        subscriber = Subscriber(asyncio.Queue(maxsize=self._queue_size), symbols)
        subscriber.queue.put_nowait(encode(self._snapshot(symbols)))
        self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.discard(subscriber)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, batch: Sequence[tuple[Message, str | None]]) -> None:
        """Queues one tick's updates, each ``(message, symbol)`` (symbol None: for everyone).
        A subscriber whose queue cannot take its share of the batch is resynchronised."""
        if not self._subscribers or not batch:
            return
        encoded: dict[int, str] = {}  # Each message is serialised once, for all subscribers.
        for subscriber in self._subscribers:
            if subscriber.closed:
                continue
            wanted = [index for index, (_, symbol) in enumerate(batch) if subscriber.wants(symbol)]
            if not wanted:
                continue
            if subscriber.queue.qsize() + len(wanted) > self._queue_size:
                self._resync(subscriber)
                continue
            for index in wanted:
                text = encoded.get(index)
                if text is None:
                    text = encoded[index] = encode(batch[index][0])
                subscriber.queue.put_nowait(text)

    def resync_all(self) -> None:
        """Replaces every subscriber's pending updates with a fresh snapshot (after a reset,
        when the previous state no longer applies)."""
        for subscriber in self._subscribers:
            if not subscriber.closed:
                self._resync(subscriber)

    def close(self) -> None:
        """Tells every subscriber the stream has ended."""
        text = encode({"type": "closed"})
        for subscriber in self._subscribers:
            if subscriber.queue.full():
                _clear(subscriber.queue)  # The final message must always fit.
            subscriber.queue.put_nowait(text)
            subscriber.closed = True

    def _resync(self, subscriber: Subscriber) -> None:
        _clear(subscriber.queue)
        subscriber.queue.put_nowait(encode(self._snapshot(subscriber.symbols)))
        subscriber.resyncs += 1


def _clear(queue: asyncio.Queue[str]) -> None:
    while not queue.empty():
        queue.get_nowait()
