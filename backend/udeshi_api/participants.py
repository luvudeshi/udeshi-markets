"""Participants as the API shows them: leaderboard entries, detail views and news.

Every amount comes from the kernel's portfolios, which book each execution exactly
(``fractions.Fraction``); it is only rounded here, for display. Positions are valued at the
simulation's marks: the midprice when both sides are quoted, else the last trade, else the
reference price.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Callable, Iterable, Sequence
from fractions import Fraction
from typing import Literal

from udeshi_api.errors import not_found
from udeshi_api.manual import ManualTrader
from udeshi_api.schemas import (
    AgentDetailOut,
    AgentOut,
    AgentStatsOut,
    NewsOut,
    OpenOrderOut,
    OrderReportOut,
    PositionOut,
    StatusName,
    StrategyName,
)
from udeshi_api.serialization import PriceFormat, open_order_out
from udeshi_markets import OrderStatus, OrderType, Side, TimeInForce
from udeshi_markets.agents import TradingAgent
from udeshi_markets.simulation import (
    MarketEnvironment,
    News,
    OrderReport,
    Participant,
    Position,
    Simulation,
    to_seconds,
)
from udeshi_markets.simulation.portfolio import exact_tick

RECENT_ORDERS = 50

STATUS_NAMES: dict[OrderStatus, StatusName] = {
    OrderStatus.REJECTED: "rejected",
    OrderStatus.RESTING: "resting",
    OrderStatus.FILLED: "filled",
    OrderStatus.CANCELLED: "cancelled",
}
_STRATEGIES: frozenset[str] = frozenset(
    {"random", "market_maker", "momentum", "fundamental", "pairs"}
)


def reason_name(name: str) -> str | None:
    return None if name == "NONE" else name.lower()


def strategy_name(participant: Participant) -> StrategyName:
    if isinstance(participant, ManualTrader):
        return "manual"
    if isinstance(participant, MarketEnvironment):
        return "environment"
    if isinstance(participant, TradingAgent) and participant.strategy in _STRATEGIES:
        return participant.strategy  # type: ignore[return-value]
    raise TypeError(f"no API strategy name for {type(participant).__name__}")


_SENTIMENTS: dict[int, Literal["positive", "negative", "neutral"]] = {
    1: "positive",
    -1: "negative",
    0: "neutral",  # News that moved nothing.
}


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def news_out(news: News, symbols: Sequence[str]) -> NewsOut:
    return NewsOut(
        time_seconds=to_seconds(news.time),
        symbol=symbols[news.instrument],
        impact=round(news.impact, 8),
        change_percent=round(math.expm1(news.impact) * 100.0, 6),
        sentiment=_SENTIMENTS[news.sentiment],
        headline=news.headline,
    )


class ParticipantViews:
    """Builds the API's view of one simulation's participants."""

    def __init__(
        self,
        simulation: Simulation,
        formats: Sequence[PriceFormat],
        *,
        sharpe: Callable[[int], float | None] | None = None,
    ) -> None:
        """``sharpe`` gives a participant's Sharpe ratio from the session's analytics (None:
        not measured)."""
        self._simulation = simulation
        self._sharpe = sharpe
        self._formats = tuple(formats)
        self._ticks = tuple(exact_tick(fmt.tick_size) for fmt in self._formats)
        # Cash moves in multiples of a tick's value, so the finest tick's decimal places (plus
        # two, for average-cost results) show every amount exactly enough.
        self._money_places = max(fmt.places for fmt in self._formats) + 2

    def leaderboard(self) -> list[AgentOut]:
        marks = self._simulation.marks()
        return [
            AgentOut.model_validate(self._summary(p, marks))
            for p in self._simulation.participants.values()
        ]

    def detail(self, participant_id: int) -> AgentDetailOut:
        participant = self._simulation.participants.get(participant_id)
        if participant is None:
            raise not_found("unknown_agent", f"no participant with id {participant_id}")
        marks = self._simulation.marks()
        reports = list(_history(participant))[-RECENT_ORDERS:]
        stats = (
            AgentStatsOut(**dataclasses.asdict(participant.stats))
            if isinstance(participant, TradingAgent)
            else None
        )
        return AgentDetailOut.model_validate(
            {
                **self._summary(participant, marks),
                "position_details": self._positions(participant, marks),
                "open_orders": self._open_orders(participant),
                "recent_orders": [self._report(report) for report in reports],
                "stats": stats,
                "metrics": _metrics(participant),
            }
        )

    # ---- Parts ---------------------------------------------------------------------------------
    def _summary(self, participant: Participant, marks: Sequence[Fraction]) -> dict[str, object]:
        simulation = self._simulation
        pid = participant.participant_id
        portfolio = simulation.portfolio(pid)
        symbols = simulation.config.symbols
        details = [portfolio.details(i) for i in range(len(symbols))]
        # Valuation is the portfolio's own (one implementation, in the simulation layer).
        equity = portfolio.equity(marks)
        unrealized = portfolio.unrealized_pnl(marks)
        initial = portfolio.initial_cash
        total = portfolio.total_pnl(marks)
        orders_submitted, fills = _activity(participant, sum(d.trades for d in details))
        return {
            "id": pid,
            "name": participant.name,
            "strategy": strategy_name(participant),
            "symbols": [symbols[i] for i in _instruments(participant, len(symbols))],
            "cash": self._money(portfolio.cash),
            "equity": self._money(equity),
            "total_pnl": self._money(total),
            "realized_pnl": self._money(portfolio.realized_pnl()),
            "unrealized_pnl": self._money(unrealized),
            "return_pct": None if initial == 0 else round(float(total / initial * 100), 6),
            "sharpe_ratio": None if self._sharpe is None else _rounded(self._sharpe(pid)),
            "positions": {
                symbols[i]: details[i].quantity for i in self._held(participant, details)
            },
            "trades": sum(d.trades for d in details),
            "orders_submitted": orders_submitted,
            "fills": fills,
            "filled_quantity": sum(d.bought + d.sold for d in details),
        }

    def _positions(self, participant: Participant, marks: Sequence[Fraction]) -> list[PositionOut]:
        simulation = self._simulation
        portfolio = simulation.portfolio(participant.participant_id)
        symbols = simulation.config.symbols
        details = [portfolio.details(i) for i in range(len(symbols))]
        positions = []
        for i in self._held(participant, details):
            d = details[i]
            fmt = self._formats[i]
            average = d.average_price
            value = self._value(i, d.quantity, marks)
            positions.append(
                PositionOut(
                    symbol=symbols[i],
                    quantity=d.quantity,
                    average_price=None
                    if average is None
                    else round(float(average), fmt.places + 2),
                    realized_pnl=self._money(d.realized),
                    unrealized_pnl=self._money(value - d.cost if d.quantity else Fraction(0)),
                    bought=d.bought,
                    sold=d.sold,
                    trades=d.trades,
                )
            )
        return positions

    def _open_orders(self, participant: Participant) -> list[OpenOrderOut]:
        exchange = self._simulation.exchange
        symbols = self._simulation.config.symbols
        candidates: Iterable[tuple[int, int]]
        if isinstance(participant, TradingAgent):
            candidates = sorted(
                (o.order_id, o.instrument) for o in participant.open_orders.values()
            )
        elif isinstance(participant, ManualTrader):
            candidates = sorted(participant.open_orders.items())
        else:
            candidates = ()
        orders = []
        for order_id, instrument in candidates:
            order = exchange.order(instrument, order_id)
            if order is not None and order.owner == participant.participant_id:
                orders.append(open_order_out(order, symbols[instrument], self._formats[instrument]))
        return orders

    def _report(self, report: OrderReport) -> OrderReportOut:
        intent = report.intent
        symbols = self._simulation.config.symbols
        known = 0 <= intent.instrument < len(symbols)
        limit = intent.order_type is OrderType.LIMIT
        return OrderReportOut(
            time_seconds=to_seconds(report.time),
            order_id=report.order_id or None,
            symbol=symbols[intent.instrument] if known else f"#{intent.instrument}",
            side="buy" if intent.side is Side.BUY else "sell",
            type="limit" if limit else "market",
            price=self._formats[intent.instrument].price(intent.price) if limit and known else None,
            quantity=intent.quantity,
            time_in_force=(
                "ioc" if intent.time_in_force is TimeInForce.IMMEDIATE_OR_CANCEL else "gtc"
            ),
            status=STATUS_NAMES[report.status],
            reject_reason=reason_name(report.reject_reason.name),
            cancel_reason=reason_name(report.cancel_reason.name),
            filled_quantity=report.filled_quantity,
            resting_quantity=report.resting_quantity,
            cancelled_quantity=report.cancelled_quantity,
        )

    def _held(self, participant: Participant, details: Sequence[Position]) -> list[int]:
        """The instruments a participant trades, plus any other it holds a position in."""
        traded = set(_instruments(participant, len(details)))
        return sorted(traded | {i for i, d in enumerate(details) if d.quantity})

    def _value(self, instrument: int, quantity: int, marks: Sequence[Fraction]) -> Fraction:
        """``quantity`` shares at the mark, in currency (exact)."""
        return self._ticks[instrument] * marks[instrument] * quantity

    def _money(self, value: Fraction) -> float:
        return round(float(value), self._money_places)


def _instruments(participant: Participant, count: int) -> tuple[int, ...]:
    if isinstance(participant, TradingAgent):
        return participant.instruments
    return tuple(range(count))


def _activity(participant: Participant, trades: int) -> tuple[int, int]:
    """(orders submitted, fills received) as the participant itself recorded them."""
    if isinstance(participant, TradingAgent):
        return participant.stats.orders_submitted, participant.stats.fills
    if isinstance(participant, ManualTrader):
        return participant.orders_submitted, participant.fills
    if isinstance(participant, MarketEnvironment):
        return participant.liquidity_shocks, trades  # One market order per liquidity shock.
    return 0, trades


def _history(participant: Participant) -> Iterable[OrderReport]:
    if isinstance(participant, TradingAgent | ManualTrader):
        return participant.history
    return ()


def _metrics(participant: Participant) -> dict[str, float]:
    if isinstance(participant, TradingAgent):
        return participant.metrics()
    if isinstance(participant, MarketEnvironment):
        return {
            "news_published": float(participant.news_published),
            "shocks": float(participant.shocks),
            "liquidity_shocks": float(participant.liquidity_shocks),
            "regime_changes": float(participant.regime_changes),
            "activity": participant.activity,
        }
    return {}
