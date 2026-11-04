"""Analytics of live simulations: the observer, the analytics endpoint and the leaderboard's
Sharpe ratios."""

from __future__ import annotations

import time
from typing import Any

import pytest
from apiclient import ONE_MARKET, action, create, get
from fastapi.testclient import TestClient

from udeshi_api.runner import LiveRunner
from udeshi_api.sessions import SessionManager
from udeshi_markets.agents import market_simulation
from udeshi_markets.analytics import MarketObserver
from udeshi_markets.simulation import SECOND, Simulation, SimulationConfig

AGENTS: dict[str, Any] = {"agents": {}, "speed": 200}  # The default population of 26 agents.


def run_for(client: TestClient, simulation_id: str, simulated_seconds: float) -> None:
    deadline = time.monotonic() + 20
    while get(client, f"/api/simulations/{simulation_id}")["time_seconds"] < simulated_seconds:
        assert time.monotonic() < deadline, "the simulation did not advance"
        time.sleep(0.02)


def test_analytics_of_a_live_market(client: TestClient) -> None:
    simulation_id = create(client, **AGENTS)["id"]
    action(client, simulation_id, "start")
    run_for(client, simulation_id, 60)
    analytics = get(client, f"/api/simulations/{simulation_id}/analytics")
    assert analytics["interval_seconds"] == 1.0
    assert analytics["markout_seconds"] == 5.0
    assert analytics["samples"] >= 50
    symbols = [m["symbol"] for m in analytics["market"]]
    assert symbols == ["ACME", "NOVA", "QUANT", "ORBIT", "VECTOR"]
    assert all(m["volume"] > 0 for m in analytics["market"])
    assert all(m["mean_spread_bps"] is not None for m in analytics["market"])
    micro = analytics["microstructure"]
    assert all(m["measured_trades"] > 0 for m in micro)
    for m in micro:  # Effective = realized + impact, trade by trade, so also on average.
        total = m["realized_spread_bps"] + m["price_impact_bps"]
        assert m["effective_spread_bps"] == pytest.approx(total)
    strategies = {a["strategy"] for a in analytics["agents"]}
    assert {"random", "market_maker", "fundamental", "environment", "other"} <= strategies
    board = get(client, "/api/agents", simulation_id=simulation_id)
    assert any(agent["sharpe_ratio"] is not None for agent in board)


def test_live_samples_are_taken_exactly_at_their_times() -> None:
    """The runner stops the clock at every sampling instant and markout horizon, as the
    experiment runner does, rather than sampling wherever a tick happens to end."""

    class Session:
        def __init__(self, simulation: Simulation) -> None:
            self._simulation = simulation
            self.observer = MarketObserver(simulation)

        id = "exact"

        @property
        def simulation(self) -> Simulation:
            return self._simulation

        @property
        def speed(self) -> float:
            return 1.0

        def publish_changes(self) -> None:
            pass

        def record_failure(self, error: BaseException) -> None:
            raise error

    simulation = market_simulation(SimulationConfig(duration_ns=30 * SECOND, seed=3))
    session = Session(simulation)
    runner = LiveRunner(session, tick_seconds=1.0, max_events_per_tick=1_000_000)
    simulation.start()
    for target in (2_500_000_000, 6_250_000_000, 9_000_000_000):  # Ticks of irregular length.
        runner._advance(simulation, target)
    observer = session.observer
    assert observer.times == [k * SECOND for k in range(10)]
    due = [t.time + observer.markout_ns for t in observer.trades]
    measured = [i for i in range(len(due)) if due[i] <= simulation.now]
    assert set(observer.mid_after) == set(measured)


def test_analytics_need_a_market(client: TestClient) -> None:
    simulation_id = create(client, **ONE_MARKET)["id"]
    response = client.get(f"/api/simulations/{simulation_id}/analytics")
    assert (response.status_code, response.json()["error"]["code"]) == (409, "no_analytics")
    board = get(client, "/api/agents", simulation_id=simulation_id)
    assert all(agent["sharpe_ratio"] is None for agent in board)


@pytest.fixture
def manager(client: TestClient) -> SessionManager:
    sessions = client.app.state.sessions  # type: ignore[attr-defined]
    assert isinstance(sessions, SessionManager)
    return sessions


def test_a_reset_starts_the_analytics_afresh(client: TestClient, manager: SessionManager) -> None:
    simulation_id = create(client, **AGENTS)["id"]
    action(client, simulation_id, "start")
    run_for(client, simulation_id, 20)
    session = manager.get(simulation_id)
    observer = session.observer
    assert isinstance(observer, MarketObserver)
    assert observer.trades
    action(client, simulation_id, "reset")
    assert session.observer is observer  # The same observer, reset with the simulation...
    assert observer.trades == []  # ...so nothing of the previous run remains.
    # Exactly one of each is registered: the counterparty ledger and the analytics.
    assert session.simulation._observers == [session.counterparties, observer]
    analytics = get(client, f"/api/simulations/{simulation_id}/analytics")
    assert analytics["samples"] == 0
    assert all(m["volume"] == 0 for m in analytics["market"])
