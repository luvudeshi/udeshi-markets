"""HTTP and WebSocket integration tests for the API (real engine, real simulations)."""

from __future__ import annotations

import inspect
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from udeshi_api.main import create_app
from udeshi_api.routes import health, markets, simulations, stream
from udeshi_api.sessions import MANUAL_TRADER_ID, SessionManager
from udeshi_api.settings import Settings
from udeshi_markets import OrderRequest, Side

ONE_MARKET = {"instruments": [{"symbol": "ACME", "reference_price": 1.00, "tick_size": 0.01}]}


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(Settings(tick_seconds=0.01))) as test_client:
        yield test_client


def create(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post("/api/simulations", json=body)
    assert response.status_code == 201, response.text
    payload: dict[str, Any] = response.json()
    return payload


def action(client: TestClient, simulation_id: str, name: str, **body: Any) -> dict[str, Any]:
    response = client.post(f"/api/simulations/{simulation_id}/{name}", json=body or None)
    assert response.status_code == 200, response.text
    payload: dict[str, Any] = response.json()
    return payload


def order(client: TestClient, symbol: str, **body: Any) -> dict[str, Any]:
    response = client.post(f"/api/markets/{symbol}/orders", json=body)
    assert response.status_code == 201, response.text
    payload: dict[str, Any] = response.json()
    return payload


def error_code(response: Any) -> str:
    code: str = response.json()["error"]["code"]
    return code


def started_paused(client: TestClient, **body: Any) -> str:
    simulation_id: str = create(client, **{**ONE_MARKET, **body})["id"]
    action(client, simulation_id, "start")
    action(client, simulation_id, "pause")
    return simulation_id


# ---- Basics ----------------------------------------------------------------------------------


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.2.0"}


def test_every_handler_is_async() -> None:
    # The engine is single-threaded; synchronous handlers would run in a thread pool.
    handlers = [
        route.endpoint
        for router in (health.router, markets.router, simulations.router, stream.router)
        for route in router.routes
        if isinstance(route, APIRoute | APIWebSocketRoute)
    ]
    assert len(handlers) >= 20
    assert all(inspect.iscoroutinefunction(handler) for handler in handlers)


def test_markets_require_a_simulation(client: TestClient) -> None:
    response = client.get("/api/markets")
    assert response.status_code == 404
    assert error_code(response) == "no_active_simulation"


def test_unknown_routes_use_the_error_format(client: TestClient) -> None:
    response = client.get("/api/simulations/nope")
    assert response.status_code == 404
    assert error_code(response) == "unknown_simulation"
    assert client.get("/api/nothing-here").json()["error"]["code"] == "not_found"


# ---- Simulation lifecycle --------------------------------------------------------------------


def test_create_with_defaults_and_list(client: TestClient) -> None:
    created = create(client)
    assert created["state"] == "created"
    assert created["active"] is True
    assert created["symbols"] == ["ACME", "NOVA", "QUANT", "ORBIT", "VECTOR"]
    assert created["duration_seconds"] == 1800.0
    assert created["summary"]["events_processed"] == 0
    second = create(client, **ONE_MARKET, seed=7)
    listed = client.get("/api/simulations").json()
    assert [(s["id"], s["active"]) for s in listed] == [
        (created["id"], False),
        (second["id"], True),
    ]
    assert client.get(f"/api/simulations/{second['id']}").json()["seed"] == 7


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"surprise": 1}, "validation_error"),
        ({"duration_seconds": 0}, "validation_error"),
        ({"instruments": [{"symbol": "bad", "reference_price": 1}]}, "validation_error"),
        ({"instruments": []}, "validation_error"),
        ({"instruments": [{"symbol": "X", "reference_price": 1.005}]}, "invalid_configuration"),
        ({"bar_interval_seconds": 5000, "duration_seconds": 10}, "invalid_configuration"),
    ],
)
def test_create_validation(client: TestClient, body: dict[str, Any], code: str) -> None:
    response = client.post("/api/simulations", json=body)
    assert response.status_code == 422
    assert error_code(response) == code


def test_lifecycle_transitions_and_conflicts(client: TestClient) -> None:
    simulation_id = create(client, **ONE_MARKET)["id"]
    assert client.post(f"/api/simulations/{simulation_id}/pause").status_code == 409
    assert action(client, simulation_id, "start")["state"] == "running"
    conflict = client.post(f"/api/simulations/{simulation_id}/start")
    assert conflict.status_code == 409
    assert error_code(conflict) == "invalid_state"
    assert client.post(f"/api/simulations/{simulation_id}/step", json={}).status_code == 409
    assert action(client, simulation_id, "pause")["state"] == "paused"
    assert action(client, simulation_id, "resume")["state"] == "running"
    assert action(client, simulation_id, "stop")["state"] == "stopped"
    assert client.post(f"/api/simulations/{simulation_id}/resume").status_code == 409
    reset = action(client, simulation_id, "reset")
    assert (reset["state"], reset["time_seconds"]) == ("created", 0.0)


def test_runner_advances_simulated_time_and_completes(client: TestClient) -> None:
    simulation_id = create(client, **ONE_MARKET, duration_seconds=5, speed=500)["id"]
    action(client, simulation_id, "start")
    deadline = time.monotonic() + 10
    state: dict[str, Any] = {}
    while time.monotonic() < deadline:
        state = client.get(f"/api/simulations/{simulation_id}").json()
        if state["state"] == "completed":
            break
        time.sleep(0.01)
    assert state["state"] == "completed"
    assert state["time_seconds"] == 5.0
    assert state["progress"] == 1.0
    bars = client.get("/api/markets/ACME/history").json()
    assert len(bars) == 5
    assert all(bar["volume"] == 0 and bar["close"] == 1.0 for bar in bars)


def test_speed_and_step(client: TestClient) -> None:
    simulation_id = started_paused(client)
    assert action(client, simulation_id, "speed", speed=25)["speed"] == 25
    invalid = client.post(f"/api/simulations/{simulation_id}/speed", json={"speed": 0})
    assert invalid.status_code == 422
    stepped = action(client, simulation_id, "step", events=5)
    assert stepped["state"] == "completed"  # Nothing is scheduled, so the run ends.


def test_delete_and_eviction() -> None:
    with TestClient(create_app(Settings(tick_seconds=0.01, max_sessions=2))) as client:
        first, second, third = (create(client, **ONE_MARKET)["id"] for _ in range(3))
        ids = [s["id"] for s in client.get("/api/simulations").json()]
        assert ids == [second, third]  # The oldest was evicted.
        assert first not in ids
        assert client.delete(f"/api/simulations/{third}").status_code == 204
        assert client.get(f"/api/simulations/{third}").status_code == 404
        assert client.get("/api/simulations").json()[0]["active"] is True  # Falls back.


# ---- Manual trading --------------------------------------------------------------------------


def test_spec_example_through_the_api(client: TestClient) -> None:
    started_paused(client)
    order(client, "ACME", side="sell", price=1.01, quantity=10)
    order(client, "ACME", side="sell", price=1.02, quantity=5)
    result = order(client, "ACME", side="buy", price=1.02, quantity=12)
    assert result["status"] == "filled"
    assert [(f["quantity"], f["price"], f["is_aggressor"]) for f in result["fills"]] == [
        (10, 1.01, True),
        (2, 1.02, True),
    ]
    book = client.get("/api/markets/ACME/orderbook").json()
    assert [(level["price"], level["quantity"]) for level in book["asks"]] == [(1.02, 3)]
    assert book["bids"] == []
    assert book["best_ask"] == 1.02
    tape = client.get("/api/markets/ACME/trades").json()
    assert [(t["price"], t["quantity"], t["aggressor_side"]) for t in tape] == [
        (1.01, 10, "buy"),
        (1.02, 2, "buy"),
    ]
    assert not {"buyer", "seller", "buy_order_id"} & set(tape[0])
    (market,) = client.get("/api/markets").json()
    assert (market["last_price"], market["volume"], market["trade_count"]) == (1.02, 12, 2)
    assert market["change"] == pytest.approx(0.02)
    assert market["change_percent"] == pytest.approx(2.0)
    detail = client.get("/api/markets/ACME").json()
    assert (detail["ask_depth"], detail["resting_orders"]) == (3, 1)


def test_market_order_without_liquidity(client: TestClient) -> None:
    started_paused(client)
    result = order(client, "ACME", side="buy", type="market", quantity=5)
    assert (result["status"], result["cancel_reason"], result["cancelled_quantity"]) == (
        "cancelled",
        "no_liquidity",
        5,
    )


@pytest.mark.parametrize(
    ("body", "status_code", "code"),
    [
        ({"side": "buy", "quantity": 1}, 422, "validation_error"),  # Limit without price.
        ({"side": "buy", "type": "market", "price": 1, "quantity": 1}, 422, "validation_error"),
        ({"side": "buy", "price": 1.005, "quantity": 1}, 422, "invalid_price"),
        ({"side": "buy", "price": 1.0, "quantity": 0}, 422, "validation_error"),
        ({"side": "hold", "price": 1.0, "quantity": 1}, 422, "validation_error"),
    ],
)
def test_order_validation(
    client: TestClient, body: dict[str, Any], status_code: int, code: str
) -> None:
    started_paused(client)
    response = client.post("/api/markets/ACME/orders", json=body)
    assert response.status_code == status_code
    assert error_code(response) == code


def test_orders_require_a_started_simulation_and_a_known_symbol(client: TestClient) -> None:
    create(client, **ONE_MARKET)
    response = client.post(
        "/api/markets/ACME/orders", json={"side": "buy", "price": 1, "quantity": 1}
    )
    assert response.status_code == 409
    unknown = client.get("/api/markets/NOPE/orderbook")
    assert unknown.status_code == 404
    assert error_code(unknown) == "unknown_symbol"


def test_open_orders_and_cancel(client: TestClient) -> None:
    started_paused(client)
    resting = order(client, "ACME", side="buy", price=0.99, quantity=4)
    assert resting["status"] == "resting"
    (open_order,) = client.get("/api/markets/ACME/orders").json()
    assert (open_order["order_id"], open_order["remaining_quantity"], open_order["side"]) == (
        resting["order_id"],
        4,
        "buy",
    )
    cancelled = client.delete(f"/api/markets/ACME/orders/{resting['order_id']}")
    assert cancelled.status_code == 200
    assert cancelled.json()["cancelled_quantity"] == 4
    again = client.delete(f"/api/markets/ACME/orders/{resting['order_id']}")
    assert again.status_code == 404
    assert error_code(again) == "order_not_found"
    assert client.get("/api/markets/ACME/orders").json() == []


def test_cannot_cancel_another_participants_order(client: TestClient) -> None:
    simulation_id = started_paused(client)
    manager = client.app.state.sessions  # type: ignore[attr-defined]
    assert isinstance(manager, SessionManager)
    exchange = manager.get(simulation_id).simulation.exchange
    other = exchange.submit(
        OrderRequest(instrument=0, side=Side.SELL, price=105, quantity=2, owner=7)
    )
    assert other.accepted
    response = client.delete(f"/api/markets/ACME/orders/{other.order_id}")
    assert response.status_code == 404  # Indistinguishable from a missing order.
    remaining = exchange.order(0, other.order_id)
    assert remaining is not None
    assert remaining.remaining_quantity == 2
    assert remaining.owner != MANUAL_TRADER_ID


def test_history_interval_validation_and_aggregation(client: TestClient) -> None:
    simulation_id = create(client, **ONE_MARKET, duration_seconds=6, speed=1000)["id"]
    action(client, simulation_id, "start")
    deadline = time.monotonic() + 10
    while client.get(f"/api/simulations/{simulation_id}").json()["state"] != "completed":
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert len(client.get("/api/markets/ACME/history").json()) == 6
    three_second = client.get("/api/markets/ACME/history", params={"interval_seconds": 3}).json()
    assert [(bar["start_seconds"], bar["end_seconds"]) for bar in three_second] == [
        (0.0, 3.0),
        (3.0, 6.0),
    ]
    assert len(client.get("/api/markets/ACME/history", params={"limit": 2}).json()) == 2
    invalid = client.get("/api/markets/ACME/history", params={"interval_seconds": 1.5})
    assert invalid.status_code == 422
    assert error_code(invalid) == "invalid_interval"


def test_prices_are_rounded_to_the_tick_grid(client: TestClient) -> None:
    create(client, instruments=[{"symbol": "NICKEL", "reference_price": 0.15, "tick_size": 0.05}])
    simulation_id = client.get("/api/simulations").json()[-1]["id"]
    action(client, simulation_id, "start")
    action(client, simulation_id, "pause")
    order(client, "NICKEL", side="buy", price=0.15, quantity=1)
    book = client.get("/api/markets/NICKEL/orderbook").json()
    assert book["bids"][0]["price"] == 0.15  # Not 0.15000000000000002.
    assert book["bids"][0]["price_ticks"] == 3


# ---- WebSocket stream ------------------------------------------------------------------------


def test_stream_snapshot_then_live_updates(client: TestClient) -> None:
    simulation_id = started_paused(client)
    with client.websocket_connect(f"/api/simulations/{simulation_id}/stream?symbols=ACME") as ws:
        snapshot = [ws.receive_json() for _ in range(4)]
        assert [m["type"] for m in snapshot] == ["simulation", "book", "market", "trades"]
        assert snapshot[0]["state"] == "paused"
        assert snapshot[1]["symbol"] == "ACME"
        order(client, "ACME", side="sell", price=1.01, quantity=3)
        order(client, "ACME", side="buy", price=1.01, quantity=2)
        received: list[dict[str, Any]] = []
        while not any(m["type"] == "trades" and m["trades"] for m in received):
            received.append(ws.receive_json())
        trades = next(m for m in received if m["type"] == "trades")
        assert [(t["price"], t["quantity"]) for t in trades["trades"]] == [(1.01, 2)]
        books = [m for m in received if m["type"] == "book"]
        assert books[-1]["asks"] == [
            {"price": 1.01, "price_ticks": 101, "quantity": 1, "orders": 1}
        ]


def test_stream_rejects_unknown_simulation_and_symbols(client: TestClient) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as missing,
        client.websocket_connect("/api/simulations/nope/stream") as ws,
    ):
        ws.receive_json()
    assert missing.value.code == 4404
    simulation_id = create(client, **ONE_MARKET)["id"]
    with (
        pytest.raises(WebSocketDisconnect) as unknown,
        client.websocket_connect(f"/api/simulations/{simulation_id}/stream?symbols=ZZZ") as ws,
    ):
        ws.receive_json()
    assert unknown.value.code == 4400


def test_stream_announces_deletion(client: TestClient) -> None:
    simulation_id = create(client, **ONE_MARKET)["id"]
    with client.websocket_connect(f"/api/simulations/{simulation_id}/stream") as ws:
        for _ in range(4):
            ws.receive_json()
        assert client.delete(f"/api/simulations/{simulation_id}").status_code == 204
        messages: list[dict[str, Any]] = []
        while not messages or messages[-1]["type"] != "closed":
            messages.append(ws.receive_json())
        assert messages[-1] == {"type": "closed"}
