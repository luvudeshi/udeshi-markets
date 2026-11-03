"""FastAPI dependencies."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, WebSocket

from udeshi_api.sessions import SessionManager


def _manager(app_state: object) -> SessionManager:
    manager = getattr(app_state, "sessions", None)
    if not isinstance(manager, SessionManager):
        raise HTTPException(status_code=500, detail="session manager not configured")
    return manager


def get_sessions(request: Request) -> SessionManager:
    return _manager(request.app.state)


def get_sessions_ws(websocket: WebSocket) -> SessionManager:
    return _manager(websocket.app.state)


Sessions = Annotated[SessionManager, Depends(get_sessions)]
