"""Market data and manual order entry for a simulation (the active one by default)."""

from typing import Annotated

from fastapi import APIRouter, Query, status

from udeshi_api.deps import Sessions
from udeshi_api.errors import ApiError
from udeshi_api.schemas import (
    BarOut,
    CancelOut,
    MarketDetailOut,
    MarketOut,
    OpenOrderOut,
    OrderBookOut,
    OrderIn,
    OrderOut,
    TradeOut,
)
from udeshi_api.serialization import (
    aggregate_bars,
    bar_out,
    book_out,
    market_detail_out,
    market_out,
    trade_out,
)

router = APIRouter(prefix="/api/markets", tags=["markets"])

SimulationId = Annotated[
    str | None, Query(description="Defaults to the active (most recently created) simulation.")
]


@router.get("")
async def list_markets(sessions: Sessions, simulation_id: SimulationId = None) -> list[MarketOut]:
    session = sessions.resolve(simulation_id)
    exchange = session.simulation.exchange
    return [
        market_out(exchange, instrument, session.formats[instrument])
        for instrument in range(exchange.instrument_count)
    ]


@router.get("/{symbol}")
async def get_market(
    symbol: str, sessions: Sessions, simulation_id: SimulationId = None
) -> MarketDetailOut:
    session = sessions.resolve(simulation_id)
    instrument = session.instrument(symbol)
    return market_detail_out(session.simulation.exchange, instrument, session.formats[instrument])


@router.get("/{symbol}/orderbook")
async def get_order_book(
    symbol: str,
    sessions: Sessions,
    depth: Annotated[int, Query(ge=1, le=200)] = 10,
    simulation_id: SimulationId = None,
) -> OrderBookOut:
    session = sessions.resolve(simulation_id)
    instrument = session.instrument(symbol)
    simulation = session.simulation
    return book_out(
        simulation.exchange, instrument, depth, session.formats[instrument], simulation.now
    )


@router.get("/{symbol}/trades")
async def get_trades(
    symbol: str,
    sessions: Sessions,
    limit: Annotated[int, Query(ge=1, le=1_000)] = 50,
    simulation_id: SimulationId = None,
) -> list[TradeOut]:
    """The public tape, oldest first. Trades carry no order ids or trader identities."""
    session = sessions.resolve(simulation_id)
    instrument = session.instrument(symbol)
    fmt = session.formats[instrument]
    return [trade_out(t, fmt) for t in session.simulation.recorder.trades(instrument, limit)]


@router.get("/{symbol}/history")
async def get_history(
    symbol: str,
    sessions: Sessions,
    interval_seconds: Annotated[float | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=10_000)] = 500,
    simulation_id: SimulationId = None,
) -> list[BarOut]:
    """Closed OHLCV bars, oldest first. ``interval_seconds`` must be a whole multiple of the
    simulation's bar interval (the default)."""
    session = sessions.resolve(simulation_id)
    instrument = session.instrument(symbol)
    base = session.simulation.config.bar_interval_ns
    factor = 1
    if interval_seconds is not None:
        requested = round(interval_seconds * 1_000_000_000)
        if requested % base != 0:
            raise ApiError(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "invalid_interval",
                f"interval must be a multiple of the bar interval ({base / 1e9:g} s)",
            )
        factor = requested // base
    bars = aggregate_bars(session.simulation.recorder.bars(instrument), factor)
    fmt = session.formats[instrument]
    return [bar_out(bar, fmt) for bar in bars[-limit:]]


@router.get("/{symbol}/orders")
async def list_open_orders(
    symbol: str, sessions: Sessions, simulation_id: SimulationId = None
) -> list[OpenOrderOut]:
    """Your (the API user's) resting orders in this market."""
    return sessions.resolve(simulation_id).open_orders(symbol)


@router.post("/{symbol}/orders", status_code=status.HTTP_201_CREATED)
async def submit_order(
    symbol: str, order: OrderIn, sessions: Sessions, simulation_id: SimulationId = None
) -> OrderOut:
    """Submits an order as the API user, processed immediately at the current simulated time."""
    return sessions.resolve(simulation_id).submit(symbol, order)


@router.delete("/{symbol}/orders/{order_id}")
async def cancel_order(
    symbol: str, order_id: int, sessions: Sessions, simulation_id: SimulationId = None
) -> CancelOut:
    """Cancels one of your resting orders."""
    return sessions.resolve(simulation_id).cancel(symbol, order_id)
