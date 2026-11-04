"""Who traded with whom: the observer's view of the tape.

The recorder's tape (``PublicTrade``) is anonymous on purpose, because agents read it and must
not learn their counterparties. The API is an observer, like the analytics, so it may name the
buyer and seller of every trade, as it already shows the hidden fundamental values. This
listener records them from the orders the kernel processes; no participant can see it.
"""

from __future__ import annotations

from collections import OrderedDict

from udeshi_markets.simulation import OrderEvent, Simulation, SimulationObserver


class Counterparties(SimulationObserver):
    """Buyer and seller ids of each instrument's most recent ``limit`` trades: as many as the
    recorder keeps on its tape, so every trade the API can show has its counterparties."""

    def __init__(self, simulation: Simulation, limit: int) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        self._limit = limit
        self._trades: dict[int, OrderedDict[int, tuple[int, int]]] = {}
        simulation.add_observer(self)  # The kernel calls on_reset when it is reset.

    def on_order(self, event: OrderEvent) -> None:
        for trade in event.result.trades:
            recent = self._trades.setdefault(trade.instrument, OrderedDict())
            recent[trade.id] = (trade.buyer, trade.seller)
            if len(recent) > self._limit:
                recent.popitem(last=False)

    def on_reset(self) -> None:
        self._trades.clear()  # Trade ids restart at 1 in the rebuilt simulation.

    def of(self, instrument: int, trade_id: int) -> tuple[int, int] | None:
        """``(buyer, seller)``, or None for a trade no longer (or never) recorded."""
        recent = self._trades.get(instrument)
        return None if recent is None else recent.get(trade_id)
