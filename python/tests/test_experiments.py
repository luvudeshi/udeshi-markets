"""The experiment framework: specs, sweeps, runs, parallelism, aggregation, comparison, CSV."""

from __future__ import annotations

import csv
import dataclasses
import io

import pytest

from udeshi_markets.agents import PopulationConfig
from udeshi_markets.experiments import (
    ExperimentSpec,
    compare,
    monte_carlo,
    run_experiment,
    run_sweep,
    summarize,
    sweep,
    write_comparison_csv,
    write_runs_csv,
    write_summary_csv,
)
from udeshi_markets.simulation import MINUTE, EnvironmentConfig, PairConfig

SMALL = PopulationConfig(
    random_traders=4, market_makers=2, momentum_traders=1, fundamental_traders=2, pairs_traders=1
)


def spec(**kwargs: object) -> ExperimentSpec:
    values: dict[str, object] = {"duration_ns": MINUTE, "runs": 3, "population": SMALL}
    values.update(kwargs)
    return ExperimentSpec(**values)  # type: ignore[arg-type]


def test_specs_are_validated() -> None:
    with pytest.raises(ValueError, match="runs must lie"):
        spec(runs=0)
    with pytest.raises(ValueError, match="pairs traders need a pair"):
        spec(environment=EnvironmentConfig())
    with pytest.raises(ValueError, match="duration_ns"):
        spec(duration_ns=0)
    assert spec(base_seed=40).seeds() == [40, 41, 42]


def test_settings_can_be_changed_by_path() -> None:
    base = spec()
    changed = base.with_value("population.market_makers", 7).with_value(
        "environment.volatility", 0.05
    )
    assert changed.population.market_makers == 7
    assert changed.environment_config().volatility == 0.05
    assert changed.environment_config().pairs == base.environment_config().pairs
    assert changed.parameters == (("population.market_makers", 7), ("environment.volatility", 0.05))
    assert changed.label == "experiment [population.market_makers=7, environment.volatility=0.05]"
    assert base.with_value("duration_ns", 2 * MINUTE).duration_ns == 2 * MINUTE
    for path in ("population.nonsense", "name", "environment", "duration_ns.x"):
        with pytest.raises(ValueError, match=r"no field|cannot sweep"):
            base.with_value(path, 1)


def test_sweeps_cover_the_grid_in_a_fixed_order() -> None:
    points = sweep(spec(), {"population.market_makers": [1, 2], "order_latency_ns": [0, 10]})
    assert [p.parameters for p in points] == [
        (("population.market_makers", 1), ("order_latency_ns", 0)),
        (("population.market_makers", 1), ("order_latency_ns", 10)),
        (("population.market_makers", 2), ("order_latency_ns", 0)),
        (("population.market_makers", 2), ("order_latency_ns", 10)),
    ]
    assert sweep(spec(), {}) == [spec()]
    with pytest.raises(ValueError, match="at least one value"):
        sweep(spec(), {"runs": []})


def test_runs_are_ordered_reproducible_and_independent_of_parallelism() -> None:
    sequential = run_experiment(spec())
    assert [run.seed for run in sequential.runs] == [1, 2, 3]
    parallel = run_experiment(spec(), workers=3)
    assert [r.metrics for r in parallel.runs] == [r.metrics for r in sequential.runs]
    assert [r.digest for r in parallel.runs] == [r.digest for r in sequential.runs]
    assert len({r.digest for r in sequential.runs}) == 3  # Different seeds, different runs.
    metrics = sequential.runs[0].metrics
    for name in ("spread_bps", "effective_spread_bps", "volatility_per_hour", "market_maker.pnl"):
        assert metrics[name] is not None
    assert "environment.pnl" not in metrics


def test_summaries_and_monte_carlo() -> None:
    result = monte_carlo(spec(), runs=4)
    assert len(result.runs) == 4
    summary = result.summary()["spread_bps"]
    values = result.values("spread_bps")
    assert summary.count == 4
    assert summary.mean == pytest.approx(sum(values) / 4)
    assert summary.ci_low is not None
    assert summary.ci_high is not None
    assert summary.mean is not None
    assert summary.ci_low < summary.mean < summary.ci_high
    assert summary.minimum == min(values)
    single = summarize("x", [3.0])
    assert (single.mean, single.stdev, single.ci_low, single.median) == (3.0, None, None, 3.0)
    empty = summarize("x", [])
    assert (empty.count, empty.mean, empty.p95) == (0, None, None)


def test_comparing_experiments() -> None:
    few = run_experiment(spec(name="few makers"))
    many = run_experiment(
        spec(name="many makers", population=dataclasses.replace(SMALL, market_makers=8))
    )
    [spread] = compare(few, many, ["spread_bps"])
    assert spread.count_a == spread.count_b == 3
    assert spread.difference is not None
    assert spread.mean_b is not None
    assert spread.mean_a is not None
    assert spread.difference == pytest.approx(spread.mean_b - spread.mean_a)
    assert spread.p_value is not None
    assert 0.0 <= spread.p_value <= 1.0
    assert {c.metric for c in compare(few, many)} == set(few.metric_names)


def test_csv_exports() -> None:
    results = run_sweep(spec(runs=2), {"population.market_makers": [1, 3]})
    runs = io.StringIO()
    write_runs_csv(results, runs)
    rows = list(csv.DictReader(io.StringIO(runs.getvalue())))
    assert len(rows) == 4
    assert [row["population.market_makers"] for row in rows] == ["1", "1", "3", "3"]
    assert rows[0]["experiment"] == "experiment [population.market_makers=1]"
    assert float(rows[0]["spread_bps"]) > 0
    assert rows[0]["digest"] == results[0].runs[0].digest

    summary = io.StringIO()
    write_summary_csv(results, summary)
    table = list(csv.DictReader(io.StringIO(summary.getvalue())))
    assert {row["metric"] for row in table} >= {"spread_bps", "pricing_error_bps"}
    assert all(row["count"] == "2" for row in table if row["metric"] == "spread_bps")

    comparison = io.StringIO()
    write_comparison_csv(compare(results[0], results[1], ["depth"]), comparison)
    [row] = list(csv.DictReader(io.StringIO(comparison.getvalue())))
    assert row["metric"] == "depth"
    assert row["p_value"] != ""


def test_the_order_of_settings_cannot_change_the_market() -> None:
    """Environment settings act on the final market: changing the instruments after the
    volatility gives the same experiment as the other way round."""
    from udeshi_markets.simulation import DEFAULT_INSTRUMENTS

    three = DEFAULT_INSTRUMENTS[2:]  # QUANT, ORBIT, VECTOR: no ACME/NOVA pair.
    base = spec(population=dataclasses.replace(SMALL, pairs_traders=0))
    one = base.with_value("environment.volatility", 0.05).with_value("instruments", three)
    other = base.with_value("instruments", three).with_value("environment.volatility", 0.05)
    assert one.environment_config() == other.environment_config()
    assert one.environment_config().pairs == ()
    assert one.environment_config().volatility == 0.05
    with pytest.raises(ValueError, match="unknown symbol"):
        spec(instruments=three, environment=EnvironmentConfig(pairs=(PairConfig("ACME", "NOVA"),)))


def test_comparison_statistics_share_the_sign_of_the_difference() -> None:
    few = run_experiment(spec(name="few", runs=3))
    many = run_experiment(
        spec(name="many", runs=3, population=dataclasses.replace(SMALL, random_traders=12))
    )
    for comparison in compare(few, many, ["volume", "spread_bps", "depth"]):
        assert comparison.t is not None
        assert comparison.difference is not None
        assert (comparison.t > 0) == (comparison.difference > 0)
