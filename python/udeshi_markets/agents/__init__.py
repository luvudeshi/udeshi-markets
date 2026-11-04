"""Autonomous trading agents and the market they trade in."""

from udeshi_markets.agents.base import ACT, AgentStats, OpenOrder, TradingAgent
from udeshi_markets.agents.population import (
    MAX_AGENTS,
    PopulationConfig,
    build_agents,
    market_simulation,
)
from udeshi_markets.agents.strategies import (
    FundamentalTrader,
    MarketMaker,
    MomentumTrader,
    PairsTrader,
    RandomTrader,
)

STRATEGIES: tuple[type[TradingAgent], ...] = (
    RandomTrader,
    MarketMaker,
    MomentumTrader,
    FundamentalTrader,
    PairsTrader,
)

__all__ = [
    "ACT",
    "MAX_AGENTS",
    "STRATEGIES",
    "AgentStats",
    "FundamentalTrader",
    "MarketMaker",
    "MomentumTrader",
    "OpenOrder",
    "PairsTrader",
    "PopulationConfig",
    "RandomTrader",
    "TradingAgent",
    "build_agents",
    "market_simulation",
]
