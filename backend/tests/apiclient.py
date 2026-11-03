"""HTTP helpers shared by the API test modules."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from udeshi_api.sessions import SessionManager

ONE_MARKET: dict[str, Any] = {
    "instruments": [{"symbol": "ACME", "reference_price": 1.00, "tick_size": 0.01}]
}
TWO_MARKETS: dict[str, Any] = {
    "instruments": [
        {"symbol": "ACME", "reference_price": 1.00, "tick_size": 0.01},
        {"symbol": "NOVA", "reference_price": 2.00, "tick_size": 0.01},
    ]
}
FROZEN = 1e-9
"""A speed at which a 10 ms runner tick advances simulated time by round(0.01) = 0 ns, so the
clock stays at zero and runs are reproducible to the nanosecond."""


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


def get(client: TestClient, path: str, **params: Any) -> Any:
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def error_code(response: Any) -> str:
    code: str = response.json()["error"]["code"]
    return code


def started_paused(client: TestClient, **body: Any) -> str:
    simulation_id: str = create(client, **{**ONE_MARKET, **body})["id"]
    action(client, simulation_id, "start")
    action(client, simulation_id, "pause")
    return simulation_id


def cross(client: TestClient, symbol: str, price: float, quantity: int, aggressor: str) -> None:
    """One trade of ``quantity`` at ``price``: rests the passive side, then the aggressor."""
    passive = "sell" if aggressor == "buy" else "buy"
    order(client, symbol, side=passive, price=price, quantity=quantity)
    order(client, symbol, side=aggressor, price=price, quantity=quantity)


def manager(client: TestClient) -> SessionManager:
    sessions = client.app.state.sessions  # type: ignore[attr-defined]
    assert isinstance(sessions, SessionManager)
    return sessions
