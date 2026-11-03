"""Bar aggregation and formatting, with data that tells every merged field apart."""

from __future__ import annotations

from udeshi_api.serialization import PriceFormat, aggregate_bars, bar_out
from udeshi_markets.simulation import Bar


def test_merged_bars_take_extremes_and_totals_from_every_member() -> None:
    # The extreme high and low sit in the middle bar, so first/last shortcuts are caught.
    bars = [
        Bar(0, 0, 10, open=5, high=6, low=4, close=5, volume=1, trade_count=1, notional=5),
        Bar(0, 10, 20, open=5, high=9, low=2, close=6, volume=3, trade_count=2, notional=17),
        Bar(0, 20, 30, open=6, high=7, low=5, close=7, volume=2, trade_count=2, notional=13),
    ]
    [merged] = aggregate_bars(bars, 10, 3)
    assert merged == Bar(0, 0, 30, open=5, high=9, low=2, close=7, volume=6, trade_count=5,
                         notional=35)  # fmt: skip
    assert merged.vwap == 35 / 6
    assert bar_out(merged, PriceFormat(0.5)).model_dump() == {
        "start_seconds": 0.0,
        "end_seconds": 3e-8,
        "open": 2.5,
        "high": 4.5,
        "low": 1.0,
        "close": 3.5,
        "volume": 6,
        "trade_count": 5,
        "vwap": 2.917,  # 35 / 6 ticks x 0.5, to one place plus two.
    }
