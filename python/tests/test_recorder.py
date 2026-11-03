"""Tests for the trade tape and OHLCV bar recorder."""

import pytest

from udeshi_markets import Exchange, OrderRequest, Side, Trade
from udeshi_markets.simulation import MarketRecorder


def _trades(*fills: tuple[int, int, int]) -> list[Trade]:
    """Real engine trades for (timestamp, price, quantity) triples on instrument 0."""
    exchange = Exchange()
    exchange.add_instrument(symbol="BAR", tick_size=1.0, reference_price=100)
    trades: list[Trade] = []
    for timestamp, price, quantity in fills:
        exchange.submit(
            OrderRequest(instrument=0, side=Side.SELL, price=price, quantity=quantity, owner=1)
        )
        result = exchange.submit(
            OrderRequest(
                instrument=0,
                side=Side.BUY,
                price=price,
                quantity=quantity,
                owner=2,
                timestamp=timestamp,
            )
        )
        trades.extend(result.trades)
    return trades


def test_bars_aggregate_trades_by_timestamp() -> None:
    recorder = MarketRecorder([100], bar_width_ns=10)
    for trade in _trades((1, 101, 2), (5, 99, 3), (9, 104, 1), (10, 102, 4), (31, 98, 1)):
        recorder.record(trade)
    # Recording the trade at 31 already closed [0,10), [10,20) and [20,30); advance closes the rest.
    assert [(b.start, b.end) for b in recorder.advance_to(40)] == [(30, 40)]
    bars = recorder.bars(0)
    assert [(b.start, b.end) for b in bars] == [(0, 10), (10, 20), (20, 30), (30, 40)]
    first, second, empty, last = bars
    assert (first.open, first.high, first.low, first.close) == (101, 104, 99, 104)
    assert (first.volume, first.trade_count, first.notional) == (6, 3, 202 + 297 + 104)
    assert first.vwap == pytest.approx((202 + 297 + 104) / 6)
    assert (second.open, second.close, second.volume) == (102, 102, 4)
    # A bar without trades carries the previous close and has no volume.
    assert (empty.open, empty.high, empty.low, empty.close, empty.volume) == (102, 102, 102, 102, 0)
    assert empty.vwap is None
    assert (last.open, last.close, last.volume) == (98, 98, 1)


def test_every_closed_bar_is_reported_once_whatever_closed_it() -> None:
    recorder = MarketRecorder([100], bar_width_ns=10, report_closed_bars=True)
    for trade in _trades((3, 101, 1), (25, 103, 1)):  # The second trade closes two bars.
        recorder.record(trade)
    assert [(b.start, b.close) for b in recorder.drain_closed_bars()] == [(0, 101), (10, 101)]
    recorder.advance_to(30)
    recorder.finalize(35)
    assert [(b.start, b.end) for b in recorder.drain_closed_bars()] == [(20, 30), (30, 35)]
    assert recorder.drain_closed_bars() == []
    with pytest.raises(RuntimeError):
        MarketRecorder([100], bar_width_ns=10).drain_closed_bars()


def test_bars_before_any_trade_use_the_reference_price() -> None:
    recorder = MarketRecorder([250, 75], bar_width_ns=5)
    closed = recorder.advance_to(10)
    assert [(b.instrument, b.close, b.volume) for b in closed] == [
        (0, 250, 0),
        (0, 250, 0),
        (1, 75, 0),
        (1, 75, 0),
    ]


def test_advance_never_closes_the_bar_containing_now() -> None:
    recorder = MarketRecorder([100], bar_width_ns=10)
    assert recorder.advance_to(9) == []
    trade = _trades((10, 105, 1))[0]
    assert len(recorder.advance_to(10)) == 1  # [0, 10) closes at time 10...
    recorder.record(trade)  # ...and a trade at exactly 10 belongs to [10, 20).
    assert recorder.advance_to(19) == []
    (bar,) = recorder.advance_to(20)
    assert (bar.start, bar.close, bar.volume) == (10, 105, 1)


def test_finalize_closes_a_partial_final_bar() -> None:
    recorder = MarketRecorder([100], bar_width_ns=10)
    recorder.record(_trades((12, 101, 1))[0])  # Closes [0, 10) as a side effect.
    assert [(b.start, b.end, b.close) for b in recorder.finalize(15)] == [(10, 15, 101)]
    assert [(b.start, b.end, b.close) for b in recorder.bars(0)] == [(0, 10, 100), (10, 15, 101)]
    assert recorder.finalized


def test_nothing_can_be_recorded_after_finalize() -> None:
    recorder = MarketRecorder([100], bar_width_ns=10)
    recorder.finalize(10)
    trade = _trades((12, 101, 1))[0]
    for call in (
        lambda: recorder.record(trade),
        lambda: recorder.advance_to(20),
        lambda: recorder.finalize(20),
    ):
        with pytest.raises(RuntimeError, match="finalized"):
            call()


def test_a_trade_at_the_final_instant_is_in_a_bar() -> None:
    """A run stopped at t ends at t + 1, so a trade at exactly t, even on a bar boundary, lands
    in a final bar covering that instant. Without a trade there, no such bar is made."""
    recorder = MarketRecorder([100], bar_width_ns=10)
    recorder.record(_trades((20, 103, 4))[0])
    assert [(b.start, b.end, b.volume) for b in recorder.finalize(21)] == [(20, 21, 4)]
    quiet = MarketRecorder([100], bar_width_ns=10)
    assert [(b.start, b.end) for b in quiet.finalize(21)] == [(0, 10), (10, 20)]
    assert sum(b.volume for b in recorder.bars(0)) == 4


def test_out_of_order_trades_are_rejected() -> None:
    recorder = MarketRecorder([100], bar_width_ns=10)
    late, early = _trades((25, 100, 1), (3, 100, 1))
    recorder.record(late)
    with pytest.raises(ValueError, match="non-decreasing"):
        recorder.record(early)


def test_tape_is_public_and_optionally_bounded() -> None:
    recorder = MarketRecorder([100], bar_width_ns=10, trade_history_limit=2)
    for trade in _trades((1, 100, 1), (2, 101, 1), (3, 102, 1)):
        public = recorder.record(trade)
        assert not hasattr(public, "buyer")
        assert not hasattr(public, "buy_order_id")
        # The engine's global sequence would reveal other participants' unseen actions (rejected
        # or cancelled orders) through the gaps between consecutive trades.
        assert not hasattr(public, "sequence")
    assert [t.price for t in recorder.trades(0)] == [101, 102]
    assert [t.price for t in recorder.trades(0, limit=1)] == [102]
    assert recorder.trades_recorded(0) == 3


def test_recent_trades_slice_from_the_end() -> None:
    recorder = MarketRecorder([100], bar_width_ns=10)
    for trade in _trades(*((t, 100 + t, 1) for t in range(1, 8))):
        recorder.record(trade)
    assert [t.price for t in recorder.trades(0, limit=3)] == [105, 106, 107]
    assert [t.price for t in recorder.trades(0, limit=0)] == []
    assert len(recorder.trades(0, limit=100)) == 7
    with pytest.raises(ValueError, match="limit must be non-negative"):
        recorder.trades(0, limit=-1)


def test_invalid_bar_width() -> None:
    with pytest.raises(ValueError, match="bar width must be positive"):
        MarketRecorder([100], bar_width_ns=0)
