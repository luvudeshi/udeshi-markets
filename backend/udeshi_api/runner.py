"""Real-time pacing of a simulation for live viewing.

The simulation kernel has no notion of wall-clock time. The runner is a presentation layer on
top: every tick it measures the wall-clock time actually elapsed (not the nominal sleep), asks
the simulation to process events up to ``now + elapsed * speed``, and publishes what changed.
Headless runs and experiments never use it. If the simulation cannot keep up with the requested
speed (the per-tick event cap is reached), it simply falls behind; correctness is unaffected
because event order never depends on pacing.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Protocol

from udeshi_markets.analytics import MarketObserver
from udeshi_markets.simulation import SECOND, Simulation, SimulationState

logger = logging.getLogger("udeshi_api.runner")

MAX_OBSERVER_STOPS = 100
"""Clock stops for analytics in one tick (one per sampling instant or markout horizon)."""

_FINISHED = frozenset({SimulationState.STOPPED, SimulationState.COMPLETED, SimulationState.FAILED})


class Paced(Protocol):
    """What the runner needs from a live session."""

    @property
    def id(self) -> str: ...
    @property
    def simulation(self) -> Simulation: ...
    @property
    def speed(self) -> float: ...
    def publish_changes(self) -> None: ...
    def record_failure(self, error: BaseException) -> None: ...


class LiveRunner:
    def __init__(
        self,
        session: Paced,
        *,
        tick_seconds: float,
        max_events_per_tick: int,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._session = session
        self._tick = tick_seconds
        self._max_events = max_events_per_tick
        self._clock = clock
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if not self.running:
            self._task = asyncio.create_task(self._run(), name=f"simulation-{self._session.id}")

    def cancel(self) -> asyncio.Task[None] | None:
        """Requests cancellation without waiting. Returns the task if it was still running.
        The runner never awaits between advancing the simulation and publishing, so it cannot
        be interrupted half-way through a tick."""
        task, self._task = self._task, None
        if task is None or task.done():
            return None
        task.cancel()
        return task

    async def stop(self) -> None:
        task = self.cancel()
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def _advance(self, simulation: Simulation, target: int) -> None:
        """Advances to ``target``, processing at most the per-tick event cap. With an analytics
        observer, the clock also stops at each of its sampling times and markout horizons (up to
        MAX_OBSERVER_STOPS a tick; beyond that, at very high speeds, samples thin out)."""
        observer = getattr(self._session, "observer", None)
        if not isinstance(observer, MarketObserver):
            simulation.run_until(target, max_events=self._max_events)
            return
        budget = self._max_events
        for _ in range(MAX_OBSERVER_STOPS):
            if simulation.state is not SimulationState.RUNNING or budget <= 0:
                break
            stop = min(target, observer.next_stop())
            budget -= simulation.run_until(stop, max_events=budget)
            observer.observe()
            if simulation.now < stop or simulation.now >= target:
                break  # The event cap stopped it early, or the tick is done.
        else:
            if simulation.state is SimulationState.RUNNING and budget > 0:
                simulation.run_until(target, max_events=budget)
                observer.observe()
        if simulation.state in _FINISHED:
            observer.finish()

    async def _run(self) -> None:
        clock = self._clock or asyncio.get_running_loop().time
        last = clock()
        try:
            while True:
                await asyncio.sleep(self._tick)
                now = clock()
                elapsed, last = now - last, now
                simulation = self._session.simulation
                if simulation.state is SimulationState.RUNNING:
                    advance = round(elapsed * self._session.speed * SECOND)
                    self._advance(simulation, simulation.now + advance)
                self._session.publish_changes()
                if simulation.state in _FINISHED:
                    logger.info("simulation %s %s", self._session.id, simulation.state.value)
                    return
        except asyncio.CancelledError:
            raise
        except Exception as error:  # A participant or engine failure ends this session only.
            logger.exception("live runner for simulation %s failed", self._session.id)
            self._session.record_failure(error)
