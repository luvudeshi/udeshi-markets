"""Exact API contracts: full payloads, several markets, addressing, order options, counters,
limits, error bodies and the async-only rule (real engine, real simulations)."""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Callable, Iterable, Iterator
from typing import Any, NoReturn

import pytest
from apiclient import (
    FROZEN,
    ONE_MARKET,
    action,
    create,
    cross,
    error_code,
    get,
    manager,
    order,
    started_paused,
)
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient
from starlette.routing import BaseRoute
from wsclient import receive_snapshot, receive_until

from udeshi_api.main import create_app
from udeshi_api.manual import MANUAL_TRADER_ID as ME
from udeshi_api.sessions import LiveSession
from udeshi_api.settings import Settings
from udeshi_markets import MAX_ORDER_QUANTITY

# ---- Several markets ---------------------------------------------------------------------------


def test_trading_on_several_markets_with_different_tick_sizes(client: TestClient) -> None:
    """The default markets: QUANT (index 2) has a 0.05 tick, NOVA (index 1) a 0.01 tick. Every
    price must be converted with its own market's tick size, and totals summed over markets."""
    simulation_id = create(client, speed=FROZEN)["id"]
    action(client, simulation_id, "start")
    action(client, simulation_id, "pause")
    with client.websocket_connect(f"/api/simulations/{simulation_id}/stream") as ws:
        receive_snapshot(ws)
        order(client, "QUANT", side="sell", price=250.05, quantity=3)
        quant = order(client, "QUANT", side="buy", price=250.05, quantity=2)
        order(client, "NOVA", side="sell", price=42.55, quantity=4)
        nova = order(client, "NOVA", side="buy", type="market", quantity=1)
        streamed = [receive_until(ws, "trades")[-1] for _ in range(2)]

    assert quant["fills"] == [
        {"trade_id": 1, "price": 250.05, "price_ticks": 5001, "quantity": 2, "is_aggressor": True}
    ]
    assert nova["fills"] == [
        {"trade_id": 2, "price": 42.55, "price_ticks": 4255, "quantity": 1, "is_aggressor": True}
    ]
    assert [
        (m["symbol"], [(t["price"], t["price_ticks"]) for t in m["trades"]]) for m in streamed
    ] == [
        ("QUANT", [(250.05, 5001)]),
        ("NOVA", [(42.55, 4255)]),
    ]
    assert [t["price"] for t in get(client, "/api/markets/QUANT/trades")] == [250.05]
    assert [t["price"] for t in get(client, "/api/markets/NOVA/trades")] == [42.55]
    assert {m["symbol"]: m["last_price"] for m in get(client, "/api/markets")} == {
        "ACME": None,
        "NOVA": 42.55,
        "QUANT": 250.05,
        "ORBIT": None,
        "VECTOR": None,
    }
    assert get(client, "/api/markets/QUANT")["notional"] == 500.1  # 2 x 5001 ticks x 0.05.
    [resting] = get(client, "/api/markets/QUANT/orders")
    assert (resting["price"], resting["price_ticks"], resting["remaining_quantity"]) == (
        250.05,
        5001,
        1,
    )
    summary = get(client, f"/api/simulations/{simulation_id}")["summary"]
    assert (summary["trades"], summary["volume"]) == (2, 3)  # Summed over both markets.


# ---- Addressing --------------------------------------------------------------------------------


def test_market_endpoints_address_the_active_or_a_named_simulation(client: TestClient) -> None:
    first = create(client, **ONE_MARKET)["id"]
    second = create(client, instruments=[{"symbol": "BETA", "reference_price": 2.0}])["id"]
    assert [m["symbol"] for m in get(client, "/api/markets")] == ["BETA"]  # The newest.
    assert [m["symbol"] for m in get(client, "/api/markets", simulation_id=first)] == ["ACME"]
    assert get(client, "/api/markets/ACME/orderbook", simulation_id=first)["symbol"] == "ACME"
    not_here = client.get("/api/markets/ACME")
    assert (not_here.status_code, error_code(not_here)) == (404, "unknown_symbol")
    missing = client.get("/api/markets", params={"simulation_id": "nope"})
    assert (missing.status_code, error_code(missing)) == (404, "unknown_simulation")

    third = create(client, instruments=[{"symbol": "GAMMA", "reference_price": 3.0}])["id"]
    assert client.delete(f"/api/simulations/{third}").status_code == 204
    assert [(s["id"], s["active"]) for s in get(client, "/api/simulations")] == [
        (first, False),
        (second, True),  # The newest remaining one takes over.
    ]
    assert [m["symbol"] for m in get(client, "/api/markets")] == ["BETA"]


# ---- Order options -----------------------------------------------------------------------------


def test_immediate_or_cancel_through_the_api(client: TestClient) -> None:
    started_paused(client)
    order(client, "ACME", side="sell", price=1.01, quantity=3)
    ioc = order(client, "ACME", side="buy", price=1.01, quantity=5, time_in_force="ioc")
    assert {k: ioc[k] for k in ioc if k != "fills"} == {
        "order_id": 2,
        "symbol": "ACME",
        "status": "cancelled",
        "reject_reason": None,
        "cancel_reason": "no_liquidity",
        "filled_quantity": 3,
        "resting_quantity": 0,
        "cancelled_quantity": 2,
    }
    assert get(client, "/api/markets/ACME/orderbook")["bids"] == []
    assert get(client, "/api/markets/ACME/orders") == []


def test_self_trade_prevention_modes_through_the_api(client: TestClient) -> None:
    started_paused(client)
    order(client, "ACME", side="sell", price=1.01, quantity=3)
    kept = order(
        client, "ACME", side="buy", price=1.01, quantity=2, self_trade_prevention="cancel_resting"
    )
    # Your resting sell is removed instead of trading with your own buy, which then rests.
    assert (kept["status"], kept["filled_quantity"], kept["resting_quantity"]) == ("resting", 0, 2)
    assert [o["order_id"] for o in get(client, "/api/markets/ACME/orders")] == [kept["order_id"]]
    assert get(client, "/api/markets/ACME/orderbook")["asks"] == []

    refused = order(
        client, "ACME", side="sell", price=1.00, quantity=3, self_trade_prevention="cancel_incoming"
    )
    assert (refused["status"], refused["cancel_reason"]) == ("cancelled", "self_trade_prevention")
    assert (refused["filled_quantity"], refused["cancelled_quantity"]) == (0, 3)
    assert [o["order_id"] for o in get(client, "/api/markets/ACME/orders")] == [kept["order_id"]]
    assert get(client, "/api/simulations")[0]["summary"]["trades"] == 0


# ---- Full payloads -----------------------------------------------------------------------------


def _paused_after_some_time(client: TestClient) -> tuple[str, float]:
    """A paused simulation whose clock has moved past zero, so times are checkable."""
    simulation_id = create(client, **ONE_MARKET, duration_seconds=1000)["id"]
    action(client, simulation_id, "start")
    deadline = time.monotonic() + 10
    while get(client, f"/api/simulations/{simulation_id}")["time_seconds"] == 0:
        assert time.monotonic() < deadline, "the runner never advanced the clock"
        time.sleep(0.005)
    now: float = action(client, simulation_id, "pause")["time_seconds"]
    assert now > 0
    return simulation_id, now


def test_market_book_tape_orders_and_bars_payloads(client: TestClient) -> None:
    simulation_id, now = _paused_after_some_time(client)
    # Five trades, two of them sell-initiated, give distinct open, high, low and close.
    cross(client, "ACME", 1.00, 1, aggressor="buy")
    cross(client, "ACME", 1.03, 1, aggressor="buy")
    cross(client, "ACME", 0.98, 1, aggressor="sell")
    cross(client, "ACME", 1.01, 1, aggressor="sell")
    best_bid = order(client, "ACME", side="buy", price=0.99, quantity=5)
    second_bid = order(client, "ACME", side="buy", price=0.97, quantity=2)
    best_ask = order(client, "ACME", side="sell", price=1.02, quantity=3)
    second_ask = order(client, "ACME", side="sell", price=1.04, quantity=1)
    hit = order(client, "ACME", side="sell", type="market", quantity=2)  # Partially fills 0.99.

    assert hit == {
        "order_id": hit["order_id"],
        "symbol": "ACME",
        "status": "filled",
        "reject_reason": None,
        "cancel_reason": None,
        "filled_quantity": 2,
        "resting_quantity": 0,
        "cancelled_quantity": 0,
        "fills": [
            {"trade_id": 5, "price": 0.99, "price_ticks": 99, "quantity": 2, "is_aggressor": True}
        ],
    }
    summary = {
        "symbol": "ACME",
        "tick_size": 0.01,
        "reference_price": 1.0,
        "last_price": 0.99,
        "change": -0.01,
        "change_percent": -1.0,
        "open": 1.0,
        "high": 1.03,
        "low": 0.98,
        "volume": 6,
        "trade_count": 5,
        "vwap": 1.0,  # (100 + 103 + 98 + 101 + 2 x 99) / 6 ticks.
        "best_bid": 0.99,
        "best_ask": 1.02,
        "midprice": 1.005,
        "spread": 0.03,
        "fundamental_value": None,  # No market environment in this simulation.
    }
    assert get(client, "/api/markets") == [summary]
    assert get(client, "/api/markets/ACME") == {
        **summary,
        "bid_depth": 5,
        "ask_depth": 4,
        "resting_orders": 4,
        "notional": 6.0,
    }
    assert get(client, "/api/markets/ACME/orderbook") == {
        "symbol": "ACME",
        "time_seconds": now,
        "bids": [
            {
                "price": 0.99,
                "price_ticks": 99,
                "quantity": 3,
                "orders": 1,
                "cumulative_quantity": 3,
            },
            {
                "price": 0.97,
                "price_ticks": 97,
                "quantity": 2,
                "orders": 1,
                "cumulative_quantity": 5,
            },
        ],
        "asks": [
            {
                "price": 1.02,
                "price_ticks": 102,
                "quantity": 3,
                "orders": 1,
                "cumulative_quantity": 3,
            },
            {
                "price": 1.04,
                "price_ticks": 104,
                "quantity": 1,
                "orders": 1,
                "cumulative_quantity": 4,
            },
        ],
        "best_bid": 0.99,
        "best_ask": 1.02,
        "midprice": 1.005,
        "spread": 0.03,
    }
    assert get(client, "/api/markets/ACME/trades") == [
        {"trade_id": 1, "time_seconds": now, "price": 1.0, "price_ticks": 100, "quantity": 1,
         "aggressor_side": "buy", "buyer": ME, "seller": ME},
        {"trade_id": 2, "time_seconds": now, "price": 1.03, "price_ticks": 103, "quantity": 1,
         "aggressor_side": "buy", "buyer": ME, "seller": ME},
        {"trade_id": 3, "time_seconds": now, "price": 0.98, "price_ticks": 98, "quantity": 1,
         "aggressor_side": "sell", "buyer": ME, "seller": ME},
        {"trade_id": 4, "time_seconds": now, "price": 1.01, "price_ticks": 101, "quantity": 1,
         "aggressor_side": "sell", "buyer": ME, "seller": ME},
        {"trade_id": 5, "time_seconds": now, "price": 0.99, "price_ticks": 99, "quantity": 2,
         "aggressor_side": "sell", "buyer": ME, "seller": ME},
    ]  # fmt: skip
    assert get(client, "/api/markets/ACME/orders") == [
        {"order_id": best_bid["order_id"], "symbol": "ACME", "side": "buy", "price": 0.99,
         "price_ticks": 99, "original_quantity": 5, "remaining_quantity": 3, "time_seconds": now},
        {"order_id": second_bid["order_id"], "symbol": "ACME", "side": "buy", "price": 0.97,
         "price_ticks": 97, "original_quantity": 2, "remaining_quantity": 2, "time_seconds": now},
        {"order_id": best_ask["order_id"], "symbol": "ACME", "side": "sell", "price": 1.02,
         "price_ticks": 102, "original_quantity": 3, "remaining_quantity": 3, "time_seconds": now},
        {"order_id": second_ask["order_id"], "symbol": "ACME", "side": "sell", "price": 1.04,
         "price_ticks": 104, "original_quantity": 1, "remaining_quantity": 1, "time_seconds": now},
    ]  # fmt: skip

    action(client, simulation_id, "step", events=1)  # Nothing queued: completes the run.
    bars = get(client, "/api/markets/ACME/history", limit=10_000)
    index = int(now)  # One-second bars; every trade happened at `now`.
    assert bars[index] == {
        "start_seconds": float(index),
        "end_seconds": float(index + 1),
        "open": 1.0,
        "high": 1.03,
        "low": 0.98,
        "close": 0.99,
        "volume": 6,
        "trade_count": 5,
        "vwap": 1.0,
    }
    quiet = {"volume": 0, "trade_count": 0, "vwap": None}
    assert bars[index + 1] == {
        "start_seconds": float(index + 1),
        "end_seconds": float(index + 2),
        **dict.fromkeys(("open", "high", "low", "close"), 0.99),
        **quiet,
    }
    for before in bars[:index]:
        assert {k: before[k] for k in ("open", "high", "low", "close", "volume", "vwap")} == {
            **dict.fromkeys(("open", "high", "low", "close"), 1.0),
            "volume": 0,
            "vwap": None,
        }


# ---- Lifecycle ---------------------------------------------------------------------------------


def test_stop_and_reset_from_paused_and_running(client: TestClient) -> None:
    simulation_id = started_paused(client)
    assert action(client, simulation_id, "stop")["state"] == "stopped"
    assert action(client, simulation_id, "reset")["state"] == "created"
    action(client, simulation_id, "start")
    assert action(client, simulation_id, "reset")["state"] == "created"  # From running.
    action(client, simulation_id, "start")
    action(client, simulation_id, "pause")
    assert action(client, simulation_id, "reset")["state"] == "created"  # From paused.


# ---- Counters and configuration ----------------------------------------------------------------


def test_configuration_round_trips(client: TestClient) -> None:
    created = create(
        client,
        **ONE_MARKET,
        duration_seconds=20,
        seed=99,
        order_latency_ms=12.5,
        bar_interval_seconds=0.5,
        speed=3,
    )
    echoed = {k: created[k] for k in created if k not in ("id", "created_at", "summary")}
    assert echoed == {
        "state": "created",
        "active": True,
        "time_seconds": 0.0,
        "duration_seconds": 20.0,
        "progress": 0.0,
        "speed": 3.0,
        "seed": 99,
        "order_latency_ms": 12.5,
        "bar_interval_seconds": 0.5,
        "symbols": ["ACME"],
        "initial_cash": 1_000_000.0,
        "agents": None,
        "market": None,
        "error": None,
    }
    config = manager(client).get(created["id"]).simulation.config
    assert (config.duration_ns, config.seed, config.order_latency_ns, config.bar_interval_ns) == (
        20_000_000_000,
        99,
        12_500_000,
        500_000_000,
    )


def _manual_session(client: TestClient, prices: Iterable[float]) -> dict[str, Any]:
    simulation_id = create(client, **ONE_MARKET, speed=FROZEN)["id"]
    action(client, simulation_id, "start")
    action(client, simulation_id, "pause")
    sell, buy = (
        order(client, "ACME", side=side, price=1.01, quantity=3) for side in ("sell", "buy")
    )
    assert buy["filled_quantity"] == 3
    resting = [order(client, "ACME", side="buy", price=price, quantity=1) for price in prices]
    assert client.delete(f"/api/markets/ACME/orders/{resting[0]['order_id']}").status_code == 200
    assert client.delete(f"/api/markets/ACME/orders/{sell['order_id']}").status_code == 404
    simulation: dict[str, Any] = get(client, f"/api/simulations/{simulation_id}")
    assert simulation["time_seconds"] == 0.0  # Frozen, so runs are reproducible.
    return simulation


def test_summary_counts_and_digests_manual_activity(client: TestClient) -> None:
    first = _manual_session(client, [0.99, 0.98])
    assert {k: v for k, v in first["summary"].items() if k != "digest"} == {
        "events_processed": 0,  # Manual actions are processed at once, not queued as events.
        "orders_submitted": 4,
        "orders_rejected": 0,
        "cancels_requested": 2,
        "trades": 1,
        "volume": 3,
    }
    digest = first["summary"]["digest"]
    assert len(digest) == 32
    int(digest, 16)
    assert _manual_session(client, [0.99, 0.98])["summary"]["digest"] == digest
    assert _manual_session(client, [0.99, 0.97])["summary"]["digest"] != digest


# ---- Limits ------------------------------------------------------------------------------------


def test_limits_return_the_most_recent_items(client: TestClient) -> None:
    simulation_id = started_paused(client, duration_seconds=6, speed=FROZEN)
    for price in (1.01, 1.02, 1.03):
        cross(client, "ACME", price, 1, aggressor="buy")
    assert [t["price"] for t in get(client, "/api/markets/ACME/trades", limit=2)] == [1.02, 1.03]
    for side, price in (("buy", 0.99), ("buy", 0.98), ("sell", 1.05), ("sell", 1.06)):
        order(client, "ACME", side=side, price=price, quantity=1)
    deep = get(client, "/api/markets/ACME/orderbook")
    assert ([lv["price"] for lv in deep["bids"]], [lv["price"] for lv in deep["asks"]]) == (
        [0.99, 0.98],
        [1.05, 1.06],
    )
    top = get(client, "/api/markets/ACME/orderbook", depth=1)
    assert ([lv["price"] for lv in top["bids"]], [lv["price"] for lv in top["asks"]]) == (
        [0.99],
        [1.05],
    )
    action(client, simulation_id, "step", events=1)
    recent = get(client, "/api/markets/ACME/history", limit=2)
    assert [bar["start_seconds"] for bar in recent] == [4.0, 5.0]
    wide = get(client, "/api/markets/ACME/history", interval_seconds=2, limit=2)
    assert [(bar["start_seconds"], bar["end_seconds"]) for bar in wide] == [(2.0, 4.0), (4.0, 6.0)]


# ---- Error bodies ------------------------------------------------------------------------------


def test_unexpected_errors_are_logged_and_reported_without_internals(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(*_: object, **__: object) -> NoReturn:
        raise RuntimeError("secret internal detail")

    with TestClient(create_app(Settings(tick_seconds=0.01)), raise_server_exceptions=False) as http:
        simulation_id = create(http, **ONE_MARKET)["id"]
        monkeypatch.setattr(LiveSession, "describe", boom)
        with caplog.at_level(logging.ERROR, logger="udeshi_api"):
            response = http.get(f"/api/simulations/{simulation_id}")
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "internal server error"}
    }
    assert "secret" not in response.text
    [record] = [r for r in caplog.records if r.name == "udeshi_api"]
    assert record.getMessage() == f"unhandled error on GET /api/simulations/{simulation_id}"
    assert record.exc_info is not None


def test_validation_errors_list_every_problem(client: TestClient) -> None:
    started_paused(client)
    response = client.post("/api/markets/ACME/orders", json={"side": "up", "quantity": 0})
    assert response.status_code == 422
    error = response.json()["error"]
    assert (error["code"], error["message"]) == ("validation_error", "request validation failed")
    assert [(d["location"], d["type"]) for d in error["details"]] == [
        (["body", "side"], "literal_error"),
        (["body", "quantity"], "greater_than_equal"),
    ]
    assert all(isinstance(d["message"], str) and d["message"] for d in error["details"])

    too_big = client.post(
        "/api/markets/ACME/orders",
        json={"side": "buy", "price": 1.0, "quantity": MAX_ORDER_QUANTITY + 1},
    )
    assert too_big.status_code == 422
    assert [(d["location"], d["type"]) for d in too_big.json()["error"]["details"]] == [
        (["body", "quantity"], "less_than_equal")
    ]
    assert get(client, "/api/simulations")[0]["summary"]["orders_submitted"] == 0


# ---- Concurrency model -------------------------------------------------------------------------


def _routes(routes: Iterable[BaseRoute]) -> Iterator[APIRoute | APIWebSocketRoute]:
    for route in routes:
        # FastAPI (0.162) wraps each included router; its own routes carry the full path.
        nested = getattr(getattr(route, "original_router", None), "routes", None)
        if nested is not None:
            yield from _routes(nested)
        elif isinstance(route, APIRoute | APIWebSocketRoute):
            yield route


def _calls(dependant: Dependant) -> Iterator[Callable[..., Any]]:
    if dependant.call is not None:
        yield dependant.call
    for child in dependant.dependencies:
        yield from _calls(child)


def test_every_handler_and_dependency_of_the_app_is_async() -> None:
    """FastAPI runs synchronous handlers and dependencies in a worker thread, which would
    break the single-threaded model, so the whole dependency tree of every route is checked."""
    routes = list(_routes(create_app(Settings()).routes))
    assert len(routes) >= 20
    calls = [call for route in routes for call in _calls(route.dependant)]
    assert len(calls) > len(routes)  # The session dependencies are included.
    assert [c.__qualname__ for c in calls if not inspect.iscoroutinefunction(c)] == []
