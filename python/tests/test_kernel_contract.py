"""Contract tests for the simulation kernel: end-of-run semantics, the re-entrancy guard, the
FAILED state, information boundaries, input validation, notification order and the digest."""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from decimal import Decimal, getcontext

import pytest

from udeshi_markets import OrderStatus, SelfTradePrevention, Side
from udeshi_markets.simulation import (
    MAX_BARS,
    SECOND,
    CancelReport,
    CancelStatus,
    DeterministicRng,
    EventQueue,
    Fill,
    InstrumentConfig,
    OrderEvent,
    OrderIntent,
    OrderReport,
    Participant,
    ScriptAction,
    ScriptedCancel,
    ScriptedOrder,
    ScriptedParticipant,
    Simulation,
    SimulationConfig,
    SimulationContext,
    SimulationObserver,
    SimulationState,
    SimulationStateError,
    deterministic_log,
)

ONE_INSTRUMENT = (InstrumentConfig("ACME", 1.00, tick_size=0.01),)


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
    def factory() -> list[Participant]:
        return [ScriptedParticipant(pid, actions) for pid, actions in scripts]

    return factory


def state_of(simulation: Simulation) -> SimulationState:
    return simulation.state  # A call, so mypy does not narrow across state changes.


def bar_volume(simulation: Simulation, instrument: int = 0) -> int:
    return sum(bar.volume for bar in simulation.recorder.bars(instrument))


# ---- End of the run --------------------------------------------------------------------------


def test_a_run_covers_its_duration_half_open() -> None:
    """A run of duration D covers [0, D): an event at D - 1 is processed, an event at D never
    is, and every trade lands in a bar."""
    last = 10 * SECOND - 1
    for at, expected_orders in ((last, 2), (10 * SECOND, 0)):
        simulation = Simulation(
            config(),
            scripted((1, [ScriptedOrder(at, sell(100, 3))]), (2, [ScriptedOrder(at, buy(100, 3))])),
        )
        summary = simulation.run()
        assert summary.orders_submitted == expected_orders
        assert bar_volume(simulation) == summary.volume


def test_run_until_processes_an_event_at_exactly_its_target() -> None:
    simulation = Simulation(config(), scripted((1, [ScriptedOrder(2 * SECOND, sell(100, 1))])))
    simulation.start()
    simulation.run_until(2 * SECOND)
    assert simulation.summary().orders_submitted == 1


@pytest.mark.parametrize("stop_at", [2 * SECOND, 2 * SECOND + 300])
def test_trades_at_the_stop_instant_are_in_a_bar(stop_at: int) -> None:
    """Stopping at t keeps everything that happened at t, on a bar boundary or not."""
    simulation = Simulation(config(), lambda: [Participant(7), Participant(8)])
    simulation.start()
    simulation.run_until(stop_at)
    simulation.submit_now(7, sell(100, 5))
    simulation.submit_now(8, buy(100, 5))
    simulation.stop()
    assert simulation.exchange.statistics(0).volume == 5
    assert bar_volume(simulation) == 5
    assert simulation.recorder.bars(0)[-1].end == stop_at + 1


def test_bars_always_account_for_every_trade() -> None:
    """Whatever the timing of trades and of the end of the run, bar volume equals traded
    volume."""
    rng = random.Random(11)
    for _ in range(60):
        times = sorted(rng.randrange(0, 10 * SECOND, SECOND // 4) for _ in range(6))
        script: list[ScriptAction] = [ScriptedOrder(t, sell(100, 1)) for t in times]
        takers: list[ScriptAction] = [ScriptedOrder(t, buy(100, 1)) for t in times]
        simulation = Simulation(config(), scripted((1, script), (2, takers)))
        simulation.start()
        if rng.random() < 0.5:
            simulation.run_until(rng.choice(times))
            simulation.stop()
        else:
            simulation.run_until(10 * SECOND)
        assert bar_volume(simulation) == simulation.exchange.statistics(0).volume


def test_how_and_when_a_run_ended_is_part_of_its_digest() -> None:
    def digest(end: Callable[[Simulation], None]) -> str:
        simulation = Simulation(config(), scripted((1, [ScriptedOrder(SECOND, sell(100, 1))])))
        simulation.start()
        end(simulation)
        return simulation.digest

    def stop_at(t: int) -> Callable[[Simulation], None]:
        def end(simulation: Simulation) -> None:
            simulation.run_until(t)
            simulation.stop()

        return end

    def complete(simulation: Simulation) -> None:
        simulation.run_until(10 * SECOND)

    digests = {digest(stop_at(5 * SECOND)), digest(stop_at(6 * SECOND)), digest(complete)}
    assert len(digests) == 3


def test_stop_and_reset_from_every_active_state() -> None:
    for pause in (False, True):
        simulation = Simulation(config(), lambda: [Participant(1)])
        simulation.start()
        if pause:
            simulation.pause()
        simulation.stop()
        assert state_of(simulation) is SimulationState.STOPPED
        simulation = Simulation(config(), lambda: [Participant(1)])
        simulation.start()
        if pause:
            simulation.pause()
        simulation.reset()
        assert state_of(simulation) is SimulationState.CREATED


def test_the_bar_count_is_bounded() -> None:
    with pytest.raises(ValueError, match="bars"):
        config(duration_ns=MAX_BARS + 1, bar_interval_ns=1)
    assert config(duration_ns=MAX_BARS, bar_interval_ns=1).duration_ns == MAX_BARS


# ---- Re-entrancy guard -----------------------------------------------------------------------


class Meddler(Participant):
    """Tries to drive the simulation from inside each of its own callbacks."""

    def __init__(self, simulation: list[Simulation]) -> None:
        super().__init__(1)
        self._simulation = simulation
        self.refused: list[str] = []
        self.messages: set[str] = set()

    def _meddle(self, where: str) -> None:
        simulation = self._simulation[0]
        attempts: dict[str, Callable[[], object]] = {
            "submit_now": lambda: simulation.submit_now(1, buy(99, 1)),
            "cancel_now": lambda: simulation.cancel_now(1, 0, 1),
            "step": lambda: simulation.step(),
            "run_until": lambda: simulation.run_until(simulation.now),
            "stop": simulation.stop,
            "pause": simulation.pause,
            "reset": simulation.reset,
        }
        for name, attempt in attempts.items():
            try:
                attempt()
            except SimulationStateError as error:
                self.refused.append(f"{where}:{name}")
                self.messages.add(str(error).split(" from ")[-1])

    def on_start(self, ctx: SimulationContext) -> None:
        self._meddle("start")
        ctx.submit(sell(100, 1))
        ctx.submit(buy(100, 1))
        ctx.wakeup(SECOND)

    def on_wakeup(self, ctx: SimulationContext, tag: str) -> None:
        self._meddle("wakeup")

    def on_order_report(self, ctx: SimulationContext, report: OrderReport) -> None:
        self._meddle("report")

    def on_fill(self, ctx: SimulationContext, fill: Fill) -> None:
        self._meddle("fill")

    def on_stop(self, ctx: SimulationContext) -> None:
        self._meddle("stop")


def test_no_callback_can_drive_the_simulation() -> None:
    holder: list[Simulation] = []
    simulation = Simulation(config(), lambda: [Meddler(holder)])
    holder.append(simulation)
    summary = simulation.run()
    meddler = simulation.participants[1]
    assert isinstance(meddler, Meddler)
    per_callback = len(meddler.refused) // 7
    assert len(meddler.refused) == 7 * per_callback
    assert {entry.split(":")[0] for entry in meddler.refused} == {
        "start",
        "wakeup",
        "report",
        "fill",
        "stop",
    }
    assert meddler.messages == {"inside a participant callback"}
    assert summary.state is SimulationState.COMPLETED
    assert summary.orders_submitted == 2  # Only the two scheduled through the context.


# ---- Failure ---------------------------------------------------------------------------------


class Exploder(Participant):
    def on_fill(self, ctx: SimulationContext, fill: Fill) -> None:
        raise RuntimeError("strategy bug")


def test_a_failing_callback_ends_the_run_in_failed() -> None:
    """Some participants may already have been told about an event and others not, so the
    simulation cannot carry on: it moves to FAILED, keeps the error and refuses to continue."""

    class Taker(Exploder):
        def on_start(self, ctx: SimulationContext) -> None:
            ctx.wakeup(3 * SECOND)

        def on_wakeup(self, ctx: SimulationContext, tag: str) -> None:
            ctx.submit(buy(100, 1))

    simulation = Simulation(
        config(),
        lambda: [ScriptedParticipant(1, [ScriptedOrder(SECOND, sell(100, 2))]), Taker(2)],
    )
    with pytest.raises(RuntimeError, match="strategy bug"):
        simulation.run()
    assert state_of(simulation) is SimulationState.FAILED
    assert isinstance(simulation.failure, RuntimeError)
    assert simulation.recorder.finalized
    assert bar_volume(simulation) == simulation.exchange.statistics(0).volume == 1
    for proceed in (simulation.step, simulation.stop, simulation.run, simulation.resume):
        with pytest.raises(SimulationStateError, match="while failed"):
            proceed()
    simulation.reset()
    assert state_of(simulation) is SimulationState.CREATED
    assert simulation.failure is None


def test_observers_see_an_order_whose_fill_callback_failed() -> None:
    """The order's trades are on the tape even though the run failed, so observers (the
    analytics, the API's counterparty ledger) must have been told about them too."""

    class Taker(Exploder):
        def on_start(self, ctx: SimulationContext) -> None:
            ctx.wakeup(3 * SECOND)

        def on_wakeup(self, ctx: SimulationContext, tag: str) -> None:
            ctx.submit(buy(100, 1))

    class Recorder(SimulationObserver):
        def __init__(self) -> None:
            self.trades: list[tuple[int, int]] = []

        def on_order(self, event: OrderEvent) -> None:
            self.trades += [(trade.buyer, trade.seller) for trade in event.result.trades]

    simulation = Simulation(
        config(),
        lambda: [ScriptedParticipant(1, [ScriptedOrder(SECOND, sell(100, 2))]), Taker(2)],
    )
    observer = Recorder()
    simulation.add_observer(observer)
    with pytest.raises(RuntimeError, match="strategy bug"):
        simulation.run()
    assert state_of(simulation) is SimulationState.FAILED
    assert len(simulation.recorder.trades(0)) == 1
    assert observer.trades == [(2, 1)]


def test_a_failure_in_on_start_also_fails_the_run() -> None:
    class Broken(Participant):
        def on_start(self, ctx: SimulationContext) -> None:
            raise ValueError("bad parameters")

    simulation = Simulation(config(), lambda: [Broken(1)])
    with pytest.raises(ValueError, match="bad parameters"):
        simulation.start()
    assert state_of(simulation) is SimulationState.FAILED


# ---- Information boundaries and notification order -------------------------------------------


def test_cancelling_someone_elses_order_is_digested_but_not_revealed() -> None:
    """The participant is told NOT_FOUND either way, yet the digest still records what really
    happened."""

    def run(order_id: int) -> tuple[CancelStatus, str]:
        simulation = Simulation(config(), lambda: [Participant(1), Participant(2)])
        simulation.start()
        simulation.submit_now(1, sell(105, 3))
        report = simulation.cancel_now(2, 0, order_id)
        return report.status, simulation.digest

    (owned_status, owned_digest), (missing_status, missing_digest) = run(1), run(999)
    assert owned_status is missing_status is CancelStatus.NOT_FOUND
    assert owned_digest != missing_digest


class Logger(Participant):
    def __init__(self, pid: int, log: list[str], script: Sequence[tuple[int, OrderIntent]]):
        super().__init__(pid)
        self._log = log
        self._script = list(script)

    def on_start(self, ctx: SimulationContext) -> None:
        for index, (time, _) in enumerate(self._script):
            ctx.wakeup(time, str(index))

    def on_wakeup(self, ctx: SimulationContext, tag: str) -> None:
        ctx.submit(self._script[int(tag)][1])

    def on_order_report(self, ctx: SimulationContext, report: OrderReport) -> None:
        self._log.append(f"{self.participant_id}:report:{report.status.name}")

    def on_fill(self, ctx: SimulationContext, fill: Fill) -> None:
        role = "aggressor" if fill.is_aggressor else "resting"
        self._log.append(f"{self.participant_id}:fill:{fill.order_id}:{role}")

    def on_cancel(self, ctx: SimulationContext, report: CancelReport) -> None:
        self._log.append(f"{self.participant_id}:cancel:{report.order_id}:{report.reason.name}")


def test_notifications_follow_exchange_sequence_with_the_report_last() -> None:
    """Participant 1 rests sells at 100 (order 1) and 101 (order 3); participant 3 queues a sell
    at 100 behind order 1 (order 2). Participant 1 then buys through both prices with
    CANCEL_RESTING: its own order 1 is cancelled, it trades with order 2, its own order 3 is
    cancelled, and the remainder rests. Every callback must arrive in exchange order."""
    log: list[str] = []
    stp = SelfTradePrevention.CANCEL_RESTING
    simulation = Simulation(
        config(),
        lambda: [
            Logger(
                1,
                log,
                [
                    (1 * SECOND, sell(100, 2)),
                    (3 * SECOND, sell(101, 3)),
                    (4 * SECOND, buy(101, 4, self_trade_prevention=stp)),
                ],
            ),
            Logger(3, log, [(2 * SECOND, sell(100, 1))]),
        ],
    )
    simulation.run()
    assert log[3:] == [
        "1:cancel:1:SELF_TRADE_PREVENTION",
        "1:fill:4:aggressor",
        "3:fill:2:resting",
        "1:cancel:3:SELF_TRADE_PREVENTION",
        "1:report:RESTING",
    ]


def test_participants_act_in_id_order_whatever_the_factory_order() -> None:
    def run(order: Sequence[int]) -> tuple[str, list[str]]:
        log: list[str] = []
        scripts = {
            1: [(SECOND, sell(100, 1))],
            2: [(SECOND, buy(100, 1))],
            3: [(SECOND, sell(100, 1))],
        }
        simulation = Simulation(config(), lambda: [Logger(pid, log, scripts[pid]) for pid in order])
        simulation.run()
        return simulation.digest, log

    assert run([1, 2, 3]) == run([3, 2, 1]) == run([2, 3, 1])


def test_the_context_shows_public_data_and_only_its_own_orders() -> None:
    seen: dict[str, object] = {}

    class Observer(Participant):
        def on_start(self, ctx: SimulationContext) -> None:
            ctx.wakeup(2 * SECOND)

        def on_wakeup(self, ctx: SimulationContext, tag: str) -> None:
            seen.update(
                participant_id=ctx.participant_id,
                now=ctx.now,
                end_time=ctx.end_time,
                symbols=ctx.symbols,
                instrument=ctx.instrument_id("ACME"),
                tick_size=ctx.tick_size(0),
                best_bid=ctx.best_bid(0),
                best_ask=ctx.best_ask(0),
                midprice=ctx.midprice(0),
                spread=ctx.spread(0),
                bids=[(level.price, level.quantity) for level in ctx.book(0).bids],
                volume=ctx.statistics(0).volume,
                tape=[(t.price, t.quantity) for t in ctx.recent_trades(0, limit=1)],
                other_order=ctx.own_order(0, 1),
            )

    scripts: list[tuple[int, Sequence[ScriptAction]]] = [
        (1, [ScriptedOrder(SECOND, buy(98, 4)), ScriptedOrder(SECOND, sell(103, 2))]),
        (2, [ScriptedOrder(SECOND, sell(101, 1)), ScriptedOrder(SECOND, buy(101, 1))]),
    ]
    simulation = Simulation(config(), lambda: [*scripted(*scripts)(), Observer(9)])
    simulation.run()
    assert seen == {
        "participant_id": 9,
        "now": 2 * SECOND,
        "end_time": 10 * SECOND,
        "symbols": ("ACME",),
        "instrument": 0,
        "tick_size": 0.01,
        "best_bid": 98,
        "best_ask": 103,
        "midprice": 100.5,
        "spread": 5,
        "bids": [(98, 4)],
        "volume": 1,
        "tape": [(101, 1)],
        "other_order": None,  # Order 1 rests, but it is participant 1's.
    }


# ---- Scripted participants --------------------------------------------------------------------


@pytest.mark.parametrize(("latency", "cancel_at"), [(0, SECOND), (10_000_000, SECOND + 5_000_000)])
def test_a_scripted_cancel_before_its_report_is_sent_once_the_order_is_known(
    latency: int, cancel_at: int
) -> None:
    simulation = Simulation(
        config(order_latency_ns=latency),
        scripted((1, [ScriptedOrder(SECOND, buy(99, 4)), ScriptedCancel(cancel_at, 0)])),
    )
    simulation.run()
    assert simulation.summary().cancels_requested == 1
    assert simulation.exchange.resting_order_count(0) == 0


def test_a_scripted_cancel_of_a_rejected_order_is_not_sent() -> None:
    simulation = Simulation(
        config(), scripted((1, [ScriptedOrder(SECOND, buy(0, 1)), ScriptedCancel(2 * SECOND, 0)]))
    )
    simulation.run()
    script = simulation.participants[1]
    assert isinstance(script, ScriptedParticipant)
    assert script.reports[0].status is OrderStatus.REJECTED
    assert simulation.summary().cancels_requested == 0


def test_scripts_are_validated() -> None:
    with pytest.raises(ValueError, match="before time zero"):
        ScriptedParticipant(1, [ScriptedOrder(-1, buy(99, 1))])
    with pytest.raises(ValueError, match="earlier scripted order"):
        ScriptedParticipant(1, [ScriptedCancel(SECOND, 0)])
    with pytest.raises(ValueError, match="earlier scripted order"):
        ScriptedParticipant(1, [ScriptedOrder(SECOND, buy(99, 1)), ScriptedCancel(SECOND, 1)])


# ---- Input validation ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("delay", "tag", "error"),
    [
        (0.5 * SECOND, "", TypeError),  # A float delay would make the clock a float.
        (True, "", TypeError),
        (-1, "", ValueError),
        (SECOND, None, TypeError),
        (SECOND, "\ud800", UnicodeEncodeError),
    ],
)
def test_wakeups_are_validated_when_scheduled(
    delay: object, tag: object, error: type[Exception]
) -> None:
    holder: list[SimulationContext] = []

    class Holder(Participant):
        def on_start(self, ctx: SimulationContext) -> None:
            holder.append(ctx)

    simulation = Simulation(config(), lambda: [Holder(1)])
    simulation.start()
    with pytest.raises(error):
        holder[0].wakeup(delay, tag)  # type: ignore[arg-type]
    assert simulation.pending_events == 0
    assert state_of(simulation) is SimulationState.RUNNING


def test_guard_rails() -> None:
    simulation = Simulation(config(), lambda: [Participant(1)])
    simulation.start()
    with pytest.raises(ValueError, match="at least 1"):
        simulation.step(0)
    for budget in (0, -3):
        with pytest.raises(ValueError, match="at least 1"):
            simulation.run_until(10 * SECOND, max_events=budget)
    simulation.run_until(SECOND)
    with pytest.raises(ValueError, match="backwards"):
        simulation.run_until(SECOND - 1)
    with pytest.raises(TypeError, match="OrderIntent"):
        simulation.submit_now(1, "buy 1")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="event time -1 is outside"):
        EventQueue().push(-1, None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="seed"):
        config(seed=1 << 64)


# ---- Digest and RNG --------------------------------------------------------------------------


def test_wakeup_tags_cannot_impersonate_other_records() -> None:
    """Without a length prefix, one wakeup whose tag spells out a second wakeup record would hash
    exactly like two wakeups."""

    class Tagger(Participant):
        def __init__(self, tags: Sequence[str]) -> None:
            super().__init__(1)
            self._tags = tags

        def on_start(self, ctx: SimulationContext) -> None:
            for tag in self._tags:
                ctx.wakeup(0, tag)

    forged = "\x03" + "\x00" * 8 + "\x01" + "\x00" * 7 + "x"

    def digest(tags: Sequence[str]) -> str:
        simulation = Simulation(config(), lambda: [Tagger(tags)])
        simulation.start()
        simulation.run_until(SECOND)
        return simulation.digest

    assert digest(["", "x"]) != digest([forged])


def test_stream_labels_are_typed_and_seeds_validated() -> None:
    def first(seed: int, *labels: int | str) -> int:
        return DeterministicRng.derive(seed, *labels).next_u64()

    assert first(5, "participant", 3) != first(5, "participant", "3")
    assert first(5, "a", "bc") != first(5, "ab", "c")
    with pytest.raises(TypeError, match="int or str"):
        DeterministicRng.derive(5, True)
    for seed in (-1, 1 << 64):
        with pytest.raises(ValueError, match="seed"):
            DeterministicRng.derive(seed, "participant", 1)


def test_deterministic_log_is_within_two_ulps() -> None:
    """Against a 60-digit reference, including the inputs near sqrt(1/2) where ln(m) and
    -ln(2) nearly cancel, which the earlier implementation got wrong by 4.5 ulps."""
    getcontext().prec = 60
    rng = random.Random(3)
    samples = [0.6985143491810898, 0.707809861358358, 5e-324, 1.7976931348623157e308]
    samples += [rng.uniform(0.69, 0.72) for _ in range(1500)]
    samples += [rng.uniform(1.39, 1.43) for _ in range(1500)]
    samples += [rng.uniform(0.0, 1.0) or 0.5 for _ in range(1000)]
    for x in samples:
        reference = Decimal(x).ln()
        error = abs(Decimal(deterministic_log(x)) - reference)
        assert error <= 2 * Decimal(math.ulp(float(reference))), x
