"""Live simulation sessions and their registry.

Concurrency model: every route handler is ``async`` and runs on the single asyncio event loop,
as does each session's LiveRunner. The engine is therefore only ever called from one thread,
with no locking, which is what its single-threaded design requires. (A synchronous ``def``
handler would run in a thread pool and break this, so none exist.)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from collections import OrderedDict, deque
from datetime import UTC, datetime

from fastapi import status

from udeshi_api.errors import ApiError, not_found
from udeshi_api.hub import Message, StreamHub
from udeshi_api.runner import LiveRunner
from udeshi_api.schemas import (
    CancelOut,
    FillOut,
    OpenOrderOut,
    OrderBookOut,
    OrderIn,
    OrderOut,
    SimulationOut,
    SimulationSummaryOut,
    StateName,
    StatusName,
)
from udeshi_api.serialization import (
    PriceFormat,
    bar_out,
    book_out,
    market_out,
    open_order_out,
    trade_out,
)
from udeshi_api.settings import Settings
from udeshi_markets import OrderStatus, OrderType, SelfTradePrevention, Side, TimeInForce
from udeshi_markets.simulation import (
    Bar,
    CancelReport,
    CancelStatus,
    ChangeSet,
    Fill,
    OrderIntent,
    OrderReport,
    Participant,
    Simulation,
    SimulationConfig,
    SimulationContext,
    SimulationState,
    SimulationStateError,
    to_seconds,
)

logger = logging.getLogger("udeshi_api.sessions")

MANUAL_TRADER_ID = 1_000_000
"""Participant id of the API user. Simulated agents use ids below this."""

SNAPSHOT_TRADES = 100
"""Most recent trades per symbol in a stream snapshot."""
SNAPSHOT_BARS = 100
"""Most recent closed bars per symbol in a stream snapshot."""
STREAM_BAR_LIMIT = 1_000
"""Most bars per symbol in one ``bars`` update; earlier ones are counted in ``omitted``."""

_FINISHED = frozenset({SimulationState.STOPPED, SimulationState.COMPLETED})

_SIDES = {"buy": Side.BUY, "sell": Side.SELL}
_TIME_IN_FORCE = {"gtc": TimeInForce.GOOD_TILL_CANCEL, "ioc": TimeInForce.IMMEDIATE_OR_CANCEL}
_STP = {
    "allow": SelfTradePrevention.ALLOW,
    "cancel_resting": SelfTradePrevention.CANCEL_RESTING,
    "cancel_incoming": SelfTradePrevention.CANCEL_INCOMING,
}
_STATUS_NAMES: dict[OrderStatus, StatusName] = {
    OrderStatus.REJECTED: "rejected",
    OrderStatus.RESTING: "resting",
    OrderStatus.FILLED: "filled",
    OrderStatus.CANCELLED: "cancelled",
}
_STATE_NAMES: dict[SimulationState, StateName] = {
    SimulationState.CREATED: "created",
    SimulationState.RUNNING: "running",
    SimulationState.PAUSED: "paused",
    SimulationState.STOPPED: "stopped",
    SimulationState.COMPLETED: "completed",
}


class ManualTrader(Participant):
    """The API user's participant. It trades only through the API (``submit_now``)."""

    def __init__(self) -> None:
        super().__init__(MANUAL_TRADER_ID, "manual")
        self.open_orders: dict[int, int] = {}  # Resting order id -> instrument.
        self.recent_fills: deque[Fill] = deque(maxlen=1_000)
        self._capture: list[Fill] | None = None

    def begin_capture(self) -> None:
        self._capture = []

    def end_capture(self) -> list[Fill]:
        captured, self._capture = self._capture or [], None
        return captured

    def on_fill(self, ctx: SimulationContext, fill: Fill) -> None:
        self.recent_fills.append(fill)
        if self._capture is not None:
            self._capture.append(fill)
        order = ctx.own_order(fill.instrument, fill.order_id)
        if order is None:  # Fully filled.
            self.open_orders.pop(fill.order_id, None)

    def on_order_report(self, ctx: SimulationContext, report: OrderReport) -> None:
        if report.status is OrderStatus.RESTING:
            self.open_orders[report.order_id] = report.intent.instrument

    def on_cancel(self, ctx: SimulationContext, report: CancelReport) -> None:
        if report.status is CancelStatus.CANCELLED:
            self.open_orders.pop(report.order_id, None)


class LiveSession:
    """One simulation being run in real time, with its runner and stream hub."""

    def __init__(self, session_id: str, config: SimulationConfig, speed: float, settings: Settings):
        self._id = session_id
        self.created_at = datetime.now(UTC)
        self.config = config
        self._speed = speed
        self._settings = settings
        self.error: str | None = None
        self._trader = ManualTrader()

        def participants() -> list[Participant]:
            self._trader = ManualTrader()
            return [self._trader]

        self._simulation = Simulation(config, participants, track_changes=True)
        self._last_status: Message | None = None
        self.hub = StreamHub(settings.stream_queue_size, self.snapshot_message)
        self.runner = LiveRunner(
            self,
            tick_seconds=settings.tick_seconds,
            max_events_per_tick=settings.max_events_per_tick,
        )
        self.formats = [PriceFormat(instrument.tick_size) for instrument in config.instruments]

    # ---- Paced protocol ----------------------------------------------------------------------
    @property
    def id(self) -> str:
        return self._id

    @property
    def simulation(self) -> Simulation:
        return self._simulation

    @property
    def speed(self) -> float:
        return self._speed

    def record_failure(self, error: BaseException) -> None:
        self.error = f"{type(error).__name__}: {error}"
        if self._simulation.state in (SimulationState.RUNNING, SimulationState.PAUSED):
            self._simulation.stop()
        self.publish_changes()

    # ---- Lookups -----------------------------------------------------------------------------
    @property
    def trader(self) -> ManualTrader:
        return self._trader

    def instrument(self, symbol: str) -> int:
        found = self._simulation.exchange.find_instrument(symbol)
        if found is None:
            raise not_found("unknown_symbol", f"no market for symbol '{symbol}'")
        return found

    # ---- Lifecycle ---------------------------------------------------------------------------
    def start(self) -> None:
        self._simulation.start()
        self.runner.start()
        logger.info("simulation %s started (speed %.3gx)", self._id, self._speed)
        self.publish_changes()

    def pause(self) -> None:
        self._simulation.pause()
        self.publish_changes()

    def resume(self) -> None:
        self._simulation.resume()
        self.runner.start()  # No-op if still running.
        self.publish_changes()

    async def stop(self) -> None:
        self._simulation.stop()
        await self.runner.stop()
        self.publish_changes()

    async def reset(self) -> None:
        await self.runner.stop()
        self._simulation.reset()
        self.error = None
        # The previous state no longer applies (trade ids even restart at 1), so incremental
        # updates cannot express the change: every subscriber gets a fresh snapshot instead.
        self._simulation.drain_changes()
        self._last_status = self.status_message()
        self.hub.resync_all()

    def step(self, events: int) -> int:
        if self._simulation.state is not SimulationState.PAUSED:
            raise SimulationStateError("pause the simulation before stepping it")
        processed = self._simulation.step(events)
        self.publish_changes()
        return processed

    def set_speed(self, speed: float) -> None:
        self._speed = speed
        self.publish_changes()

    async def close(self) -> None:
        await self.runner.stop()
        self.hub.close()

    def discard(self) -> asyncio.Task[None] | None:
        """Like ``close`` but without waiting: cancels the runner and returns its task (if it
        was running) for the caller to await later."""
        task = self.runner.cancel()
        self.hub.close()
        return task

    # ---- Manual trading ----------------------------------------------------------------------
    def submit(self, symbol: str, order: OrderIn) -> OrderOut:
        instrument = self.instrument(symbol)
        fmt = self.formats[instrument]
        price = 0
        if order.type == "limit":
            assert order.price is not None  # Guaranteed by OrderIn validation.
            try:
                price = self._simulation.exchange.to_ticks(instrument, order.price)
            except ValueError as error:
                raise ApiError(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_price", str(error)
                ) from error
        intent = OrderIntent(
            instrument=instrument,
            side=_SIDES[order.side],
            quantity=order.quantity,
            order_type=OrderType.LIMIT if order.type == "limit" else OrderType.MARKET,
            price=price,
            time_in_force=_TIME_IN_FORCE[order.time_in_force],
            self_trade_prevention=_STP[order.self_trade_prevention],
        )
        self._trader.begin_capture()
        try:
            report = self._simulation.submit_now(MANUAL_TRADER_ID, intent)
        finally:
            fills = self._trader.end_capture()
        self.publish_changes()
        own_fills = [fill for fill in fills if fill.order_id == report.order_id]
        return OrderOut(
            order_id=report.order_id or None,
            symbol=symbol,
            status=_STATUS_NAMES[report.status],
            reject_reason=_reason(report.reject_reason.name),
            cancel_reason=_reason(report.cancel_reason.name),
            filled_quantity=report.filled_quantity,
            resting_quantity=report.resting_quantity,
            cancelled_quantity=report.cancelled_quantity,
            fills=[
                FillOut(
                    trade_id=fill.trade_id,
                    price=fmt.price(fill.price),
                    price_ticks=fill.price,
                    quantity=fill.quantity,
                    is_aggressor=fill.is_aggressor,
                )
                for fill in own_fills
            ],
        )

    def cancel(self, symbol: str, order_id: int) -> CancelOut:
        instrument = self.instrument(symbol)
        report = self._simulation.cancel_now(MANUAL_TRADER_ID, instrument, order_id)
        self.publish_changes()
        if report.status is not CancelStatus.CANCELLED:
            # Another participant's order is reported exactly like a missing one, so the API
            # cannot be used to probe which order ids exist.
            raise not_found("order_not_found", f"you have no resting order {order_id} in {symbol}")
        return CancelOut(
            order_id=order_id, symbol=symbol, cancelled_quantity=report.cancelled_quantity
        )

    def open_orders(self, symbol: str) -> list[OpenOrderOut]:
        instrument = self.instrument(symbol)
        exchange = self._simulation.exchange
        orders = []
        for order_id, order_instrument in sorted(self._trader.open_orders.items()):
            if order_instrument != instrument:
                continue
            order = exchange.order(instrument, order_id)
            if order is not None and order.owner == MANUAL_TRADER_ID:
                orders.append(open_order_out(order, symbol, self.formats[instrument]))
        return orders

    # ---- Views and publishing ----------------------------------------------------------------
    def describe(self, *, active: bool) -> SimulationOut:
        simulation = self._simulation
        summary = simulation.summary()
        duration = simulation.config.duration_ns
        return SimulationOut(
            id=self._id,
            state=_STATE_NAMES[simulation.state],
            active=active,
            created_at=self.created_at.isoformat(),
            time_seconds=to_seconds(simulation.now),
            duration_seconds=to_seconds(duration),
            progress=simulation.now / duration,
            speed=self._speed,
            seed=simulation.config.seed,
            order_latency_ms=simulation.config.order_latency_ns / 1e6,
            bar_interval_seconds=to_seconds(simulation.config.bar_interval_ns),
            symbols=list(simulation.config.symbols),
            error=self.error,
            summary=SimulationSummaryOut(
                events_processed=summary.events_processed,
                orders_submitted=summary.orders_submitted,
                orders_rejected=summary.orders_rejected,
                cancels_requested=summary.cancels_requested,
                trades=summary.trades,
                volume=summary.volume,
                digest=summary.digest,
            ),
        )

    def status_message(self) -> Message:
        return {"type": "simulation", **self._status()}

    def _status(self) -> Message:
        simulation = self._simulation
        return {
            "id": self._id,
            "state": simulation.state.value,
            "time_seconds": to_seconds(simulation.now),
            "progress": simulation.now / simulation.config.duration_ns,
            "speed": self._speed,
            "error": self.error,
        }

    def snapshot_message(self, symbols: frozenset[str] | None) -> Message:
        """Everything a client needs, replacing whatever it held. Sent first on every
        connection, then again after a reset or when the client fell behind."""
        simulation = self._simulation
        exchange = simulation.exchange
        recorder = simulation.recorder
        markets = []
        for instrument, symbol in enumerate(simulation.config.symbols):
            if symbols is not None and symbol not in symbols:
                continue
            fmt = self.formats[instrument]
            markets.append(
                {
                    "symbol": symbol,
                    "book": self._book(instrument).model_dump(),
                    "market": market_out(exchange, instrument, fmt).model_dump(),
                    "trades": [
                        trade_out(t, fmt).model_dump()
                        for t in recorder.trades(instrument, SNAPSHOT_TRADES)
                    ],
                    "bars": [
                        bar_out(b, fmt).model_dump()
                        for b in recorder.bars(instrument)[-SNAPSHOT_BARS:]
                    ],
                }
            )
        return {"type": "snapshot", "simulation": self._status(), "markets": markets}

    def publish_changes(self) -> None:
        changes = self._simulation.drain_changes()
        batch: list[tuple[Message, str | None]] = []
        status_message = self.status_message()
        if status_message != self._last_status:  # Unchanged while paused: say nothing.
            batch.append((status_message, None))
            self._last_status = status_message
        if self.hub.subscriber_count:  # Payloads are built only if someone will receive them.
            batch.extend(self._updates(changes))
        self.hub.publish(batch)

    def _updates(self, changes: ChangeSet) -> list[tuple[Message, str]]:
        symbols = self._simulation.config.symbols
        exchange = self._simulation.exchange
        trades_by_instrument: dict[int, list[Message]] = {}
        for trade in changes.trades:
            fmt = self.formats[trade.instrument]
            trades_by_instrument.setdefault(trade.instrument, []).append(
                trade_out(trade, fmt).model_dump()
            )
        bars_by_instrument: dict[int, list[Bar]] = {}
        for bar in changes.bars:
            bars_by_instrument.setdefault(bar.instrument, []).append(bar)
        updates: list[tuple[Message, str]] = []
        for instrument in sorted(changes.instruments | trades_by_instrument.keys()):
            symbol = symbols[instrument]
            fmt = self.formats[instrument]
            updates.append(({"type": "book", **self._book(instrument).model_dump()}, symbol))
            updates.append(
                ({"type": "market", **market_out(exchange, instrument, fmt).model_dump()}, symbol)
            )
            if instrument in trades_by_instrument:
                trades = trades_by_instrument[instrument]
                updates.append(({"type": "trades", "symbol": symbol, "trades": trades}, symbol))
        for instrument, bars in sorted(bars_by_instrument.items()):
            # A jump in simulated time (a step to the end, a fast runner tick) can close many
            # bars at once. Clients get the most recent ones and a count of the rest, which the
            # history endpoint serves.
            shown = bars[-STREAM_BAR_LIMIT:]
            fmt = self.formats[instrument]
            message = {
                "type": "bars",
                "symbol": symbols[instrument],
                "bars": [bar_out(bar, fmt).model_dump() for bar in shown],
                "omitted": len(bars) - len(shown),
            }
            updates.append((message, symbols[instrument]))
        return updates

    def _book(self, instrument: int) -> OrderBookOut:
        return book_out(
            self._simulation.exchange,
            instrument,
            self._settings.stream_book_depth,
            self.formats[instrument],
            self._simulation.now,
        )


def _reason(name: str) -> str | None:
    return None if name == "NONE" else name.lower()


class SessionManager:
    """Registry of live sessions; the most recently created one is the active market."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sessions: OrderedDict[str, LiveSession] = OrderedDict()  # Oldest first.
        self._active: str | None = None
        self._retiring: set[asyncio.Task[None]] = set()

    @property
    def settings(self) -> Settings:
        return self._settings

    def create(self, config: SimulationConfig, speed: float) -> LiveSession:
        """Registers a new session and makes it active. Synchronous on purpose: registering it,
        evicting old sessions and the caller's response all see one consistent registry, with
        no other request able to interleave."""
        session = LiveSession(secrets.token_hex(6), config, speed, self._settings)
        self._sessions[session.id] = session
        self._active = session.id
        while len(self._sessions) > self._settings.max_sessions:
            victim = self._eviction_candidate()
            del self._sessions[victim.id]
            task = victim.discard()
            if task is not None:  # Awaited at shutdown.
                self._retiring.add(task)
                task.add_done_callback(self._retiring.discard)
            logger.info("evicted simulation %s", victim.id)
        logger.info("created simulation %s (%d instruments)", session.id, len(config.instruments))
        return session

    def get(self, session_id: str) -> LiveSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise not_found("unknown_simulation", f"no simulation with id '{session_id}'")
        return session

    def active(self) -> LiveSession:
        if self._active is None:
            raise not_found("no_active_simulation", "create a simulation first")
        return self._sessions[self._active]

    def resolve(self, session_id: str | None) -> LiveSession:
        return self.active() if session_id is None else self.get(session_id)

    def is_active(self, session: LiveSession) -> bool:
        return session.id == self._active

    def all(self) -> list[LiveSession]:
        return list(self._sessions.values())

    async def delete(self, session_id: str) -> None:
        session = self.get(session_id)
        del self._sessions[session_id]
        if self._active == session_id:
            self._active = next(reversed(self._sessions), None)
        await session.close()

    async def shutdown(self) -> None:
        for session in list(self._sessions.values()):
            await session.close()
        self._sessions.clear()
        self._active = None
        for task in list(self._retiring):
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def _eviction_candidate(self) -> LiveSession:
        """The oldest stopped or completed session, else the oldest session; never the active
        one. (A session that was created but not started yet is kept like a running one: its
        creator is presumably about to start it.)"""
        candidates = [s for s in self._sessions.values() if s.id != self._active]
        finished = [s for s in candidates if s.simulation.state in _FINISHED]
        return (finished or candidates)[0]
