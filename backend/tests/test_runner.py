"""The live runner's pacing across pause and resume, its per-tick event cap, and its task."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator

import pytest

from udeshi_api.runner import LiveRunner
from udeshi_api.sessions import LiveSession
from udeshi_api.settings import Settings
from udeshi_markets.simulation import (
    MILLISECOND,
    SECOND,
    InstrumentConfig,
    Participant,
    Simulation,
    SimulationConfig,
    SimulationContext,
    SimulationState,
)

pytestmark = pytest.mark.anyio

CONFIG = SimulationConfig(instruments=(InstrumentConfig("ACME", 1.0),), duration_ns=100 * SECOND)


def ticking_clock() -> Callable[[], float]:
    """Each call is one more wall-clock second."""
    values: Iterator[float] = iter(range(1_000_000))
    return lambda: float(next(values))


class ScriptedSession:
    """The runner's Paced protocol around a real simulation. On each tick it records the
    simulation's state, then runs that tick's scripted action, if any."""

    def __init__(
        self, simulation: Simulation, script: dict[int, Callable[[Simulation], None]], ticks: int
    ) -> None:
        self._simulation = simulation
        self._script = script
        self._ticks = ticks
        self.seen: list[tuple[int, int]] = []  # (simulated time, events processed) per tick.
        self.done = asyncio.Event()

    @property
    def id(self) -> str:
        return "scripted"

    @property
    def simulation(self) -> Simulation:
        return self._simulation

    @property
    def speed(self) -> float:
        return 1.0

    def publish_changes(self) -> None:
        if self.done.is_set():
            return
        self.seen.append((self._simulation.now, self._simulation.summary().events_processed))
        action = self._script.get(len(self.seen))
        if action is not None:
            action(self._simulation)
        if len(self.seen) == self._ticks:
            self.done.set()

    def record_failure(self, error: BaseException) -> None:
        raise AssertionError("the runner failed") from error


async def run_ticks(session: ScriptedSession, max_events_per_tick: int = 1000) -> None:
    runner = LiveRunner(
        session,
        tick_seconds=0.001,
        max_events_per_tick=max_events_per_tick,
        clock=ticking_clock(),
    )
    runner.start()
    await asyncio.wait_for(session.done.wait(), timeout=10)
    await runner.stop()


async def test_paused_wall_time_is_never_credited_to_the_simulation() -> None:
    simulation = Simulation(CONFIG, track_changes=True)
    simulation.start()
    session = ScriptedSession(simulation, {3: Simulation.pause, 6: Simulation.resume}, ticks=8)
    await run_ticks(session)
    # Three running ticks of one second, three paused ones, then two more running ones: the
    # three paused seconds must not reappear as a jump when the simulation resumes.
    assert [now // SECOND for now, _ in session.seen] == [1, 2, 3, 3, 3, 3, 4, 5]


async def test_each_tick_processes_at_most_the_event_cap() -> None:
    class Busy(Participant):
        def on_start(self, ctx: SimulationContext) -> None:
            for index in range(1, 51):
                ctx.wakeup(index * MILLISECOND)

    simulation = Simulation(CONFIG, lambda: [Busy(1)], track_changes=True)
    simulation.start()
    session = ScriptedSession(simulation, {}, ticks=3)
    await run_ticks(session, max_events_per_tick=10)
    # Each one-second tick could reach all 50 events; the cap allows 10, and the clock stays
    # at the last processed event instead of jumping to the tick's target time.
    assert session.seen == [(10 * MILLISECOND, 10), (20 * MILLISECOND, 20), (30 * MILLISECOND, 30)]
    assert simulation.state is SimulationState.RUNNING


async def test_starting_a_running_runner_keeps_one_task() -> None:
    session = LiveSession("single", CONFIG, 1.0, Settings(tick_seconds=0.01))

    def runner_tasks() -> list[asyncio.Task[object]]:
        return [t for t in asyncio.all_tasks() if t.get_name() == "simulation-single"]

    session.start()
    session.pause()
    session.resume()  # Starts the runner again, which must be a no-op while it runs.
    session.runner.start()
    assert len(runner_tasks()) == 1
    await session.close()
    await asyncio.sleep(0)
    assert [t for t in runner_tasks() if not t.done()] == []
