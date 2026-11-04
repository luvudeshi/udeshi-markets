"""The observer's tape: every trade names its buyer and seller, consistently with the books."""

from __future__ import annotations

from collections import Counter

import pytest
from apiclient import FROZEN, action, create, get
from fastapi.testclient import TestClient
from test_agents_api import SMALL, session_of
from wsclient import receive_snapshot

from udeshi_api.counterparties import Counterparties
from udeshi_markets import Side
from udeshi_markets.simulation import (
    InstrumentConfig,
    OrderIntent,
    ScriptedOrder,
    ScriptedParticipant,
    Simulation,
    SimulationConfig,
)


def test_the_tape_agrees_with_every_participants_own_books(client: TestClient) -> None:
    """Shares bought and sold per participant, summed over the tape, equal what each one's
    portfolio booked: the tape names the right counterparties for every trade."""
    simulation_id = create(client, agents=SMALL, speed=FROZEN, seed=3)["id"]
    action(client, simulation_id, "start")
    action(client, simulation_id, "pause")
    action(client, simulation_id, "step", events=12_000)
    agents = {a["id"]: a for a in get(client, "/api/agents")}
    bought: Counter[tuple[int, str]] = Counter()
    sold: Counter[tuple[int, str]] = Counter()
    trades = 0
    for symbol in get(client, f"/api/simulations/{simulation_id}")["symbols"]:
        tape = get(client, f"/api/markets/{symbol}/trades", limit=1_000)
        stats = get(client, f"/api/markets/{symbol}")
        assert len(tape) == stats["trade_count"] < 1_000  # The whole tape is checked.
        for trade in tape:
            assert {trade["buyer"], trade["seller"]} <= agents.keys()
            assert trade["buyer"] != trade["seller"]  # Agents prevent self-trades.
            bought[trade["buyer"], symbol] += trade["quantity"]
            sold[trade["seller"], symbol] += trade["quantity"]
        trades += len(tape)
    assert trades > 100
    for agent_id in agents:
        detail = get(client, f"/api/agents/{agent_id}")
        for position in detail["position_details"]:
            key = (agent_id, position["symbol"])
            assert (position["bought"], position["sold"]) == (bought[key], sold[key])


def test_streamed_trades_name_their_counterparties(client: TestClient) -> None:
    simulation_id = create(client, agents=SMALL, speed=FROZEN, seed=4)["id"]
    action(client, simulation_id, "start")
    action(client, simulation_id, "pause")
    action(client, simulation_id, "step", events=20_000)
    with client.websocket_connect(f"/api/simulations/{simulation_id}/stream") as ws:
        snapshot = receive_snapshot(ws)
    ids = {agent["id"] for agent in snapshot["agents"]}
    trades = [trade for market in snapshot["markets"] for trade in market["trades"]]
    assert trades
    assert all(t["buyer"] in ids and t["seller"] in ids for t in trades)


def test_a_reset_forgets_the_previous_runs_counterparties(client: TestClient) -> None:
    simulation_id = create(client, agents=SMALL, speed=FROZEN, seed=5)["id"]
    action(client, simulation_id, "start")
    action(client, simulation_id, "pause")
    action(client, simulation_id, "step", events=20_000)
    session = session_of(client, simulation_id)
    [first] = session.simulation.recorder.trades(0, 1)  # Trade ids are exchange-wide.
    assert session.counterparties.of(0, first.trade_id) is not None
    action(client, simulation_id, "reset")
    # Trade ids restart, so nothing of the previous run may remain.
    assert session.counterparties.of(0, first.trade_id) is None


def test_the_ledger_keeps_only_the_most_recent_trades() -> None:
    one = (InstrumentConfig("AAA", 100.0, tick_size=1.0),)
    seller = ScriptedParticipant(1, [ScriptedOrder(0, OrderIntent(0, Side.SELL, 3, price=100))])
    buyer = ScriptedParticipant(
        2, [ScriptedOrder(t, OrderIntent(0, Side.BUY, 1, price=100)) for t in (1, 2, 3)]
    )
    simulation = Simulation(SimulationConfig(instruments=one), lambda: [seller, buyer])
    ledger = Counterparties(simulation, limit=2)
    simulation.run()
    assert [ledger.of(0, trade_id) for trade_id in (1, 2, 3)] == [None, (2, 1), (2, 1)]
    assert ledger.of(1, 1) is None  # No such instrument.
    with pytest.raises(ValueError, match="at least 1"):
        Counterparties(simulation, limit=0)
