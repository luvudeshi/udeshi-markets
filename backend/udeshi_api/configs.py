"""Turning the API's agent and market settings into the library's configurations (shared by
live simulations and experiments)."""

from udeshi_api.schemas import AgentsIn, MarketIn
from udeshi_markets.agents import PopulationConfig
from udeshi_markets.simulation import EnvironmentConfig, PairConfig

DEFAULT_PAIR = ("ACME", "NOVA")
"""The pair used when no pairs are given, if both symbols exist."""


def market_configs(
    agents: AgentsIn | None, market: MarketIn, symbols: set[str]
) -> tuple[PopulationConfig | None, EnvironmentConfig]:
    """The population (None without ``agents``) and environment. Raises ValueError for anything
    the library rejects, or for pairs traders without a pair."""
    pairs: tuple[PairConfig, ...]
    if market.pairs is None:
        pairs = (PairConfig(*DEFAULT_PAIR),) if set(DEFAULT_PAIR) <= symbols else ()
    else:
        for pair in market.pairs:
            unknown = sorted({pair.leader, pair.follower} - symbols)
            if unknown:
                raise ValueError(f"pair {pair.leader}/{pair.follower}: unknown {unknown}")
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
        if agents is None
        else PopulationConfig(
            random_traders=agents.random_traders,
            market_makers=agents.market_makers,
            momentum_traders=agents.momentum_traders,
            fundamental_traders=agents.fundamental_traders,
            pairs_traders=agents.pairs_traders,
            order_frequency=agents.order_frequency,
        )
    )
    if population is not None and population.pairs_traders and not environment.pairs:
        raise ValueError("pairs traders need a pair: add one under market.pairs")
    return population, environment
