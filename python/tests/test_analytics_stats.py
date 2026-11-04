"""Statistics: known values, edge cases (no data, one value, zero variance), the t distribution."""

from __future__ import annotations

import math

import pytest

from udeshi_markets.analytics import (
    confidence_interval,
    log_returns,
    max_drawdown,
    mean,
    quantile,
    realized_volatility,
    sample_stdev,
    sample_variance,
    sharpe_ratio,
    simple_returns,
    student_t_cdf,
    t_quantile,
    weighted_mean,
    welch_t_test,
)


def test_mean_and_variance() -> None:
    assert mean([]) is None
    assert mean([2.0, 4.0, 9.0]) == 5.0
    assert sample_variance([1.0]) is None
    assert sample_variance([1.0, 2.0, 3.0, 4.0]) == pytest.approx(5 / 3)
    assert sample_stdev([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]) == pytest.approx(2.13809, 1e-5)


def test_zero_variance_is_exactly_zero() -> None:
    """The float mean of identical values is not always that value; computing on deviations
    keeps the variance of a constant series exactly zero."""
    value = 0.1 + 0.2
    assert sample_variance([value] * 21) == 0.0
    assert realized_volatility([42.5] * 50, periods_per_hour=3600) == 0.0
    assert sharpe_ratio([0.001] * 30, periods_per_hour=3600) is None  # Undefined, not infinite.


def test_weighted_mean() -> None:
    assert weighted_mean([1.0, 3.0], [1.0, 3.0]) == 2.5
    assert weighted_mean([], []) is None
    assert weighted_mean([1.0], [0.0]) is None
    with pytest.raises(ValueError, match="same length"):
        weighted_mean([1.0], [])


def test_quantiles_interpolate_like_numpy() -> None:
    values = [4.0, 1.0, 3.0, 2.0]
    assert [quantile(values, q) for q in (0.0, 0.25, 0.5, 0.9, 1.0)] == [
        1.0,
        1.75,
        2.5,
        pytest.approx(3.7),
        4.0,
    ]
    assert quantile([], 0.5) is None
    with pytest.raises(ValueError, match="q must lie"):
        quantile(values, 1.5)


def test_returns_skip_gaps_and_undefined_periods() -> None:
    assert log_returns([100.0, None, 110.0, 121.0]) == [pytest.approx(math.log(1.1))]
    assert log_returns([None, None]) == []
    assert simple_returns([100.0, 110.0, 0.0, 5.0, 10.0]) == [
        pytest.approx(0.1),
        pytest.approx(-1.0),
        pytest.approx(1.0),
    ]


def test_volatility_and_sharpe_scale_to_one_hour() -> None:
    assert realized_volatility([100.0, 101.0], periods_per_hour=1) is None  # One return.
    prices = [100.0, 101.0, 100.0, 101.0, 100.0]
    per_period = sample_stdev(log_returns(prices))
    assert per_period is not None
    assert realized_volatility(prices, periods_per_hour=3600) == pytest.approx(per_period * 60)
    assert sharpe_ratio([0.01, 0.03], periods_per_hour=4) == pytest.approx(
        0.02 / math.sqrt(0.0002) * 2
    )
    assert sharpe_ratio([0.01], periods_per_hour=4) is None


def test_max_drawdown() -> None:
    assert max_drawdown([]) is None
    assert max_drawdown([100.0, 120.0, 90.0, 130.0, 117.0]) == (30.0, 0.25)
    assert max_drawdown([1.0, 2.0, 3.0]) == (0.0, 0.0)
    assert max_drawdown([-5.0, -10.0]) == (5.0, None)  # No positive peak: no fraction.


@pytest.mark.parametrize(
    ("p", "df", "expected"),
    [
        (0.975, 1, 12.7062047361747),
        (0.975, 4, 2.77644510519779),
        (0.975, 10, 2.22813885198627),
        (0.975, 30, 2.04227245630124),
        (0.995, 5, 4.03214298355523),
        # z + (z**3 + z) / (4 df): just above the normal quantile 1.95996398454.
        (0.975, 1e7, 1.9599642223),
        (0.05, 12, -1.78228755564932),
    ],
)
def test_t_quantiles_match_published_tables(p: float, df: float, expected: float) -> None:
    assert t_quantile(p, df) == pytest.approx(expected, rel=1e-6)
    assert student_t_cdf(expected, df) == pytest.approx(p, abs=1e-8)


def test_t_distribution_shape() -> None:
    assert student_t_cdf(0.0, 3) == 0.5
    assert student_t_cdf(-1.3, 7) == pytest.approx(1 - student_t_cdf(1.3, 7))
    assert student_t_cdf(math.inf, 2) == 1.0
    with pytest.raises(ValueError, match="degrees of freedom"):
        student_t_cdf(1.0, 0)


def test_confidence_interval() -> None:
    low_high = confidence_interval([1.0, 2.0, 3.0, 4.0, 5.0])
    assert low_high is not None
    half = 2.77644510519779 * math.sqrt(2.5) / math.sqrt(5)
    assert low_high[0] == pytest.approx(3 - half)
    assert low_high[1] == pytest.approx(3 + half)
    assert confidence_interval([1.0]) is None


def test_welch_t_test() -> None:
    result = welch_t_test([1.0, 2.0, 3.0, 4.0, 5.0], [6.0, 7.0, 8.0, 9.0, 10.0])
    assert result is not None
    t, df, p = result
    assert t == pytest.approx(-5.0)
    assert df == pytest.approx(8.0)
    assert p == pytest.approx(0.0010528, rel=1e-3)
    same = welch_t_test([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
    assert same is not None
    assert same[0] == 0.0
    assert same[2] == pytest.approx(1.0)
    assert welch_t_test([1.0, 1.0], [2.0, 2.0]) is None  # Zero variance on both sides.
    assert welch_t_test([1.0], [2.0, 3.0]) is None
