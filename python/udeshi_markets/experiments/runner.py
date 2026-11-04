"""Running experiments: independent seeded runs, Monte Carlo, sweeps, aggregation, comparison.

A run is a pure function of (spec, seed): it builds the market, drives it to the end under a
``MarketObserver`` and reduces the observations to scalar metrics. Runs therefore parallelise
across processes without changing any result. Results are always ordered by seed, whatever
order the workers finish in.
"""

from __future__ import annotations

import math
import multiprocessing
import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

from udeshi_markets.agents import market_simulation
from udeshi_markets.analytics import (
    MarketObserver,
    RunAnalytics,
    analyze,
    by_strategy,
    confidence_interval,
    mean,
    quantile,
    run_observed,
    sample_stdev,
    welch_t_test,
)
from udeshi_markets.experiments.spec import ExperimentSpec, sweep

STRATEGIES = ("random", "market_maker", "momentum", "fundamental", "pairs")


@dataclass(frozen=True, slots=True)
class RunResult:
    experiment: str
    seed: int
    parameters: tuple[tuple[str, object], ...]
    metrics: dict[str, float | None]
    analytics: RunAnalytics
    digest: str
    wall_seconds: float  # Measured on the machine that ran it: informational, not a metric.


@dataclass(frozen=True, slots=True)
class MetricSummary:
    metric: str
    count: int  # Runs in which the metric was defined.
    mean: float | None
    stdev: float | None
    stderr: float | None
    ci_low: float | None  # 95% confidence interval for the mean (Student t).
    ci_high: float | None
    minimum: float | None
    p05: float | None
    median: float | None
    p95: float | None
    maximum: float | None


@dataclass(frozen=True, slots=True)
class ExperimentResult:
    spec: ExperimentSpec
    runs: tuple[RunResult, ...]

    @property
    def metric_names(self) -> list[str]:
        names: set[str] = set()
        for run in self.runs:
            names.update(run.metrics)
        return sorted(names)

    def values(self, metric: str) -> list[float]:
        """The metric's defined values, in seed order."""
        return [v for run in self.runs if (v := run.metrics.get(metric)) is not None]

    def summary(self) -> dict[str, MetricSummary]:
        return {name: summarize(name, self.values(name)) for name in self.metric_names}


@dataclass(frozen=True, slots=True)
class Comparison:
    metric: str
    count_a: int
    mean_a: float | None
    count_b: int
    mean_b: float | None
    difference: float | None  # mean_b - mean_a.
    t: float | None  # Welch's t of b against a: the same sign as ``difference``.
    df: float | None
    p_value: float | None  # Two-sided.


# ---- One run ---------------------------------------------------------------------------------


def run_once(spec: ExperimentSpec, seed: int) -> RunResult:
    started = time.perf_counter()
    simulation = market_simulation(
        spec.simulation_config(seed), spec.population, spec.environment_config()
    )
    observer = MarketObserver(
        simulation,
        interval_ns=spec.sample_interval_ns,
        markout_ns=spec.markout_ns,
        record=False,  # The metrics need only the running aggregates.
    )
    run_observed(simulation, observer)
    analytics = analyze(observer)
    return RunResult(
        experiment=spec.label,
        seed=seed,
        parameters=spec.parameters,
        metrics=flatten(analytics),
        analytics=analytics,
        digest=simulation.digest,
        wall_seconds=time.perf_counter() - started,
    )


def flatten(analytics: RunAnalytics) -> dict[str, float | None]:
    """The scalar metrics of one run (names documented in docs/quantitative-metrics.md).

    Market-wide values are means over instruments (each instrument's value already a mean over
    its samples or trades); counts are sums. Strategy values are means over the strategy's
    agents of the agents for whom the metric is defined.
    """
    market, micro = analytics.market, analytics.microstructure
    metrics: dict[str, float | None] = {
        "trades": float(sum(m.trades for m in market)),
        "volume": float(sum(m.volume for m in market)),
        "volatility_per_hour": _mean_of(m.volatility_per_hour for m in market),
        "spread_bps": _mean_of(m.mean_spread_bps for m in market),
        "depth": _mean_of(m.mean_depth for m in market),
        "abs_imbalance": _mean_of(
            None if m.mean_imbalance is None else abs(m.mean_imbalance) for m in market
        ),
        "one_sided_fraction": _mean_of(m.one_sided_fraction for m in market),
        "pricing_error_bps": _mean_of(m.pricing_error_bps for m in market),
        "effective_spread_bps": _mean_of(m.effective_spread_bps for m in micro),
        "realized_spread_bps": _mean_of(m.realized_spread_bps for m in micro),
        "price_impact_bps": _mean_of(m.price_impact_bps for m in micro),
        "fill_rate": _mean_of(m.fill_rate for m in micro),
        "average_trade_size": _mean_of(m.average_trade_size for m in micro),
    }
    groups = by_strategy(analytics.agents)
    for strategy in STRATEGIES:
        agents = groups.get(strategy, [])
        if not agents:
            continue
        metrics[f"{strategy}.pnl"] = _mean_of(a.total_pnl for a in agents)
        metrics[f"{strategy}.return_pct"] = _mean_of(a.return_pct for a in agents)
        metrics[f"{strategy}.sharpe"] = _mean_of(a.sharpe_ratio for a in agents)
        metrics[f"{strategy}.max_drawdown_pct"] = _mean_of(a.max_drawdown_pct for a in agents)
        metrics[f"{strategy}.win_rate"] = _mean_of(a.win_rate for a in agents)
        metrics[f"{strategy}.trades"] = _mean_of(float(a.trades) for a in agents)
    return metrics


def _mean_of(values: Iterable[float | None]) -> float | None:
    return mean([v for v in values if v is not None])


# ---- Many runs -------------------------------------------------------------------------------


def run_experiment(
    spec: ExperimentSpec,
    *,
    workers: int = 1,
    on_run: Callable[[RunResult], None] | None = None,
) -> ExperimentResult:
    """Runs every seed of ``spec`` (in ``workers`` processes when more than one), then orders the
    results by seed. ``on_run`` is called in this process as each run finishes."""
    seeds = spec.seeds()
    return ExperimentResult(
        spec, tuple(_run_all([(spec, seed) for seed in seeds], workers, on_run))
    )


def monte_carlo(
    spec: ExperimentSpec,
    runs: int,
    *,
    workers: int = 1,
    on_run: Callable[[RunResult], None] | None = None,
) -> ExperimentResult:
    """``runs`` independent runs of one configuration: the distribution of every metric over the
    randomness of the market (see ``ExperimentResult.summary``)."""
    from dataclasses import replace

    return run_experiment(replace(spec, runs=runs), workers=workers, on_run=on_run)


def run_sweep(
    spec: ExperimentSpec,
    grid: dict[str, Sequence[object]],
    *,
    workers: int = 1,
    on_run: Callable[[RunResult], None] | None = None,
) -> list[ExperimentResult]:
    """Every point of the grid, each with ``spec.runs`` seeds. All runs of all points share one
    pool of workers."""
    specs = sweep(spec, grid)
    jobs = [(point, seed) for point in specs for seed in point.seeds()]
    results = _run_all(jobs, workers, on_run)
    grouped: list[ExperimentResult] = []
    position = 0
    for point in specs:
        grouped.append(ExperimentResult(point, tuple(results[position : position + point.runs])))
        position += point.runs
    return grouped


def _run_all(
    jobs: Sequence[tuple[ExperimentSpec, int]],
    workers: int,
    on_run: Callable[[RunResult], None] | None,
) -> list[RunResult]:
    if workers < 1:
        raise ValueError("workers must be at least 1")
    if workers == 1 or len(jobs) == 1:
        results = []
        for spec, seed in jobs:
            result = run_once(spec, seed)
            if on_run is not None:
                on_run(result)
            results.append(result)
        return results
    # Spawned (not forked) workers: each imports the package afresh, the portable choice.
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=min(workers, len(jobs)), mp_context=context) as pool:
        futures = [pool.submit(run_once, spec, seed) for spec, seed in jobs]
        results = []
        for future in futures:  # Job order, so results are deterministic.
            result = future.result()
            if on_run is not None:
                on_run(result)
            results.append(result)
    return results


# ---- Aggregation and comparison --------------------------------------------------------------


def summarize(metric: str, values: Sequence[float]) -> MetricSummary:
    stdev = sample_stdev(values)
    interval = confidence_interval(values)
    return MetricSummary(
        metric=metric,
        count=len(values),
        mean=mean(values),
        stdev=stdev,
        stderr=None if stdev is None else stdev / math.sqrt(len(values)),
        ci_low=None if interval is None else interval[0],
        ci_high=None if interval is None else interval[1],
        minimum=min(values) if values else None,
        p05=quantile(values, 0.05),
        median=quantile(values, 0.5),
        p95=quantile(values, 0.95),
        maximum=max(values) if values else None,
    )


def compare(
    a: ExperimentResult, b: ExperimentResult, metrics: Sequence[str] | None = None
) -> list[Comparison]:
    """Metric by metric: the two experiments' means and Welch's t-test of their difference."""
    names = metrics if metrics is not None else sorted(set(a.metric_names) & set(b.metric_names))
    comparisons = []
    for name in names:
        values_a, values_b = a.values(name), b.values(name)
        mean_a, mean_b = mean(values_a), mean(values_b)
        test = welch_t_test(values_b, values_a)
        comparisons.append(
            Comparison(
                metric=name,
                count_a=len(values_a),
                mean_a=mean_a,
                count_b=len(values_b),
                mean_b=mean_b,
                difference=None if mean_a is None or mean_b is None else mean_b - mean_a,
                t=None if test is None else test[0],
                df=None if test is None else test[1],
                p_value=None if test is None else test[2],
            )
        )
    return comparisons
