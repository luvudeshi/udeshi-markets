"""The running aggregates against the batch definitions they replace: bit for bit where the
arithmetic is the same, within 1e-12 where Welford's algorithm stands in for the two-pass
variance. Then whole runs: ``analyze()`` against the batch computation over the full record."""

from __future__ import annotations

import random
import struct
from collections.abc import Sequence

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from udeshi_markets.agents import market_simulation
from udeshi_markets.analytics import MarketObserver, analyze, run_observed
from udeshi_markets.analytics.observer import BPS
from udeshi_markets.analytics.running import (
    FloatSum,
    RunningDrawdown,
    RunningMean,
    RunningSharpe,
    RunningVariance,
)
from udeshi_markets.analytics.stats import (
    max_drawdown,
    mean,
    realized_volatility,
    sample_variance,
    sharpe_ratio,
    simple_returns,
    weighted_mean,
)
from udeshi_markets.simulation import MINUTE, SimulationConfig, deterministic_log

finite = st.floats(allow_nan=False, allow_infinity=False)


def bits(value: float) -> bytes:
    return struct.pack("<d", value)


def same(a: float | None, b: float | None) -> bool:
    """Equal bit for bit (so 0.0 and -0.0 differ), or both None."""
    if a is None or b is None:
        return a is None and b is None
    return bits(a) == bits(b)


# ---- Accumulators ------------------------------------------------------------------------------


@settings(max_examples=500, deadline=None)
@given(st.lists(finite, min_size=1, max_size=60))
def test_float_sum_is_the_builtin_sum_bit_for_bit(values: list[float]) -> None:
    total = FloatSum()
    for value in values:
        total.add(value)
    assert same(total.value, sum(values))


@pytest.mark.parametrize(
    "values",
    [
        [-0.0],
        [-0.0, -0.0],
        [1e16, 1.0, -1e16],  # Compensation recovers what plain addition loses.
        [1e308, 1e308, -1e308],  # Overflow stays infinite, never NaN.
        [0.1] * 10,
    ],
)
def test_float_sum_edge_cases(values: list[float]) -> None:
    total = FloatSum()
    for value in values:
        total.add(value)
    assert same(total.value, sum(values))


@settings(max_examples=500, deadline=None)
@given(st.lists(st.floats(-1e12, 1e12), min_size=1, max_size=60))
def test_running_mean_is_stats_mean_bit_for_bit(values: list[float]) -> None:
    running = RunningMean()
    for value in values:
        running.add(value)
    assert same(running.value, mean(values))


@settings(max_examples=200, deadline=None)
@given(st.lists(st.integers(-(10**6), 10**6), min_size=1, max_size=60))
def test_running_mean_of_integers_is_exact(values: list[int]) -> None:
    running = RunningMean()
    for value in values:
        running.add(value)
    assert same(running.value, mean(values))


def series(seed: int, length: int, scale: float, level: float) -> list[float]:
    rng = random.Random(seed)
    return [level + rng.gauss(0.0, scale) for _ in range(length)]


@pytest.mark.parametrize(
    ("seed", "length", "scale", "level"),
    [(1, 2, 1.0, 0.0), (2, 50, 1e-4, 0.0), (3, 2_000, 1.0, 1e6), (4, 500, 1e-9, 1.0)],
)
def test_running_variance_matches_the_two_pass_variance(
    seed: int, length: int, scale: float, level: float
) -> None:
    values = series(seed, length, scale, level)
    running = RunningVariance()
    for value in values:
        running.add(value)
    expected = sample_variance(values)
    assert expected is not None
    assert running.value == pytest.approx(expected, rel=1e-12)


def test_running_variance_is_exactly_zero_or_undefined_like_the_batch() -> None:
    for values in ([], [3.0], [2.5, 2.5, 2.5], [1e6] * 100):
        running = RunningVariance()
        for value in values:
            running.add(value)
        assert running.value == sample_variance(values)
    flat = RunningVariance()
    for value in [7.0] * 10:
        flat.add(value)
    assert same(flat.value, 0.0)


def sharpe_of(values: Sequence[float], periods: float) -> float | None:
    running = RunningSharpe()
    for value in values:
        running.add(value)
    return running.ratio(periods)


@pytest.mark.parametrize("seed", range(5))
def test_running_sharpe_matches_the_batch_ratio(seed: int) -> None:
    rng = random.Random(seed)
    equity = [1_000_000.0]
    for _ in range(3_600):
        equity.append(equity[-1] * (1 + rng.gauss(0.0001, 0.001)))
    expected = sharpe_ratio(simple_returns(equity), 3600.0)
    assert expected is not None
    assert sharpe_of(equity, 3600.0) == pytest.approx(expected, rel=1e-12)


@pytest.mark.parametrize(
    "equity",
    [
        [],
        [1.0],
        [1.0, 2.0],  # One return: no standard deviation.
        [5.0, 5.0, 5.0, 5.0],  # Zero variance: undefined.
        [2.0, 4.0, 8.0, 16.0],  # Every return 1.0: zero variance again.
        [0.0, 1.0, 2.0, 3.0],  # A period from zero has no return.
        [-1.0, 1.0, -1.0, 2.0, 3.0],  # Nor does one from a negative value.
    ],
)
def test_running_sharpe_is_undefined_exactly_where_the_batch_is(equity: list[float]) -> None:
    expected = sharpe_ratio(simple_returns(equity), 60.0)
    got = sharpe_of(equity, 60.0)
    assert got is None if expected is None else got == pytest.approx(expected, rel=1e-12)


@settings(max_examples=500, deadline=None)
@given(st.lists(st.floats(-1e9, 1e9), min_size=0, max_size=60))
def test_running_drawdown_is_max_drawdown_exactly(values: list[float]) -> None:
    running = RunningDrawdown()
    for value in values:
        running.add(value)
    assert running.value == max_drawdown(values)


# ---- Whole runs --------------------------------------------------------------------------------


def batch_market(observer: MarketObserver, instrument: int) -> dict[str, float | None]:
    """The market metrics as computed from the full record before the running aggregates."""
    tick = observer.simulation.exchange.instrument(instrument).tick_size
    series = observer.series[instrument]
    two_sided = [
        (mid, spread)
        for mid, spread in zip(series.mids, series.spreads, strict=True)
        if mid is not None and spread is not None
    ]
    depths = [b + a for b, a in zip(series.bid_depth, series.ask_depth, strict=True)]
    imbalances = [
        (b - a) / (b + a)
        for b, a in zip(series.bid_depth, series.ask_depth, strict=True)
        if b + a > 0
    ]
    errors = [
        abs(deterministic_log(mid * tick) - deterministic_log(value))
        for mid, value in zip(series.mids, series.fundamentals, strict=True)
        if mid is not None and value is not None and value > 0
    ]
    samples = len(series.mids)
    spread_mean = mean([spread for _, spread in two_sided])
    return {
        "volatility_per_hour": realized_volatility(series.mids, observer.periods_per_hour),
        "mean_spread": None if spread_mean is None else spread_mean * tick,
        "mean_spread_bps": mean([spread / mid * BPS for mid, spread in two_sided]),
        "one_sided_fraction": (samples - len(two_sided)) / samples if samples else 0.0,
        "mean_depth": mean([float(d) for d in depths]),
        "mean_imbalance": mean(imbalances),
        "pricing_error_bps": None if not errors else (mean(errors) or 0.0) * BPS,
    }


def batch_microstructure(observer: MarketObserver, instrument: int) -> dict[str, float | None]:
    trades = [(index, t) for index, t in enumerate(observer.trades) if t.instrument == instrument]
    effective: list[float] = []
    realized: list[float] = []
    impact: list[float] = []
    weights: list[float] = []
    without_quote = without_markout = 0
    for index, trade in trades:
        if trade.mid_before is None:
            without_quote += 1
            continue
        after = observer.mid_after.get(index)
        if after is None:
            without_markout += 1
            continue
        m, m_after, p, s = float(trade.mid_before), float(after), trade.price, trade.sign
        effective.append(2 * s * (p - m) / m * BPS)
        realized.append(2 * s * (p - m_after) / m * BPS)
        impact.append(2 * s * (m_after - m) / m * BPS)
        weights.append(float(trade.quantity))
    volume = sum(t.quantity for _, t in trades)
    return {
        "effective_spread_bps": weighted_mean(effective, weights),
        "realized_spread_bps": weighted_mean(realized, weights),
        "price_impact_bps": weighted_mean(impact, weights),
        "measured_trades": len(weights),
        "trades_without_quote": without_quote,
        "trades_without_markout": without_markout,
        "average_trade_size": volume / len(trades) if trades else None,
        "buyer_initiated_fraction": (
            sum(1 for _, t in trades if t.sign > 0) / len(trades) if trades else None
        ),
    }


def batch_agent(observer: MarketObserver, participant_id: int) -> dict[str, float | None]:
    equity = observer.equity[participant_id]
    exposure = observer.exposure[participant_id]
    drawdown = max_drawdown(equity)
    return {
        "sharpe_ratio": sharpe_ratio(simple_returns(equity), observer.periods_per_hour),
        "max_drawdown": None if drawdown is None else drawdown[0],
        "max_drawdown_pct": (
            None if drawdown is None or drawdown[1] is None else drawdown[1] * 100
        ),
        "mean_exposure": mean(exposure),
        "max_exposure": max(exposure) if exposure else None,
    }


# Welford's variance (volatility, and Sharpe through its standard deviation) may differ from
# the two-pass one in the last bits; everything else is the same arithmetic in the same order.
ROUNDED = {"volatility_per_hour", "sharpe_ratio"}


def assert_same(name: str, got: float | None, expected: float | None) -> None:
    if name in ROUNDED and got is not None and expected is not None:
        assert got == pytest.approx(expected, rel=1e-12), name
    else:
        assert same(got, expected), (name, got, expected)


@pytest.mark.parametrize("seed", [1, 7])
def test_analyze_equals_the_batch_computation_over_the_record(seed: int) -> None:
    simulation = market_simulation(SimulationConfig(duration_ns=3 * MINUTE, seed=seed))
    observer = MarketObserver(simulation)
    run_observed(simulation, observer)
    analytics = analyze(observer)
    for instrument, (market, micro) in enumerate(
        zip(analytics.market, analytics.microstructure, strict=True)
    ):
        for name, expected in batch_market(observer, instrument).items():
            assert_same(name, getattr(market, name), expected)
        for name, expected in batch_microstructure(observer, instrument).items():
            assert_same(name, getattr(micro, name), expected)
    for agent in analytics.agents:
        for name, expected in batch_agent(observer, agent.participant_id).items():
            assert_same(name, getattr(agent, name), expected)
    assert sum(m.measured_trades for m in analytics.microstructure) > 100  # Not vacuous.


def test_a_live_observer_reports_the_same_mid_run_as_the_batch() -> None:
    """Analytics asked for while a run is under way (as the live API does, repeatedly) agree
    with the batch computation at every point."""
    simulation = market_simulation(SimulationConfig(duration_ns=4 * MINUTE, seed=3))
    observer = MarketObserver(simulation)
    simulation.start()
    for minute in (1, 2, 3):
        while simulation.now < minute * MINUTE:
            simulation.run_until(min(observer.next_stop(), minute * MINUTE))
            observer.observe()
        analytics = analyze(observer)
        for agent in analytics.agents:
            expected = batch_agent(observer, agent.participant_id)["sharpe_ratio"]
            assert_same("sharpe_ratio", agent.sharpe_ratio, expected)
            assert_same("sharpe_ratio", observer.sharpe_ratio(agent.participant_id), expected)
        for instrument, micro in enumerate(analytics.microstructure):
            expected_micro = batch_microstructure(observer, instrument)
            assert micro.trades_without_markout == expected_micro["trades_without_markout"]
            assert_same(
                "effective_spread_bps",
                micro.effective_spread_bps,
                expected_micro["effective_spread_bps"],
            )


def test_running_mean_refuses_to_mix_ints_and_floats() -> None:
    """A mix would be averaged differently from ``stats.mean``: refused, not silently wrong.
    Each case mixes only at its last value."""
    mixed: tuple[Sequence[float], ...] = ([1, 2, 2.5], [0.5, 1.5, 2], [100, 101, 99.5], [True, 2.5])
    for values in mixed:
        running = RunningMean()
        for value in values[:-1]:
            running.add(value)
        with pytest.raises(TypeError, match="all ints or all floats"):
            running.add(values[-1])
    pure: tuple[Sequence[float], ...] = ([1, 2, 4], [0.5, 1.5, 2.25], [7])
    for values in pure:
        running = RunningMean()
        for value in values:
            running.add(value)
        assert running.value == mean(values)
