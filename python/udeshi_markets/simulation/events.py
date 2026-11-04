"""Events and the deterministic event queue.

Events are ordered by (simulated time, insertion sequence). The sequence breaks ties in FIFO
order, so the processing order is fully determined by the order in which events were scheduled.
"""

import heapq
from dataclasses import dataclass

from udeshi_markets._engine import OrderType, SelfTradePrevention, Side, TimeInForce

# Ranges of the engine's integer types (engine/include/udeshi/types.hpp).
INT64_MIN, INT64_MAX = -(1 << 63), (1 << 63) - 1
UINT32_MAX = (1 << 32) - 1
UINT64_MAX = (1 << 64) - 1


def require_int(name: str, value: object, low: int, high: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an int, not {type(value).__name__}")
    if not low <= value <= high:
        raise ValueError(f"{name} {value} is outside [{low}, {high}]")


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

    def __post_init__(self) -> None:
        # Values must fit the engine's C++ types, so that a bad intent fails here, in the
        # participant's own call, rather than in the middle of event dispatch. Values that fit
        # but break the exchange's rules (a zero quantity, an off-grid price) are rejected by the
        # exchange, with a reason, like any other order.
        require_int("instrument", self.instrument, 0, UINT32_MAX)
        require_int("quantity", self.quantity, INT64_MIN, INT64_MAX)
        require_int("price", self.price, INT64_MIN, INT64_MAX)
        for name, value, kind in (
            ("side", self.side, Side),
            ("order_type", self.order_type, OrderType),
            ("time_in_force", self.time_in_force, TimeInForce),
            ("self_trade_prevention", self.self_trade_prevention, SelfTradePrevention),
        ):
            if not isinstance(value, kind):
                raise TypeError(f"{name} must be a {kind.__name__}, not {type(value).__name__}")


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


@dataclass(frozen=True, slots=True)
class News:
    """A public announcement about one instrument. ``impact`` is the change it caused in the
    log fundamental value (positive news: ``impact`` > 0)."""

    time: int
    instrument: int
    impact: float
    headline: str

    @property
    def sentiment(self) -> int:
        """+1 for positive news, -1 for negative, 0 for news that moved nothing."""
        return (self.impact > 0) - (self.impact < 0)


@dataclass(frozen=True, slots=True)
class NewsArrival:
    """A news item reaching every participant."""

    news: News


Event = OrderArrival | CancelArrival | Wakeup | NewsArrival


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
        require_int("event time", time, 0, INT64_MAX)  # Never before time zero.
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
