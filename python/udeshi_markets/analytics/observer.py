"""Watching a simulation run: the raw observations that analytics are computed from.

A ``MarketObserver`` is a read-only kernel observer, not a participant: it cannot act, but it
sees every order with the quote that prevailed just before it, and it may read every portfolio.
It records:
* every order (quantity submitted, per instrument and per participant);
* every trade, with its aggressor's direction and the midprice just before the aggressive order
  arrived;
* at a fixed sampling interval: the midprice, spread, depth and fundamental value of every
  instrument, and every participant's equity and gross exposure;
* each trade's midprice exactly ``markout_ns`` later (for realized spread and price impact).

Everything is recorded at the time it happens. The post-trade midprice is read when that later
time arrives, never earlier: these are ex-post measurements of what happened, and nothing feeds
them back into the simulation.

Drive it with ``run_observed``, which stops the clock exactly at every sampling time and markout
horizon. A live runner that only calls ``observe`` once per tick gets the same metrics, with
each measurement taken at the first tick at or after its time.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from fractions import Fraction

from udeshi_markets._engine import Side
from udeshi_markets.simulation import (
    SECOND,
    OrderEvent,
    Simulation,
    SimulationObserver,
    SimulationState,
)


@dataclass(frozen=True, slots=True)
class TradeRecord:
    time: int
    instrument: int
    price: int  # Ticks.
    quantity: int
    sign: int  # +1 if the buyer was the aggressor, -1 if the seller was.
    mid_before: Fraction | None  # Ticks; None if the book was one-sided.
    buyer: int
    seller: int


@dataclass(slots=True)
class InstrumentSeries:
    mids: list[float | None]
    spreads: list[int | None]  # Ticks.
    bid_depth: list[int]  # Shares within the top ``depth_levels`` levels.
    ask_depth: list[int]
    fundamentals: list[float | None]  # Currency.


class MarketObserver(SimulationObserver):
    def __init__(
        self,
        simulation: Simulation,
        *,
        interval_ns: int = SECOND,
        markout_ns: int = 5 * SECOND,
        depth_levels: int = 5,
    ) -> None:
        if interval_ns <= 0 or markout_ns <= 0 or depth_levels <= 0:
            raise ValueError("interval, markout horizon and depth levels must be positive")
        self.simulation = simulation
        self.interval_ns = interval_ns
        self.markout_ns = markout_ns
        self.depth_levels = depth_levels
        count = simulation.exchange.instrument_count
        self.times: list[int] = []
        self.series = [InstrumentSeries([], [], [], [], []) for _ in range(count)]
        self.equity: dict[int, list[float]] = {pid: [] for pid in simulation.participants}
        self.exposure: dict[int, list[float]] = {pid: [] for pid in simulation.participants}
        self.trades: list[TradeRecord] = []
        self.mid_after: dict[int, Fraction | None] = {}  # Trade index -> mid at t + horizon.
        self.submitted: list[int] = [0] * count  # Shares submitted per instrument.
        self.submitted_by: dict[int, int] = dict.fromkeys(simulation.participants, 0)
        self._pending: deque[tuple[int, int]] = deque()  # (due time, trade index), in order.
        self._next_sample = 0
        self._ticks = [simulation.exchange.instrument(i).tick_size for i in range(count)]
        simulation.add_observer(self)

    # ---- From the kernel ------------------------------------------------------------------------
    def on_order(self, event: OrderEvent) -> None:
        intent = event.intent
        self.submitted[intent.instrument] += intent.quantity
        self.submitted_by[event.participant_id] = (
            self.submitted_by.get(event.participant_id, 0) + intent.quantity
        )
        mid = None
        if event.bid_before is not None and event.ask_before is not None:
            mid = Fraction(event.bid_before + event.ask_before, 2)
        for trade in event.result.trades:
            self.trades.append(
                TradeRecord(
                    trade.timestamp,
                    trade.instrument,
                    trade.price,
                    trade.quantity,
                    1 if trade.aggressor_side is Side.BUY else -1,
                    mid,
                    trade.buyer,
                    trade.seller,
                )
            )
            self._pending.append((trade.timestamp + self.markout_ns, len(self.trades) - 1))

    # ---- Driving --------------------------------------------------------------------------------
    def next_stop(self) -> int:
        """The next time the observer needs the clock stopped at."""
        if self._pending:
            return min(self._next_sample, self._pending[0][0])
        return self._next_sample

    def observe(self) -> None:
        """Records whatever is due at the simulation's current time."""
        now = self.simulation.now
        while self._pending and self._pending[0][0] <= now:
            _, index = self._pending.popleft()
            self.mid_after[index] = self._mid(self.trades[index].instrument)
        if now >= self._next_sample:
            self._sample(now)
            while self._next_sample <= now:
                self._next_sample += self.interval_ns

    def _mid(self, instrument: int) -> Fraction | None:
        exchange = self.simulation.exchange
        bid, ask = exchange.best_bid(instrument), exchange.best_ask(instrument)
        return None if bid is None or ask is None else Fraction(bid + ask, 2)

    def _sample(self, now: int) -> None:
        simulation = self.simulation
        exchange = simulation.exchange
        environment = simulation.environment
        self.times.append(now)
        marks = [float(mark) for mark in simulation.marks()]
        for i, series in enumerate(self.series):
            bid, ask = exchange.best_bid(i), exchange.best_ask(i)
            series.mids.append(None if bid is None or ask is None else (bid + ask) / 2)
            series.spreads.append(None if bid is None or ask is None else ask - bid)
            book = exchange.book_snapshot(i, self.depth_levels)
            series.bid_depth.append(sum(level.quantity for level in book.bids))
            series.ask_depth.append(sum(level.quantity for level in book.asks))
            series.fundamentals.append(None if environment is None else environment.fundamental(i))
        for pid in self.equity:
            portfolio = simulation.portfolio(pid)
            value = 0.0
            exposure = 0.0
            for i, tick in enumerate(self._ticks):
                quantity = portfolio.position(i)
                if quantity:
                    worth = quantity * marks[i] * tick
                    value += worth
                    exposure += abs(worth)
            self.equity[pid].append(float(portfolio.cash) + value)
            self.exposure[pid].append(exposure)

    def finish(self) -> None:
        """Takes a last sample at the current time if the latest one is older, so equity-based
        metrics end on the final state of the run."""
        self.observe()
        if not self.times or self.times[-1] < self.simulation.now:
            self._sample(self.simulation.now)

    @property
    def periods_per_hour(self) -> float:
        return 3600 * SECOND / self.interval_ns


def run_observed(simulation: Simulation, observer: MarketObserver) -> None:
    """Runs a simulation to the end, stopping the clock exactly at every time the observer
    needs (sampling instants and markout horizons)."""
    if simulation.state is SimulationState.CREATED:
        simulation.start()
    end = simulation.config.duration_ns
    while simulation.state in (SimulationState.RUNNING, SimulationState.PAUSED):
        simulation.run_until(min(observer.next_stop(), end))
        observer.observe()
    observer.finish()
