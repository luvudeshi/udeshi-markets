"""The WebSocket stream on more than one market, and its connection lifecycle."""

from __future__ import annotations

import pytest
from apiclient import FROZEN, TWO_MARKETS, action, create, cross, manager, order, started_paused
from fastapi.testclient import TestClient
from wsclient import receive_json, receive_snapshot, receive_until


def test_a_symbol_filter_covers_the_snapshot_updates_and_bars(client: TestClient) -> None:
    simulation_id = create(client, **TWO_MARKETS, duration_seconds=3, speed=FROZEN)["id"]
    action(client, simulation_id, "start")
    action(client, simulation_id, "pause")
    for price in (1.01, 1.03, 0.99):  # Earlier trades, which the snapshot must carry.
        cross(client, "ACME", price, 1, aggressor="buy")
    with client.websocket_connect(f"/api/simulations/{simulation_id}/stream?symbols=ACME") as ws:
        [market] = receive_snapshot(ws)["markets"]
        assert market["symbol"] == "ACME"
        assert [(t["trade_id"], t["price"]) for t in market["trades"]] == [
            (1, 1.01),
            (2, 1.03),
            (3, 0.99),
        ]
        cross(client, "NOVA", 2.01, 1, aggressor="buy")  # Not for this subscriber...
        cross(client, "ACME", 1.02, 2, aggressor="sell")  # ...but this is.
        updates = receive_until(ws, "trades")
        action(client, simulation_id, "step", events=1)  # Completes the run, closing all bars.
        updates += receive_until(ws, "bars")
        assert {m["symbol"] for m in updates if "symbol" in m} == {"ACME"}
        trades = next(m for m in updates if m["type"] == "trades")
        assert [(t["price"], t["quantity"], t["aggressor_side"]) for t in trades["trades"]] == [
            (1.02, 2, "sell")
        ]
        bars = updates[-1]
        assert (bars["symbol"], bars["omitted"]) == ("ACME", 0)
        flat = dict.fromkeys(("open", "high", "low", "close"), 1.02)
        assert bars["bars"] == [
            {"start_seconds": 0.0, "end_seconds": 1.0, "open": 1.01, "high": 1.03, "low": 0.99,
             "close": 1.02, "volume": 5, "trade_count": 4, "vwap": 1.014},  # 507 ticks / 5.
            {"start_seconds": 1.0, "end_seconds": 2.0, **flat, "volume": 0, "trade_count": 0,
             "vwap": None},
            {"start_seconds": 2.0, "end_seconds": 3.0, **flat, "volume": 0, "trade_count": 0,
             "vwap": None},
        ]  # fmt: skip
        with pytest.raises(TimeoutError):  # Nothing follows, in particular no NOVA bars.
            receive_json(ws, timeout=0.3)


def test_subscribers_are_removed_when_clients_disconnect(client: TestClient) -> None:
    simulation_id = started_paused(client)
    hub = manager(client).get(simulation_id).hub
    portal = client.portal
    assert portal is not None
    for _ in range(3):
        with client.websocket_connect(f"/api/simulations/{simulation_id}/stream") as ws:
            receive_snapshot(ws)
            assert portal.call(lambda: hub.subscriber_count) == 1
        # Leaving the block disconnects and waits for the server's handler to finish.
        assert portal.call(lambda: hub.subscriber_count) == 0


def test_client_frames_of_any_kind_are_ignored(client: TestClient) -> None:
    simulation_id = started_paused(client)
    with client.websocket_connect(f"/api/simulations/{simulation_id}/stream") as ws:
        receive_snapshot(ws)
        for index in range(3):
            ws.send_text(f"message {index}")
            ws.send_bytes(bytes([index]))
        ws.send_json({"type": "subscribe", "symbols": ["NOVA"]})
        for quantity, total in ((1, 1), (2, 3)):  # Updates keep flowing.
            order(client, "ACME", side="buy", price=0.99, quantity=quantity)
            assert receive_until(ws, "book")[-1]["bids"][0]["quantity"] == total
