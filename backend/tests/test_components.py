"""Unit tests for the live runner, the stream hub and serialization helpers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator

import pytest

from udeshi_api.hub import StreamHub
from udeshi_api.runner import LiveRunner
from udeshi_api.serialization import PriceFormat, aggregate_bars
from udeshi_markets.simulation import (
    SECOND,
    Bar,
    InstrumentConfig,
    Participant,
    Simulation,
    SimulationConfig,
    SimulationContext,
    SimulationState,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeSession:
    """Implements the runner's Paced protocol around a real simulation."""

    def __init__(self, simulation: Simulation, speed: float) -> None:
        self._simulation = simulation
        self._speed = speed
        self.publishes = 0
        self.failures: list[BaseException] = []

    @property
    def id(self) -> str:
        return "test"

    @property
    def simulation(self) -> Simulation:
        return self._simulation

    @property
    def speed(self) -> float:
        return self._speed

    def publish_changes(self) -> None:
        self.publishes += 1

    def record_failure(self, error: BaseException) -> None:
        self.failures.append(error)


def fake_clock(step: float) -> Iterator[float]:
    value = 0.0
    while True:
        yield value
        value += step


def make_simulation(participants: list[Participant] | None = None) -> Simulation:
    config = SimulationConfig(instruments=(InstrumentConfig("ACME", 1.0),), duration_ns=10 * SECOND)
    simulation = Simulation(config, lambda: participants or [], track_changes=True)
    simulation.start()
    return simulation


async def test_runner_advances_by_measured_elapsed_time_times_speed() -> None:
    session = FakeSession(make_simulation(), speed=2.0)
    ticks = fake_clock(1.0)  # Each tick "measures" one wall-clock second.
    runner = LiveRunner(
        session, tick_seconds=0.001, max_events_per_tick=1000, clock=lambda: next(ticks)
    )
    runner.start()
    for _ in range(1000):
        await asyncio.sleep(0.001)
        if not runner.running:
            break
    assert not runner.running
    assert session.simulation.state is SimulationState.COMPLETED
    assert session.simulation.now == 10 * SECOND
    assert session.publishes == 5  # 2 simulated seconds per tick, 10 seconds in total.
    assert session.failures == []


async def test_runner_does_not_advance_a_paused_simulation() -> None:
    session = FakeSession(make_simulation(), speed=1.0)
    session.simulation.pause()
    ticks = fake_clock(1.0)
    runner = LiveRunner(
        session, tick_seconds=0.001, max_events_per_tick=1000, clock=lambda: next(ticks)
    )
    runner.start()
    await asyncio.sleep(0.05)
    assert session.simulation.now == 0
    assert session.publishes > 0
    await runner.stop()
    assert not runner.running


async def test_runner_records_participant_failures() -> None:
    class Broken(Participant):
        def on_start(self, ctx: SimulationContext) -> None:
            ctx.wakeup(SECOND)

        def on_wakeup(self, ctx: SimulationContext, tag: str) -> None:
            raise RuntimeError("strategy bug")

    session = FakeSession(make_simulation([Broken(1)]), speed=10.0)
    ticks = fake_clock(1.0)
    runner = LiveRunner(
        session, tick_seconds=0.001, max_events_per_tick=1000, clock=lambda: next(ticks)
    )
    runner.start()
    for _ in range(1000):
        await asyncio.sleep(0.001)
        if not runner.running:
            break
    assert [str(error) for error in session.failures] == ["strategy bug"]


async def test_hub_filters_by_symbol_and_drops_oldest_when_full() -> None:
    hub = StreamHub(queue_size=2)
    everything = hub.subscribe(None)
    acme_only = hub.subscribe(frozenset({"ACME"}))
    for index in range(3):
        hub.publish({"n": index}, "ACME")
    hub.publish({"n": "nova"}, "NOVA")
    hub.publish({"n": "all"})
    assert [json.loads(everything.queue.get_nowait())["n"] for _ in range(2)] == ["nova", "all"]
    assert everything.dropped == 3
    assert [json.loads(acme_only.queue.get_nowait())["n"] for _ in range(2)] == [2, "all"]
    assert acme_only.dropped == 2
    hub.unsubscribe(acme_only)
    assert hub.subscriber_count == 1


def test_price_format_rounds_to_the_tick_grid() -> None:
    nickel = PriceFormat(0.05)
    assert nickel.places == 2
    assert nickel.price(3) == 0.15
    assert nickel.fractional(2.5) == 0.125
    assert nickel.maybe_price(None) is None
    assert PriceFormat(1.0).places == 0
    assert PriceFormat(1e-12).places == 12
    assert PriceFormat(0.0001).price(750_001) == 75.0001


def _bar(start: int, close: int, volume: int) -> Bar:
    return Bar(0, start, start + 10, close, close, close, close, volume, volume, close * volume)


def test_aggregate_bars_aligns_to_the_wider_interval() -> None:
    bars = [_bar(start, close, vol) for start, close, vol in [(0, 5, 1), (10, 7, 2), (20, 6, 0)]]
    merged = aggregate_bars(bars, 2)
    assert [(b.start, b.end, b.open, b.high, b.low, b.close, b.volume) for b in merged] == [
        (0, 20, 5, 7, 5, 7, 3),
        (20, 30, 6, 6, 6, 6, 0),
    ]
    assert aggregate_bars(bars, 1) == bars
    assert aggregate_bars([], 3) == []
    with pytest.raises(ValueError, match="at least 1"):
        aggregate_bars(bars, 0)
