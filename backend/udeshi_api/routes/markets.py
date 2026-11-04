"""Market data and manual order entry for a simulation (the active one by default)."""

from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from udeshi_api.deps import Sessions
from udeshi_api.errors import ApiError
from udeshi_api.schemas import (
    MAX_DURATION_SECONDS,
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
    bar_out,
    book_out,
    market_detail_out,
    market_out,
    recent_bars,
)
from udeshi_api.settings import MAX_BOOK_DEPTH

router = APIRouter(prefix="/api/markets", tags=["markets"])

SimulationId = Annotated[
    str | None, Query(description="Defaults to the active (most recently created) simulation.")
]


@router.get("")
async def list_markets(sessions: Sessions, simulation_id: SimulationId = None) -> list[MarketOut]:
    session = sessions.resolve(simulation_id)
    exchange = session.simulation.exchange
    return [
        market_out(exchange, i, session.formats[i], session.fundamental(i))
        for i in range(exchange.instrument_count)
    ]


@router.get("/{symbol}")
async def get_market(
    symbol: str, sessions: Sessions, simulation_id: SimulationId = None
) -> MarketDetailOut:
    session = sessions.resolve(simulation_id)
    instrument = session.instrument(symbol)
    return market_detail_out(
        session.simulation.exchange,
        instrument,
        session.formats[instrument],
        session.fundamental(instrument),
    )


@router.get("/{symbol}/orderbook")
async def get_order_book(
    symbol: str,
    sessions: Sessions,
    depth: Annotated[int, Query(ge=1, le=MAX_BOOK_DEPTH)] = 10,
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
    """The tape, oldest first, with each trade's buyer and seller: the observer's view (agents
    see an anonymous tape). No order ids."""
    session = sessions.resolve(simulation_id)
    return session.trades(session.instrument(symbol), limit)


@router.get("/{symbol}/history")
async def get_history(
    symbol: str,
    sessions: Sessions,
    interval_seconds: Annotated[
        float | None, Query(gt=0, le=MAX_DURATION_SECONDS, allow_inf_nan=False)
    ] = None,
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
        if requested < base or requested % base != 0:
            raise ApiError(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "invalid_interval",
                f"interval must be a whole multiple of the bar interval ({base / 1e9:g} s)",
            )
        factor = requested // base
    bars = recent_bars(session.simulation.recorder.bars(instrument), base, factor, limit)
    fmt = session.formats[instrument]
    return [bar_out(bar, fmt) for bar in bars]


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
    symbol: str,
    order_id: Annotated[int, Path(ge=1, le=(1 << 64) - 1)],
    sessions: Sessions,
    simulation_id: SimulationId = None,
) -> CancelOut:
    """Cancels one of your resting orders."""
    return sessions.resolve(simulation_id).cancel(symbol, order_id)
