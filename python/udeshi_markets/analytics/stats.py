"""Statistics for analytics and experiments.

Every function returns None when its result is undefined, rather than NaN or infinity:
* too few observations (a standard deviation needs two, a return needs two prices);
* zero variance where it would be a divisor (a Sharpe ratio of a flat equity curve);
* missing values.

Zero variance is detected exactly (all values equal), not with a tolerance. Statistics are
computed on deviations from the first value, so the rounding error in the mean of identical
values cannot masquerade as a tiny variance.

The Student-t distribution is implemented here (regularized incomplete beta function by Lentz's
continued fraction), so the package needs no SciPy. Tails are computed directly rather than as
1 - CDF, so tiny p-values keep their relative precision. Measured against SciPy on 576 points
(t from 1e-12 to 1e4, df from 0.3 to 1e7): absolute CDF error at most 3.2e-10 for df <= 1e5
(1.6e-9 at df = 1e7), and tail probabilities within 1e-9 relative for df <= 1e5.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence

from udeshi_markets.simulation import deterministic_log


def mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    origin = values[0]
    return origin + sum(v - origin for v in values) / len(values)


def sample_variance(values: Sequence[float]) -> float | None:
    """Unbiased (n - 1) variance. None for fewer than two values."""
    count = len(values)
    if count < 2:
        return None
    if max(values) == min(values):
        return 0.0
    origin = values[0]
    deviations = [v - origin for v in values]
    centre = sum(deviations) / count
    return sum((d - centre) ** 2 for d in deviations) / (count - 1)


def sample_stdev(values: Sequence[float]) -> float | None:
    variance = sample_variance(values)
    return None if variance is None else math.sqrt(variance)


def weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float | None:
    """Sum(w * v) / sum(w). None if there are no values or the weights sum to zero."""
    if len(values) != len(weights):
        raise ValueError("values and weights must have the same length")
    total = sum(weights)
    if not values or total == 0:
        return None
    return sum(v * w for v, w in zip(values, weights, strict=True)) / total


def quantile(values: Sequence[float], q: float) -> float | None:
    """Linear interpolation between order statistics (Hyndman and Fan type 7, the default of
    R and NumPy)."""
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must lie in [0, 1]")
    if not values:
        return None
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def log_returns(prices: Sequence[float | None]) -> list[float]:
    """ln(p_t / p_{t-1}) for each pair of consecutive defined, positive prices. A missing price
    breaks the series: no return spans the gap."""
    returns: list[float] = []
    for previous, current in itertools.pairwise(prices):
        if previous is not None and current is not None and previous > 0 and current > 0:
            returns.append(deterministic_log(current) - deterministic_log(previous))
    return returns


def simple_returns(values: Sequence[float]) -> list[float]:
    """(v_t - v_{t-1}) / v_{t-1}, skipping periods that start at zero or below (where a return
    is undefined)."""
    return [
        (current - previous) / previous
        for previous, current in itertools.pairwise(values)
        if previous > 0
    ]


def realized_volatility(prices: Sequence[float | None], periods_per_hour: float) -> float | None:
    """Sample standard deviation of log returns, scaled to one hour: s * sqrt(periods per
    hour). Zero for a constant price; None with fewer than two returns."""
    stdev = sample_stdev(log_returns(prices))
    return None if stdev is None else stdev * math.sqrt(periods_per_hour)


def sharpe_ratio(returns: Sequence[float], periods_per_hour: float) -> float | None:
    """mean / stdev of per-period returns, scaled to one hour (risk-free rate zero). None with
    fewer than two returns or zero variance, where the ratio is undefined."""
    stdev = sample_stdev(returns)
    centre = mean(returns)
    if stdev is None or centre is None or stdev == 0.0:
        return None
    return centre / stdev * math.sqrt(periods_per_hour)


def max_drawdown(equity: Sequence[float]) -> tuple[float, float | None] | None:
    """The largest fall from a running peak: (in currency, as a fraction of that peak). The
    fraction is None if the peak was not positive. None for an empty series."""
    if not equity:
        return None
    peak = equity[0]
    worst = 0.0
    worst_fraction: float | None = 0.0 if peak > 0 else None
    for value in equity:
        peak = max(peak, value)
        drop = peak - value
        if drop > worst:
            worst = drop
            worst_fraction = drop / peak if peak > 0 else None
    return worst, worst_fraction


# ---- Student-t distribution ------------------------------------------------------------------


def _incomplete_beta(x: float, a: float, b: float, complement: float) -> float:
    """Regularized incomplete beta I_x(a, b), by the continued fraction of Numerical Recipes
    (modified Lentz). ``complement`` is 1 - x, passed in because the caller can compute it
    without the cancellation of subtracting from 1. The symmetry I_x(a, b) = 1 - I_{1-x}(b, a)
    keeps the continued fraction in the range where it converges quickly."""
    if x <= 0.0:
        return 0.0
    if complement <= 0.0:
        return 1.0
    if x > (a + 1.0) / (a + b + 2.0):
        return 1.0 - _incomplete_beta(complement, b, a, x)
    log_front = (
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x)
    ) + b * math.log(complement)
    tiny = 1e-300
    c, d = 1.0, 1.0 - (a + b) * x / (a + 1.0)
    d = 1.0 / (d if abs(d) > tiny else tiny)
    fraction = d
    for m in range(1, 300):
        for numerator in (
            m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m)),
            -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1)),
        ):
            d = 1.0 + numerator * d
            d = 1.0 / (d if abs(d) > tiny else tiny)
            c = 1.0 + numerator / c
            c = c if abs(c) > tiny else tiny
            fraction *= c * d
        if abs(c * d - 1.0) < 1e-15:
            break
    return math.exp(log_front) * fraction / a


def student_t_sf(t: float, df: float) -> float:
    """P(T > t), the upper tail, computed directly (so small tails keep full relative
    precision instead of cancelling in 1 - CDF)."""
    if not df > 0:
        raise ValueError("degrees of freedom must be positive")
    if math.isinf(t):
        return 0.0 if t > 0 else 1.0
    t2 = t * t
    # P(|T| > |t|) = I_x(df/2, 1/2) with x = df / (df + t^2); 1 - x is t^2 / (df + t^2).
    tail = 0.5 * _incomplete_beta(df / (df + t2), df / 2.0, 0.5, t2 / (df + t2))
    return tail if t > 0 else 1.0 - tail


def student_t_cdf(t: float, df: float) -> float:
    """P(T <= t) for Student's t with ``df`` degrees of freedom."""
    return student_t_sf(-t, df)


def t_quantile(p: float, df: float) -> float:
    """The t such that P(T <= t) = p, by bisection on the CDF."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must lie in (0, 1)")
    if p == 0.5:
        return 0.0
    low, high = -1.0, 1.0
    while student_t_cdf(low, df) > p:
        low *= 2.0
    while student_t_cdf(high, df) < p:
        high *= 2.0
    for _ in range(200):
        middle = 0.5 * (low + high)
        if student_t_cdf(middle, df) < p:
            low = middle
        else:
            high = middle
        if high - low < 1e-12 * max(1.0, abs(middle)):
            break
    return 0.5 * (low + high)


def confidence_interval(values: Sequence[float], level: float = 0.95) -> tuple[float, float] | None:
    """Two-sided interval for the mean, mean +- t * s / sqrt(n), with t from Student's
    distribution with n - 1 degrees of freedom. None with fewer than two values."""
    centre = mean(values)
    stdev = sample_stdev(values)
    if centre is None or stdev is None:
        return None
    if not 0.0 < level < 1.0:
        raise ValueError("level must lie in (0, 1)")
    half = t_quantile(0.5 + level / 2.0, len(values) - 1) * stdev / math.sqrt(len(values))
    return centre - half, centre + half


def welch_t_test(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float] | None:
    """Welch's unequal-variance t-test of mean(a) = mean(b): (t, degrees of freedom by the
    Welch-Satterthwaite formula, two-sided p-value). None if either sample has fewer than two
    values, or both have zero variance (the statistic is then undefined)."""
    mean_a, mean_b = mean(a), mean(b)
    var_a, var_b = sample_variance(a), sample_variance(b)
    if mean_a is None or mean_b is None or var_a is None or var_b is None:
        return None
    se_a, se_b = var_a / len(a), var_b / len(b)
    se = se_a + se_b
    if se == 0.0:
        return None
    t = (mean_a - mean_b) / math.sqrt(se)
    df = se * se / (se_a * se_a / (len(a) - 1) + se_b * se_b / (len(b) - 1))
    p = min(1.0, 2.0 * student_t_sf(abs(t), df))
    return t, df, p
