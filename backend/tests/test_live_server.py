"""Tests against a real uvicorn server and a real WebSocket client.

Starlette's TestClient does not reproduce some protocol behaviour. For example, it surfaces a
close sent before the handshake completes as a close code, whereas real servers turn it into an
HTTP 403 rejection. These tests cover what only a real server shows.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest
import uvicorn
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import ClientConnection, connect

from udeshi_api.main import create_app
from udeshi_api.settings import Settings


@pytest.fixture
def server() -> Iterator[str]:
    config = uvicorn.Config(
        create_app(Settings(tick_seconds=0.01)), host="127.0.0.1", port=0, log_level="warning"
    )
    instance = uvicorn.Server(config)
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not instance.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.01)
    port = instance.servers[0].sockets[0].getsockname()[1]
    yield f"127.0.0.1:{port}"
    instance.should_exit = True
    thread.join(timeout=10)
    assert not thread.is_alive()


def post(server: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(
        f"http://{server}{path}",
        data=json.dumps(body or {}).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload: dict[str, Any] = json.load(response)
    return payload


def receive(ws: ClientConnection) -> dict[str, Any]:
    message: dict[str, Any] = json.loads(ws.recv(timeout=10))
    return message


ONE_MARKET = {"instruments": [{"symbol": "ACME", "reference_price": 1.00, "tick_size": 0.01}]}


@pytest.mark.parametrize(
    ("query", "code", "reason"),
    [
        ("nope/stream", 4404, "no simulation with id 'nope'"),
        ("{id}/stream?symbols=ACME,ZZZ", 4400, "unknown symbols: ZZZ"),
    ],
)
def test_close_codes_reach_real_clients(server: str, query: str, code: int, reason: str) -> None:
    simulation_id = post(server, "/api/simulations", ONE_MARKET)["id"]
    url = f"ws://{server}/api/simulations/{query.format(id=simulation_id)}"
    with connect(url) as ws, pytest.raises(ConnectionClosed) as closed:
        ws.recv(timeout=10)
    assert closed.value.rcvd is not None
    assert (closed.value.rcvd.code, closed.value.rcvd.reason) == (code, reason)


def test_binary_frames_are_ignored(server: str, caplog: pytest.LogCaptureFixture) -> None:
    simulation_id = post(server, "/api/simulations", ONE_MARKET)["id"]
    post(server, f"/api/simulations/{simulation_id}/start")
    with connect(f"ws://{server}/api/simulations/{simulation_id}/stream") as ws:
        assert receive(ws)["type"] == "snapshot"
        ws.send(b"\x00ping")
        post(server, "/api/markets/ACME/orders", {"side": "buy", "price": 0.99, "quantity": 1})
        while (message := receive(ws))["type"] != "book":
            pass
        assert message["bids"][0]["quantity"] == 1
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
