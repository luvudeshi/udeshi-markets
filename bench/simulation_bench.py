"""Benchmarks of the Python layer: the simulation kernel's throughput, and what the live API's
analytics cost as a run gets longer.

    uv run python bench/simulation_bench.py [--quick] [--json PATH]

Every figure is measured with time.perf_counter: the median of three runs on fixed seeds (the
runs are deterministic, so each one does exactly the same work). Short calls (the analytics,
the leaderboard, a runner tick) are timed as timeit times them: after a full garbage collection,
with the collector paused, because a collection of a large live heap would otherwise land on
whichever call happened to trigger it. See docs/benchmarking.md.
"""

from __future__ import annotations

import argparse
import gc
import json
import platform
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

from udeshi_api.hub import Subscriber
from udeshi_api.sessions import LiveSession
from udeshi_api.settings import Settings
from udeshi_markets.agents import PopulationConfig, default_environment, market_simulation
from udeshi_markets.analytics import MarketObserver, analyze, run_observed
from udeshi_markets.simulation import MILLISECOND, MINUTE, SECOND, SimulationConfig

RUNS = 3


def machine() -> dict[str, str]:
    cpu = platform.processor() or platform.machine()
    if sys.platform == "darwin":
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            text=True,
            check=False,
        )
        cpu = result.stdout.strip() or cpu
    elif sys.platform.startswith("linux"):
        with open("/proc/cpuinfo", encoding="utf-8") as cpuinfo:
            for line in cpuinfo:
                if line.startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
    return {
        "cpu": cpu,
        "platform": platform.platform(),
        "python": f"{platform.python_implementation()} {platform.python_version()}",
    }


def timed(work: Callable[[], object]) -> float:
    """Seconds one call takes, after a full collection and with the collector paused."""
    gc.collect()
    gc.disable()
    try:
        start = time.perf_counter()
        work()
        return time.perf_counter() - start
    finally:
        gc.enable()


def median_seconds(work: Callable[[], object], runs: int = RUNS) -> float:
    return statistics.median(timed(work) for _ in range(runs))


def kernel(minutes: int, observed: bool) -> dict[str, Any]:
    """The default market (26 agents, five instruments) run headless for ``minutes``."""
    walls = []
    digests = set()
    summary = None
    for _ in range(RUNS):
        simulation = market_simulation(SimulationConfig(duration_ns=minutes * MINUTE, seed=1))
        start = time.perf_counter()
        if observed:
            run_observed(simulation, MarketObserver(simulation))
        else:
            simulation.run()
        walls.append(time.perf_counter() - start)
        summary = simulation.summary()
        digests.add(summary.digest)
    assert summary is not None
    if len(digests) != 1:
        raise RuntimeError("runs of one seed disagree")  # Determinism, checked as we go.
    wall = statistics.median(walls)
    return {
        "simulated_minutes": minutes,
        "observed": observed,
        "events": summary.events_processed,
        "orders": summary.orders_submitted,
        "trades": summary.trades,
        "wall_seconds": wall,
        "events_per_second": summary.events_processed / wall,
        "orders_per_second": summary.orders_submitted / wall,
        "trades_per_second": summary.trades / wall,
        "simulated_seconds_per_second": minutes * 60 / wall,
        "digest": summary.digest,
    }


def live_session(minutes: int) -> LiveSession:
    """A live session of the default market advanced to ``minutes`` exactly as the live runner
    advances it (stopping the clock for the analytics), publishing every simulated second to one
    subscriber that keeps up."""
    config = SimulationConfig(duration_ns=(minutes + 1) * MINUTE, seed=1)
    session = LiveSession(
        "bench",
        config,
        1.0,
        Settings(),
        population=PopulationConfig(),
        environment=default_environment(config),
    )
    subscriber = session.hub.subscribe(None)
    simulation = session.simulation
    simulation.start()
    while simulation.now < minutes * MINUTE:
        session.runner._advance(simulation, min(simulation.now + SECOND, minutes * MINUTE))
        session.publish_changes()
        drain(subscriber)
    session.hub.unsubscribe(subscriber)
    return session


def drain(subscriber: Subscriber) -> None:
    """Empties a subscriber's queue, as a client that keeps up would."""
    while not subscriber.queue.empty():
        subscriber.queue.get_nowait()


def live_costs(minutes: int) -> dict[str, Any]:
    session = live_session(minutes)
    observer = session.observer
    assert observer is not None
    participants = list(session.simulation.participants)

    def sharpe_all() -> None:
        for pid in participants:
            session._sharpe(pid)

    # One runner tick (50 ms of simulated time at speed 1) and its publication to one client.
    # The leaderboard goes out at most once per wall second and is measured on its own, so it
    # is held back here.
    session.clock = lambda: 0.0
    session._leaderboard_at = 0.0
    subscriber = session.hub.subscribe(None)
    drain(subscriber)  # Its first message is the snapshot.
    simulation = session.simulation

    def tick() -> None:
        session.runner._advance(simulation, simulation.now + 50 * MILLISECOND)
        session.publish_changes()

    ticks = []
    for _ in range(20):
        ticks.append(timed(tick))
        drain(subscriber)
    return {
        "simulated_minutes": minutes,
        "samples": len(observer.times),
        "trades": len(observer.trades),
        "participants": len(participants),
        "analyze_ms": median_seconds(lambda: analyze(observer)) * 1e3,
        "analytics_endpoint_ms": median_seconds(session.analytics) * 1e3,
        "sharpe_all_participants_ms": median_seconds(sharpe_all) * 1e3,
        "leaderboard_ms": median_seconds(session.agents) * 1e3,
        "tick_ms": statistics.median(ticks) * 1e3,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--quick", action="store_true", help="short runs, for a smoke test")
    parser.add_argument("--json", help="also write the results to this file")
    arguments = parser.parse_args()
    kernel_minutes = 2 if arguments.quick else 10
    lengths = (2, 5) if arguments.quick else (10, 30, 120)

    results: dict[str, Any] = {"machine": machine(), "quick": arguments.quick, "runs": RUNS}
    print(f"{results['machine']['cpu']} · {results['machine']['python']}\n")

    results["kernel"] = [kernel(kernel_minutes, observed) for observed in (False, True)]
    print(f"{'kernel':<24}{'events/s':>12}{'orders/s':>12}{'trades/s':>12}{'sim s/s':>10}")
    for row in results["kernel"]:
        label = "with analytics observer" if row["observed"] else "headless"
        print(
            f"{label:<24}{row['events_per_second']:>12,.0f}{row['orders_per_second']:>12,.0f}"
            f"{row['trades_per_second']:>12,.0f}{row['simulated_seconds_per_second']:>10,.0f}"
        )

    results["live"] = [live_costs(minutes) for minutes in lengths]
    print(
        f"\n{'live, after':<14}{'analyze':>10}{'endpoint':>10}{'sharpe':>10}"
        f"{'leaderboard':>13}{'tick':>8}   (ms)"
    )
    for row in results["live"]:
        print(
            f"{str(row['simulated_minutes']) + ' min':<14}{row['analyze_ms']:>10.2f}"
            f"{row['analytics_endpoint_ms']:>10.2f}{row['sharpe_all_participants_ms']:>10.2f}"
            f"{row['leaderboard_ms']:>13.2f}{row['tick_ms']:>8.2f}"
        )

    if arguments.json:
        with open(arguments.json, "w", encoding="utf-8") as out:
            json.dump(results, out, indent=2)
            out.write("\n")
        print(f"\nwrote {arguments.json}")


if __name__ == "__main__":
    main()
