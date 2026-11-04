"""Building a market: a population of agents plus the market environment."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from udeshi_markets.agents.base import TradingAgent
from udeshi_markets.agents.strategies import (
    FundamentalTrader,
    MarketMaker,
    MomentumTrader,
    PairsTrader,
    RandomTrader,
)
from udeshi_markets.simulation import (
    ENVIRONMENT_ID,
    EnvironmentConfig,
    MarketEnvironment,
    PairConfig,
    Participant,
    Simulation,
    SimulationConfig,
)

MAX_AGENTS = 10_000


@dataclass(frozen=True, slots=True)
class PopulationConfig:
    """How many agents of each strategy, and how often they act (1.0: the defaults; 2.0: twice
    as often)."""

    random_traders: int = 10
    market_makers: int = 5
    momentum_traders: int = 5
    fundamental_traders: int = 5
    pairs_traders: int = 1
    order_frequency: float = 1.0

    def __post_init__(self) -> None:
        counts = self.counts()
        if any(count < 0 for count in counts.values()):
            raise ValueError("agent counts must be non-negative")
        if sum(counts.values()) > MAX_AGENTS:
            raise ValueError(f"at most {MAX_AGENTS} agents")
        if not (math.isfinite(self.order_frequency) and self.order_frequency > 0):
            raise ValueError("order_frequency must be positive")

    def counts(self) -> dict[str, int]:
        return {
            RandomTrader.strategy: self.random_traders,
            MarketMaker.strategy: self.market_makers,
            MomentumTrader.strategy: self.momentum_traders,
            FundamentalTrader.strategy: self.fundamental_traders,
            PairsTrader.strategy: self.pairs_traders,
        }


def build_agents(
    config: SimulationConfig,
    population: PopulationConfig,
    pairs: Sequence[PairConfig] = (),
) -> list[TradingAgent]:
    """Agents with ids 1, 2, ..., one instrument each (pairs traders: one pair each), assigned
    round-robin so every instrument gets its share of every strategy."""
    if population.pairs_traders and not pairs:
        raise ValueError("pairs traders need at least one pair in the environment")
    symbols = {symbol: i for i, symbol in enumerate(config.symbols)}
    count = len(symbols)
    frequency = population.order_frequency
    agents: list[TradingAgent] = []

    def next_id() -> int:
        return len(agents) + 1

    for k in range(population.market_makers):
        agents.append(MarketMaker(next_id(), [k % count], frequency=frequency))
    for k in range(population.random_traders):
        agents.append(RandomTrader(next_id(), [k % count], frequency=frequency))
    for k in range(population.momentum_traders):
        agents.append(MomentumTrader(next_id(), [k % count], frequency=frequency))
    for k in range(population.fundamental_traders):
        agents.append(FundamentalTrader(next_id(), [k % count], frequency=frequency))
    for k in range(population.pairs_traders):
        pair = pairs[k % len(pairs)]
        if pair.leader not in symbols or pair.follower not in symbols:
            raise ValueError(f"pair {pair.leader}/{pair.follower} names an unknown symbol")
        agents.append(
            PairsTrader(
                next_id(), symbols[pair.leader], symbols[pair.follower], frequency=frequency
            )
        )
    if len(agents) >= ENVIRONMENT_ID:
        raise ValueError("too many agents")
    return agents


def market_simulation(
    config: SimulationConfig,
    population: PopulationConfig | None = None,
    environment: EnvironmentConfig | None = None,
    *,
    extra: Callable[[], Sequence[Participant]] | None = None,
    track_changes: bool = False,
) -> Simulation:
    """A simulation of agents trading in a market environment (both rebuilt on every reset).
    ``extra`` adds further participants, such as the API's manual trader."""
    population = population or PopulationConfig()
    environment = environment or EnvironmentConfig()

    def participants() -> list[Participant]:
        built: list[Participant] = list(build_agents(config, population, environment.pairs))
        if extra is not None:
            built.extend(extra())
        return built

    return Simulation(
        config,
        participants,
        environment=lambda: MarketEnvironment(environment),
        track_changes=track_changes,
    )
