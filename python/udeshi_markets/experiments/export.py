"""CSV export of experiment results: one row per run, per metric summary, or per comparison."""

from __future__ import annotations

import csv
import dataclasses
from collections.abc import Sequence
from typing import TextIO

from udeshi_markets.experiments.runner import Comparison, ExperimentResult


def _cell(value: object) -> object:
    return "" if value is None else value


def write_runs_csv(results: Sequence[ExperimentResult], stream: TextIO) -> None:
    """experiment, seed, every swept parameter, every metric, digest, wall_seconds. Missing
    (undefined) values are empty cells."""
    parameters = sorted({path for r in results for run in r.runs for path, _ in run.parameters})
    metrics = sorted({name for r in results for name in r.metric_names})
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["experiment", "seed", *parameters, *metrics, "digest", "wall_seconds"])
    for result in results:
        for run in result.runs:
            values = dict(run.parameters)
            writer.writerow(
                [
                    run.experiment,
                    run.seed,
                    *(_cell(values.get(p)) for p in parameters),
                    *(_cell(run.metrics.get(m)) for m in metrics),
                    run.digest,
                    f"{run.wall_seconds:.3f}",
                ]
            )


def write_summary_csv(results: Sequence[ExperimentResult], stream: TextIO) -> None:
    """One row per experiment and metric: count, mean, stdev, stderr, 95% CI, quantiles."""
    writer = csv.writer(stream, lineterminator="\n")
    columns = ["count", "mean", "stdev", "stderr", "ci_low", "ci_high"]
    columns += ["minimum", "p05", "median", "p95", "maximum"]
    writer.writerow(["experiment", "metric", *columns])
    for result in results:
        for name, summary in result.summary().items():
            row = dataclasses.asdict(summary)
            writer.writerow([result.spec.label, name, *(_cell(row[c]) for c in columns)])


def write_comparison_csv(comparisons: Sequence[Comparison], stream: TextIO) -> None:
    writer = csv.writer(stream, lineterminator="\n")
    fields = [f.name for f in dataclasses.fields(Comparison)]
    writer.writerow(fields)
    for comparison in comparisons:
        row = dataclasses.asdict(comparison)
        writer.writerow([_cell(row[f]) for f in fields])
