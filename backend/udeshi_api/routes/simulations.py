"""Simulation lifecycle: create, list, start / pause / resume / stop / reset, speed and step."""

from fastapi import APIRouter, Response, status

from udeshi_api.configs import market_configs
from udeshi_api.deps import Sessions
from udeshi_api.errors import ApiError
from udeshi_api.experiment_schemas import AnalyticsOut
from udeshi_api.schemas import MarketIn, SimulationCreate, SimulationOut, SpeedIn, StepIn
from udeshi_api.settings import Settings
from udeshi_markets.agents import PopulationConfig
from udeshi_markets.simulation import (
    DEFAULT_INSTRUMENTS,
    EnvironmentConfig,
    InstrumentConfig,
    SimulationConfig,
    seconds,
)

router = APIRouter(prefix="/api/simulations", tags=["simulations"])


def _config(body: SimulationCreate, settings: Settings) -> SimulationConfig:
    try:
        instruments = (
            DEFAULT_INSTRUMENTS
            if body.instruments is None
            else tuple(
                InstrumentConfig(i.symbol, i.reference_price, i.tick_size) for i in body.instruments
            )
        )
        config = SimulationConfig(
            instruments=instruments,
            duration_ns=seconds(body.duration_seconds),
            seed=body.seed,
            order_latency_ns=round(body.order_latency_ms * 1_000_000),
            bar_interval_ns=seconds(body.bar_interval_seconds),
            initial_cash=body.initial_cash,
            trade_history_limit=settings.trade_history_limit,
        )
    except ValueError as error:
        raise _invalid(str(error)) from error
    per_instrument = -(-config.duration_ns // config.bar_interval_ns)  # Ceiling division.
    bars = per_instrument * len(config.instruments)
    if bars > settings.max_bars_per_simulation:
        raise _invalid(
            f"{len(config.instruments)} instruments x {per_instrument} bars = {bars} bars, more "
            f"than the limit of {settings.max_bars_per_simulation}: use a wider bar interval "
            "or a shorter duration"
        )
    return config


def _market(
    body: SimulationCreate, config: SimulationConfig
) -> tuple[PopulationConfig | None, EnvironmentConfig | None]:
    """The agent population and market environment requested (both None: neither)."""
    if body.agents is None and body.market is None:
        return None, None
    try:
        return market_configs(body.agents, body.market or MarketIn(), set(config.symbols))
    except ValueError as error:
        raise _invalid(str(error)) from error


def _invalid(message: str) -> ApiError:
    return ApiError(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_configuration", message)


@router.get("")
async def list_simulations(sessions: Sessions) -> list[SimulationOut]:
    return [s.describe(active=sessions.is_active(s)) for s in sessions.all()]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_simulation(body: SimulationCreate, sessions: Sessions) -> SimulationOut:
    config = _config(body, sessions.settings)
    population, environment = _market(body, config)
    session = sessions.create(config, body.speed, population=population, environment=environment)
    return session.describe(active=True)


@router.get("/{simulation_id}")
async def get_simulation(simulation_id: str, sessions: Sessions) -> SimulationOut:
    session = sessions.get(simulation_id)
    return session.describe(active=sessions.is_active(session))


@router.delete("/{simulation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_simulation(simulation_id: str, sessions: Sessions) -> Response:
    await sessions.delete(simulation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{simulation_id}/start")
async def start(simulation_id: str, sessions: Sessions) -> SimulationOut:
    session = sessions.get(simulation_id)
    session.start()
    return session.describe(active=sessions.is_active(session))


@router.post("/{simulation_id}/pause")
async def pause(simulation_id: str, sessions: Sessions) -> SimulationOut:
    session = sessions.get(simulation_id)
    session.pause()
    return session.describe(active=sessions.is_active(session))


@router.post("/{simulation_id}/resume")
async def resume(simulation_id: str, sessions: Sessions) -> SimulationOut:
    session = sessions.get(simulation_id)
    session.resume()
    return session.describe(active=sessions.is_active(session))


@router.post("/{simulation_id}/stop")
async def stop(simulation_id: str, sessions: Sessions) -> SimulationOut:
    session = sessions.get(simulation_id)
    await session.stop()
    return session.describe(active=sessions.is_active(session))


@router.post("/{simulation_id}/reset")
async def reset(simulation_id: str, sessions: Sessions) -> SimulationOut:
    session = sessions.get(simulation_id)
    await session.reset()
    return session.describe(active=sessions.is_active(session))


@router.post("/{simulation_id}/speed")
async def set_speed(simulation_id: str, body: SpeedIn, sessions: Sessions) -> SimulationOut:
    session = sessions.get(simulation_id)
    session.set_speed(body.speed)
    return session.describe(active=sessions.is_active(session))


@router.post("/{simulation_id}/step")
async def step(simulation_id: str, body: StepIn, sessions: Sessions) -> SimulationOut:
    session = sessions.get(simulation_id)
    session.step(body.events)
    return session.describe(active=sessions.is_active(session))


@router.get("/{simulation_id}/analytics")
async def get_analytics(simulation_id: str, sessions: Sessions) -> AnalyticsOut:
    """Market, microstructure and per-agent metrics of the run so far (simulations with agents
    or a market environment). Live sampling happens as the runner advances: markouts are read at
    the first sampling stop at or after their horizon."""
    return sessions.get(simulation_id).analytics()
