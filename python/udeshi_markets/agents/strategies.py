"""The five trading strategies.

Prices inside the strategies are in ticks; every threshold and spread is relative to price
(a fraction such as 0.001 for 10 basis points), so a strategy behaves the same on a 0.01-tick
stock at 100 and a 0.05-tick stock at 250. Every signal uses only information available at the
moment of the decision: public market data observed so far, the agent's own fills and, for the
fundamental trader, a noisy signal of the *current* fundamental value.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Sequence

from udeshi_markets._engine import Side
from udeshi_markets.agents.base import ACT, TradingAgent
from udeshi_markets.simulation import (
    SECOND,
    Fill,
    News,
    SimulationContext,
    deterministic_log,
)

# Prices computed in floating point can land a hair off an integer (10000 * 1.001 is
# 10009.999999999998), and rounding that down would cost a whole tick. Values within this
# tolerance of an integer are treated as that integer.
_TICK_TOLERANCE = 1e-6


def _ticks_up(value: float) -> int:
    """The smallest whole tick at or above ``value`` (at least 1)."""
    return max(1, math.ceil(value - _TICK_TOLERANCE))


def _ticks_down(value: float) -> int:
    """The largest whole tick at or below ``value`` (at least 1)."""
    return max(1, math.floor(value + _TICK_TOLERANCE))


class RandomTrader(TradingAgent):
    """Zero-intelligence liquidity: random side, size, price and timing.

    Each action picks a side with probability 1/2 and a size uniform in [1, ``max_quantity``].
    With probability ``market_order_probability`` it sends a market order; otherwise a limit order
    at mark * (1 + ``price_noise`` * Z), Z standard normal, so about half its limit orders cross
    the spread. Orders older than ``order_lifetime_s`` are cancelled.
    """

    strategy = "random"

    def __init__(
        self,
        participant_id: int,
        instruments: Sequence[int],
        *,
        mean_interval_s: float = 2.0,
        max_quantity: int = 10,
        price_noise: float = 0.001,
        market_order_probability: float = 0.1,
        order_lifetime_s: float = 30.0,
        max_position: int = 200,
        frequency: float = 1.0,
    ) -> None:
        super().__init__(
            participant_id,
            instruments,
            mean_interval_s=mean_interval_s,
            frequency=frequency,
            max_position=max_position,
        )
        if max_quantity < 1:
            raise ValueError("max_quantity must be at least 1")
        if not 0.0 <= market_order_probability <= 1.0:
            raise ValueError("market_order_probability must lie in [0, 1]")
        if not price_noise >= 0:
            raise ValueError("price_noise must be non-negative")
        self.max_quantity = max_quantity
        self.price_noise = price_noise
        self.market_order_probability = market_order_probability
        self.order_lifetime_ns = order_lifetime_s * SECOND

    def act(self, ctx: SimulationContext, tag: str) -> None:
        for order in list(self.open_orders.values()):
            if ctx.now - order.time >= self.order_lifetime_ns:
                self.cancel(ctx, order)
        rng = ctx.rng
        instrument = rng.choice(self.instruments)
        side = Side.BUY if rng.bernoulli(0.5) else Side.SELL
        if self.room(ctx, instrument, side) == 0:
            side = Side.SELL if side is Side.BUY else Side.BUY
        room = self.room(ctx, instrument, side)
        if room == 0:
            return
        quantity = min(room, rng.randint(1, self.max_quantity))
        if rng.bernoulli(self.market_order_probability):
            self.market(ctx, instrument, side, quantity)
            return
        mark = float(ctx.mark(instrument))
        price = round(mark * (1.0 + self.price_noise * rng.normal()))
        self.limit(ctx, instrument, side, quantity, price)


class MarketMaker(TradingAgent):
    """Quotes a bid and an ask around its estimate of fair value, skewed by inventory.

    Fair value f is an exponentially weighted average of trade prices (weight
    ``fair_value_alpha`` per new trade), starting at the reference price. With position q and
    limit Q, the quotes are centred on a reservation price r = f * (1 - ``skew_at_limit`` * q / Q):
    a long market maker lowers both quotes (selling becomes attractive to buyers, buying less
    attractive to sellers), a short one raises them. Bid = floor(r - h), ask = ceil(r + h) with
    h = max(1 tick, ``half_spread`` * f). The side that would push |q| past Q is not quoted.
    Quotes are cancelled and replaced at every action.

    Measured (in currency):
    * spread earned: for each passive fill, (f - price) * qty on buys and (price - f) * qty on
      sells, with f the fair value when the fill happened;
    * adverse selection: ``markout_seconds`` after each passive fill, (price - mark) * qty on
      buys and (mark - price) * qty on sells, the loss from the market moving against the fill.
    """

    strategy = "market_maker"

    def __init__(
        self,
        participant_id: int,
        instruments: Sequence[int],
        *,
        mean_interval_s: float = 1.0,
        half_spread: float = 0.0005,
        quote_size: int = 20,
        skew_at_limit: float = 0.001,
        fair_value_alpha: float = 0.3,
        markout_seconds: float = 5.0,
        max_position: int = 200,
        frequency: float = 1.0,
    ) -> None:
        super().__init__(
            participant_id,
            instruments,
            mean_interval_s=mean_interval_s,
            frequency=frequency,
            max_position=max_position,
        )
        if quote_size < 1:
            raise ValueError("quote_size must be at least 1")
        if not 0.0 < fair_value_alpha <= 1.0:
            raise ValueError("fair_value_alpha must lie in (0, 1]")
        self.half_spread = half_spread
        self.quote_size = quote_size
        self.skew_at_limit = skew_at_limit
        self.fair_value_alpha = fair_value_alpha
        self.markout_ns = markout_seconds * SECOND
        self.fair: dict[int, float] = {}
        self._last_trade_seen: dict[int, int] = {}
        self._markouts: deque[tuple[int, int, Side, int, int, float]] = deque()
        self.spread_earned = 0.0
        self.adverse_selection = 0.0
        self.markouts_measured = 0

    def on_start(self, ctx: SimulationContext) -> None:
        for instrument in self.instruments:
            self.fair[instrument] = float(ctx.reference_price(instrument))
            self._last_trade_seen[instrument] = 0
        super().on_start(ctx)

    def on_wakeup(self, ctx: SimulationContext, tag: str) -> None:
        if tag == "markout":  # Measurement only: no requoting.
            self._measure_markouts(ctx)
            return
        super().on_wakeup(ctx, tag)

    def act(self, ctx: SimulationContext, tag: str) -> None:
        self._measure_markouts(ctx)
        for instrument in self.instruments:
            self._update_fair(ctx, instrument)
            if self.busy(instrument):
                continue  # Quotes or cancels still on their way: requote once they land.
            self.cancel_all(ctx, instrument)
            self._quote(ctx, instrument)

    def _update_fair(self, ctx: SimulationContext, instrument: int) -> None:
        last_seen = self._last_trade_seen[instrument]
        for trade in ctx.recent_trades(instrument, limit=50):
            if trade.trade_id > last_seen:
                alpha = self.fair_value_alpha
                self.fair[instrument] = (1 - alpha) * self.fair[instrument] + alpha * trade.price
                self._last_trade_seen[instrument] = trade.trade_id

    def _quote(self, ctx: SimulationContext, instrument: int) -> None:
        fair = self.fair[instrument]
        position = ctx.portfolio.position(instrument)
        reservation = fair * (1.0 - self.skew_at_limit * position / self.max_position)
        half = max(1.0, self.half_spread * fair)
        bid = _ticks_down(reservation - half)
        ask = max(bid + 1, _ticks_up(reservation + half))
        # The quotes being cancelled still count against the limit until their cancels land
        # (they could fill first), so the limit holds whatever the order latency.
        bid_size = min(self.quote_size, self.room(ctx, instrument, Side.BUY))
        ask_size = min(self.quote_size, self.room(ctx, instrument, Side.SELL))
        if bid_size > 0:
            self.limit(ctx, instrument, Side.BUY, bid_size, bid)
        if ask_size > 0:
            self.limit(ctx, instrument, Side.SELL, ask_size, ask)

    def on_fill(self, ctx: SimulationContext, fill: Fill) -> None:
        super().on_fill(ctx, fill)
        if fill.is_aggressor:
            return
        fair = self.fair.get(fill.instrument, float(fill.price))
        tick = ctx.tick_size(fill.instrument)
        edge = (fair - fill.price) if fill.side is Side.BUY else (fill.price - fair)
        self.spread_earned += edge * fill.quantity * tick
        self._markouts.append(
            (fill.time, fill.instrument, fill.side, fill.price, fill.quantity, tick)
        )
        ctx.wakeup(round(self.markout_ns), "markout")  # Measured exactly at the horizon.

    def _measure_markouts(self, ctx: SimulationContext) -> None:
        while self._markouts and ctx.now - self._markouts[0][0] >= self.markout_ns:
            _, instrument, side, price, quantity, tick = self._markouts.popleft()
            mark = float(ctx.mark(instrument))
            loss = (price - mark) if side is Side.BUY else (mark - price)
            self.adverse_selection += loss * quantity * tick
            self.markouts_measured += 1

    def metrics(self) -> dict[str, float]:
        return {
            "spread_earned": self.spread_earned,
            "adverse_selection": self.adverse_selection,
            "markouts_measured": float(self.markouts_measured),
        }


class MomentumTrader(TradingAgent):
    """Trend following with moving averages of the mark price.

    At each action the agent samples the mark. With the last ``short_window`` and
    ``long_window`` samples (past and present only), signal = SMA_short / SMA_long - 1. Above
    ``entry_threshold`` it buys, below -``entry_threshold`` it sells, ``trade_size`` at a time up
    to ``max_position``. When |signal| < ``exit_threshold`` it trades its position back towards
    zero. Orders are immediate-or-cancel, priced ``slippage`` beyond the mark.
    """

    strategy = "momentum"

    def __init__(
        self,
        participant_id: int,
        instruments: Sequence[int],
        *,
        mean_interval_s: float = 2.0,
        short_window: int = 10,
        long_window: int = 40,
        entry_threshold: float = 0.001,
        exit_threshold: float = 0.0002,
        trade_size: int = 10,
        slippage: float = 0.001,
        max_position: int = 100,
        frequency: float = 1.0,
    ) -> None:
        super().__init__(
            participant_id,
            instruments,
            mean_interval_s=mean_interval_s,
            frequency=frequency,
            max_position=max_position,
        )
        if not 1 <= short_window < long_window:
            raise ValueError("windows must satisfy 1 <= short_window < long_window")
        if not 0 <= exit_threshold <= entry_threshold:
            raise ValueError("thresholds must satisfy 0 <= exit_threshold <= entry_threshold")
        self.short_window = short_window
        self.long_window = long_window
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.trade_size = trade_size
        self.slippage = slippage
        self.samples: dict[int, deque[float]] = {
            i: deque(maxlen=long_window) for i in self.instruments
        }
        self.signal: dict[int, float | None] = dict.fromkeys(self.instruments)

    def act(self, ctx: SimulationContext, tag: str) -> None:
        for instrument in self.instruments:
            mark = float(ctx.mark(instrument))
            samples = self.samples[instrument]
            samples.append(mark)
            if len(samples) < self.long_window:
                continue
            short = sum(list(samples)[-self.short_window :]) / self.short_window
            long = sum(samples) / len(samples)
            signal = short / long - 1.0
            self.signal[instrument] = signal
            position = ctx.portfolio.position(instrument)
            if signal > self.entry_threshold:
                self._trade(ctx, instrument, Side.BUY, self.trade_size, mark)
            elif signal < -self.entry_threshold:
                self._trade(ctx, instrument, Side.SELL, self.trade_size, mark)
            elif abs(signal) < self.exit_threshold and position:
                side = Side.SELL if position > 0 else Side.BUY
                self._trade(ctx, instrument, side, min(self.trade_size, abs(position)), mark)

    def _trade(
        self, ctx: SimulationContext, instrument: int, side: Side, quantity: int, mark: float
    ) -> None:
        quantity = min(quantity, self.room(ctx, instrument, side))
        if quantity <= 0:
            return
        if side is Side.BUY:
            price = _ticks_up(mark * (1.0 + self.slippage))
        else:
            price = _ticks_down(mark * (1.0 - self.slippage))
        self.limit(ctx, instrument, side, quantity, price, immediate=True)

    def metrics(self) -> dict[str, float]:
        return {f"signal_{i}": s for i, s in self.signal.items() if s is not None}


class FundamentalTrader(TradingAgent):
    """Value investing on a noisy private signal of the fundamental value.

    At each action, and as soon as news about one of its instruments is published, the agent
    draws an estimate E of the *current* fundamental (its own noise, ``signal_noise``). If the
    mark is below E * (1 - ``threshold``) it buys, at most at that price; above
    E * (1 + ``threshold``) it sells, at least at that price. Orders are immediate-or-cancel.
    """

    strategy = "fundamental"

    def __init__(
        self,
        participant_id: int,
        instruments: Sequence[int],
        *,
        mean_interval_s: float = 2.0,
        signal_noise: float = 0.005,
        threshold: float = 0.003,
        trade_size: int = 25,
        max_position: int = 1_000,
        frequency: float = 1.0,
    ) -> None:
        super().__init__(
            participant_id,
            instruments,
            mean_interval_s=mean_interval_s,
            frequency=frequency,
            max_position=max_position,
        )
        if not (math.isfinite(signal_noise) and signal_noise >= 0):
            raise ValueError("signal_noise must be non-negative")
        self.signal_noise = signal_noise
        self.threshold = threshold
        self.trade_size = trade_size
        self.estimate: dict[int, float | None] = dict.fromkeys(self.instruments)

    def on_news(self, ctx: SimulationContext, news: News) -> None:
        if news.instrument in self.instruments:
            ctx.wakeup(0, "news")  # Act on it now (one-off; the regular schedule continues).

    def act(self, ctx: SimulationContext, tag: str) -> None:
        for instrument in self.instruments:
            estimate = ctx.fundamental_estimate(instrument)
            self.estimate[instrument] = estimate
            mark = float(ctx.mark(instrument))
            low = estimate * (1.0 - self.threshold)
            high = estimate * (1.0 + self.threshold)
            if mark < low:
                quantity = min(self.trade_size, self.room(ctx, instrument, Side.BUY))
                if quantity:
                    self.limit(
                        ctx, instrument, Side.BUY, quantity, _ticks_down(low), immediate=True
                    )
            elif mark > high:
                quantity = min(self.trade_size, self.room(ctx, instrument, Side.SELL))
                if quantity:
                    self.limit(
                        ctx, instrument, Side.SELL, quantity, _ticks_up(high), immediate=True
                    )


class PairsTrader(TradingAgent):
    """Statistical arbitrage on two cointegrated instruments.

    At each action the agent computes s = ln P_leader - ln P_follower from the marks. Using the
    previous ``window`` values of s (never the current one, and never anything later), it forms
    z = (s - mean) / stdev; with fewer than ``min_observations`` values or zero variance there is
    no signal. When flat: z > ``entry_z`` sells the spread (sell leader, buy follower), z <
    -``entry_z`` buys it. When |z| < ``exit_z`` it unwinds both legs. The legs are sized for
    equal currency value: ``trade_size`` leader shares against the follower shares worth the same.
    """

    strategy = "pairs"

    def __init__(
        self,
        participant_id: int,
        leader: int,
        follower: int,
        *,
        mean_interval_s: float = 2.0,
        window: int = 60,
        min_observations: int = 20,
        entry_z: float = 2.0,
        exit_z: float = 0.5,
        trade_size: int = 10,
        slippage: float = 0.001,
        max_position: int = 100,
        frequency: float = 1.0,
    ) -> None:
        super().__init__(
            participant_id,
            (leader, follower),
            mean_interval_s=mean_interval_s,
            frequency=frequency,
            max_position=max_position,
        )
        if leader == follower:
            raise ValueError("a pair needs two different instruments")
        if not 2 <= min_observations <= window:
            raise ValueError("min_observations must lie in [2, window]")
        if not 0 <= exit_z < entry_z:
            raise ValueError("thresholds must satisfy 0 <= exit_z < entry_z")
        self.leader = leader
        self.follower = follower
        self.window = window
        self.min_observations = min_observations
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.trade_size = trade_size
        self.slippage = slippage
        self.spreads: deque[float] = deque(maxlen=window)
        self.z: float | None = None

    def act(self, ctx: SimulationContext, tag: str) -> None:
        leader_mark = float(ctx.mark(self.leader))
        follower_mark = float(ctx.mark(self.follower))
        leader_value = leader_mark * ctx.tick_size(self.leader)
        follower_value = follower_mark * ctx.tick_size(self.follower)
        spread = deterministic_log(leader_value) - deterministic_log(follower_value)
        self.z = self._z_score(spread)
        self.spreads.append(spread)  # Only after the signal: it never sees its own value.
        if self.z is None:
            return
        leader_position = ctx.portfolio.position(self.leader)
        follower_position = ctx.portfolio.position(self.follower)
        flat = leader_position == 0 and follower_position == 0
        if flat and abs(self.z) > self.entry_z:
            # z > entry: the spread is wide, so sell the leader and buy the follower.
            leader_side = Side.SELL if self.z > 0 else Side.BUY
            follower_side = Side.BUY if self.z > 0 else Side.SELL
            sizes = self._leg_sizes(ctx, leader_side, follower_side, leader_value, follower_value)
            if sizes is not None:
                self._leg(ctx, self.leader, leader_side, sizes[0], leader_mark)
                self._leg(ctx, self.follower, follower_side, sizes[1], follower_mark)
        elif not flat and abs(self.z) < self.exit_z:
            for instrument, position, mark in (
                (self.leader, leader_position, leader_mark),
                (self.follower, follower_position, follower_mark),
            ):
                if position:
                    side = Side.SELL if position > 0 else Side.BUY
                    self._leg(ctx, instrument, side, abs(position), mark)

    def _z_score(self, spread: float) -> float | None:
        count = len(self.spreads)
        if count < self.min_observations:
            return None
        if max(self.spreads) == min(self.spreads):
            return None  # Zero variance: no signal (and nothing to divide by).
        # Deviations from the first value: the mean of identical floats is not always exactly
        # that float, and the rounding error would show up as a tiny, spurious variance.
        origin = self.spreads[0]
        deviations = [s - origin for s in self.spreads]
        mean = sum(deviations) / count
        variance = sum((d - mean) ** 2 for d in deviations) / count
        if variance <= 0.0:
            return None
        return (spread - origin - mean) / math.sqrt(variance)

    def _leg_sizes(
        self,
        ctx: SimulationContext,
        leader_side: Side,
        follower_side: Side,
        leader_value: float,
        follower_value: float,
    ) -> tuple[int, int] | None:
        """Leg sizes of equal currency value, both within the position limits: if either leg
        is capped, both shrink together. None if no balanced trade fits."""
        leader_room = self.room(ctx, self.leader, leader_side)
        follower_room = self.room(ctx, self.follower, follower_side)
        leader = min(
            self.trade_size, leader_room, math.floor(follower_room * follower_value / leader_value)
        )
        if leader <= 0:
            return None
        follower = min(follower_room, max(1, round(leader * leader_value / follower_value)))
        return leader, follower

    def _leg(
        self, ctx: SimulationContext, instrument: int, side: Side, quantity: int, mark: float
    ) -> None:
        quantity = min(quantity, self.room(ctx, instrument, side))
        if quantity <= 0:
            return
        if side is Side.BUY:
            price = _ticks_up(mark * (1.0 + self.slippage))
        else:
            price = _ticks_down(mark * (1.0 - self.slippage))
        self.limit(ctx, instrument, side, quantity, price, immediate=True)

    def metrics(self) -> dict[str, float]:
        return {} if self.z is None else {"z_score": self.z}


__all__ = [
    "ACT",
    "FundamentalTrader",
    "MarketMaker",
    "MomentumTrader",
    "PairsTrader",
    "RandomTrader",
]
