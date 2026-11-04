"""Market, microstructure and agent metrics, computed from a ``MarketObserver``'s record.

The definitions are in docs/quantitative-metrics.md and docs/market-microstructure.md. In
short, with s = +1 for buyer-initiated and -1 for seller-initiated trades, p the trade price,
m the midprice just before the trade and m' the midprice ``markout`` later:
* effective spread = 2 s (p - m) / m;
* realized spread = 2 s (p - m') / m;
* price impact = 2 s (m' - m) / m, so effective = realized + impact, trade by trade.

All three are volume-weighted and computed over the same trades: those with both quotes. A
trade from a one-sided book, or too close to the end of the run for its markout, is counted but
excluded. Spreads and impacts are in basis points.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from udeshi_markets.agents import TradingAgent
from udeshi_markets.analytics.observer import MarketObserver
from udeshi_markets.analytics.stats import (
    max_drawdown,
    mean,
    realized_volatility,
    sharpe_ratio,
    simple_returns,
    weighted_mean,
)
from udeshi_markets.simulation import ENVIRONMENT_ID, Participant, deterministic_log

BPS = 10_000.0


@dataclass(frozen=True, slots=True)
class MarketMetrics:
    symbol: str
    trades: int
    volume: int
    last_price: float | None
    volatility_per_hour: float | None  # Of the midprice (log returns).
    mean_spread: float | None  # Currency, over samples with both sides quoted.
    mean_spread_bps: float | None  # Spread / midprice.
    one_sided_fraction: float  # Share of samples with an empty side.
    mean_depth: float | None  # Shares within the top levels, both sides together.
    mean_imbalance: float | None  # (bid - ask) / (bid + ask) depth, in [-1, 1].
    pricing_error_bps: float | None  # Mean |ln(mid / fundamental)|: market efficiency.


@dataclass(frozen=True, slots=True)
class MicrostructureMetrics:
    symbol: str
    effective_spread_bps: float | None
    realized_spread_bps: float | None
    price_impact_bps: float | None
    measured_trades: int  # Trades with both quotes, over which the three above are computed.
    trades_without_quote: int  # Trades from a one-sided book.
    trades_without_markout: int  # Trades too close to the end for their markout.
    fill_rate: float | None  # Shares filled / shares submitted, over all orders.
    average_trade_size: float | None
    buyer_initiated_fraction: float | None


@dataclass(frozen=True, slots=True)
class AgentMetrics:
    participant_id: int
    name: str
    strategy: str
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float
    return_pct: float | None
    sharpe_ratio: float | None  # Per hour, of equity returns between samples.
    max_drawdown: float | None  # Currency.
    max_drawdown_pct: float | None  # Of the running peak.
    mean_exposure: float | None  # Gross |position| * mark, currency.
    max_exposure: float | None
    win_rate: float | None  # Profitable closing fills / closing fills.
    trades: int
    filled_quantity: int
    submitted_quantity: int
    fill_rate: float | None
    average_price: dict[str, float] = field(default_factory=dict)  # VWAP of own fills.


@dataclass(frozen=True, slots=True)
class RunAnalytics:
    market: tuple[MarketMetrics, ...]
    microstructure: tuple[MicrostructureMetrics, ...]
    agents: tuple[AgentMetrics, ...]
    interval_ns: int
    markout_ns: int


def strategy_of(participant: Participant) -> str:
    if isinstance(participant, TradingAgent):
        return participant.strategy
    if participant.participant_id == ENVIRONMENT_ID:
        return "environment"
    return "other"


def analyze(observer: MarketObserver) -> RunAnalytics:
    return RunAnalytics(
        market=tuple(_market(observer, i) for i in range(len(observer.series))),
        microstructure=tuple(_microstructure(observer, i) for i in range(len(observer.series))),
        agents=tuple(_agent(observer, pid) for pid in sorted(observer.equity)),
        interval_ns=observer.interval_ns,
        markout_ns=observer.markout_ns,
    )


def _market(observer: MarketObserver, instrument: int) -> MarketMetrics:
    simulation = observer.simulation
    stats = simulation.exchange.statistics(instrument)
    tick = simulation.exchange.instrument(instrument).tick_size
    series = observer.series[instrument]
    two_sided = [
        (mid, spread)
        for mid, spread in zip(series.mids, series.spreads, strict=True)
        if mid is not None and spread is not None
    ]
    depths = [b + a for b, a in zip(series.bid_depth, series.ask_depth, strict=True)]
    imbalances = [
        (b - a) / (b + a)
        for b, a in zip(series.bid_depth, series.ask_depth, strict=True)
        if b + a > 0
    ]
    errors = [
        abs(deterministic_log(mid * tick) - deterministic_log(value))
        for mid, value in zip(series.mids, series.fundamentals, strict=True)
        if mid is not None and value is not None and value > 0
    ]
    samples = len(series.mids)
    spread_mean = mean([spread for _, spread in two_sided])
    return MarketMetrics(
        symbol=simulation.config.symbols[instrument],
        trades=stats.trade_count,
        volume=stats.volume,
        last_price=None if stats.last_price is None else stats.last_price * tick,
        volatility_per_hour=realized_volatility(series.mids, observer.periods_per_hour),
        mean_spread=None if spread_mean is None else spread_mean * tick,
        mean_spread_bps=mean([spread / mid * BPS for mid, spread in two_sided]),
        one_sided_fraction=(samples - len(two_sided)) / samples if samples else 0.0,
        mean_depth=mean([float(d) for d in depths]),
        mean_imbalance=mean(imbalances),
        pricing_error_bps=None if not errors else (mean(errors) or 0.0) * BPS,
    )


def _microstructure(observer: MarketObserver, instrument: int) -> MicrostructureMetrics:
    simulation = observer.simulation
    trades = [(index, t) for index, t in enumerate(observer.trades) if t.instrument == instrument]
    effective: list[float] = []
    realized: list[float] = []
    impact: list[float] = []
    weights: list[float] = []
    without_quote = without_markout = 0
    for index, trade in trades:
        if trade.mid_before is None:
            without_quote += 1
            continue
        after = observer.mid_after.get(index)
        if after is None:
            # Not yet due (too close to the end), or the book was one-sided at the horizon.
            without_markout += 1
            continue
        m, m_after, p, s = float(trade.mid_before), float(after), trade.price, trade.sign
        effective.append(2 * s * (p - m) / m * BPS)
        realized.append(2 * s * (p - m_after) / m * BPS)
        impact.append(2 * s * (m_after - m) / m * BPS)
        weights.append(float(trade.quantity))
    volume = sum(t.quantity for _, t in trades)
    submitted = observer.submitted[instrument]
    return MicrostructureMetrics(
        symbol=simulation.config.symbols[instrument],
        effective_spread_bps=weighted_mean(effective, weights),
        realized_spread_bps=weighted_mean(realized, weights),
        price_impact_bps=weighted_mean(impact, weights),
        measured_trades=len(weights),
        trades_without_quote=without_quote,
        trades_without_markout=without_markout,
        # Every trade fills two orders (the aggressor's and the resting one).
        fill_rate=2 * volume / submitted if submitted else None,
        average_trade_size=volume / len(trades) if trades else None,
        buyer_initiated_fraction=(
            sum(1 for _, t in trades if t.sign > 0) / len(trades) if trades else None
        ),
    )


def _agent(observer: MarketObserver, participant_id: int) -> AgentMetrics:
    simulation = observer.simulation
    participant = simulation.participants[participant_id]
    portfolio = simulation.portfolio(participant_id)
    marks = simulation.marks()
    symbols = simulation.config.symbols
    details = [portfolio.details(i) for i in range(len(symbols))]
    equity = observer.equity[participant_id]
    exposure = observer.exposure[participant_id]
    drawdown = max_drawdown(equity)
    initial = float(portfolio.initial_cash)
    total = float(portfolio.total_pnl(marks))
    closing = sum(d.closing_trades for d in details)
    filled = sum(d.bought + d.sold for d in details)
    submitted = observer.submitted_by.get(participant_id, 0)
    average_price = {
        symbols[i]: float((d.buy_notional + d.sell_notional) / (d.bought + d.sold))
        for i, d in enumerate(details)
        if d.bought + d.sold
    }
    return AgentMetrics(
        participant_id=participant_id,
        name=participant.name,
        strategy=strategy_of(participant),
        realized_pnl=float(portfolio.realized_pnl()),
        unrealized_pnl=float(portfolio.unrealized_pnl(marks)),
        total_pnl=total,
        return_pct=total / initial * 100 if initial > 0 else None,
        sharpe_ratio=sharpe_ratio(simple_returns(equity), observer.periods_per_hour),
        max_drawdown=None if drawdown is None else drawdown[0],
        max_drawdown_pct=None if drawdown is None or drawdown[1] is None else drawdown[1] * 100,
        mean_exposure=mean(exposure),
        max_exposure=max(exposure) if exposure else None,
        win_rate=sum(d.winning_trades for d in details) / closing if closing else None,
        trades=sum(d.trades for d in details),
        filled_quantity=filled,
        submitted_quantity=submitted,
        fill_rate=filled / submitted if submitted else None,
        average_price=average_price,
    )


def by_strategy(agents: Sequence[AgentMetrics]) -> dict[str, list[AgentMetrics]]:
    grouped: dict[str, list[AgentMetrics]] = {}
    for agent in agents:
        grouped.setdefault(agent.strategy, []).append(agent)
    return grouped
