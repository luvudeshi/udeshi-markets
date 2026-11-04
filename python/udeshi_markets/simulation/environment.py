"""The market environment: hidden fundamental values and the events that move them.

The environment is the source of everything exogenous in a simulation. It is a participant of
the kernel with a reserved id, so its timers are ordinary events (deterministic and digested),
and it can act through its context like anyone else: liquidity shocks are real market orders.
It never reveals the fundamentals directly. What participants can learn of them is a noisy
private signal (for participants granted one) and the public news.

Model, per instrument i, in log space (x = ln V):
* Diffusion every ``step_ns``: x += -sigma**2 * dt / 2 + sigma * sqrt(dt) * Z, so V is a
  martingale. sigma is ``volatility`` per sqrt(hour), times ``volatile_multiplier`` while the
  market is in the volatile regime.
* Regimes: calm and volatile alternate, with exponentially distributed durations (means
  ``calm_seconds`` and ``volatile_seconds``). Trading activity is ``volatile_activity`` times
  higher in the volatile regime; agents scale their order rates by it.
* News: a Poisson process (``news_per_hour`` per instrument). Each item has a random sign and an
  exponentially distributed magnitude (mean ``news_impact``): x += sign * magnitude, and the
  item is published to every participant at the same instant.
* Unannounced shocks: the same, at ``shocks_per_hour``, but never published. The market only
  finds out through informed trading.
* Liquidity shocks: a Poisson process (``liquidity_shocks_per_hour``, market-wide). A forced
  trader sends a market order of ``liquidity_shock_quantity`` shares, random side and
  instrument, that sweeps whatever liquidity is there.
* Pairs: a follower's log value is the leader's log move plus an Ornstein-Uhlenbeck deviation,
  x_f(t) = x_f(0) + (x_l(t) - x_l(0)) + D(t), with D mean-reverting (half-life
  ``half_life_seconds``, stationary standard deviation ``deviation_volatility``). The two prices
  are cointegrated: ln P_l - ln P_f is stationary. Followers have no news or shocks of their own.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from udeshi_markets._engine import OrderType, Side
from udeshi_markets.simulation.events import OrderIntent
from udeshi_markets.simulation.participant import Participant
from udeshi_markets.simulation.rng import DeterministicRng, deterministic_exp, deterministic_log
from udeshi_markets.simulation.simtime import HOUR, SECOND

if TYPE_CHECKING:
    from udeshi_markets.simulation.kernel import EnvironmentContext, SimulationContext

ENVIRONMENT_ID = 999_999
"""Participant id of the environment (its forced trades come from this id)."""


@dataclass(frozen=True, slots=True)
class PairConfig:
    leader: str
    follower: str
    half_life_seconds: float = 120.0
    deviation_volatility: float = 0.01

    def __post_init__(self) -> None:
        if self.leader == self.follower:
            raise ValueError("a pair needs two different instruments")
        if not self.half_life_seconds > 0:
            raise ValueError("half_life_seconds must be positive")
        if not self.deviation_volatility >= 0:
            raise ValueError("deviation_volatility must be non-negative")


@dataclass(frozen=True, slots=True)
class EnvironmentConfig:
    step_ns: int = SECOND
    volatility: float = 0.02
    """Standard deviation of the log fundamental per sqrt(hour), in the calm regime."""
    volatile_multiplier: float = 3.0
    calm_seconds: float = 600.0
    volatile_seconds: float = 120.0
    volatile_activity: float = 2.0
    news_per_hour: float = 6.0
    news_impact: float = 0.01
    shocks_per_hour: float = 2.0
    shock_impact: float = 0.005
    liquidity_shocks_per_hour: float = 2.0
    liquidity_shock_quantity: int = 200
    pairs: tuple[PairConfig, ...] = field(default=())

    def __post_init__(self) -> None:
        if self.step_ns <= 0:
            raise ValueError("step_ns must be positive")
        for name in (
            "volatility",
            "news_per_hour",
            "news_impact",
            "shocks_per_hour",
            "shock_impact",
            "liquidity_shocks_per_hour",
        ):
            value = getattr(self, name)
            if not (math.isfinite(value) and value >= 0):
                raise ValueError(f"{name} must be finite and non-negative")
        if not (math.isfinite(self.volatile_multiplier) and self.volatile_multiplier >= 1):
            raise ValueError("volatile_multiplier must be at least 1")
        if not (math.isfinite(self.volatile_activity) and self.volatile_activity > 0):
            raise ValueError("volatile_activity must be positive")
        if not (self.calm_seconds > 0 and self.volatile_seconds > 0):
            raise ValueError("regime durations must be positive")
        if self.liquidity_shock_quantity < 1:
            raise ValueError("liquidity_shock_quantity must be at least 1")
        followers = [pair.follower for pair in self.pairs]
        if len(set(followers)) != len(followers):
            raise ValueError("an instrument can follow only one leader")
        if set(followers) & {pair.leader for pair in self.pairs}:
            raise ValueError("a follower cannot also lead a pair")


class MarketEnvironment(Participant):
    """Evolves hidden fundamental values and generates news, shocks and regime changes."""

    def __init__(self, config: EnvironmentConfig) -> None:
        super().__init__(ENVIRONMENT_ID, "environment")
        self.config = config
        self._log_values: list[float] = []
        self._log_start: list[float] = []
        self._values: list[float] = []  # Currency.
        self._start_values: list[float] = []
        self._follows: dict[int, tuple[int, float, float]] = {}  # follower -> leader, a, s.
        self._deviation: dict[int, float] = {}
        self._volatile = False
        self._rng: DeterministicRng | None = None
        self._ticks: list[float] = []
        self.news_published = 0
        self.shocks = 0
        self.liquidity_shocks = 0
        self.regime_changes = 0

    # ---- What the kernel and other participants may ask -----------------------------------
    def fundamental(self, instrument: int) -> float:
        """The true value in currency. The kernel uses it to produce noisy signals; it is never
        handed to participants directly."""
        return self._values[instrument]

    @property
    def activity(self) -> float:
        return self.config.volatile_activity if self._volatile else 1.0

    @property
    def volatile(self) -> bool:
        return self._volatile

    # ---- Event handling ------------------------------------------------------------------------
    def on_start(self, ctx: SimulationContext) -> None:
        env = self._env(ctx)
        self._rng = env.rng
        count = len(env.symbols)
        self._ticks = [env.tick_size(i) for i in range(count)]
        self._values = [env.reference_price(i) * self._ticks[i] for i in range(count)]
        self._start_values = list(self._values)
        self._log_values = [deterministic_log(v) for v in self._values]
        self._log_start = list(self._log_values)
        index = {symbol: i for i, symbol in enumerate(env.symbols)}
        dt = self.config.step_ns / SECOND
        for pair in self.config.pairs:
            if pair.leader not in index or pair.follower not in index:
                raise ValueError(f"pair {pair.leader}/{pair.follower} names an unknown symbol")
            kappa = math.log(2.0) / pair.half_life_seconds
            a = deterministic_exp(-kappa * dt)
            s = pair.deviation_volatility * math.sqrt(1.0 - a * a)
            self._follows[index[pair.follower]] = (index[pair.leader], a, s)
            self._deviation[index[pair.follower]] = 0.0
        ctx.wakeup(self.config.step_ns, "step")
        self._schedule_regime(ctx)
        for i in self._independent(count):
            self._schedule(ctx, self.config.news_per_hour, f"news:{i}")
            self._schedule(ctx, self.config.shocks_per_hour, f"shock:{i}")
        self._schedule(ctx, self.config.liquidity_shocks_per_hour, "liquidity")

    def on_wakeup(self, ctx: SimulationContext, tag: str) -> None:
        kind, _, target = tag.partition(":")
        rng = self._require_rng()
        if kind == "step":
            self._diffuse()
            ctx.wakeup(self.config.step_ns, "step")
        elif kind == "regime":
            self._volatile = not self._volatile
            self.regime_changes += 1
            self._schedule_regime(ctx)
        elif kind == "news":
            i = int(target)
            impact = self._jump(i, self.config.news_impact)
            self.news_published += 1
            word = "beats expectations" if impact > 0 else "misses expectations"
            self._env(ctx).publish_news(i, impact, f"{self._env(ctx).symbols[i]} {word}")
            self._schedule(ctx, self.config.news_per_hour, tag)
        elif kind == "shock":
            self._jump(int(target), self.config.shock_impact)
            self.shocks += 1
            self._schedule(ctx, self.config.shocks_per_hour, tag)
        elif kind == "liquidity":
            instrument = rng.randint(0, len(self._values) - 1)
            side = Side.BUY if rng.bernoulli(0.5) else Side.SELL
            quantity = self.config.liquidity_shock_quantity
            ctx.submit(OrderIntent(instrument, side, quantity, order_type=OrderType.MARKET))
            self.liquidity_shocks += 1
            self._schedule(ctx, self.config.liquidity_shocks_per_hour, tag)
        else:
            raise ValueError(f"unknown environment timer {tag!r}")

    # ---- Dynamics ------------------------------------------------------------------------------
    def _diffuse(self) -> None:
        rng = self._require_rng()
        dt = self.config.step_ns / SECOND
        sigma = self.config.volatility / math.sqrt(HOUR / SECOND)
        if self._volatile:
            sigma *= self.config.volatile_multiplier
        drift = -0.5 * sigma * sigma * dt
        scale = sigma * math.sqrt(dt)
        for i in self._independent(len(self._values)):
            self._log_values[i] += drift + scale * rng.normal()
        for follower, (_, a, s) in self._follows.items():
            self._deviation[follower] = self._deviation[follower] * a + s * rng.normal()
        self._refresh()

    def _jump(self, instrument: int, mean_impact: float) -> float:
        rng = self._require_rng()
        magnitude = rng.exponential(1.0 / mean_impact) if mean_impact > 0 else 0.0
        impact = magnitude if rng.bernoulli(0.5) else -magnitude
        self._log_values[instrument] += impact
        self._refresh()
        return impact

    def _refresh(self) -> None:
        for follower, (leader, _, _) in self._follows.items():
            self._log_values[follower] = (
                self._log_start[follower]
                + (self._log_values[leader] - self._log_start[leader])
                + self._deviation[follower]
            )
        self._values = [
            start * deterministic_exp(log - log_start)
            for start, log, log_start in zip(
                self._start_values, self._log_values, self._log_start, strict=True
            )
        ]

    # ---- Helpers -------------------------------------------------------------------------------
    def _independent(self, count: int) -> list[int]:
        return [i for i in range(count) if i not in self._follows]

    def _schedule(self, ctx: SimulationContext, per_hour: float, tag: str) -> None:
        if per_hour <= 0:
            return
        rate_per_ns = per_hour / HOUR
        delay = self._require_rng().exponential(rate_per_ns)
        ctx.wakeup(max(1, round(delay)), tag)

    def _schedule_regime(self, ctx: SimulationContext) -> None:
        mean = self.config.volatile_seconds if self._volatile else self.config.calm_seconds
        delay = self._require_rng().exponential(1.0 / (mean * SECOND))
        ctx.wakeup(max(1, round(delay)), "regime")

    def _require_rng(self) -> DeterministicRng:
        if self._rng is None:
            raise RuntimeError("the environment has not started")
        return self._rng

    @staticmethod
    def _env(ctx: SimulationContext) -> EnvironmentContext:
        from udeshi_markets.simulation.kernel import EnvironmentContext

        if not isinstance(ctx, EnvironmentContext):
            raise TypeError("the market environment must be installed with environment=")
        return ctx
