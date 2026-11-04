"""The market environment: fundamentals, news, shocks, regimes, pairs and private signals."""

from __future__ import annotations

import math

import pytest

from udeshi_markets import Side
from udeshi_markets.simulation import (
    ENVIRONMENT_ID,
    MINUTE,
    SECOND,
    EnvironmentConfig,
    InstrumentConfig,
    MarketEnvironment,
    News,
    OrderIntent,
    PairConfig,
    Participant,
    ScriptedOrder,
    ScriptedParticipant,
    Simulation,
    SimulationConfig,
    SimulationContext,
    SimulationStateError,
    deterministic_log,
)

TWO = (InstrumentConfig("AAA", 100.0), InstrumentConfig("BBB", 50.0))
QUIET = EnvironmentConfig(
    volatility=0.0, news_per_hour=0.0, shocks_per_hour=0.0, liquidity_shocks_per_hour=0.0
)


def simulation(
    env: EnvironmentConfig,
    participants: list[Participant] | None = None,
    duration: int = 10 * MINUTE,
    seed: int = 4,
) -> Simulation:
    config = SimulationConfig(instruments=TWO, duration_ns=duration, seed=seed)
    return Simulation(
        config, lambda: list(participants or []), environment=lambda: MarketEnvironment(env)
    )


def environment_of(sim: Simulation) -> MarketEnvironment:
    assert sim.environment is not None
    return sim.environment


def test_a_quiet_environment_keeps_the_reference_values() -> None:
    sim = simulation(QUIET)
    sim.run()
    env = environment_of(sim)
    assert (env.fundamental(0), env.fundamental(1)) == (100.0, 50.0)


def test_fundamentals_are_reproducible_and_seed_dependent() -> None:
    def path(seed: int) -> list[float]:
        sim = simulation(EnvironmentConfig(), seed=seed)
        sim.start()
        values = []
        for minute in range(1, 6):
            sim.run_until(minute * MINUTE)
            values.append(environment_of(sim).fundamental(0))
        return values

    assert path(1) == path(1)
    assert path(1) != path(2)


def test_news_moves_the_fundamental_by_its_impact_and_reaches_everyone() -> None:
    received: list[tuple[int, int, News]] = []

    class Reader(Participant):
        def on_news(self, ctx: SimulationContext, news: News) -> None:
            received.append((self.participant_id, ctx.now, news))

    env_config = EnvironmentConfig(
        volatility=0.0, news_per_hour=60.0, shocks_per_hour=0.0, liquidity_shocks_per_hour=0.0
    )
    sim = simulation(env_config, [Reader(2), Reader(1)], duration=30 * MINUTE)
    sim.run()
    env = environment_of(sim)
    news = sim.recent_news(1000)
    assert len(news) == env.news_published > 10
    for instrument, start in ((0, 100.0), (1, 50.0)):
        total_impact = sum(item.impact for item in news if item.instrument == instrument)
        log_move = deterministic_log(env.fundamental(instrument)) - deterministic_log(start)
        assert math.isclose(log_move, total_impact, abs_tol=1e-12)
    # Every item reaches every participant once, in id order, at its own time.
    assert [(pid, now) for pid, now, _ in received[:2]] == [(1, news[0].time), (2, news[0].time)]
    assert len(received) == 2 * len(news)
    assert all(item.sentiment == (1 if item.impact > 0 else -1) for item in news)


def test_unannounced_shocks_move_values_without_news() -> None:
    env_config = EnvironmentConfig(
        volatility=0.0, news_per_hour=0.0, shocks_per_hour=60.0, liquidity_shocks_per_hour=0.0
    )
    sim = simulation(env_config)
    sim.run()
    env = environment_of(sim)
    assert env.shocks > 0
    assert sim.recent_news(1000) == []
    assert env.fundamental(0) != 100.0


def test_a_follower_moves_with_its_leader_plus_a_reverting_deviation() -> None:
    exact = EnvironmentConfig(
        news_per_hour=0.0,
        shocks_per_hour=0.0,
        liquidity_shocks_per_hour=0.0,
        pairs=(PairConfig("AAA", "BBB", deviation_volatility=0.0),),
    )
    sim = simulation(exact)
    sim.run()
    env = environment_of(sim)
    leader_move = deterministic_log(env.fundamental(0)) - deterministic_log(100.0)
    follower_move = deterministic_log(env.fundamental(1)) - deterministic_log(50.0)
    assert leader_move != 0.0
    assert math.isclose(follower_move, leader_move, abs_tol=1e-12)

    noisy = EnvironmentConfig(
        volatility=0.0,
        news_per_hour=0.0,
        shocks_per_hour=0.0,
        liquidity_shocks_per_hour=0.0,
        pairs=(PairConfig("AAA", "BBB", half_life_seconds=30.0, deviation_volatility=0.02),),
    )
    sim = simulation(noisy, duration=60 * MINUTE)
    sim.start()
    deviations = []
    for second in range(30, 3600, 30):
        sim.run_until(second * SECOND)
        deviations.append(deterministic_log(environment_of(sim).fundamental(1) / 50.0))
    # Stationary with standard deviation 0.02: it stays within a few deviations of zero.
    assert max(abs(d) for d in deviations) < 0.1
    assert abs(sum(deviations) / len(deviations)) < 0.02


def test_liquidity_shocks_are_market_orders_from_the_environment() -> None:
    env_config = EnvironmentConfig(
        volatility=0.0,
        news_per_hour=0.0,
        shocks_per_hour=0.0,
        liquidity_shocks_per_hour=120.0,
        liquidity_shock_quantity=7,
    )
    makers = [
        ScriptedParticipant(
            1,
            [
                ScriptedOrder(0, OrderIntent(i, side, 10_000, price=price))
                for i, (bid, ask) in enumerate(((9_990, 10_010), (4_990, 5_010)))
                for side, price in ((Side.BUY, bid), (Side.SELL, ask))
            ],
        )
    ]
    sim = simulation(env_config, list(makers))
    sim.run()
    env = environment_of(sim)
    assert env.liquidity_shocks > 0
    portfolio = sim.portfolio(ENVIRONMENT_ID)
    traded = sum(portfolio.details(i).bought + portfolio.details(i).sold for i in (0, 1))
    assert traded == 7 * env.liquidity_shocks


def test_regimes_switch_trading_activity() -> None:
    env_config = EnvironmentConfig(calm_seconds=20.0, volatile_seconds=20.0, volatile_activity=3)
    seen: set[float] = set()

    class Watcher(Participant):
        def on_start(self, ctx: SimulationContext) -> None:
            ctx.wakeup(SECOND)

        def on_wakeup(self, ctx: SimulationContext, tag: str) -> None:
            seen.add(ctx.activity)
            ctx.wakeup(SECOND)

    sim = simulation(env_config, [Watcher(1)])
    sim.run()
    assert seen == {1.0, 3.0}
    assert environment_of(sim).regime_changes > 2


def test_only_participants_granted_a_signal_can_estimate_fundamentals() -> None:
    estimates: list[float] = []

    class Analyst(Participant):
        signal_noise = 0.0

        def on_start(self, ctx: SimulationContext) -> None:
            estimates.append(ctx.fundamental_estimate(0))

    class Outsider(Participant):
        def on_start(self, ctx: SimulationContext) -> None:
            with pytest.raises(PermissionError, match="no fundamental signal"):
                ctx.fundamental_estimate(0)
            estimates.append(-1.0)

    sim = simulation(QUIET, [Analyst(1), Outsider(2)])
    sim.start()
    assert estimates == [10_000.0, -1.0]  # In ticks; exact with zero noise.

    class Lost(Participant):
        signal_noise = 0.01

        def on_start(self, ctx: SimulationContext) -> None:
            with pytest.raises(SimulationStateError, match="no market environment"):
                ctx.fundamental_estimate(0)

    Simulation(SimulationConfig(instruments=TWO), lambda: [Lost(1)]).start()


def test_noisy_signals_scatter_around_the_true_value() -> None:
    estimates: list[float] = []

    class Analyst(Participant):
        signal_noise = 0.01

        def on_start(self, ctx: SimulationContext) -> None:
            estimates.extend(ctx.fundamental_estimate(0) for _ in range(2000))

    simulation(QUIET, [Analyst(1)]).start()
    logs = [deterministic_log(e / 10_000) for e in estimates]
    mean = sum(logs) / len(logs)
    stdev = math.sqrt(sum((x - mean) ** 2 for x in logs) / len(logs))
    assert abs(mean) < 0.001
    assert 0.009 < stdev < 0.011


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"volatility": -0.1}, "volatility"),
        ({"news_impact": math.inf}, "news_impact"),
        ({"volatile_multiplier": 0.5}, "volatile_multiplier"),
        ({"calm_seconds": 0}, "regime durations"),
        ({"liquidity_shock_quantity": 0}, "liquidity_shock_quantity"),
        (
            {"pairs": (PairConfig("A", "B"), PairConfig("C", "B"))},
            "follow only one leader",
        ),
        ({"pairs": (PairConfig("A", "B"), PairConfig("B", "C"))}, "cannot also lead"),
    ],
)
def test_environment_configuration_is_validated(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        EnvironmentConfig(**kwargs)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="two different"):
        PairConfig("A", "A")


def test_the_environment_must_be_installed_as_the_environment() -> None:
    sim = Simulation(
        SimulationConfig(instruments=TWO), lambda: [MarketEnvironment(EnvironmentConfig())]
    )
    with pytest.raises(TypeError, match="environment="):
        sim.start()
