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

Besides the record, the observer keeps running aggregates of everything the metrics need
(running.py), updated as each observation arrives. Computing the metrics then takes time that
does not grow with the length of the run, which matters to a live market asked for its
analytics again and again.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from fractions import Fraction

from udeshi_markets._engine import Side
from udeshi_markets.analytics.running import (
    FloatSum,
    RunningDrawdown,
    RunningMean,
    RunningSharpe,
    RunningVariance,
)
from udeshi_markets.simulation import (
    SECOND,
    OrderEvent,
    Simulation,
    SimulationObserver,
    SimulationState,
    deterministic_log,
)

BPS = 10_000.0


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


@dataclass(slots=True)
class InstrumentAggregates:
    """Running aggregates of one instrument's samples and trades: the batch computations of
    the metrics (metrics.py) carried along one observation at a time, in the same order."""

    two_sided: int = 0  # Samples with both sides quoted.
    spread: RunningMean = field(default_factory=RunningMean)  # Ticks, two-sided samples.
    spread_bps: RunningMean = field(default_factory=RunningMean)
    depth: RunningMean = field(default_factory=RunningMean)  # Both sides together.
    imbalance: RunningMean = field(default_factory=RunningMean)
    pricing_error: RunningMean = field(default_factory=RunningMean)  # |ln(mid / fundamental)|.
    log_returns: RunningVariance = field(default_factory=RunningVariance)  # Of the midprice.
    last_log_mid: float | None = None
    trades: int = 0
    volume: int = 0
    buyer_initiated: int = 0
    without_quote: int = 0
    measured: int = 0  # Trades with both quotes and a markout: the spread decomposition's.
    weights: FloatSum = field(default_factory=FloatSum)  # Their quantities.
    effective: FloatSum = field(default_factory=FloatSum)  # Sums of bps x quantity.
    realized: FloatSum = field(default_factory=FloatSum)
    impact: FloatSum = field(default_factory=FloatSum)


@dataclass(slots=True)
class ParticipantAggregates:
    sharpe: RunningSharpe = field(default_factory=RunningSharpe)  # Of equity returns.
    drawdown: RunningDrawdown = field(default_factory=RunningDrawdown)
    exposure: RunningMean = field(default_factory=RunningMean)
    max_exposure: float | None = None


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
        self._initialize()
        simulation.add_observer(self)

    def _initialize(self) -> None:
        simulation = self.simulation
        count = simulation.exchange.instrument_count
        self.times: list[int] = []
        self.series = [InstrumentSeries([], [], [], [], []) for _ in range(count)]
        self.equity: dict[int, list[float]] = {pid: [] for pid in simulation.participants}
        self.exposure: dict[int, list[float]] = {pid: [] for pid in simulation.participants}
        self.trades: list[TradeRecord] = []
        self.mid_after: dict[int, Fraction | None] = {}  # Trade index -> mid at t + horizon.
        self.submitted: list[int] = [0] * count  # Shares in accepted orders, per instrument.
        self.submitted_by: dict[int, int] = dict.fromkeys(simulation.participants, 0)
        self._pending: deque[tuple[int, int]] = deque()  # (due time, trade index), in order.
        self._next_sample = simulation.now  # Observation starts now, even mid-run.
        self._ticks = [simulation.exchange.instrument(i).tick_size for i in range(count)]
        self.aggregates = [InstrumentAggregates() for _ in range(count)]
        self.participant_aggregates = {pid: ParticipantAggregates() for pid in self.equity}

    # ---- From the kernel ------------------------------------------------------------------------
    def on_reset(self) -> None:
        self._initialize()

    def on_order(self, event: OrderEvent) -> None:
        intent = event.intent
        if not event.result.accepted:
            return  # A rejected order never reached the book: nothing was submitted to it.
        self.submitted[intent.instrument] += intent.quantity
        self.submitted_by[event.participant_id] = (
            self.submitted_by.get(event.participant_id, 0) + intent.quantity
        )
        mid = None
        if event.bid_before is not None and event.ask_before is not None:
            mid = Fraction(event.bid_before + event.ask_before, 2)
        for trade in event.result.trades:
            record = TradeRecord(
                trade.timestamp,
                trade.instrument,
                trade.price,
                trade.quantity,
                1 if trade.aggressor_side is Side.BUY else -1,
                mid,
                trade.buyer,
                trade.seller,
            )
            self.trades.append(record)
            self._pending.append((trade.timestamp + self.markout_ns, len(self.trades) - 1))
            aggregates = self.aggregates[record.instrument]
            aggregates.trades += 1
            aggregates.volume += record.quantity
            aggregates.buyer_initiated += record.sign > 0
            aggregates.without_quote += mid is None

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
            trade = self.trades[index]
            after = self._mid(trade.instrument)
            self.mid_after[index] = after
            if trade.mid_before is not None and after is not None:
                self._measure(trade, trade.mid_before, after)
        if now >= self._next_sample:
            self._sample(now)
            while self._next_sample <= now:
                self._next_sample += self.interval_ns

    def _measure(self, trade: TradeRecord, before: Fraction, after: Fraction) -> None:
        """Adds a trade's effective spread, realized spread and price impact (bps) to its
        instrument's volume-weighted sums. Markouts fall due in trade order, so the sums are
        taken in the order the batch definition takes them."""
        aggregates = self.aggregates[trade.instrument]
        m, m_after, p, s = float(before), float(after), trade.price, trade.sign
        weight = float(trade.quantity)
        aggregates.measured += 1
        aggregates.weights.add(weight)
        aggregates.effective.add(2 * s * (p - m) / m * BPS * weight)
        aggregates.realized.add(2 * s * (p - m_after) / m * BPS * weight)
        aggregates.impact.add(2 * s * (m_after - m) / m * BPS * weight)

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
            mid = None if bid is None or ask is None else (bid + ask) / 2
            spread = None if bid is None or ask is None else ask - bid
            book = exchange.book_snapshot(i, self.depth_levels)
            bid_depth = sum(level.quantity for level in book.bids)
            ask_depth = sum(level.quantity for level in book.asks)
            fundamental = None if environment is None else environment.fundamental(i)
            series.mids.append(mid)
            series.spreads.append(spread)
            series.bid_depth.append(bid_depth)
            series.ask_depth.append(ask_depth)
            series.fundamentals.append(fundamental)
            self._aggregate_sample(i, mid, spread, bid_depth, ask_depth, fundamental)
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
            equity = float(portfolio.cash) + value
            self.equity[pid].append(equity)
            self.exposure[pid].append(exposure)
            aggregates = self.participant_aggregates[pid]
            aggregates.sharpe.add(equity)
            aggregates.drawdown.add(equity)
            aggregates.exposure.add(exposure)
            if aggregates.max_exposure is None or exposure > aggregates.max_exposure:
                aggregates.max_exposure = exposure

    def _aggregate_sample(
        self,
        instrument: int,
        mid: float | None,
        spread: int | None,
        bid_depth: int,
        ask_depth: int,
        fundamental: float | None,
    ) -> None:
        aggregates = self.aggregates[instrument]
        if mid is not None and spread is not None:
            aggregates.two_sided += 1
            aggregates.spread.add(spread)
            aggregates.spread_bps.add(spread / mid * BPS)
        aggregates.depth.add(float(bid_depth + ask_depth))
        if bid_depth + ask_depth > 0:
            aggregates.imbalance.add((bid_depth - ask_depth) / (bid_depth + ask_depth))
        if mid is not None and fundamental is not None and fundamental > 0:
            tick = self._ticks[instrument]
            error = abs(deterministic_log(mid * tick) - deterministic_log(fundamental))
            aggregates.pricing_error.add(error)
        # A log return needs two consecutive positive midprices: a missing one breaks the series.
        log_mid = None if mid is None or mid <= 0 else deterministic_log(mid)
        if log_mid is not None and aggregates.last_log_mid is not None:
            aggregates.log_returns.add(log_mid - aggregates.last_log_mid)
        aggregates.last_log_mid = log_mid

    @property
    def samples(self) -> int:
        return len(self.times)

    def sharpe_ratio(self, participant_id: int) -> float | None:
        """Per hour, of the participant's equity returns between samples (None if it is not
        observed, or the ratio is undefined)."""
        aggregates = self.participant_aggregates.get(participant_id)
        return None if aggregates is None else aggregates.sharpe.ratio(self.periods_per_hour)

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
        simulation.run_until(max(simulation.now, min(observer.next_stop(), end)))
        observer.observe()
    observer.finish()
