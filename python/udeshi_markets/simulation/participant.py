"""Participants: anything that trades in a simulation (agents, scripted flows, a manual user).

A participant interacts with the market only through the SimulationContext it is handed, which
binds its identity: it can submit and cancel only its own orders, and it observes public market
data plus reports about its own orders. It never learns a counterparty's identity.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

from udeshi_markets._engine import CancelReason, OrderStatus, RejectReason, Side
from udeshi_markets.simulation.events import OrderIntent

if TYPE_CHECKING:
    from udeshi_markets.simulation.kernel import SimulationContext


class CancelStatus(enum.Enum):
    CANCELLED = "cancelled"
    NOT_FOUND = "not_found"  # Never existed, already filled, or already cancelled.
    NOT_OWNER = "not_owner"  # Resting, but owned by someone else; the exchange is not touched.
    UNKNOWN_INSTRUMENT = "unknown_instrument"


@dataclass(frozen=True, slots=True)
class Fill:
    """One execution, from one participant's point of view."""

    trade_id: int
    time: int
    instrument: int
    order_id: int
    side: Side
    price: int
    quantity: int
    is_aggressor: bool  # True if this participant's order removed liquidity.


@dataclass(frozen=True, slots=True)
class OrderReport:
    """The outcome of one of the participant's own submissions."""

    time: int
    intent: OrderIntent
    order_id: int  # 0 if rejected.
    status: OrderStatus
    reject_reason: RejectReason
    cancel_reason: CancelReason
    filled_quantity: int
    resting_quantity: int
    cancelled_quantity: int


@dataclass(frozen=True, slots=True)
class CancelReport:
    """A participant's resting order left the book without trading (or a cancel failed)."""

    time: int
    instrument: int
    order_id: int
    status: CancelStatus
    cancelled_quantity: int
    reason: CancelReason  # USER_REQUESTED for the participant's own cancels.


class Participant:
    """Base class with no-op callbacks. Subclasses override what they need.

    Callbacks run inside the simulation's event dispatch and must not block. They act through
    the context: submissions and cancels are scheduled (arriving after the configured latency),
    never executed re-entrantly.
    """

    def __init__(self, participant_id: int, name: str | None = None) -> None:
        if participant_id <= 0:
            raise ValueError("participant ids must be positive (0 is the anonymous trader)")
        self.participant_id = participant_id
        self.name = name or f"participant-{participant_id}"

    def on_start(self, ctx: SimulationContext) -> None:
        """Called once when the simulation starts."""

    def on_wakeup(self, ctx: SimulationContext, tag: str) -> None:
        """Called when a timer scheduled with ``ctx.wakeup`` fires."""

    def on_order_report(self, ctx: SimulationContext, report: OrderReport) -> None:
        """Called once for every order this participant submitted, after it was processed."""

    def on_fill(self, ctx: SimulationContext, fill: Fill) -> None:
        """Called for every execution involving one of this participant's orders."""

    def on_cancel(self, ctx: SimulationContext, report: CancelReport) -> None:
        """Called when this participant's cancel is processed or its order is removed by
        self-trade prevention."""

    def on_stop(self, ctx: SimulationContext) -> None:
        """Called once when the simulation stops or completes."""
