"""Agents, the market environment and news through the API (Phase 3)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from apiclient import FROZEN, ONE_MARKET, action, create, error_code, get, manager, order
from fastapi.testclient import TestClient
from wsclient import receive_snapshot, receive_until

from udeshi_api.manual import MANUAL_TRADER_ID
from udeshi_api.sessions import LiveSession
from udeshi_markets.simulation import ENVIRONMENT_ID

SMALL = {
    "random_traders": 4,
    "market_makers": 2,
    "momentum_traders": 2,
    "fundamental_traders": 2,
    "pairs_traders": 1,
}
SYMBOLS = ["ACME", "NOVA", "QUANT", "ORBIT", "VECTOR"]
NEWSY = {
    "volatility": 0.0,
    "news_per_hour": 360.0,
    "shocks_per_hour": 0.0,
    "liquidity_shocks_per_hour": 0.0,
}


def run_market(client: TestClient, events: int = 60_000, **body: Any) -> str:
    """A simulation advanced by ``events`` events while paused: deterministic, since at the
    frozen speed the live runner never moves the clock."""
    simulation_id: str = create(client, speed=FROZEN, **body)["id"]
    action(client, simulation_id, "start")
    action(client, simulation_id, "pause")
    action(client, simulation_id, "step", events=events)
    return simulation_id


def session_of(client: TestClient, simulation_id: str) -> LiveSession:
    return manager(client).get(simulation_id)


# ---- Creation and the participant list -----------------------------------------------------


def test_agents_come_with_a_market_and_are_listed_in_id_order(client: TestClient) -> None:
    created = create(client, agents=SMALL, speed=FROZEN)
    assert created["agents"] == {**SMALL, "order_frequency": 1.0}
    assert created["initial_cash"] == 1_000_000.0
    # No market given: the default environment, with the default ACME/NOVA pair.
    assert created["market"]["pairs"] == [
        {"leader": "ACME", "follower": "NOVA", "half_life_seconds": 120.0,
         "deviation_volatility": 0.01}
    ]  # fmt: skip
    agents = get(client, "/api/agents")
    assert [a["id"] for a in agents] == [*range(1, 12), ENVIRONMENT_ID, MANUAL_TRADER_ID]
    assert [a["strategy"] for a in agents] == [
        *["market_maker"] * 2,
        *["random"] * 4,
        *["momentum"] * 2,
        *["fundamental"] * 2,
        "pairs",
        "environment",
        "manual",
    ]
    assert agents[0]["symbols"] == ["ACME"]  # Round-robin: market maker k trades symbol k.
    assert agents[1]["symbols"] == ["NOVA"]
    assert agents[10]["symbols"] == ["ACME", "NOVA"]  # The pairs trader trades both legs.
    assert agents[-1]["symbols"] == agents[-2]["symbols"] == SYMBOLS
    for agent in agents:  # Nothing has happened yet.
        assert agent["cash"] == agent["equity"] == 1_000_000.0
        assert (agent["total_pnl"], agent["realized_pnl"], agent["unrealized_pnl"]) == (0, 0, 0)
        assert agent["return_pct"] == 0.0
        assert set(agent["positions"].values()) == {0}
        assert (agent["trades"], agent["fills"], agent["filled_quantity"]) == (0, 0, 0)


def test_a_market_without_agents_has_only_the_environment_and_the_user(client: TestClient) -> None:
    created = create(client, market={"volatility": 0.05})
    assert created["agents"] is None
    assert created["market"]["volatility"] == 0.05
    assert [a["strategy"] for a in get(client, "/api/agents")] == ["environment", "manual"]


def test_without_agents_or_market_only_the_user_trades(client: TestClient) -> None:
    created = create(client, **ONE_MARKET)
    assert (created["agents"], created["market"]) == (None, None)
    assert [a["id"] for a in get(client, "/api/agents")] == [MANUAL_TRADER_ID]
    assert get(client, "/api/news") == []


def test_no_default_pair_without_acme_and_nova(client: TestClient) -> None:
    created = create(client, **ONE_MARKET, agents={**SMALL, "pairs_traders": 0})
    assert created["market"]["pairs"] == []


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"agents": {"pairs_traders": 1}, "market": {"pairs": []}}, "invalid_configuration"),
        ({**ONE_MARKET, "agents": {}}, "invalid_configuration"),  # Pairs trader, no pair.
        ({"market": {"pairs": [{"leader": "ACME", "follower": "ZZZ"}]}}, "invalid_configuration"),
        ({"market": {"pairs": [{"leader": "ACME", "follower": "ACME"}]}}, "invalid_configuration"),
        (
            {
                "market": {
                    "pairs": [
                        {"leader": "ACME", "follower": "NOVA"},
                        {"leader": "QUANT", "follower": "NOVA"},
                    ]
                }
            },
            "invalid_configuration",
        ),  # The same follower in two pairs.
        ({"agents": {"random_traders": 501}}, "validation_error"),
        ({"agents": {"random_traders": 300, "market_makers": 201}}, "validation_error"),
        ({"agents": {"order_frequency": 0}}, "validation_error"),
        ({"agents": {"surprise": 1}}, "validation_error"),
        ({"market": {"volatility": -1}}, "validation_error"),
        ({"market": {"news_impact": 2}}, "validation_error"),
        ({"market": {"liquidity_shock_quantity": 0}}, "validation_error"),
        ({"initial_cash": -1}, "validation_error"),
    ],
)
def test_market_configuration_is_validated(
    client: TestClient, body: dict[str, Any], code: str
) -> None:
    response = client.post("/api/simulations", json=body)
    assert response.status_code == 422, response.text
    assert error_code(response) == code


def test_non_finite_market_parameters_are_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/simulations",
        content=b'{"market": {"news_per_hour": Infinity}}',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    assert error_code(response) == "validation_error"


# ---- Accounting --------------------------------------------------------------------------------


def test_portfolios_conserve_cash_and_shares_and_add_up(client: TestClient) -> None:
    simulation_id = run_market(client, agents=SMALL)
    summary = get(client, f"/api/simulations/{simulation_id}")["summary"]
    agents = get(client, "/api/agents")
    assert summary["trades"] > 100
    count = len(agents)
    # Trades move cash and shares between participants, never create them.
    assert sum(a["cash"] for a in agents) == pytest.approx(count * 1_000_000.0, abs=0.01)
    assert sum(a["total_pnl"] for a in agents) == pytest.approx(0.0, abs=0.01)
    for symbol in SYMBOLS:
        assert sum(a["positions"].get(symbol, 0) for a in agents) == 0
    # Each trade is booked twice: once for the buyer, once for the seller.
    assert sum(a["trades"] for a in agents) == 2 * summary["trades"]
    assert sum(a["filled_quantity"] for a in agents) == 2 * summary["volume"]
    for a in agents:
        assert a["realized_pnl"] + a["unrealized_pnl"] == pytest.approx(a["total_pnl"], abs=1e-3)
        assert a["equity"] == pytest.approx(1_000_000.0 + a["total_pnl"], abs=1e-3)
        assert a["return_pct"] == pytest.approx(a["total_pnl"] / 10_000.0, abs=1e-5)
    # Agents record their own fills; the portfolio books the same executions.
    for a in agents:
        if a["strategy"] not in ("manual", "environment"):
            assert a["fills"] == a["trades"]


def test_zero_initial_cash_has_no_return(client: TestClient) -> None:
    create(client, agents=SMALL, initial_cash=0, speed=FROZEN)
    assert {a["return_pct"] for a in get(client, "/api/agents")} == {None}


def test_agent_detail_by_strategy(client: TestClient) -> None:
    run_market(client, agents=SMALL)
    listed = {a["id"]: a for a in get(client, "/api/agents")}
    for agent_id, summary in listed.items():
        detail = get(client, f"/api/agents/{agent_id}")
        assert {k: detail[k] for k in summary} == summary  # Same marks: same numbers.
        held = {p["symbol"]: p["quantity"] for p in detail["position_details"]}
        assert held == summary["positions"]
        assert sum(p["realized_pnl"] for p in detail["position_details"]) == pytest.approx(
            summary["realized_pnl"], abs=1e-3
        )
        assert sum(p["unrealized_pnl"] for p in detail["position_details"]) == pytest.approx(
            summary["unrealized_pnl"], abs=1e-3
        )
        assert len(detail["recent_orders"]) <= 50
        for record in (*detail["open_orders"], *detail["recent_orders"]):
            assert record["symbol"] in summary["symbols"]
        if summary["strategy"] in ("manual", "environment"):
            assert detail["stats"] is None
        else:
            assert detail["stats"]["fills"] == summary["fills"]
            assert detail["stats"]["orders_submitted"] == summary["orders_submitted"]
    maker = get(client, "/api/agents/1")
    assert set(maker["metrics"]) == {"spread_earned", "adverse_selection", "markouts_measured"}
    assert maker["stats"]["passive_fills"] > 0
    assert all(o["symbol"] == "ACME" for o in maker["open_orders"])
    environment = get(client, f"/api/agents/{ENVIRONMENT_ID}")
    assert set(environment["metrics"]) == {
        "news_published",
        "shocks",
        "liquidity_shocks",
        "regime_changes",
        "activity",
    }
    assert environment["open_orders"] == environment["recent_orders"] == []
    assert get(client, f"/api/agents/{MANUAL_TRADER_ID}")["metrics"] == {}


def test_unknown_agent(client: TestClient) -> None:
    create(client, agents=SMALL)
    for agent_id in (0, 12, 424242):
        response = client.get(f"/api/agents/{agent_id}")
        assert response.status_code == 404
        assert error_code(response) == "unknown_agent"


def test_the_api_user_is_a_participant_with_its_own_book(client: TestClient) -> None:
    simulation_id = create(client, **ONE_MARKET, speed=FROZEN)["id"]
    action(client, simulation_id, "start")
    action(client, simulation_id, "pause")
    resting = order(client, "ACME", side="buy", price=0.99, quantity=3)
    order(client, "ACME", side="sell", type="market", quantity=1)  # Trades with itself.
    order(client, "ACME", side="buy", price=0.50, quantity=1, time_in_force="ioc")
    [me] = get(client, "/api/agents")
    assert me["strategy"] == "manual"
    assert (me["orders_submitted"], me["trades"], me["fills"], me["filled_quantity"]) == (
        3,
        2,
        2,
        2,
    )
    assert me["positions"] == {"ACME": 0}  # Bought 1 from itself and sold it.
    assert me["cash"] == 1_000_000.0
    detail = get(client, f"/api/agents/{MANUAL_TRADER_ID}")
    assert [o["order_id"] for o in detail["open_orders"]] == [resting["order_id"]]
    assert detail["open_orders"][0]["remaining_quantity"] == 2
    assert [(r["type"], r["status"], r["time_in_force"]) for r in detail["recent_orders"]] == [
        ("limit", "resting", "gtc"),
        ("market", "filled", "gtc"),
        ("limit", "cancelled", "ioc"),
    ]
    assert detail["position_details"][0]["bought"] == detail["position_details"][0]["sold"] == 1


def test_reset_rebuilds_the_agents(client: TestClient) -> None:
    simulation_id = run_market(client, agents=SMALL)
    assert any(a["trades"] for a in get(client, "/api/agents"))
    action(client, simulation_id, "reset")
    agents = get(client, "/api/agents")
    assert [a["id"] for a in agents] == [*range(1, 12), ENVIRONMENT_ID, MANUAL_TRADER_ID]
    assert {(a["trades"], a["cash"]) for a in agents} == {(0, 1_000_000.0)}


# ---- Fundamental values and news ---------------------------------------------------------------


def test_fundamental_values_are_shown_to_observers_only_with_an_environment(
    client: TestClient,
) -> None:
    create(client, **ONE_MARKET)
    assert get(client, "/api/markets")[0]["fundamental_value"] is None
    assert get(client, "/api/markets/ACME")["fundamental_value"] is None
    simulation_id = create(client, market={"volatility": 0.5}, speed=FROZEN)["id"]
    # Before the start the environment sits at the reference prices.
    assert [m["fundamental_value"] for m in get(client, "/api/markets")] == [
        100.0, 42.5, 250.0, 18.75, 73.2
    ]  # fmt: skip
    action(client, simulation_id, "start")
    action(client, simulation_id, "pause")
    action(client, simulation_id, "step", events=300)
    environment = session_of(client, simulation_id).simulation.environment
    assert environment is not None
    acme = get(client, "/api/markets/ACME")["fundamental_value"]
    assert acme == round(environment.fundamental(0), 4)
    assert acme != 100.0


def test_news_is_published_and_listed(client: TestClient) -> None:
    simulation_id = run_market(
        client, events=1_000_000, duration_seconds=600, market={**NEWSY, "pairs": []}
    )
    assert get(client, f"/api/simulations/{simulation_id}")["state"] == "completed"
    news = get(client, "/api/news", limit=1_000)
    published = get(client, f"/api/agents/{ENVIRONMENT_ID}")["metrics"]["news_published"]
    assert 100 < len(news) == published  # About 600 s x 0.1/s x 5 symbols.
    assert [n["time_seconds"] for n in news] == sorted(n["time_seconds"] for n in news)
    for item in news:
        assert item["symbol"] in SYMBOLS
        assert item["sentiment"] == ("positive" if item["impact"] > 0 else "negative")
        assert item["headline"].startswith(item["symbol"])
    assert get(client, "/api/news", limit=5) == news[-5:]
    assert client.get("/api/news", params={"limit": 0}).status_code == 422


# ---- Stream --------------------------------------------------------------------------------------


def test_snapshot_carries_the_leaderboard_and_recent_news(client: TestClient) -> None:
    simulation_id = run_market(client, events=4_000, agents=SMALL, market=NEWSY)
    agents = get(client, "/api/agents")
    news = get(client, "/api/news", limit=20)
    assert news
    with client.websocket_connect(f"/api/simulations/{simulation_id}/stream") as ws:
        snapshot = receive_snapshot(ws)
        assert snapshot["agents"] == agents
        assert snapshot["news"] == news
    with client.websocket_connect(f"/api/simulations/{simulation_id}/stream?symbols=ACME") as ws:
        assert receive_snapshot(ws)["news"] == [n for n in news if n["symbol"] == "ACME"]


def test_news_is_streamed(client: TestClient) -> None:
    simulation_id = create(client, market=NEWSY, speed=FROZEN)["id"]
    action(client, simulation_id, "start")
    action(client, simulation_id, "pause")
    with client.websocket_connect(f"/api/simulations/{simulation_id}/stream") as ws:
        receive_snapshot(ws)
        action(client, simulation_id, "step", events=2_000)
        item = receive_until(ws, "news")[-1]
    assert set(item) == {"type", "time_seconds", "symbol", "impact", "sentiment", "headline"}
    assert item == {"type": "news", **get(client, "/api/news", limit=1_000)[0]}


def _drain_kinds(client: TestClient, session: LiveSession, subscriber: Any) -> list[str]:
    portal = client.portal
    assert portal is not None
    kinds = []
    while portal.call(subscriber.queue.qsize):
        kinds.append(json.loads(portal.call(subscriber.queue.get_nowait))["type"])
    return kinds


def test_leaderboard_is_paced_and_sent_only_when_it_changed(client: TestClient) -> None:
    simulation_id = create(client, agents=SMALL, speed=FROZEN)["id"]
    action(client, simulation_id, "start")
    action(client, simulation_id, "pause")
    session = session_of(client, simulation_id)
    portal = client.portal
    assert portal is not None
    portal.call(session.runner.stop)  # Only this test publishes from here on.
    now = [100.0]
    session.clock = lambda: now[0]
    subscriber = portal.call(session.hub.subscribe, None)
    assert _drain_kinds(client, session, subscriber) == ["snapshot"]
    portal.call(session.publish_changes)
    assert _drain_kinds(client, session, subscriber) == ["agents"]
    now[0] = 100.5  # Too soon.
    for _ in range(3):
        portal.call(session.publish_changes)
    assert "agents" not in _drain_kinds(client, session, subscriber)
    now[0] = 101.5  # Due, but nothing changed.
    portal.call(session.publish_changes)
    assert "agents" not in _drain_kinds(client, session, subscriber)
    order(client, "ACME", side="buy", price=99.0, quantity=1)  # The user's order count changes.
    _drain_kinds(client, session, subscriber)
    now[0] = 103.0
    portal.call(session.publish_changes)
    assert _drain_kinds(client, session, subscriber) == ["agents"]


def test_plain_sessions_never_send_a_leaderboard(client: TestClient) -> None:
    simulation_id = create(client, **ONE_MARKET, speed=FROZEN)["id"]
    session = session_of(client, simulation_id)
    portal = client.portal
    assert portal is not None
    now = [0.0]
    session.clock = lambda: now[0]
    subscriber = portal.call(session.hub.subscribe, None)
    for step in range(5):
        now[0] = 10.0 * step
        portal.call(session.publish_changes)
    assert "agents" not in _drain_kinds(client, session, subscriber)
