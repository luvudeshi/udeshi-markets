"""Experiment specifications: one market configuration, run with many seeds."""

from __future__ import annotations

import dataclasses
import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from udeshi_markets.agents import PopulationConfig, default_environment
from udeshi_markets.simulation import (
    DEFAULT_INSTRUMENTS,
    MINUTE,
    SECOND,
    EnvironmentConfig,
    InstrumentConfig,
    SimulationConfig,
)

MAX_RUNS = 10_000


@dataclass(frozen=True, slots=True)
class ExperimentSpec:
    """A market (instruments, agents, environment, duration) and how often to run it.

    Run k uses seed ``base_seed + k``, so the seeds of an experiment are explicit and any run can
    be reproduced on its own. ``parameters`` records the values a sweep set, for reporting.
    """

    name: str = "experiment"
    instruments: tuple[InstrumentConfig, ...] = DEFAULT_INSTRUMENTS
    duration_ns: int = 30 * MINUTE
    order_latency_ns: int = 0
    initial_cash: float = 1_000_000.0
    population: PopulationConfig = field(default_factory=PopulationConfig)
    environment: EnvironmentConfig | None = None  # None: the default environment.
    runs: int = 10
    base_seed: int = 1
    sample_interval_ns: int = SECOND
    markout_ns: int = 5 * SECOND
    parameters: tuple[tuple[str, object], ...] = ()

    def __post_init__(self) -> None:
        if not 1 <= self.runs <= MAX_RUNS:
            raise ValueError(f"runs must lie in [1, {MAX_RUNS}]")
        if self.base_seed < 0 or self.base_seed + self.runs - 1 >= 1 << 64:
            raise ValueError("seeds must lie in [0, 2**64)")
        if self.sample_interval_ns <= 0 or self.markout_ns <= 0:
            raise ValueError("sample_interval_ns and markout_ns must be positive")
        self.simulation_config(self.base_seed)  # Validates the market itself.
        if self.population.pairs_traders and not self.environment_config().pairs:
            raise ValueError("pairs traders need a pair in the environment")

    def seeds(self) -> list[int]:
        return [self.base_seed + k for k in range(self.runs)]

    def simulation_config(self, seed: int) -> SimulationConfig:
        return SimulationConfig(
            instruments=self.instruments,
            duration_ns=self.duration_ns,
            seed=seed,
            order_latency_ns=self.order_latency_ns,
            initial_cash=self.initial_cash,
        )

    def environment_config(self) -> EnvironmentConfig:
        if self.environment is not None:
            return self.environment
        return default_environment(self.simulation_config(self.base_seed))

    def with_value(self, path: str, value: object) -> ExperimentSpec:
        """A copy with one setting changed: a field of the spec ("duration_ns"), of the
        population ("population.market_makers") or of the environment ("environment.volatility").
        The change is recorded in ``parameters``."""
        head, _, rest = path.partition(".")
        parameters = (*self.parameters, (path, value))
        if head == "population" and rest:
            population = _replace(self.population, rest, value)
            return dataclasses.replace(self, population=population, parameters=parameters)
        if head == "environment" and rest:
            environment = _replace(self.environment_config(), rest, value)
            return dataclasses.replace(self, environment=environment, parameters=parameters)
        if head in {"name", "parameters", "population", "environment"} or rest:
            raise ValueError(f"cannot sweep {path!r}")
        return dataclasses.replace(self, **{head: value}, parameters=parameters)  # type: ignore[arg-type]

    @property
    def label(self) -> str:
        if not self.parameters:
            return self.name
        settings = ", ".join(f"{path}={value}" for path, value in self.parameters)
        return f"{self.name} [{settings}]"


def _replace(config: Any, name: str, value: object) -> Any:
    """``config`` (a PopulationConfig or EnvironmentConfig) with one field replaced; the field is
    validated by the config's own checks."""
    if name not in {f.name for f in dataclasses.fields(config)}:
        raise ValueError(f"{type(config).__name__} has no field {name!r}")
    return dataclasses.replace(config, **{name: value})


def sweep(spec: ExperimentSpec, grid: Mapping[str, Sequence[object]]) -> list[ExperimentSpec]:
    """One experiment per point of the grid's Cartesian product, in a fixed order (the grid's
    keys in insertion order, each key's values in the given order)."""
    if not grid:
        return [spec]
    keys = list(grid)
    if any(not grid[key] for key in keys):
        raise ValueError("every swept setting needs at least one value")
    specs = []
    for values in itertools.product(*(grid[key] for key in keys)):
        point = spec
        for key, value in zip(keys, values, strict=True):
            point = point.with_value(key, value)
        specs.append(point)
    return specs
