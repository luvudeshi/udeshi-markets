"""Bounded receiving for TestClient WebSocket sessions.

Starlette's ``receive_json`` waits forever, so a regression that stops a message from being sent
would hang the suite instead of failing it. These helpers give every receive a timeout and every
"wait for a message of this kind" loop a message limit.
"""

from __future__ import annotations

import json
from typing import Any

import anyio
from starlette.testclient import WebSocketTestSession
from starlette.types import Message
from starlette.websockets import WebSocketDisconnect

TIMEOUT_SECONDS = 5.0
MAX_MESSAGES = 200


def receive_message(ws: WebSocketTestSession, timeout: float = TIMEOUT_SECONDS) -> Message:
    """The next ASGI message the application sent, or TimeoutError."""

    async def receive() -> Message:
        with anyio.fail_after(timeout):
            # The stream behind WebSocketTestSession.receive, read with a deadline.
            message: Message = await ws._send_rx.receive()
            return message

    return ws.portal.call(receive)


def receive_json(ws: WebSocketTestSession, timeout: float = TIMEOUT_SECONDS) -> dict[str, Any]:
    message = receive_message(ws, timeout)
    if message["type"] == "websocket.close":
        raise WebSocketDisconnect(message.get("code", 1000), message.get("reason"))
    payload: dict[str, Any] = json.loads(message["text"])
    return payload


def receive_snapshot(ws: WebSocketTestSession) -> dict[str, Any]:
    message = receive_json(ws)
    assert message["type"] == "snapshot", message
    return message


def receive_until(
    ws: WebSocketTestSession, kind: str, *, limit: int = MAX_MESSAGES
) -> list[dict[str, Any]]:
    """Messages up to and including the first one of type ``kind``."""
    received: list[dict[str, Any]] = []
    for _ in range(limit):
        received.append(receive_json(ws))
        if received[-1]["type"] == kind:
            return received
    raise AssertionError(f"no {kind!r} message in {limit}: {[m['type'] for m in received]}")


def receive_close(ws: WebSocketTestSession) -> int:
    """Waits for the server to close the socket and returns the close code."""
    for _ in range(MAX_MESSAGES):
        message = receive_message(ws)
        if message["type"] == "websocket.close":
            code: int = message.get("code", 1000)
            return code
    raise AssertionError(f"the server did not close the socket within {MAX_MESSAGES} messages")
