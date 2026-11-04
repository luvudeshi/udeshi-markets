"""The API user's own participant in a live simulation."""

from __future__ import annotations

from collections import deque

from udeshi_markets import OrderStatus
from udeshi_markets.simulation import (
    CancelReport,
    CancelStatus,
    Fill,
    OrderReport,
    Participant,
    SimulationContext,
)

MANUAL_TRADER_ID = 1_000_000
"""Participant id of the API user. Simulated agents use ids below this."""


class ManualTrader(Participant):
    """The API user's participant. It trades only through the API (``submit_now``)."""

    def __init__(self) -> None:
        super().__init__(MANUAL_TRADER_ID, "manual")
        self.open_orders: dict[int, int] = {}  # Resting order id -> instrument.
        self.recent_fills: deque[Fill] = deque(maxlen=1_000)
        self.history: deque[OrderReport] = deque(maxlen=1_000)
        self.orders_submitted = 0  # Every processed submission produces exactly one report.
        self.fills = 0
        self._capture: list[Fill] | None = None

    def begin_capture(self) -> None:
        self._capture = []

    def end_capture(self) -> list[Fill]:
        captured, self._capture = self._capture or [], None
        return captured

    def on_fill(self, ctx: SimulationContext, fill: Fill) -> None:
        self.fills += 1
        self.recent_fills.append(fill)
        if self._capture is not None:
            self._capture.append(fill)
        order = ctx.own_order(fill.instrument, fill.order_id)
        if order is None:  # Fully filled.
            self.open_orders.pop(fill.order_id, None)

    def on_order_report(self, ctx: SimulationContext, report: OrderReport) -> None:
        self.orders_submitted += 1
        self.history.append(report)
        if report.status is OrderStatus.RESTING:
            self.open_orders[report.order_id] = report.intent.instrument

    def on_cancel(self, ctx: SimulationContext, report: CancelReport) -> None:
        if report.status is CancelStatus.CANCELLED:
            self.open_orders.pop(report.order_id, None)
