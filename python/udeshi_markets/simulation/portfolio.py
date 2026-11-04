"""Portfolio accounting: cash, positions and profit and loss, kept by the kernel for every
participant from the trades the exchange executes.

Amounts are exact rational numbers (``fractions.Fraction``). A trade moves q * p * tick of cash
from the buyer to the seller and q shares the other way, so across all participants cash and
shares are conserved exactly, with no rounding drift however long the run.

Profit and loss uses the average-cost method:
* Buying into a long position (or selling into a short one) adds to the position's cost basis.
* Reducing a position realizes (price - average cost) * quantity closed, with the sign of the
  position. A trade that crosses zero closes the old position and opens the new one at the
  trade price.
* Unrealized P&L is sum(position * mark - cost basis). Realized + unrealized always equals
  equity - initial cash, where equity = cash + sum(position * mark).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from udeshi_markets._engine import Side


def exact_tick(tick_size: float) -> Fraction:
    """The tick size as the decimal it was written as (0.05, not 0.05000000000000000277...)."""
    return Fraction(Decimal(repr(tick_size)))


@dataclass(slots=True)
class Position:
    quantity: int = 0  # Positive: long. Negative: short.
    cost: Fraction = Fraction(0)  # Cost basis of the open position (same sign as quantity).
    realized: Fraction = Fraction(0)
    bought: int = 0
    sold: int = 0
    buy_notional: Fraction = Fraction(0)
    sell_notional: Fraction = Fraction(0)
    trades: int = 0
    closing_trades: int = 0  # Fills that reduced the position.
    winning_trades: int = 0  # Closing fills that realized a profit (> 0).

    @property
    def average_price(self) -> Fraction | None:
        """Average cost per share of the open position, in currency."""
        return self.cost / self.quantity if self.quantity else None


class Portfolio:
    """One participant's cash and positions. Mutated only by the kernel."""

    __slots__ = ("_cash", "_initial_cash", "_positions", "_ticks")

    def __init__(self, initial_cash: Fraction, tick_sizes: Sequence[Fraction]) -> None:
        self._initial_cash = initial_cash
        self._cash = initial_cash
        self._ticks = tuple(tick_sizes)
        self._positions = [Position() for _ in self._ticks]

    def apply_fill(self, instrument: int, side: Side, price: int, quantity: int) -> Fraction:
        """Books one execution at ``price`` ticks. Returns the P&L it realized."""
        if quantity <= 0:
            raise ValueError("fill quantity must be positive")
        position = self._positions[instrument]
        unit = self._ticks[instrument] * price  # Currency per share.
        signed = quantity if side is Side.BUY else -quantity
        self._cash -= unit * signed
        position.trades += 1
        if signed > 0:
            position.bought += quantity
            position.buy_notional += unit * quantity
        else:
            position.sold += quantity
            position.sell_notional += unit * quantity

        realized = Fraction(0)
        if position.quantity == 0 or (position.quantity > 0) == (signed > 0):
            position.cost += unit * signed  # Opening or adding to the position.
            position.quantity += signed
        else:
            direction = 1 if position.quantity > 0 else -1
            closing = min(abs(signed), abs(position.quantity))
            average = position.cost / position.quantity
            realized = (unit - average) * closing * direction
            position.cost -= average * closing * direction
            position.quantity -= closing * direction
            remaining = abs(signed) - closing
            if remaining:  # Crossed zero: the rest opens a position the other way.
                position.quantity = remaining if signed > 0 else -remaining
                position.cost = unit * position.quantity
            position.realized += realized
            position.closing_trades += 1
            if realized > 0:
                position.winning_trades += 1
        return realized

    def apply_self_trade(self, instrument: int, price: int, quantity: int) -> None:
        """Books a trade between two of this participant's own orders. Nothing changes hands,
        so cash, position, cost basis and realized P&L stay as they are. Only the activity
        counters move: two fills, quantity bought and sold."""
        if quantity <= 0:
            raise ValueError("fill quantity must be positive")
        position = self._positions[instrument]
        notional = self._ticks[instrument] * price * quantity
        position.trades += 2
        position.bought += quantity
        position.sold += quantity
        position.buy_notional += notional
        position.sell_notional += notional

    # ---- Read access ------------------------------------------------------------------------
    @property
    def initial_cash(self) -> Fraction:
        return self._initial_cash

    @property
    def cash(self) -> Fraction:
        return self._cash

    def position(self, instrument: int) -> int:
        return self._positions[instrument].quantity

    def details(self, instrument: int) -> Position:
        """A copy of the instrument's position record."""
        p = self._positions[instrument]
        return Position(
            p.quantity,
            p.cost,
            p.realized,
            p.bought,
            p.sold,
            p.buy_notional,
            p.sell_notional,
            p.trades,
            p.closing_trades,
            p.winning_trades,
        )

    @property
    def instrument_count(self) -> int:
        return len(self._positions)

    def realized_pnl(self) -> Fraction:
        return sum((p.realized for p in self._positions), Fraction(0))

    def unrealized_pnl(self, marks: Sequence[int | Fraction]) -> Fraction:
        """Open positions valued at ``marks`` (ticks, one per instrument; a midprice can be a
        half tick, so marks may be fractions) less their cost."""
        return sum(
            (
                self._ticks[i] * marks[i] * p.quantity - p.cost
                for i, p in enumerate(self._positions)
                if p.quantity
            ),
            Fraction(0),
        )

    def market_value(self, marks: Sequence[int | Fraction]) -> Fraction:
        return sum(
            (self._ticks[i] * marks[i] * p.quantity for i, p in enumerate(self._positions)),
            Fraction(0),
        )

    def equity(self, marks: Sequence[int | Fraction]) -> Fraction:
        return self._cash + self.market_value(marks)

    def total_pnl(self, marks: Sequence[int | Fraction]) -> Fraction:
        return self.equity(marks) - self._initial_cash

    def gross_exposure(self, marks: Sequence[int | Fraction]) -> Fraction:
        """Sum of |position| * mark: the capital at risk in open positions."""
        return sum(
            (self._ticks[i] * marks[i] * abs(p.quantity) for i, p in enumerate(self._positions)),
            Fraction(0),
        )

    def trade_count(self) -> int:
        return sum(p.trades for p in self._positions)


class PortfolioView:
    """Read-only access to a participant's own portfolio (what its context hands out)."""

    __slots__ = ("_portfolio",)

    def __init__(self, portfolio: Portfolio) -> None:
        self._portfolio = portfolio

    @property
    def cash(self) -> Fraction:
        return self._portfolio.cash

    @property
    def initial_cash(self) -> Fraction:
        return self._portfolio.initial_cash

    def position(self, instrument: int) -> int:
        return self._portfolio.position(instrument)

    def details(self, instrument: int) -> Position:
        return self._portfolio.details(instrument)

    def realized_pnl(self) -> Fraction:
        return self._portfolio.realized_pnl()

    def unrealized_pnl(self, marks: Sequence[int | Fraction]) -> Fraction:
        return self._portfolio.unrealized_pnl(marks)

    def equity(self, marks: Sequence[int | Fraction]) -> Fraction:
        return self._portfolio.equity(marks)

    def total_pnl(self, marks: Sequence[int | Fraction]) -> Fraction:
        return self._portfolio.total_pnl(marks)
