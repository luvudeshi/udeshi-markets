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

from udeshi_markets.simulation import SECOND, Simulation, SimulationState

logger = logging.getLogger("udeshi_api.runner")

_FINISHED = frozenset({SimulationState.STOPPED, SimulationState.COMPLETED})


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

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

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
                    simulation.run_until(simulation.now + advance, max_events=self._max_events)
                self._session.publish_changes()
                if simulation.state in _FINISHED:
                    logger.info("simulation %s %s", self._session.id, simulation.state.value)
                    return
        except asyncio.CancelledError:
            raise
        except Exception as error:  # A participant or engine failure ends this session only.
            logger.exception("live runner for simulation %s failed", self._session.id)
            self._session.record_failure(error)
