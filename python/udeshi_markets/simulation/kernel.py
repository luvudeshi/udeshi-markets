"""The discrete-event simulation kernel.

A Simulation owns one C++ Exchange, an event queue ordered by (simulated time, sequence), the
participants, and a market recorder. Processing an event advances simulated time to the event's
timestamp; nothing reads the wall clock, so a run is a pure function of its configuration, its
participants and any external inputs (such as manual orders submitted through the API, which
are folded into the run's digest like every other event).

Each processed event and its outcome are hashed into a running BLAKE2b digest, so two runs can
be compared for exact equality without storing their event logs.
"""

from __future__ import annotations

import contextlib
import enum
import hashlib
import math
import struct
from collections import deque
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from types import MappingProxyType

from udeshi_markets._engine import (
    BookSnapshot,
    CancelReason,
    Exchange,
    MarketStatistics,
    Order,
    OrderRequest,
    Side,
    SubmitResult,
)
from udeshi_markets.simulation.config import SimulationConfig
from udeshi_markets.simulation.environment import MarketEnvironment
from udeshi_markets.simulation.events import (
    INT64_MAX,
    UINT32_MAX,
    UINT64_MAX,
    CancelArrival,
    EventQueue,
    News,
    NewsArrival,
    OrderArrival,
    OrderIntent,
    ScheduledEvent,
    Wakeup,
    require_int,
)
from udeshi_markets.simulation.participant import (
    CancelReport,
    CancelStatus,
    Fill,
    OrderReport,
    Participant,
)
from udeshi_markets.simulation.portfolio import Portfolio, PortfolioView, exact_tick
from udeshi_markets.simulation.recorder import Bar, MarketRecorder, PublicTrade
from udeshi_markets.simulation.rng import DeterministicRng, deterministic_exp


class SimulationState(enum.Enum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"  # A participant callback (or the engine) raised; the run cannot continue.


_ACTIVE = frozenset({SimulationState.RUNNING, SimulationState.PAUSED})


class SimulationStateError(RuntimeError):
    """The requested operation is not allowed in the simulation's current state."""


ParticipantFactory = Callable[[], Iterable[Participant]]
"""Builds fresh participants. Called at construction and on every reset."""


@dataclass(frozen=True, slots=True)
class ChangeSet:
    """What changed since the previous ``drain_changes`` call (for live publishers)."""

    time: int
    state: SimulationState
    instruments: frozenset[int]  # Instruments whose order book changed.
    trades: tuple[PublicTrade, ...]
    bars: tuple[Bar, ...]
    news: tuple[News, ...] = ()


@dataclass(frozen=True, slots=True)
class OrderEvent:
    """What an observer learns about one processed order: who sent it, what happened, and the
    best bid and ask just before it reached the exchange (the prevailing quote its trades are
    measured against)."""

    time: int
    participant_id: int
    intent: OrderIntent
    result: SubmitResult
    bid_before: int | None
    ask_before: int | None


class SimulationObserver:
    """A read-only listener for analytics (not a participant: it can see every order, but it
    cannot act). Called synchronously, in processing order, after the order is booked."""

    def on_order(self, event: OrderEvent) -> None:
        """Called once for every order the exchange processes."""

    def on_reset(self) -> None:
        """Called after the simulation was reset (rebuilt): whatever the observer recorded
        belongs to a run that no longer exists."""


@dataclass(frozen=True, slots=True)
class SimulationSummary:
    state: SimulationState
    time: int
    events_processed: int
    orders_submitted: int
    orders_rejected: int
    cancels_requested: int
    trades: int
    volume: int
    digest: str


# Digest record kinds.
_ORDER, _CANCEL, _WAKEUP, _END, _NEWS = 1, 2, 3, 4, 5
# Cancel outcomes as digested. "Not the owner" is recorded but never reported: the participant
# is told NOT_FOUND, so cancels cannot be used to discover other participants' orders.
_CANCELLED, _NOT_FOUND, _NOT_OWNER, _UNKNOWN_INSTRUMENT = 0, 1, 2, 3
_END_STATE_CODE = {
    SimulationState.STOPPED: 1,
    SimulationState.COMPLETED: 2,
    SimulationState.FAILED: 3,
}


def _check_cancel_target(instrument: int, order_id: int) -> None:
    """Rejects ids that cannot be represented by the engine's types, before any state changes.
    (An id that fits but names no order is a normal outcome: NOT_FOUND or UNKNOWN_INSTRUMENT.)"""
    require_int("instrument", instrument, 0, UINT32_MAX)
    require_int("order_id", order_id, 0, UINT64_MAX)


class SimulationContext:
    """A participant's handle on the simulation: its identity is bound, so it can act only for
    itself, and it sees only public market data and reports about its own orders."""

    __slots__ = ("_participant_id", "_portfolio", "_rng", "_simulation")

    def __init__(
        self,
        simulation: Simulation,
        participant_id: int,
        rng: DeterministicRng,
        portfolio: PortfolioView,
    ) -> None:
        self._simulation = simulation
        self._participant_id = participant_id
        self._rng = rng
        self._portfolio = portfolio

    @property
    def participant_id(self) -> int:
        return self._participant_id

    @property
    def now(self) -> int:
        return self._simulation.now

    @property
    def end_time(self) -> int:
        return self._simulation.config.duration_ns

    @property
    def rng(self) -> DeterministicRng:
        """This participant's own deterministic random stream."""
        return self._rng

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._simulation.config.symbols

    def instrument_id(self, symbol: str) -> int:
        return self._simulation.exchange.instrument_id(symbol)

    def tick_size(self, instrument: int) -> float:
        return self._simulation.exchange.instrument(instrument).tick_size

    def reference_price(self, instrument: int) -> int:
        """The instrument's starting reference price, in ticks."""
        return self._simulation.config.instruments[instrument].reference_price_ticks

    @property
    def portfolio(self) -> PortfolioView:
        """This participant's cash, positions and P&L (read-only)."""
        return self._portfolio

    @property
    def activity(self) -> float:
        """The market's current trading-activity level (1.0 normally; higher when busy).
        Agents scale how often they act by it."""
        environment = self._simulation.environment
        return 1.0 if environment is None else environment.activity

    def mark(self, instrument: int) -> Fraction:
        """The price positions are valued at, in ticks: the midprice when both sides are
        quoted, else the last trade, else the reference price."""
        return self._simulation.mark(instrument)

    def recent_news(self, limit: int = 20) -> list[News]:
        """The most recently published news items, oldest first."""
        return self._simulation.recent_news(limit)

    def fundamental_estimate(self, instrument: int) -> float:
        """A noisy private signal of the instrument's current fundamental value, in ticks.
        Only participants granted a signal (``signal_noise`` not None when the run was built)
        can ask, and only about the present. See ``Simulation.signal_estimate``."""
        return self._simulation.signal_estimate(self._participant_id, instrument)

    # ---- Actions (scheduled, never executed re-entrantly) ------------------------------------
    def submit(self, intent: OrderIntent) -> None:
        self._simulation.schedule_order(self._participant_id, intent)

    def cancel(self, instrument: int, order_id: int) -> None:
        self._simulation.schedule_cancel(self._participant_id, instrument, order_id)

    def wakeup(self, delay_ns: int, tag: str = "") -> None:
        self._simulation.schedule_wakeup(self._participant_id, delay_ns, tag)

    # ---- Public market data --------------------------------------------------------------------
    def best_bid(self, instrument: int) -> int | None:
        return self._simulation.exchange.best_bid(instrument)

    def best_ask(self, instrument: int) -> int | None:
        return self._simulation.exchange.best_ask(instrument)

    def midprice(self, instrument: int) -> float | None:
        return self._simulation.exchange.midprice(instrument)

    def spread(self, instrument: int) -> int | None:
        return self._simulation.exchange.spread(instrument)

    def book(self, instrument: int, depth: int = 10) -> BookSnapshot:
        return self._simulation.exchange.book_snapshot(instrument, depth)

    def statistics(self, instrument: int) -> MarketStatistics:
        return self._simulation.exchange.statistics(instrument)

    def recent_trades(self, instrument: int, limit: int = 50) -> list[PublicTrade]:
        return self._simulation.recorder.trades(instrument, limit)

    def own_order(self, instrument: int, order_id: int) -> Order | None:
        """A snapshot of one of this participant's resting orders (None for anyone else's)."""
        order = self._simulation.exchange.order(instrument, order_id)
        return order if order is not None and order.owner == self._participant_id else None


class EnvironmentContext(SimulationContext):
    """The market environment's context: a participant's, plus publishing news."""

    __slots__ = ()

    def publish_news(self, instrument: int, impact: float, headline: str) -> None:
        # Checked here, so a bad item cannot fail later in the middle of dispatch.
        require_int("instrument", instrument, 0, self._simulation.exchange.instrument_count - 1)
        if not (isinstance(impact, float) and math.isfinite(impact)):
            raise ValueError("impact must be a finite float")
        if not isinstance(headline, str):
            raise TypeError("headline must be a str")
        headline.encode()
        self._simulation.publish_news(News(self.now, instrument, impact, headline))


class Simulation:
    """Deterministic discrete-event simulation of one exchange and its participants.

    Lifecycle: CREATED --start--> RUNNING <--pause/resume--> PAUSED; RUNNING/PAUSED --stop-->
    STOPPED; reaching the configured duration --> COMPLETED; reset --> CREATED (any state).
    Not thread-safe: drive each instance from one thread (or one asyncio event loop).
    """

    def __init__(
        self,
        config: SimulationConfig,
        participants: ParticipantFactory | None = None,
        *,
        environment: Callable[[], MarketEnvironment] | None = None,
        track_changes: bool = False,
    ) -> None:
        """``environment`` builds the market environment (fresh on every reset). Without one,
        fundamental values do not exist and nothing exogenous happens."""
        self._config = config
        self._participant_factory: ParticipantFactory = participants or tuple
        self._environment_factory = environment
        self._track_changes = track_changes
        self._observers: list[SimulationObserver] = []
        self._build()

    # ---- Construction and lifecycle ------------------------------------------------------------
    def _build(self) -> None:
        """Builds a fresh run. Participants are built and checked first, before any state is
        replaced, so a factory that fails leaves the previous run intact."""
        config = self._config
        environment = None if self._environment_factory is None else self._environment_factory()
        if environment is not None and not isinstance(environment, MarketEnvironment):
            raise TypeError("environment must build a MarketEnvironment")
        previous = {id(p) for p in getattr(self, "_participants", {}).values()}
        participants: dict[int, Participant] = {}
        for participant in (
            *self._participant_factory(),
            *(() if environment is None else (environment,)),
        ):
            if participant.participant_id in participants:
                raise ValueError(f"duplicate participant id {participant.participant_id}")
            if id(participant) in previous:
                # A participant carries state from its run (positions it believes it has,
                # timers, a volatility regime): reusing one would leak it into the new run.
                raise ValueError("the factories must build new participants for every run")
            participants[participant.participant_id] = participant
        signal_noise: dict[int, float | None] = {}
        for pid, participant in participants.items():
            noise = participant.signal_noise
            if noise is not None and not (
                isinstance(noise, int | float) and math.isfinite(noise) and noise >= 0
            ):
                raise ValueError(f"participant {pid}: signal_noise must be None or finite >= 0")
            signal_noise[pid] = None if noise is None else float(noise)

        self._exchange = Exchange(config.recent_trade_capacity)
        for instrument in config.instruments:
            self._exchange.add_instrument(
                symbol=instrument.symbol,
                tick_size=instrument.tick_size,
                reference_price=instrument.reference_price_ticks,
            )
        self._recorder = MarketRecorder(
            [instrument.reference_price_ticks for instrument in config.instruments],
            bar_width_ns=config.bar_interval_ns,
            trade_history_limit=config.trade_history_limit,
            report_closed_bars=self._track_changes,
        )
        self._queue = EventQueue()
        self._now = 0
        self._state = SimulationState.CREATED
        self._callback_depth = 0  # > 0 while a participant callback is running.
        self._failure: BaseException | None = None
        self._digest = hashlib.blake2b(digest_size=16)
        self._events_processed = 0
        self._orders_submitted = 0
        self._orders_rejected = 0
        self._cancels_requested = 0
        self._dirty: set[int] = set()
        self._pending_trades: list[PublicTrade] = []
        self._pending_bars: list[Bar] = []
        self._participants = dict(sorted(participants.items()))
        self._environment = environment
        # Granted once, at build time: a participant cannot grant itself a signal later.
        self._signal_noise = signal_noise
        # Signal noise comes from streams the kernel keeps to itself, so a participant cannot
        # predict (and so remove) it by copying its own generator.
        self._signal_rngs = {
            pid: DeterministicRng.derive(config.seed, "signal", pid)
            for pid, noise in signal_noise.items()
            if noise is not None
        }
        self._signal_cache: dict[tuple[int, int], tuple[int, float]] = {}
        ticks = [exact_tick(instrument.tick_size) for instrument in config.instruments]
        initial_cash = Fraction(Decimal(repr(config.initial_cash)))
        self._portfolios = {pid: Portfolio(initial_cash, ticks) for pid in self._participants}
        self._contexts: dict[int, SimulationContext] = {}
        for pid, participant in self._participants.items():
            context_type = EnvironmentContext if participant is environment else SimulationContext
            self._contexts[pid] = context_type(
                self,
                pid,
                DeterministicRng.derive(config.seed, "participant", pid),
                PortfolioView(self._portfolios[pid]),
            )
        self._news: deque[News] = deque(maxlen=1_000)
        self._pending_news: list[News] = []
        for observer in getattr(self, "_observers", ()):
            observer.on_reset()

    def signal_estimate(self, participant_id: int, instrument: int) -> float:
        """A participant's private signal of an instrument's current fundamental value, in
        ticks: V * exp(noise * Z). Z comes from a stream only the kernel holds, and is drawn once
        per participant, instrument and instant: asking again at the same time returns the same
        estimate, so noise cannot be averaged away within an instant."""
        noise = self._signal_noise.get(participant_id)
        if noise is None:
            raise PermissionError(f"participant {participant_id} has no fundamental signal")
        if self._environment is None:
            raise SimulationStateError("this simulation has no market environment")
        if not 0 <= instrument < self._exchange.instrument_count:
            raise KeyError(f"unknown instrument {instrument}")
        key = (participant_id, instrument)
        cached = self._signal_cache.get(key)
        if cached is not None and cached[0] == self._now:
            return cached[1]
        tick = self._exchange.instrument(instrument).tick_size
        value = self._environment.fundamental(instrument) / tick
        if noise > 0:
            value *= deterministic_exp(noise * self._signal_rngs[participant_id].normal())
        self._signal_cache[key] = (self._now, value)
        return value

    def start(self) -> None:
        self._require_outside_callbacks("start")
        self._require({SimulationState.CREATED}, "start")
        self._state = SimulationState.RUNNING
        with self._failing_on_error():
            # The environment starts first, so fundamental values exist before anyone asks.
            order = sorted(self._participants, key=lambda pid: (pid != _environment_id(self), pid))
            for pid in order:
                self._callback(_bind_start(self._participants[pid], self._contexts[pid]))

    def pause(self) -> None:
        self._require_outside_callbacks("pause")
        self._require({SimulationState.RUNNING}, "pause")
        self._state = SimulationState.PAUSED

    def resume(self) -> None:
        self._require_outside_callbacks("resume")
        self._require({SimulationState.PAUSED}, "resume")
        self._state = SimulationState.RUNNING

    def stop(self) -> None:
        """Ends the run at the current time. Everything that happened at that instant is kept,
        so the last bar covers up to and including it."""
        self._require_outside_callbacks("stop")
        self._require(_ACTIVE, "stop")
        with self._failing_on_error():
            self._finish(SimulationState.STOPPED, self._now + 1)

    def reset(self) -> None:
        """Discards all state and rebuilds the simulation (with fresh participants). Allowed in
        any state, including FAILED, but not from inside a participant callback."""
        self._require_outside_callbacks("reset")
        self._build()

    @property
    def failure(self) -> BaseException | None:
        """The exception that moved the simulation to FAILED, if any."""
        return self._failure

    # ---- Advancing simulated time --------------------------------------------------------------
    def step(self, max_events: int = 1) -> int:
        """Processes up to ``max_events`` events (in RUNNING or PAUSED state). Completes the run
        if no event remains before the end time. Returns the number of events processed."""
        if max_events < 1:
            raise ValueError("max_events must be at least 1")
        self._require_outside_callbacks("step")
        self._require(_ACTIVE, "step")
        end = self._config.duration_ns
        processed = 0
        with self._failing_on_error():
            while processed < max_events:
                next_time = self._queue.peek_time()
                if next_time is None or next_time >= end:  # The run covers [0, end).
                    self._complete()
                    break
                self._dispatch(self._queue.pop())
                processed += 1
            self._after_progress()
        return processed

    def run_until(self, time_ns: int, max_events: int | None = None) -> int:
        """Processes every event at or before ``time_ns`` (capped at the end time), then advances
        the clock to that time. With ``max_events``, stops early (leaving the clock at the last
        processed event) once that many events have been processed."""
        self._require_outside_callbacks("run")
        self._require(_ACTIVE, "run_until")
        if time_ns < self._now:
            raise ValueError("cannot run backwards in simulated time")
        if max_events is not None and max_events < 1:
            raise ValueError("max_events must be at least 1")
        end = self._config.duration_ns
        target = min(time_ns, end)
        processed = 0
        with self._failing_on_error():
            while max_events is None or processed < max_events:
                next_time = self._queue.peek_time()
                # The run covers [0, end): an event at exactly the end time is never processed.
                if next_time is None or next_time > target or next_time >= end:
                    self._now = target
                    if target == end:
                        self._complete()
                    break
                self._dispatch(self._queue.pop())
                processed += 1
            self._after_progress()
        return processed

    def run(self) -> SimulationSummary:
        """Runs (starting if necessary) until the configured duration; headless."""
        if self._state is SimulationState.CREATED:
            self.start()
        self._require(_ACTIVE, "run")
        self.run_until(self._config.duration_ns)
        return self.summary()

    # ---- Scheduling (participants and the API) -------------------------------------------------
    def schedule_order(self, participant_id: int, intent: OrderIntent) -> None:
        self._require_participant(participant_id)
        self._require(_ACTIVE, "schedule an order")
        if not isinstance(intent, OrderIntent):
            raise TypeError(f"expected an OrderIntent, not {type(intent).__name__}")
        self._queue.push(
            self._now + self._config.order_latency_ns, OrderArrival(participant_id, intent)
        )

    def schedule_cancel(self, participant_id: int, instrument: int, order_id: int) -> None:
        self._require_participant(participant_id)
        self._require(_ACTIVE, "schedule a cancel")
        _check_cancel_target(instrument, order_id)
        self._queue.push(
            self._now + self._config.order_latency_ns,
            CancelArrival(participant_id, instrument, order_id),
        )

    def schedule_wakeup(self, participant_id: int, delay_ns: int, tag: str = "") -> None:
        # Checked here, in the participant's own call, so a bad value cannot fail later in the
        # middle of dispatch (a float delay would make the clock a float).
        require_int("wakeup delay", delay_ns, 0, INT64_MAX)
        if not isinstance(tag, str):
            raise TypeError(f"wakeup tag must be a str, not {type(tag).__name__}")
        tag.encode()  # Raises UnicodeEncodeError now rather than at dispatch.
        self._require_participant(participant_id)
        self._require(_ACTIVE, "schedule a wakeup")
        self._queue.push(self._now + delay_ns, Wakeup(participant_id, tag))

    def submit_now(self, participant_id: int, intent: OrderIntent) -> OrderReport:
        """Processes an order immediately at the current simulated time (manual trading). Not
        callable from inside a participant callback; participants use ``ctx.submit``."""
        self._require_external_action(participant_id, "submit an order")
        if not isinstance(intent, OrderIntent):
            raise TypeError(f"expected an OrderIntent, not {type(intent).__name__}")
        with self._failing_on_error():
            report = self._execute_order(participant_id, intent)
            self._after_progress()
        return report

    def cancel_now(self, participant_id: int, instrument: int, order_id: int) -> CancelReport:
        """Processes a cancel immediately at the current simulated time (manual trading)."""
        self._require_external_action(participant_id, "cancel an order")
        _check_cancel_target(instrument, order_id)
        with self._failing_on_error():
            report = self._execute_cancel(participant_id, instrument, order_id)
            self._after_progress()
        return report

    # ---- Observation ---------------------------------------------------------------------------
    @property
    def config(self) -> SimulationConfig:
        return self._config

    @property
    def state(self) -> SimulationState:
        return self._state

    @property
    def now(self) -> int:
        return self._now

    @property
    def exchange(self) -> Exchange:
        """The underlying exchange, for read-only queries. Submitting or cancelling through it
        directly would bypass the kernel's bookkeeping; use the scheduling methods instead."""
        return self._exchange

    @property
    def recorder(self) -> MarketRecorder:
        return self._recorder

    @property
    def participants(self) -> Mapping[int, Participant]:
        return MappingProxyType(self._participants)

    @property
    def environment(self) -> MarketEnvironment | None:
        return self._environment

    def add_observer(self, observer: SimulationObserver) -> None:
        """Registers a read-only observer of every processed order. Observers are kept across
        resets, and told about each one (``on_reset``)."""
        self._observers.append(observer)

    def remove_observer(self, observer: SimulationObserver) -> None:
        self._observers.remove(observer)

    def portfolio(self, participant_id: int) -> PortfolioView:
        """A participant's portfolio, for observers such as the API (read-only)."""
        return PortfolioView(self._portfolios[participant_id])

    def mark(self, instrument: int) -> Fraction:
        """Mark price in ticks: the midprice if both sides are quoted, else the last trade,
        else the reference price."""
        bid = self._exchange.best_bid(instrument)
        ask = self._exchange.best_ask(instrument)
        if bid is not None and ask is not None:
            return Fraction(bid + ask, 2)
        last = self._exchange.statistics(instrument).last_price
        if last is not None:
            return Fraction(last)
        return Fraction(self._config.instruments[instrument].reference_price_ticks)

    def marks(self) -> list[Fraction]:
        return [self.mark(i) for i in range(self._exchange.instrument_count)]

    def recent_news(self, limit: int = 20) -> list[News]:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        return list(self._news)[max(0, len(self._news) - limit) :]

    @property
    def pending_events(self) -> int:
        return len(self._queue)

    @property
    def digest(self) -> str:
        """Hex digest of every event processed so far and its outcome."""
        return self._digest.hexdigest()

    def summary(self) -> SimulationSummary:
        instruments = range(self._exchange.instrument_count)
        return SimulationSummary(
            state=self._state,
            time=self._now,
            events_processed=self._events_processed,
            orders_submitted=self._orders_submitted,
            orders_rejected=self._orders_rejected,
            cancels_requested=self._cancels_requested,
            trades=sum(self._recorder.trades_recorded(i) for i in instruments),
            volume=sum(self._exchange.statistics(i).volume for i in instruments),
            digest=self.digest,
        )

    def drain_changes(self) -> ChangeSet:
        """Changes since the previous call. Requires ``track_changes=True``."""
        if not self._track_changes:
            raise SimulationStateError("change tracking is disabled for this simulation")
        changes = ChangeSet(
            time=self._now,
            state=self._state,
            instruments=frozenset(self._dirty),
            trades=tuple(self._pending_trades),
            bars=tuple(self._pending_bars),
            news=tuple(self._pending_news),
        )
        self._dirty.clear()
        self._pending_trades.clear()
        self._pending_bars.clear()
        self._pending_news.clear()
        return changes

    # ---- Event processing ----------------------------------------------------------------------
    def _dispatch(self, scheduled: ScheduledEvent) -> None:
        self._now = scheduled.time
        self._events_processed += 1
        event = scheduled.event
        if isinstance(event, OrderArrival):
            self._execute_order(event.participant_id, event.intent)
        elif isinstance(event, CancelArrival):
            self._execute_cancel(event.participant_id, event.instrument, event.order_id)
        elif isinstance(event, NewsArrival):
            self._deliver_news(event.news)
        else:
            participant = self._participants[event.participant_id]
            ctx = self._contexts[event.participant_id]
            tag = event.tag
            raw = tag.encode()
            # Length-prefixed, so a tag can never be read as the start of another record.
            self._digest.update(
                struct.pack("<BqqI", _WAKEUP, self._now, event.participant_id, len(raw)) + raw
            )
            self._callback(lambda: participant.on_wakeup(ctx, tag))

    def _execute_order(self, participant_id: int, intent: OrderIntent) -> OrderReport:
        request = OrderRequest(
            instrument=intent.instrument,
            side=intent.side,
            quantity=intent.quantity,
            type=intent.order_type,
            price=intent.price,
            time_in_force=intent.time_in_force,
            owner=participant_id,
            self_trade_prevention=intent.self_trade_prevention,
            timestamp=self._now,
        )
        bid_before = ask_before = None
        if self._observers and intent.instrument < self._exchange.instrument_count:
            # (An unknown instrument has no quote; the exchange rejects the order.)
            bid_before = self._exchange.best_bid(intent.instrument)
            ask_before = self._exchange.best_ask(intent.instrument)
        result = self._exchange.submit(request)
        self._orders_submitted += 1
        if not result.accepted:
            self._orders_rejected += 1
        else:
            self._dirty.add(intent.instrument)
        self._digest_order(participant_id, intent, result)

        report = OrderReport(
            time=self._now,
            intent=intent,
            order_id=result.order_id,
            status=result.status,
            reject_reason=result.reject_reason,
            cancel_reason=result.cancel_reason,
            filled_quantity=result.filled_quantity,
            resting_quantity=result.resting_quantity,
            cancelled_quantity=result.cancelled_quantity,
        )
        self._notify_executions(result, intent)
        if self._observers:
            event = OrderEvent(self._now, participant_id, intent, result, bid_before, ask_before)
            for observer in self._observers:
                observer.on_order(event)
        owner = self._participants.get(participant_id)
        if owner is not None:
            ctx = self._contexts[participant_id]
            self._callback(lambda: owner.on_order_report(ctx, report))
        return report

    def _notify_executions(self, result: SubmitResult, intent: OrderIntent) -> None:
        """Records trades and notifies both sides of every execution and the owners of orders
        removed by self-trade prevention, in exchange sequence order."""
        notifications: list[tuple[int, Callable[[], None]]] = []
        for trade in result.trades:
            # Books first, so that every callback sees portfolios that include this trade.
            if trade.buyer == trade.seller:
                # A self-trade moves nothing: no cash, no shares, no realized P&L.
                self._portfolios[trade.buyer].apply_self_trade(
                    trade.instrument, trade.price, trade.quantity
                )
                continue
            self._portfolios[trade.buyer].apply_fill(
                trade.instrument, Side.BUY, trade.price, trade.quantity
            )
            self._portfolios[trade.seller].apply_fill(
                trade.instrument, Side.SELL, trade.price, trade.quantity
            )
        for trade in result.trades:
            public = self._recorder.record(trade)
            if self._track_changes:
                self._pending_trades.append(public)
            if trade.aggressor_side is Side.BUY:
                aggressor, resting = trade.buyer, trade.seller
            else:
                aggressor, resting = trade.seller, trade.buyer
            # With self-trades allowed both entries can name the same participant: it then
            # receives two fills, one for each of its orders.
            for owner, order_id, is_aggressor in (
                (aggressor, trade.aggressor_order_id, True),
                (resting, trade.resting_order_id, False),
            ):
                participant = self._participants.get(owner)
                if participant is None:
                    continue
                side = intent.side if is_aggressor else _opposite(intent.side)
                fill = Fill(
                    trade_id=trade.id,
                    time=trade.timestamp,
                    instrument=trade.instrument,
                    order_id=order_id,
                    side=side,
                    price=trade.price,
                    quantity=trade.quantity,
                    is_aggressor=is_aggressor,
                )
                notifications.append(
                    (trade.sequence, _bind_fill(participant, self._contexts[owner], fill))
                )
        for cancel in result.resting_cancels:
            participant = self._participants.get(cancel.owner)
            if participant is None:
                continue
            report = CancelReport(
                time=self._now,
                instrument=intent.instrument,
                order_id=cancel.order_id,
                status=CancelStatus.CANCELLED,
                cancelled_quantity=cancel.cancelled_quantity,
                reason=CancelReason.SELF_TRADE_PREVENTION,
            )
            notifications.append(
                (cancel.sequence, _bind_cancel(participant, self._contexts[cancel.owner], report))
            )
        notifications.sort(key=lambda item: item[0])  # Stable: aggressor before resting fill.
        for _, notify in notifications:
            self._callback(notify)

    def _execute_cancel(self, participant_id: int, instrument: int, order_id: int) -> CancelReport:
        self._cancels_requested += 1
        status: CancelStatus
        cancelled_quantity = 0
        if not 0 <= instrument < self._exchange.instrument_count:
            status, code = CancelStatus.UNKNOWN_INSTRUMENT, _UNKNOWN_INSTRUMENT
        else:
            order = self._exchange.order(instrument, order_id)
            if order is None:
                status, code = CancelStatus.NOT_FOUND, _NOT_FOUND
            elif order.owner != participant_id:
                # Indistinguishable from a missing order, and the exchange is never touched.
                status, code = CancelStatus.NOT_FOUND, _NOT_OWNER
            else:
                result = self._exchange.cancel(instrument, order_id)
                if not result.cancelled:
                    raise AssertionError("exchange refused to cancel a resting order")
                status, code = CancelStatus.CANCELLED, _CANCELLED
                cancelled_quantity = result.cancelled_quantity
                self._dirty.add(instrument)
        # The order id is the engine's unsigned 64-bit type, so it is packed unsigned. For ids
        # below 2**63 the bytes equal the signed encoding, so recorded digests are unchanged.
        self._digest.update(
            struct.pack(
                "<BqqqQqq",
                _CANCEL,
                self._now,
                participant_id,
                instrument,
                order_id,
                code,
                cancelled_quantity,
            )
        )
        report = CancelReport(
            time=self._now,
            instrument=instrument,
            order_id=order_id,
            status=status,
            cancelled_quantity=cancelled_quantity,
            reason=CancelReason.USER_REQUESTED,
        )
        owner = self._participants.get(participant_id)
        if owner is not None:
            ctx = self._contexts[participant_id]
            self._callback(lambda: owner.on_cancel(ctx, report))
        return report

    def publish_news(self, news: News) -> None:
        """Schedules ``news`` to reach every participant at the current time (used by the
        environment through its context)."""
        self._queue.push(self._now, NewsArrival(news))

    def _deliver_news(self, news: News) -> None:
        raw = news.headline.encode()
        self._digest.update(
            struct.pack("<BqqdI", _NEWS, self._now, news.instrument, news.impact, len(raw)) + raw
        )
        self._news.append(news)
        if self._track_changes:
            self._pending_news.append(news)
        for pid, participant in self._participants.items():
            if participant is not self._environment:
                self._callback(_bind_news(participant, self._contexts[pid], news))

    def _callback(self, call: Callable[[], None]) -> None:
        self._callback_depth += 1  # A counter, not a flag: callbacks can nest.
        try:
            call()
        finally:
            self._callback_depth -= 1

    @contextlib.contextmanager
    def _failing_on_error(self) -> Iterator[None]:
        """An exception while the simulation is changing (from a participant callback or the
        engine) can leave it half-updated: for example, some participants notified of a trade
        and others not. The simulation then moves to FAILED, a terminal state, instead of
        letting a caller carry on from an inconsistent state. The exception propagates."""
        try:
            yield
        except BaseException as error:
            if self._state is not SimulationState.FAILED:
                self._failure = error
                self._state = SimulationState.FAILED
                self._recorder.finalize(self._now + 1)
                if self._track_changes:
                    self._pending_bars.extend(self._recorder.drain_closed_bars())
                self._digest_end(SimulationState.FAILED)
            raise

    def _after_progress(self) -> None:
        if self._state in _ACTIVE:
            self._recorder.advance_to(self._now)
        if self._track_changes:
            self._pending_bars.extend(self._recorder.drain_closed_bars())

    def _complete(self) -> None:
        self._now = self._config.duration_ns
        self._finish(SimulationState.COMPLETED, self._now)

    def _finish(self, state: SimulationState, end: int) -> None:
        """Ends the run. The recorded data covers simulated time [0, end)."""
        self._recorder.finalize(end)
        if self._track_changes:
            self._pending_bars.extend(self._recorder.drain_closed_bars())
        self._digest_end(state)
        self._state = state
        for pid, participant in self._participants.items():
            self._callback(_bind_stop(participant, self._contexts[pid]))

    def _digest_end(self, state: SimulationState) -> None:
        # How and when the run ended is part of what happened.
        self._digest.update(struct.pack("<Bqq", _END, self._now, _END_STATE_CODE[state]))

    # ---- Digest ---------------------------------------------------------------------------------
    def _digest_record(self, kind: int, *fields: int) -> None:
        self._digest.update(struct.pack(f"<Bq{len(fields)}q", kind, self._now, *fields))

    def _digest_order(self, participant_id: int, intent: OrderIntent, result: SubmitResult) -> None:
        self._digest_record(
            _ORDER,
            participant_id,
            intent.instrument,
            intent.side.value,
            intent.quantity,
            intent.order_type.value,
            intent.price,
            intent.time_in_force.value,
            intent.self_trade_prevention.value,
            result.order_id,
            result.sequence,
            result.status.value,
            result.reject_reason.value,
            result.cancel_reason.value,
            result.filled_quantity,
            result.resting_quantity,
            result.cancelled_quantity,
            len(result.trades),
            len(result.resting_cancels),
        )
        for trade in result.trades:
            self._digest.update(
                struct.pack(
                    "<6q",
                    trade.id,
                    trade.sequence,
                    trade.price,
                    trade.quantity,
                    trade.buy_order_id,
                    trade.sell_order_id,
                )
            )
        for cancel in result.resting_cancels:
            self._digest.update(
                struct.pack("<3q", cancel.order_id, cancel.sequence, cancel.cancelled_quantity)
            )

    # ---- Guards ---------------------------------------------------------------------------------
    def _require(self, allowed: Collection[SimulationState], action: str) -> None:
        if self._state not in allowed:
            raise SimulationStateError(f"cannot {action} while {self._state.value}")

    def _require_participant(self, participant_id: int) -> None:
        if participant_id not in self._participants:
            raise KeyError(f"unknown participant {participant_id}")

    def _require_external_action(self, participant_id: int, action: str) -> None:
        self._require_outside_callbacks(f"{action} immediately")
        self._require_participant(participant_id)
        self._require(_ACTIVE, action)

    def _require_outside_callbacks(self, action: str) -> None:
        if self._callback_depth:
            raise SimulationStateError(f"cannot {action} from inside a participant callback")


def _opposite(side: Side) -> Side:
    return Side.SELL if side is Side.BUY else Side.BUY


def _environment_id(simulation: Simulation) -> int | None:
    environment = simulation.environment
    return None if environment is None else environment.participant_id


def _bind_news(participant: Participant, ctx: SimulationContext, news: News) -> Callable[[], None]:
    return lambda: participant.on_news(ctx, news)


def _bind_start(participant: Participant, ctx: SimulationContext) -> Callable[[], None]:
    return lambda: participant.on_start(ctx)


def _bind_stop(participant: Participant, ctx: SimulationContext) -> Callable[[], None]:
    return lambda: participant.on_stop(ctx)


def _bind_fill(participant: Participant, ctx: SimulationContext, fill: Fill) -> Callable[[], None]:
    return lambda: participant.on_fill(ctx, fill)


def _bind_cancel(
    participant: Participant, ctx: SimulationContext, report: CancelReport
) -> Callable[[], None]:
    return lambda: participant.on_cancel(ctx, report)
