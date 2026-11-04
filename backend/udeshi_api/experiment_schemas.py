"""Request and response models for experiments and analytics."""

import math
from typing import Literal

from pydantic import Field, StrictFloat, StrictInt, model_validator

from udeshi_api.schemas import AgentsIn, InstrumentIn, MarketIn, _Model
from udeshi_markets.analytics import AgentMetrics, MarketMetrics, MicrostructureMetrics

MAX_EXPERIMENT_RUNS = 500
MAX_SWEEP_POINTS = 50
MAX_TOTAL_RUNS = 2_000
MAX_EXPERIMENT_DURATION_SECONDS = 24 * 3600
MAX_SIMULATED_SECONDS = 60 * 24 * 3600
"""Simulated time across all runs of one request: bounds its total work (about an hour of one
core at the default market size)."""
MAX_BASE_SEED = (1 << 62) - 1  # Seeds are stored as signed 64-bit integers.

ExperimentStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
SweepValue = StrictInt | StrictFloat


class ExperimentIn(_Model):
    name: str = Field(default="experiment", min_length=1, max_length=100)
    instruments: list[InstrumentIn] | None = Field(
        default=None, min_length=1, max_length=50, description="Defaults to the five demo assets."
    )
    duration_seconds: float = Field(default=300.0, gt=0, le=MAX_EXPERIMENT_DURATION_SECONDS)
    runs: int = Field(default=10, ge=1, le=MAX_EXPERIMENT_RUNS, description="Seeds per point.")
    base_seed: int = Field(
        default=1, ge=0, le=MAX_BASE_SEED, description="Run k uses seed base_seed + k."
    )
    order_latency_ms: float = Field(default=0.0, ge=0, le=60_000)
    initial_cash: float = Field(default=1_000_000.0, ge=0, le=1e15)
    sample_interval_seconds: float = Field(default=1.0, ge=0.01, le=3600)
    markout_seconds: float = Field(default=5.0, gt=0, le=3600)
    agents: AgentsIn = Field(default_factory=AgentsIn)
    market: MarketIn = Field(default_factory=MarketIn)
    sweep: dict[str, list[SweepValue]] | None = Field(
        default=None,
        max_length=5,
        description="Settings to vary, each with its values: one experiment point per "
        "combination. Paths: a spec field ('duration_ns', 'order_latency_ns'), "
        "'population.<field>' or 'environment.<field>'.",
    )

    @model_validator(mode="after")
    def _bounded(self) -> "ExperimentIn":
        points = self.points
        if points > MAX_SWEEP_POINTS:
            raise ValueError(f"at most {MAX_SWEEP_POINTS} sweep points, not {points}")
        total = points * self.runs
        if total > MAX_TOTAL_RUNS:
            raise ValueError(f"at most {MAX_TOTAL_RUNS} runs in one experiment, not {total}")
        for path, values in (self.sweep or {}).items():
            if not values:
                raise ValueError(f"sweep {path!r} needs at least one value")
            if any(isinstance(v, float) and not math.isfinite(v) for v in values):
                raise ValueError(f"sweep {path!r}: values must be finite")
        if total * self.duration_seconds > MAX_SIMULATED_SECONDS:
            raise ValueError(
                f"{total} runs x {self.duration_seconds:g} s is more simulated time than the "
                f"limit of {MAX_SIMULATED_SECONDS} s for one experiment"
            )
        return self

    @property
    def points(self) -> int:
        return math.prod(len(values) for values in (self.sweep or {}).values())


class MetricSummaryOut(_Model):
    metric: str
    count: int = Field(description="Runs in which the metric was defined.")
    mean: float | None
    stdev: float | None
    stderr: float | None
    ci_low: float | None = Field(description="95% confidence interval for the mean (Student t).")
    ci_high: float | None
    minimum: float | None
    p05: float | None
    median: float | None
    p95: float | None
    maximum: float | None


class ExperimentPointOut(_Model):
    point: int
    label: str
    parameters: dict[str, float | int]
    runs: int = Field(description="Runs stored so far.")
    summary: list[MetricSummaryOut]


class ExperimentOut(_Model):
    id: str
    name: str
    status: ExperimentStatus
    points: int
    runs_done: int
    runs_total: int
    error: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    request: ExperimentIn


class ExperimentDetailOut(ExperimentOut):
    results: list[ExperimentPointOut]


class RunOut(_Model):
    point: int
    label: str
    seed: int
    parameters: dict[str, float | int]
    metrics: dict[str, float | None]
    digest: str
    wall_seconds: float = Field(description="Measured: informational, not a metric.")


class ComparisonOut(_Model):
    metric: str
    count_a: int
    mean_a: float | None
    count_b: int
    mean_b: float | None
    difference: float | None = Field(description="mean_b - mean_a.")
    t: float | None = Field(description="Welch's t statistic.")
    df: float | None = Field(description="Welch-Satterthwaite degrees of freedom.")
    p_value: float | None = Field(description="Two-sided.")


class ComparedOut(_Model):
    experiment: str
    point: int
    label: str


class ComparisonResultOut(_Model):
    a: ComparedOut
    b: ComparedOut
    metrics: list[ComparisonOut]


class AnalyticsOut(_Model):
    """Analytics of a live simulation so far (see docs/quantitative-metrics.md). Sampled every
    ``interval_seconds`` of simulated time; markouts ``markout_seconds`` after each trade."""

    time_seconds: float
    interval_seconds: float
    markout_seconds: float
    samples: int
    market: list[MarketMetrics]
    microstructure: list[MicrostructureMetrics]
    agents: list[AgentMetrics]
