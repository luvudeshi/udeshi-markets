"""WebSocket stream of live market data for one simulation.

Protocol (server to client, JSON text frames):
  {"type": "snapshot", "simulation": {...}, "markets": [...]}   full state; replaces the client's
  {"type": "simulation", ...}   lifecycle state, simulated time, progress, speed
  {"type": "book", ...}         order book for a symbol (the configured depth)
  {"type": "market", ...}       market summary for a symbol
  {"type": "trades", "symbol", "trades": [...]}              new public trades
  {"type": "bars", "symbol", "bars": [...], "omitted": n}    newly closed OHLCV bars
  {"type": "closed"}            the simulation was deleted; the server then closes the socket
A connection starts with a snapshot, then receives incremental updates once per runner tick.
A new snapshot arrives after a reset, or if the client fell so far behind that its buffer could
not hold the updates. Client messages (text or binary) are ignored.
"""

import asyncio
import contextlib
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from udeshi_api.deps import get_sessions_ws
from udeshi_api.errors import ApiError
from udeshi_api.hub import Subscriber
from udeshi_api.sessions import SessionManager

router = APIRouter(tags=["stream"])
logger = logging.getLogger("udeshi_api.stream")

# Application-defined WebSocket close codes (4000-4999).
CLOSE_UNKNOWN_SIMULATION = 4404
CLOSE_UNKNOWN_SYMBOL = 4400


@router.websocket("/api/simulations/{simulation_id}/stream")
async def stream(
    websocket: WebSocket,
    simulation_id: str,
    sessions: Annotated[SessionManager, Depends(get_sessions_ws)],
    symbols: Annotated[str | None, Query(description="Comma-separated; default: all.")] = None,
) -> None:
    # Accept first: a close before the handshake completes reaches clients as an HTTP 403
    # with no code or reason, so they could not tell a missing simulation from a bad symbol.
    await websocket.accept()
    try:
        session = sessions.get(simulation_id)
    except ApiError as error:
        await websocket.close(code=CLOSE_UNKNOWN_SIMULATION, reason=error.message)
        return
    requested: frozenset[str] | None = None
    if symbols:
        requested = frozenset(s.strip() for s in symbols.split(",") if s.strip())
        unknown = sorted(requested - set(session.simulation.config.symbols))
        if unknown:
            await websocket.close(
                code=CLOSE_UNKNOWN_SYMBOL, reason=f"unknown symbols: {', '.join(unknown)}"
            )
            return

    subscriber = session.hub.subscribe(requested)  # Its queue starts with the snapshot.
    try:
        sender = asyncio.create_task(_forward(websocket, subscriber))
        receiver = asyncio.create_task(_discard_incoming(websocket))
        done, pending = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect):
                await task
        for task in done:
            failure = task.exception()
            if failure is not None and not isinstance(failure, WebSocketDisconnect):
                logger.error("stream %s failed", simulation_id, exc_info=failure)
    finally:
        session.hub.unsubscribe(subscriber)
        with contextlib.suppress(RuntimeError):  # Already closed by the client.
            await websocket.close()


async def _forward(websocket: WebSocket, subscriber: Subscriber) -> None:
    while True:
        message = await subscriber.queue.get()
        await websocket.send_text(message)
        if subscriber.closed and subscriber.queue.empty():
            return


async def _discard_incoming(websocket: WebSocket) -> None:
    """Reads and ignores client frames of either kind, so that a disconnect is noticed
    promptly."""
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
