"""Running statistics: the analytics' aggregates, kept up to date as observations arrive.

Recomputing every metric from the full record on each request made a report cost time in
proportion to the length of the run (docs/benchmarking.md). These accumulators take each
observation once, so a report costs the same however long the run. Each one gives the value of
the batch function in stats.py on the same series:

* ``FloatSum`` adds floats with exactly the arithmetic of the built-in ``sum()`` (CPython 3.12
  and later: Neumaier's compensated summation, starting from the integer 0), so
  ``RunningMean`` equals ``stats.mean`` bit for bit, and so do sums of products.
* ``RunningVariance`` is Welford's algorithm on deviations from the first value. It equals the
  two-pass ``stats.sample_variance`` up to rounding (relative differences of order 1e-15), and
  detects zero variance exactly, as the batch function does: all values equal gives 0.0.
* ``RunningDrawdown`` performs ``stats.max_drawdown``'s steps one value at a time.

The tests check each against its batch function, bit for bit where stated.
"""

from __future__ import annotations

import math


class FloatSum:
    """A running total of floats, equal bit for bit to the built-in ``sum()`` of the same
    values in the same order."""

    __slots__ = ("_compensation", "_total", "count")

    def __init__(self) -> None:
        self._total = 0.0
        self._compensation = 0.0
        self.count = 0

    def add(self, value: float) -> None:
        if self.count == 0:
            self._total = 0 + value  # sum() starts from the int 0, so 0 + -0.0 is 0.0.
        else:
            total = self._total
            result = total + value
            if abs(total) >= abs(value):
                self._compensation += (total - result) + value
            else:
                self._compensation += (value - result) + total
            self._total = result
        self.count += 1

    @property
    def value(self) -> float:
        compensation = self._compensation
        # As sum() does: keep the sign of a negative total, and never turn an infinite or
        # overflowed total into NaN.
        if compensation and math.isfinite(compensation):
            return self._total + compensation
        return self._total


class RunningMean:
    """``stats.mean`` of the values added so far, bit for bit: the mean of the deviations from
    the first value (summed exactly for ints, as ``sum()`` adds floats otherwise), plus it."""

    __slots__ = ("_float_deviations", "_int_deviations", "_origin", "count")

    def __init__(self) -> None:
        self._origin: float = 0
        self._int_deviations = 0
        self._float_deviations = FloatSum()
        self.count = 0

    def add(self, value: float) -> None:
        if self.count == 0:
            self._origin = value
        deviation = value - self._origin
        if isinstance(deviation, int):
            self._int_deviations += deviation
        else:
            self._float_deviations.add(deviation)
        self.count += 1

    @property
    def value(self) -> float | None:
        if self.count == 0:
            return None
        total = (
            self._int_deviations if isinstance(self._origin, int) else self._float_deviations.value
        )
        return self._origin + total / self.count


class RunningVariance:
    """``stats.sample_variance`` of the values added so far, up to rounding (Welford)."""

    __slots__ = ("_high", "_low", "_m2", "_mean", "_origin", "count")

    def __init__(self) -> None:
        self._origin = 0.0
        self._mean = 0.0  # Of the deviations from the first value.
        self._m2 = 0.0
        self._low = self._high = 0.0
        self.count = 0

    def add(self, value: float) -> None:
        if self.count == 0:
            self._origin = self._low = self._high = value
        else:
            self._low = min(self._low, value)
            self._high = max(self._high, value)
        deviation = value - self._origin
        self.count += 1
        delta = deviation - self._mean
        self._mean += delta / self.count
        self._m2 += delta * (deviation - self._mean)

    @property
    def value(self) -> float | None:
        """Unbiased (n - 1) variance; None for fewer than two values."""
        if self.count < 2:
            return None
        if self._high == self._low:
            return 0.0  # All values equal: exactly zero, as stats.sample_variance gives.
        return self._m2 / (self.count - 1)

    @property
    def stdev(self) -> float | None:
        variance = self.value
        return None if variance is None else math.sqrt(variance)


class RunningSharpe:
    """``stats.sharpe_ratio(stats.simple_returns(values))`` of a series given one value at a
    time: the mean bit for bit, the standard deviation up to rounding."""

    __slots__ = ("_last", "_mean", "_variance")

    def __init__(self) -> None:
        self._last: float | None = None
        self._mean = RunningMean()
        self._variance = RunningVariance()

    def add(self, value: float) -> None:
        previous = self._last
        if previous is not None and previous > 0:
            simple_return = (value - previous) / previous
            self._mean.add(simple_return)
            self._variance.add(simple_return)
        self._last = value

    def ratio(self, periods_per_hour: float) -> float | None:
        stdev = self._variance.stdev
        centre = self._mean.value
        if stdev is None or centre is None or stdev == 0.0:
            return None
        return centre / stdev * math.sqrt(periods_per_hour)


class RunningDrawdown:
    """``stats.max_drawdown`` of the values added so far, step by step."""

    __slots__ = ("_peak", "_worst", "_worst_fraction", "count")

    def __init__(self) -> None:
        self._peak = 0.0
        self._worst = 0.0
        self._worst_fraction: float | None = None
        self.count = 0

    def add(self, value: float) -> None:
        if self.count == 0:
            self._peak = value
            self._worst = 0.0
            self._worst_fraction = 0.0 if value > 0 else None
        self._peak = max(self._peak, value)
        drop = self._peak - value
        if drop > self._worst:
            self._worst = drop
            self._worst_fraction = drop / self._peak if self._peak > 0 else None
        self.count += 1

    @property
    def value(self) -> tuple[float, float | None] | None:
        return None if self.count == 0 else (self._worst, self._worst_fraction)
