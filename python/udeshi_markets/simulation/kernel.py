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

import enum
import hashlib
import struct
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
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
from udeshi_markets.simulation.events import (
    UINT32_MAX,
    UINT64_MAX,
    CancelArrival,
    EventQueue,
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
from udeshi_markets.simulation.recorder import Bar, MarketRecorder, PublicTrade
from udeshi_markets.simulation.rng import DeterministicRng


class SimulationState(enum.Enum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    COMPLETED = "completed"


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
_ORDER, _CANCEL, _WAKEUP = 1, 2, 3
_CANCEL_STATUS_CODE = {status: code for code, status in enumerate(CancelStatus)}


def _check_cancel_target(instrument: int, order_id: int) -> None:
    """Rejects ids that cannot be represented by the engine's types, before any state changes.
    (An id that fits but names no order is a normal outcome: NOT_FOUND or UNKNOWN_INSTRUMENT.)"""
    require_int("instrument", instrument, 0, UINT32_MAX)
    require_int("order_id", order_id, 0, UINT64_MAX)


class SimulationContext:
    """A participant's handle on the simulation: its identity is bound, so it can act only for
    itself, and it sees only public market data and reports about its own orders."""

    __slots__ = ("_participant_id", "_rng", "_simulation")

    def __init__(self, simulation: Simulation, participant_id: int, rng: DeterministicRng) -> None:
        self._simulation = simulation
        self._participant_id = participant_id
        self._rng = rng

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
        track_changes: bool = False,
    ) -> None:
        self._config = config
        self._participant_factory: ParticipantFactory = participants or tuple
        self._track_changes = track_changes
        self._build()

    # ---- Construction and lifecycle ------------------------------------------------------------
    def _build(self) -> None:
        config = self._config
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
        self._dispatching = False
        self._digest = hashlib.blake2b(digest_size=16)
        self._events_processed = 0
        self._orders_submitted = 0
        self._orders_rejected = 0
        self._cancels_requested = 0
        self._dirty: set[int] = set()
        self._pending_trades: list[PublicTrade] = []
        self._pending_bars: list[Bar] = []

        participants: dict[int, Participant] = {}
        for participant in self._participant_factory():
            if participant.participant_id in participants:
                raise ValueError(f"duplicate participant id {participant.participant_id}")
            participants[participant.participant_id] = participant
        self._participants = dict(sorted(participants.items()))
        self._contexts = {
            pid: SimulationContext(
                self, pid, DeterministicRng.derive(config.seed, "participant", pid)
            )
            for pid in self._participants
        }

    def start(self) -> None:
        self._require({SimulationState.CREATED}, "start")
        self._state = SimulationState.RUNNING
        for pid, participant in self._participants.items():
            participant.on_start(self._contexts[pid])

    def pause(self) -> None:
        self._require({SimulationState.RUNNING}, "pause")
        self._state = SimulationState.PAUSED

    def resume(self) -> None:
        self._require({SimulationState.PAUSED}, "resume")
        self._state = SimulationState.RUNNING

    def stop(self) -> None:
        self._require(_ACTIVE, "stop")
        self._finish(SimulationState.STOPPED)

    def reset(self) -> None:
        """Discards all state and rebuilds the simulation (with fresh participants)."""
        if self._dispatching:
            raise SimulationStateError("cannot reset from inside event dispatch")
        self._build()

    # ---- Advancing simulated time --------------------------------------------------------------
    def step(self, max_events: int = 1) -> int:
        """Processes up to ``max_events`` events (in RUNNING or PAUSED state). Completes the run
        if no event remains before the end time. Returns the number of events processed."""
        if max_events < 1:
            raise ValueError("max_events must be at least 1")
        self._require(_ACTIVE, "step")
        end = self._config.duration_ns
        processed = 0
        while processed < max_events:
            next_time = self._queue.peek_time()
            if next_time is None or next_time > end:
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
        self._require(_ACTIVE, "run_until")
        if time_ns < self._now:
            raise ValueError("cannot run backwards in simulated time")
        end = self._config.duration_ns
        target = min(time_ns, end)
        processed = 0
        while max_events is None or processed < max_events:
            next_time = self._queue.peek_time()
            if next_time is None or next_time > target:
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
        if delay_ns < 0:
            raise ValueError("wakeup delay must be non-negative")
        self._require_participant(participant_id)
        self._require(_ACTIVE, "schedule a wakeup")
        self._queue.push(self._now + delay_ns, Wakeup(participant_id, tag))

    def submit_now(self, participant_id: int, intent: OrderIntent) -> OrderReport:
        """Processes an order immediately at the current simulated time (manual trading). Not
        callable from inside a participant callback; participants use ``ctx.submit``."""
        self._require_external_action(participant_id, "submit an order")
        report = self._execute_order(participant_id, intent)
        self._after_progress()
        return report

    def cancel_now(self, participant_id: int, instrument: int, order_id: int) -> CancelReport:
        """Processes a cancel immediately at the current simulated time (manual trading)."""
        self._require_external_action(participant_id, "cancel an order")
        _check_cancel_target(instrument, order_id)
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
        )
        self._dirty.clear()
        self._pending_trades.clear()
        self._pending_bars.clear()
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
        else:
            participant = self._participants[event.participant_id]
            ctx = self._contexts[event.participant_id]
            tag = event.tag
            self._digest_record(_WAKEUP, event.participant_id)
            self._digest.update(tag.encode())
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
            status = CancelStatus.UNKNOWN_INSTRUMENT
        else:
            order = self._exchange.order(instrument, order_id)
            if order is None:
                status = CancelStatus.NOT_FOUND
            elif order.owner != participant_id:
                status = CancelStatus.NOT_OWNER  # Never touches the exchange.
            else:
                result = self._exchange.cancel(instrument, order_id)
                if not result.cancelled:
                    raise AssertionError("exchange refused to cancel a resting order")
                status = CancelStatus.CANCELLED
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
                _CANCEL_STATUS_CODE[status],
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

    def _callback(self, call: Callable[[], None]) -> None:
        self._dispatching = True
        try:
            call()
        finally:
            self._dispatching = False

    def _after_progress(self) -> None:
        self._recorder.advance_to(self._now)
        if self._track_changes:
            self._pending_bars.extend(self._recorder.drain_closed_bars())

    def _complete(self) -> None:
        self._now = self._config.duration_ns
        self._finish(SimulationState.COMPLETED)

    def _finish(self, state: SimulationState) -> None:
        self._recorder.finalize(self._now)
        if self._track_changes:
            self._pending_bars.extend(self._recorder.drain_closed_bars())
        self._state = state
        for pid, participant in self._participants.items():
            participant.on_stop(self._contexts[pid])

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
        if self._dispatching:
            raise SimulationStateError(
                f"cannot {action} immediately from inside a participant callback"
            )
        self._require_participant(participant_id)
        self._require(_ACTIVE, action)


def _opposite(side: Side) -> Side:
    return Side.SELL if side is Side.BUY else Side.BUY


def _bind_fill(participant: Participant, ctx: SimulationContext, fill: Fill) -> Callable[[], None]:
    return lambda: participant.on_fill(ctx, fill)


def _bind_cancel(
    participant: Participant, ctx: SimulationContext, report: CancelReport
) -> Callable[[], None]:
    return lambda: participant.on_cancel(ctx, report)
