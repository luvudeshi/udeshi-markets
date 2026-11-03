"""Tests for the discrete-event simulation kernel."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest

from udeshi_markets import (
    OrderStatus,
    OrderType,
    SelfTradePrevention,
    Side,
    TimeInForce,
)
from udeshi_markets.simulation import (
    SECOND,
    CancelStatus,
    InstrumentConfig,
    OrderIntent,
    Participant,
    ScriptAction,
    ScriptedCancel,
    ScriptedOrder,
    ScriptedParticipant,
    Simulation,
    SimulationConfig,
    SimulationContext,
    SimulationState,
    SimulationStateError,
)
from udeshi_markets.simulation.participant import CancelReport, Fill, OrderReport

ONE_INSTRUMENT = (InstrumentConfig("ACME", 1.00, tick_size=0.01),)  # Reference 100 ticks.


def config(**overrides: object) -> SimulationConfig:
    values: dict[str, object] = {
        "instruments": ONE_INSTRUMENT,
        "duration_ns": 10 * SECOND,
        "bar_interval_ns": SECOND,
    }
    values.update(overrides)
    return SimulationConfig(**values)  # type: ignore[arg-type]


def buy(price: int, quantity: int, **kwargs: object) -> OrderIntent:
    return OrderIntent(0, Side.BUY, quantity, price=price, **kwargs)  # type: ignore[arg-type]


def sell(price: int, quantity: int, **kwargs: object) -> OrderIntent:
    return OrderIntent(0, Side.SELL, quantity, price=price, **kwargs)  # type: ignore[arg-type]


def scripted(*scripts: tuple[int, Sequence[ScriptAction]]) -> Callable[[], list[Participant]]:
    """A participant factory building fresh ScriptedParticipants (so reset works)."""

    def factory() -> list[Participant]:
        return [ScriptedParticipant(pid, actions) for pid, actions in scripts]

    return factory


def participant(simulation: Simulation, pid: int) -> ScriptedParticipant:
    found = simulation.participants[pid]
    assert isinstance(found, ScriptedParticipant)
    return found


SPEC_EXAMPLE = scripted(
    (1, [ScriptedOrder(1 * SECOND, sell(101, 10))]),
    (2, [ScriptedOrder(2 * SECOND, sell(102, 5))]),
    (3, [ScriptedOrder(3 * SECOND, buy(102, 12))]),
)


# ---- Lifecycle -------------------------------------------------------------------------------


def test_lifecycle_transitions() -> None:
    simulation = Simulation(config())
    assert simulation.state is SimulationState.CREATED
    for invalid in (simulation.pause, simulation.resume, simulation.stop):
        with pytest.raises(SimulationStateError):
            invalid()
    with pytest.raises(SimulationStateError):
        simulation.step()
    simulation.start()
    assert simulation.state is SimulationState.RUNNING
    with pytest.raises(SimulationStateError):
        simulation.start()
    simulation.pause()
    assert simulation.state is SimulationState.PAUSED
    with pytest.raises(SimulationStateError):
        simulation.pause()
    simulation.resume()
    simulation.stop()
    assert simulation.state is SimulationState.STOPPED
    for invalid in (simulation.start, simulation.resume, simulation.stop):
        with pytest.raises(SimulationStateError):
            invalid()
    simulation.reset()
    assert simulation.state is SimulationState.CREATED


def test_headless_run_completes_at_the_configured_duration() -> None:
    simulation = Simulation(config(), SPEC_EXAMPLE)
    summary = simulation.run()
    assert summary.state is SimulationState.COMPLETED
    assert summary.time == 10 * SECOND
    assert summary.orders_submitted == 3
    assert summary.trades == 2
    assert summary.volume == 12
    assert summary.digest == simulation.digest
    with pytest.raises(SimulationStateError):
        simulation.run()


# ---- Matching, notifications and recording ---------------------------------------------------


def test_spec_example_end_to_end() -> None:
    simulation = Simulation(config(), SPEC_EXAMPLE)
    simulation.run()
    seller_1, seller_2, buyer = (participant(simulation, pid) for pid in (1, 2, 3))

    assert [r.status for r in buyer.reports] == [OrderStatus.FILLED]
    assert [(f.quantity, f.price, f.is_aggressor, f.side) for f in buyer.fills] == [
        (10, 101, True, Side.BUY),
        (2, 102, True, Side.BUY),
    ]
    assert [(f.quantity, f.price, f.is_aggressor) for f in seller_1.fills] == [(10, 101, False)]
    assert [(f.quantity, f.price, f.side) for f in seller_2.fills] == [(2, 102, Side.SELL)]
    assert all(f.time == 3 * SECOND for f in buyer.fills + seller_1.fills + seller_2.fills)

    exchange = simulation.exchange
    asks = exchange.book_snapshot(0).asks
    assert [(level.price, level.quantity) for level in asks] == [(102, 3)]
    tape = simulation.recorder.trades(0)
    assert [(t.time, t.price, t.quantity, t.aggressor_side) for t in tape] == [
        (3 * SECOND, 101, 10, Side.BUY),
        (3 * SECOND, 102, 2, Side.BUY),
    ]
    bars = simulation.recorder.bars(0)
    assert len(bars) == 10
    trade_bar = bars[3]
    assert (trade_bar.open, trade_bar.high, trade_bar.close, trade_bar.volume) == (
        101,
        102,
        102,
        12,
    )
    assert (bars[2].close, bars[2].volume) == (100, 0)  # Reference price before any trade.
    assert (bars[9].close, bars[9].volume) == (102, 0)


def test_fills_never_reveal_the_counterparty() -> None:
    assert not {"counterparty", "buyer", "seller", "owner"} & set(Fill.__dataclass_fields__)


def test_order_latency_delays_arrival() -> None:
    simulation = Simulation(config(order_latency_ns=500_000_000), SPEC_EXAMPLE)
    simulation.run()
    buyer = participant(simulation, 3)
    assert buyer.reports[0].time == 3 * SECOND + 500_000_000
    assert simulation.recorder.trades(0)[0].time == 3 * SECOND + 500_000_000


def test_equal_timestamps_are_processed_in_scheduling_order() -> None:
    # Both sellers are scheduled for the same instant; participant 1 is started (and so
    # schedules) first, so it gets time priority at the shared price.
    simulation = Simulation(
        config(),
        scripted(
            (1, [ScriptedOrder(SECOND, sell(100, 1))]),
            (2, [ScriptedOrder(SECOND, sell(100, 1))]),
            (3, [ScriptedOrder(2 * SECOND, buy(100, 1))]),
        ),
    )
    simulation.run()
    assert len(participant(simulation, 1).fills) == 1
    assert participant(simulation, 2).fills == []


def test_self_trade_prevention_cancel_is_reported_to_the_owner() -> None:
    simulation = Simulation(
        config(),
        scripted(
            (
                1,
                [
                    ScriptedOrder(SECOND, sell(101, 5)),
                    ScriptedOrder(
                        2 * SECOND,
                        buy(101, 5, self_trade_prevention=SelfTradePrevention.CANCEL_RESTING),
                    ),
                ],
            ),
        ),
    )
    simulation.run()
    owner = participant(simulation, 1)
    assert [(c.status, c.reason.name, c.cancelled_quantity) for c in owner.cancels] == [
        (CancelStatus.CANCELLED, "SELF_TRADE_PREVENTION", 5)
    ]
    assert owner.fills == []
    assert owner.reports[1].status is OrderStatus.RESTING


def test_cancels_are_ownership_checked() -> None:
    class Intruder(Participant):
        def __init__(self) -> None:
            super().__init__(9)
            self.cancels: list[CancelReport] = []

        def on_start(self, ctx: SimulationContext) -> None:
            ctx.wakeup(2 * SECOND)

        def on_wakeup(self, ctx: SimulationContext, tag: str) -> None:
            ctx.cancel(0, 1)  # Order 1 belongs to participant 1.
            ctx.cancel(0, 999)
            ctx.cancel(5, 1)

        def on_cancel(self, ctx: SimulationContext, report: CancelReport) -> None:
            self.cancels.append(report)

    def factory() -> list[Participant]:
        return [ScriptedParticipant(1, [ScriptedOrder(SECOND, sell(105, 3))]), Intruder()]

    simulation = Simulation(config(), factory)
    simulation.run()
    intruder = simulation.participants[9]
    assert isinstance(intruder, Intruder)
    assert [c.status for c in intruder.cancels] == [
        CancelStatus.NOT_OWNER,
        CancelStatus.NOT_FOUND,
        CancelStatus.UNKNOWN_INSTRUMENT,
    ]
    resting = simulation.exchange.order(0, 1)
    assert resting is not None
    assert resting.remaining_quantity == 3


def test_scripted_cancel_of_own_order() -> None:
    simulation = Simulation(
        config(),
        scripted(
            (1, [ScriptedOrder(SECOND, buy(99, 4)), ScriptedCancel(2 * SECOND, order_index=0)]),
        ),
    )
    simulation.run()
    owner = participant(simulation, 1)
    assert [(c.status, c.cancelled_quantity) for c in owner.cancels] == [
        (CancelStatus.CANCELLED, 4)
    ]
    assert simulation.exchange.best_bid(0) is None


def test_rejected_orders_are_reported() -> None:
    simulation = Simulation(config(), scripted((1, [ScriptedOrder(SECOND, buy(0, 1))])))
    simulation.run()
    (report,) = participant(simulation, 1).reports
    assert report.status is OrderStatus.REJECTED
    assert report.reject_reason.name == "INVALID_PRICE"
    assert simulation.summary().orders_rejected == 1


def test_market_and_ioc_intents() -> None:
    simulation = Simulation(
        config(),
        scripted(
            (1, [ScriptedOrder(SECOND, sell(100, 5))]),
            (
                2,
                [
                    ScriptedOrder(2 * SECOND, OrderIntent(0, Side.BUY, 3, OrderType.MARKET)),
                    ScriptedOrder(
                        3 * SECOND,
                        buy(100, 10, time_in_force=TimeInForce.IMMEDIATE_OR_CANCEL),
                    ),
                ],
            ),
        ),
    )
    simulation.run()
    market, ioc = participant(simulation, 2).reports
    assert (market.status, market.filled_quantity) == (OrderStatus.FILLED, 3)
    assert (ioc.status, ioc.filled_quantity, ioc.cancelled_quantity) == (
        OrderStatus.CANCELLED,
        2,
        8,
    )


# ---- Advancing time --------------------------------------------------------------------------


def test_step_processes_one_event_at_a_time() -> None:
    simulation = Simulation(config(), SPEC_EXAMPLE)
    simulation.start()
    assert simulation.pending_events == 3  # One wakeup per scripted action.
    assert simulation.step() == 1
    assert simulation.now == SECOND  # Wakeup of participant 1...
    assert simulation.step() == 1
    assert simulation.now == SECOND  # ...and the order it submitted (zero latency).
    assert simulation.exchange.best_ask(0) == 101
    simulation.pause()
    assert simulation.step(10) == 4  # Stepping works while paused; then the queue is empty...
    assert simulation.state is SimulationState.COMPLETED  # ...so the run completes.
    assert simulation.now == 10 * SECOND


def test_run_until_advances_the_clock_even_without_events() -> None:
    simulation = Simulation(config())
    simulation.start()
    assert simulation.run_until(4 * SECOND) == 0
    assert simulation.now == 4 * SECOND
    assert simulation.state is SimulationState.RUNNING
    with pytest.raises(ValueError, match="backwards"):
        simulation.run_until(SECOND)
    simulation.run_until(100 * SECOND)  # Capped at the end time.
    assert simulation.now == 10 * SECOND
    assert simulation.state is SimulationState.COMPLETED


def test_run_until_respects_max_events() -> None:
    simulation = Simulation(config(), SPEC_EXAMPLE)
    simulation.start()
    assert simulation.run_until(5 * SECOND, max_events=3) == 3
    assert simulation.now == 2 * SECOND  # Stopped at the last processed event.
    assert simulation.run_until(5 * SECOND) == 3
    assert simulation.now == 5 * SECOND


def test_events_after_the_end_time_are_never_processed() -> None:
    simulation = Simulation(config(), scripted((1, [ScriptedOrder(11 * SECOND, sell(100, 1))])))
    summary = simulation.run()
    assert summary.events_processed == 0
    assert participant(simulation, 1).reports == []


def test_stop_finalizes_bars_and_notifies_participants() -> None:
    stopped: list[int] = []

    class Watcher(Participant):
        def on_stop(self, ctx: SimulationContext) -> None:
            stopped.append(ctx.now)

    simulation = Simulation(config(), lambda: [Watcher(1)])
    simulation.start()
    simulation.run_until(2_500_000_000)
    simulation.stop()
    assert stopped == [2_500_000_000]
    assert [(b.start, b.end) for b in simulation.recorder.bars(0)][-1] == (
        2 * SECOND,
        2_500_000_000,
    )


def test_reset_rebuilds_everything_with_fresh_participants() -> None:
    simulation = Simulation(config(), SPEC_EXAMPLE)
    first = simulation.run()
    simulation.reset()
    assert simulation.state is SimulationState.CREATED
    assert simulation.now == 0
    assert simulation.exchange.resting_order_count(0) == 0
    assert participant(simulation, 3).reports == []
    assert simulation.run().digest == first.digest


# ---- Manual (external) actions ---------------------------------------------------------------


def test_submit_now_and_cancel_now() -> None:
    simulation = Simulation(config(), lambda: [Participant(7, "manual")])
    with pytest.raises(SimulationStateError):
        simulation.submit_now(7, buy(100, 1))
    simulation.start()
    simulation.run_until(SECOND)
    report = simulation.submit_now(7, buy(99, 2))
    assert report.status is OrderStatus.RESTING
    assert report.time == SECOND
    with pytest.raises(KeyError):
        simulation.submit_now(8, buy(99, 1))
    cancel = simulation.cancel_now(7, 0, report.order_id)
    assert (cancel.status, cancel.cancelled_quantity) == (CancelStatus.CANCELLED, 2)


def test_participants_cannot_act_immediately_from_callbacks() -> None:
    errors: list[Exception] = []
    holder: list[Simulation] = []

    class Reentrant(Participant):
        def on_order_report(self, ctx: SimulationContext, report: OrderReport) -> None:
            try:
                holder[0].submit_now(self.participant_id, buy(1, 1))
            except SimulationStateError as error:
                errors.append(error)

    simulation = Simulation(config(), lambda: [Reentrant(1)])
    holder.append(simulation)
    simulation.start()
    simulation.submit_now(1, buy(100, 1))
    assert len(errors) == 1


# ---- Change tracking -------------------------------------------------------------------------


def test_drain_changes_reports_books_trades_and_bars_once() -> None:
    simulation = Simulation(config(), SPEC_EXAMPLE, track_changes=True)
    simulation.start()
    simulation.run_until(3 * SECOND + 1)
    changes = simulation.drain_changes()
    assert changes.instruments == frozenset({0})
    assert [t.quantity for t in changes.trades] == [10, 2]
    assert [b.start for b in changes.bars] == [0, SECOND, 2 * SECOND]
    empty = simulation.drain_changes()
    assert (empty.instruments, empty.trades, empty.bars) == (frozenset(), (), ())
    with pytest.raises(SimulationStateError):
        Simulation(config()).drain_changes()


# ---- Determinism -----------------------------------------------------------------------------


def _busy_script(offset: int) -> list[ScriptAction]:
    actions: list[ScriptAction] = []
    for k in range(40):
        price = 100 + ((k * 7 + offset) % 9) - 4
        side_intent = buy(price, 1 + k % 5) if (k + offset) % 2 else sell(price, 1 + k % 3)
        actions.append(ScriptedOrder((k + 1) * 200_000_000, side_intent))
        if k % 6 == 5:
            actions.append(
                ScriptedCancel((k + 1) * 200_000_000 + 50_000_000, order_index=len(actions) - 1)
            )
    return actions


BUSY = scripted((1, _busy_script(0)), (2, _busy_script(3)), (3, _busy_script(5)))


def test_identical_runs_have_identical_digests() -> None:
    first = Simulation(config(order_latency_ns=1_000_000), BUSY).run()
    second = Simulation(config(order_latency_ns=1_000_000), BUSY).run()
    assert first == second
    assert first.trades > 10


def test_different_inputs_change_the_digest() -> None:
    base = Simulation(config(), BUSY).run().digest
    assert Simulation(config(order_latency_ns=1), BUSY).run().digest != base
    assert Simulation(config(seed=2), BUSY).run().digest == base  # Scripts ignore the seed.


def test_golden_digest() -> None:
    # Pins complete observable behaviour of a fixed scenario on every platform. If behaviour is
    # changed on purpose, update the constant and explain why in the commit message.
    summary = Simulation(config(order_latency_ns=1_000_000), BUSY).run()
    assert (summary.trades, summary.volume) == (GOLDEN_TRADES, GOLDEN_VOLUME)
    assert summary.digest == GOLDEN_DIGEST


GOLDEN_TRADES = 68
GOLDEN_VOLUME = 102
GOLDEN_DIGEST = "e82478004d70ca387c97747531ab4551"


# ---- Configuration ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"instruments": ()}, "at least one instrument"),
        ({"instruments": (ONE_INSTRUMENT[0], ONE_INSTRUMENT[0])}, "unique"),
        ({"duration_ns": 0}, "duration_ns"),
        ({"seed": -1}, "seed"),
        ({"order_latency_ns": -1}, "order_latency_ns"),
        ({"bar_interval_ns": 0}, "bar_interval_ns"),
        ({"bar_interval_ns": 11 * SECOND}, "bar_interval_ns"),
        ({"recent_trade_capacity": 0}, "recent_trade_capacity"),
        ({"trade_history_limit": 0}, "trade_history_limit"),
    ],
)
def test_invalid_configuration(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        config(**overrides)


def test_instrument_prices_must_be_on_grid() -> None:
    with pytest.raises(ValueError, match="not a multiple of the tick size"):
        InstrumentConfig("BAD", 100.005, tick_size=0.01)
    with pytest.raises(ValueError, match="reference price must be positive"):
        InstrumentConfig("BAD", 0.0)
    assert InstrumentConfig("OK", 42.5).reference_price_ticks == 4250


def test_duplicate_participant_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        Simulation(config(), lambda: [Participant(1), Participant(1)])
    with pytest.raises(ValueError, match="participant ids must be positive"):
        Participant(0)
