"""Portfolio accounting: average-cost P&L, exactness and conservation."""

from __future__ import annotations

from fractions import Fraction

from hypothesis import given, settings
from hypothesis import strategies as st

from udeshi_markets import Side
from udeshi_markets.simulation import Portfolio
from udeshi_markets.simulation.portfolio import exact_tick

ONE = Fraction(1)  # A one-currency-unit tick keeps the worked examples readable.


def portfolio(cash: int = 10_000, ticks: tuple[Fraction, ...] = (ONE, ONE)) -> Portfolio:
    return Portfolio(Fraction(cash), ticks)


def test_reducing_a_long_position_realizes_against_the_average_cost() -> None:
    p = portfolio()
    p.apply_fill(0, Side.BUY, 100, 10)
    p.apply_fill(0, Side.BUY, 110, 10)  # Average cost 105.
    assert p.details(0).average_price == 105
    assert p.apply_fill(0, Side.SELL, 120, 5) == 75  # (120 - 105) * 5.
    assert p.position(0) == 15
    assert p.details(0).average_price == 105
    assert p.cash == 10_000 - 1000 - 1100 + 600
    assert p.unrealized_pnl([115, 0]) == (115 - 105) * 15
    assert p.total_pnl([115, 0]) == 75 + 150


def test_covering_a_short_and_crossing_zero() -> None:
    p = portfolio()
    p.apply_fill(0, Side.SELL, 50, 5)  # Short 5 at 50.
    assert p.apply_fill(0, Side.BUY, 40, 8) == 50  # Covers 5 at a profit of 10 each...
    assert p.position(0) == 3  # ...and the remaining 3 open a long at 40.
    assert p.details(0).average_price == 40
    assert p.realized_pnl() == 50
    assert p.total_pnl([40, 0]) == 50


def test_amounts_are_exact_in_the_instruments_tick() -> None:
    assert exact_tick(0.05) == Fraction(1, 20)
    assert exact_tick(0.01) == Fraction(1, 100)
    p = Portfolio(Fraction(0), (exact_tick(0.01),))
    for _ in range(1000):
        p.apply_fill(0, Side.BUY, 3, 1)  # 0.03 each: binary floats would drift.
    assert p.cash == Fraction(-30)
    assert p.details(0).buy_notional == 30


def test_reads_do_not_hand_out_mutable_state() -> None:
    p = portfolio()
    p.apply_fill(0, Side.BUY, 100, 1)
    copy = p.details(0)
    copy.quantity = 999
    assert p.position(0) == 1


fills = st.lists(
    st.tuples(
        st.integers(0, 1),  # Instrument.
        st.sampled_from([Side.BUY, Side.SELL]),
        st.integers(1, 500),  # Price in ticks.
        st.integers(1, 50),  # Quantity.
    ),
    max_size=60,
)


@settings(max_examples=200, deadline=None)
@given(trades=fills, marks=st.tuples(st.integers(1, 500), st.integers(1, 500)))
def test_two_counterparties_conserve_cash_and_shares(
    trades: list[tuple[int, Side, int, int]], marks: tuple[int, int]
) -> None:
    """Every trade books the buyer and the seller. Nothing is created or destroyed, and each
    side's realized + unrealized P&L equals its change in equity."""
    ticks = (exact_tick(0.01), exact_tick(0.05))
    a, b = Portfolio(Fraction(1000), ticks), Portfolio(Fraction(1000), ticks)
    for instrument, side, price, quantity in trades:
        other = Side.SELL if side is Side.BUY else Side.BUY
        a.apply_fill(instrument, side, price, quantity)
        b.apply_fill(instrument, other, price, quantity)
    assert a.cash + b.cash == 2000
    for instrument in (0, 1):
        assert a.position(instrument) + b.position(instrument) == 0
    for book in (a, b):
        assert book.realized_pnl() + book.unrealized_pnl(marks) == book.total_pnl(marks)
    assert a.total_pnl(marks) + b.total_pnl(marks) == 0  # A zero-sum game.
