"""Events and the deterministic event queue.

Events are ordered by (simulated time, insertion sequence). The sequence breaks ties in FIFO
order, so the processing order is fully determined by the order in which events were scheduled.
"""

import heapq
from dataclasses import dataclass

from udeshi_markets._engine import OrderType, SelfTradePrevention, Side, TimeInForce


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """What a participant wants to submit. Prices are in ticks; the owner and timestamp are
    filled in by the simulation when the order reaches the exchange."""

    instrument: int
    side: Side
    quantity: int
    order_type: OrderType = OrderType.LIMIT
    price: int = 0
    time_in_force: TimeInForce = TimeInForce.GOOD_TILL_CANCEL
    self_trade_prevention: SelfTradePrevention = SelfTradePrevention.ALLOW


@dataclass(frozen=True, slots=True)
class OrderArrival:
    """An order reaching the exchange."""

    participant_id: int
    intent: OrderIntent


@dataclass(frozen=True, slots=True)
class CancelArrival:
    """A cancel request reaching the exchange."""

    participant_id: int
    instrument: int
    order_id: int


@dataclass(frozen=True, slots=True)
class Wakeup:
    """A timer a participant scheduled for itself."""

    participant_id: int
    tag: str


Event = OrderArrival | CancelArrival | Wakeup


@dataclass(frozen=True, slots=True)
class ScheduledEvent:
    time: int
    sequence: int
    event: Event


class EventQueue:
    """Min-heap of events keyed by (time, sequence)."""

    __slots__ = ("_heap", "_next_sequence")

    def __init__(self) -> None:
        self._heap: list[tuple[int, int, Event]] = []
        self._next_sequence = 0

    def push(self, time: int, event: Event) -> ScheduledEvent:
        if time < 0:
            raise ValueError("events cannot be scheduled before time zero")
        sequence = self._next_sequence
        self._next_sequence += 1
        # The sequence is unique, so tuples never fall through to comparing events.
        heapq.heappush(self._heap, (time, sequence, event))
        return ScheduledEvent(time, sequence, event)

    def pop(self) -> ScheduledEvent:
        time, sequence, event = heapq.heappop(self._heap)
        return ScheduledEvent(time, sequence, event)

    def peek_time(self) -> int | None:
        return self._heap[0][0] if self._heap else None

    def __len__(self) -> int:
        return len(self._heap)
