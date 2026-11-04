"""The agent framework: what every trading strategy shares.

A ``TradingAgent`` is a kernel participant. Its cash, positions and P&L are kept by the kernel
(``ctx.portfolio``), so an agent cannot misreport them. The agent itself tracks its resting
orders, its order history and its trading statistics, from the reports the kernel sends it.

Agents act at random times: after each action the next one comes after an exponentially
distributed delay with mean ``mean_interval_s / (frequency * activity)``, where ``activity`` is
the market environment's current trading-activity level. Arrivals are therefore a Poisson
process whose rate rises in busy markets. All randomness comes from the agent's own seeded
stream (``ctx.rng``).
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar

from udeshi_markets._engine import OrderStatus, OrderType, Side, TimeInForce
from udeshi_markets.simulation import (
    SECOND,
    CancelReport,
    CancelStatus,
    Fill,
    OrderIntent,
    OrderReport,
    Participant,
    SimulationContext,
)

ACT = "act"  # Tag of an agent's regular, self-rescheduling wakeup.


@dataclass(frozen=True, slots=True)
class OpenOrder:
    order_id: int
    instrument: int
    side: Side
    price: int
    remaining: int
    time: int


@dataclass(slots=True)
class AgentStats:
    orders_submitted: int = 0
    orders_rejected: int = 0
    orders_filled: int = 0  # Orders filled completely.
    cancels_sent: int = 0
    fills: int = 0
    filled_quantity: int = 0
    aggressive_fills: int = 0  # Fills in which this agent took liquidity.
    passive_fills: int = 0  # Fills in which this agent's resting order provided it.


class TradingAgent(Participant):
    strategy: ClassVar[str] = "agent"

    def __init__(
        self,
        participant_id: int,
        instruments: Sequence[int],
        *,
        mean_interval_s: float,
        frequency: float = 1.0,
        max_position: int = 100,
        name: str | None = None,
        history_limit: int = 2_000,
    ) -> None:
        super().__init__(participant_id, name or f"{self.strategy}-{participant_id}")
        if not instruments:
            raise ValueError("an agent needs at least one instrument")
        if not (math.isfinite(mean_interval_s) and mean_interval_s > 0):
            raise ValueError("mean_interval_s must be positive")
        if not (math.isfinite(frequency) and frequency > 0):
            raise ValueError("frequency must be positive")
        if max_position < 1:
            raise ValueError("max_position must be at least 1")
        self.instruments = tuple(instruments)
        self.mean_interval_ns = mean_interval_s * SECOND
        self.frequency = frequency
        self.max_position = max_position
        self.open_orders: dict[int, OpenOrder] = {}
        self.history: deque[OrderReport] = deque(maxlen=history_limit)
        self.stats = AgentStats()

    # ---- Strategy hooks --------------------------------------------------------------------
    def act(self, ctx: SimulationContext, tag: str) -> None:
        """One decision. Called on every wakeup of the agent (regular or otherwise)."""
        raise NotImplementedError

    # ---- Scheduling ----------------------------------------------------------------------------
    def on_start(self, ctx: SimulationContext) -> None:
        self.schedule(ctx)

    def on_wakeup(self, ctx: SimulationContext, tag: str) -> None:
        self.act(ctx, tag)
        if tag == ACT:
            self.schedule(ctx)

    def schedule(self, ctx: SimulationContext, tag: str = ACT) -> None:
        rate = self.frequency * ctx.activity / self.mean_interval_ns  # Actions per nanosecond.
        ctx.wakeup(max(1, round(ctx.rng.exponential(rate))), tag)

    # ---- Orders --------------------------------------------------------------------------------
    def submit(self, ctx: SimulationContext, intent: OrderIntent) -> None:
        ctx.submit(intent)
        self.stats.orders_submitted += 1

    def limit(
        self,
        ctx: SimulationContext,
        instrument: int,
        side: Side,
        quantity: int,
        price: int,
        *,
        immediate: bool = False,
    ) -> None:
        """A limit order (immediate-or-cancel if ``immediate``). Prices are clamped to >= 1."""
        tif = TimeInForce.IMMEDIATE_OR_CANCEL if immediate else TimeInForce.GOOD_TILL_CANCEL
        intent = OrderIntent(instrument, side, quantity, price=max(1, price), time_in_force=tif)
        self.submit(ctx, intent)

    def market(self, ctx: SimulationContext, instrument: int, side: Side, quantity: int) -> None:
        self.submit(ctx, OrderIntent(instrument, side, quantity, order_type=OrderType.MARKET))

    def cancel(self, ctx: SimulationContext, order: OpenOrder) -> None:
        ctx.cancel(order.instrument, order.order_id)
        self.stats.cancels_sent += 1

    def cancel_all(self, ctx: SimulationContext, instrument: int | None = None) -> None:
        for order in list(self.open_orders.values()):
            if instrument is None or order.instrument == instrument:
                self.cancel(ctx, order)

    def room(self, ctx: SimulationContext, instrument: int, side: Side) -> int:
        """Shares that can still be bought (or sold) without the position, counting resting
        orders that might fill, going beyond ``max_position``."""
        position = ctx.portfolio.position(instrument)
        pending = sum(
            order.remaining
            for order in self.open_orders.values()
            if order.instrument == instrument and order.side is side
        )
        if side is Side.BUY:
            return max(0, self.max_position - position - pending)
        return max(0, self.max_position + position - pending)

    # ---- Bookkeeping ---------------------------------------------------------------------------
    def on_order_report(self, ctx: SimulationContext, report: OrderReport) -> None:
        self.history.append(report)
        if report.status is OrderStatus.REJECTED:
            self.stats.orders_rejected += 1
        elif report.status is OrderStatus.FILLED:
            self.stats.orders_filled += 1
        elif report.status is OrderStatus.RESTING:
            intent = report.intent
            self.open_orders[report.order_id] = OpenOrder(
                report.order_id,
                intent.instrument,
                intent.side,
                intent.price,
                report.resting_quantity,
                report.time,
            )

    def on_fill(self, ctx: SimulationContext, fill: Fill) -> None:
        self.stats.fills += 1
        self.stats.filled_quantity += fill.quantity
        if fill.is_aggressor:
            self.stats.aggressive_fills += 1
        else:
            self.stats.passive_fills += 1
        order = self.open_orders.get(fill.order_id)
        if order is not None:
            remaining = order.remaining - fill.quantity
            if remaining > 0:
                self.open_orders[fill.order_id] = OpenOrder(
                    order.order_id, order.instrument, order.side, order.price, remaining, order.time
                )
            else:
                del self.open_orders[fill.order_id]

    def on_cancel(self, ctx: SimulationContext, report: CancelReport) -> None:
        if report.status is CancelStatus.CANCELLED:
            self.open_orders.pop(report.order_id, None)

    def metrics(self) -> dict[str, float]:
        """Strategy-specific measures, for reporting (overridden by strategies that have any)."""
        return {}
