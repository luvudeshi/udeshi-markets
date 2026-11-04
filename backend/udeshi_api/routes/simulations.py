"""Simulation lifecycle: create, list, start / pause / resume / stop / reset, speed and step."""

from fastapi import APIRouter, Response, status

from udeshi_api.deps import Sessions
from udeshi_api.errors import ApiError
from udeshi_api.schemas import MarketIn, SimulationCreate, SimulationOut, SpeedIn, StepIn
from udeshi_api.settings import Settings
from udeshi_markets.agents import PopulationConfig
from udeshi_markets.simulation import (
    DEFAULT_INSTRUMENTS,
    EnvironmentConfig,
    InstrumentConfig,
    PairConfig,
    SimulationConfig,
    seconds,
)

router = APIRouter(prefix="/api/simulations", tags=["simulations"])

DEFAULT_PAIR = ("ACME", "NOVA")
"""The pair used when agents are requested and no pairs are given, if both symbols exist."""


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
    market = body.market or MarketIn()
    symbols = set(config.symbols)
    try:
        pairs: tuple[PairConfig, ...]
        if market.pairs is None:
            default = set(DEFAULT_PAIR) <= symbols
            pairs = (PairConfig(*DEFAULT_PAIR),) if default else ()
        else:
            for pair in market.pairs:
                unknown = sorted({pair.leader, pair.follower} - symbols)
                if unknown:
                    raise _invalid(f"pair {pair.leader}/{pair.follower}: unknown {unknown}")
            pairs = tuple(
                PairConfig(p.leader, p.follower, p.half_life_seconds, p.deviation_volatility)
                for p in market.pairs
            )
        environment = EnvironmentConfig(
            volatility=market.volatility,
            volatile_multiplier=market.volatile_multiplier,
            news_per_hour=market.news_per_hour,
            news_impact=market.news_impact,
            shocks_per_hour=market.shocks_per_hour,
            shock_impact=market.shock_impact,
            liquidity_shocks_per_hour=market.liquidity_shocks_per_hour,
            liquidity_shock_quantity=market.liquidity_shock_quantity,
            pairs=pairs,
        )
        population = (
            None
            if body.agents is None
            else PopulationConfig(
                random_traders=body.agents.random_traders,
                market_makers=body.agents.market_makers,
                momentum_traders=body.agents.momentum_traders,
                fundamental_traders=body.agents.fundamental_traders,
                pairs_traders=body.agents.pairs_traders,
                order_frequency=body.agents.order_frequency,
            )
        )
    except ValueError as error:
        raise _invalid(str(error)) from error
    if population is not None and population.pairs_traders and not environment.pairs:
        raise _invalid("pairs traders need a pair: add one under market.pairs")
    return population, environment


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
