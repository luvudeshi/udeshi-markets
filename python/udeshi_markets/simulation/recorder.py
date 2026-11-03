"""Market-data recording: the public trade tape and OHLCV bars per instrument.

Bars are half-open intervals [k * width, (k + 1) * width) of simulated time. A trade belongs to
the bar its timestamp falls in, independently of the order in which same-time events happen to
be processed, so bars are a pure function of the trade stream.
"""

import itertools
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

from udeshi_markets._engine import Side, Trade


@dataclass(frozen=True, slots=True)
class PublicTrade:
    """A trade as published on the tape: no order ids or trader identities."""

    trade_id: int
    sequence: int
    time: int
    instrument: int
    price: int
    quantity: int
    aggressor_side: Side


@dataclass(frozen=True, slots=True)
class Bar:
    """OHLCV over [start, end). An interval without trades has open = high = low = close = the
    previous close and zero volume."""

    instrument: int
    start: int
    end: int
    open: int
    high: int
    low: int
    close: int
    volume: int
    trade_count: int
    notional: int  # Sum of price * quantity in ticks (exact: Python integers do not overflow).

    @property
    def vwap(self) -> float | None:
        return self.notional / self.volume if self.volume else None


class _InstrumentHistory:
    __slots__ = (
        "bar_close",
        "bar_high",
        "bar_index",
        "bar_low",
        "bar_notional",
        "bar_open",
        "bar_trades",
        "bar_volume",
        "bars",
        "instrument",
        "trades",
        "trades_recorded",
    )

    def __init__(self, instrument: int, reference_price: int, trade_limit: int | None) -> None:
        self.instrument = instrument
        self.trades: deque[PublicTrade] = deque(maxlen=trade_limit)
        self.trades_recorded = 0
        self.bars: list[Bar] = []
        self.bar_index = 0
        self.bar_open = self.bar_high = self.bar_low = self.bar_close = reference_price
        self.bar_volume = self.bar_trades = self.bar_notional = 0


class MarketRecorder:
    """Records every instrument's public tape and bars as trades happen."""

    def __init__(
        self,
        reference_prices: Sequence[int],
        *,
        bar_width_ns: int,
        trade_history_limit: int | None = None,
        report_closed_bars: bool = False,
    ) -> None:
        """With ``report_closed_bars``, every bar closed (whether by a trade landing in a later
        bar, by ``advance_to`` or by ``finalize``) is also queued for ``drain_closed_bars``."""
        if bar_width_ns <= 0:
            raise ValueError("bar width must be positive")
        self._bar_width = bar_width_ns
        self._report_closed = report_closed_bars
        self._unreported: list[Bar] = []
        self._histories = [
            _InstrumentHistory(instrument, price, trade_history_limit)
            for instrument, price in enumerate(reference_prices)
        ]

    @property
    def bar_width_ns(self) -> int:
        return self._bar_width

    def record(self, trade: Trade) -> PublicTrade:
        history = self._histories[trade.instrument]
        index = trade.timestamp // self._bar_width
        if index < history.bar_index:
            raise ValueError("trades must be recorded in non-decreasing time order")
        self._close_until(history, index)
        if history.bar_trades == 0:
            history.bar_open = history.bar_high = history.bar_low = trade.price
        else:
            history.bar_high = max(history.bar_high, trade.price)
            history.bar_low = min(history.bar_low, trade.price)
        history.bar_close = trade.price
        history.bar_volume += trade.quantity
        history.bar_trades += 1
        history.bar_notional += trade.price * trade.quantity

        public = PublicTrade(
            trade_id=trade.id,
            sequence=trade.sequence,
            time=trade.timestamp,
            instrument=trade.instrument,
            price=trade.price,
            quantity=trade.quantity,
            aggressor_side=trade.aggressor_side,
        )
        history.trades.append(public)
        history.trades_recorded += 1
        return public

    def advance_to(self, time: int) -> list[Bar]:
        """Closes every still-open bar that ends at or before ``time`` and returns the bars this
        call closed. (Bars can also close inside ``record``; ``bars`` has the full history.)

        Safe to call at the current simulated time: any later trade has a timestamp >= time and
        so belongs to a bar that is still open.
        """
        closed: list[Bar] = []
        index = time // self._bar_width
        for history in self._histories:
            closed.extend(self._close_until(history, index))
        return closed

    def finalize(self, end_time: int) -> list[Bar]:
        """Closes the final (possibly partial) bar of every instrument at ``end_time``."""
        closed = self.advance_to(end_time)
        for history in self._histories:
            start = history.bar_index * self._bar_width
            if start < end_time:
                bar = self._make_bar(history, start, end_time)
                self._store(history, bar)
                closed.append(bar)
                history.bar_index += 1
                self._reset_bar(history)
        return closed

    def drain_closed_bars(self) -> list[Bar]:
        """Bars closed since the previous call, in closing order (requires report_closed_bars)."""
        if not self._report_closed:
            raise RuntimeError("this recorder was created without report_closed_bars")
        closed, self._unreported = self._unreported, []
        return closed

    def trades(self, instrument: int, limit: int | None = None) -> list[PublicTrade]:
        """Up to ``limit`` most recent trades, oldest first. Costs O(limit), not O(history)."""
        trades = self._histories[instrument].trades
        if limit is None or limit >= len(trades):
            return list(trades)
        if limit < 0:
            raise ValueError("limit must be non-negative")
        recent = list(itertools.islice(reversed(trades), limit))
        recent.reverse()
        return recent

    def trades_recorded(self, instrument: int) -> int:
        """Total trades ever recorded for the instrument (including any no longer retained)."""
        return self._histories[instrument].trades_recorded

    def bars(self, instrument: int) -> Sequence[Bar]:
        return self._histories[instrument].bars

    def _close_until(self, history: _InstrumentHistory, index: int) -> list[Bar]:
        closed: list[Bar] = []
        while history.bar_index < index:
            start = history.bar_index * self._bar_width
            bar = self._make_bar(history, start, start + self._bar_width)
            self._store(history, bar)
            closed.append(bar)
            history.bar_index += 1
            self._reset_bar(history)
        return closed

    def _store(self, history: _InstrumentHistory, bar: Bar) -> None:
        history.bars.append(bar)
        if self._report_closed:
            self._unreported.append(bar)

    @staticmethod
    def _make_bar(history: _InstrumentHistory, start: int, end: int) -> Bar:
        return Bar(
            instrument=history.instrument,
            start=start,
            end=end,
            open=history.bar_open,
            high=history.bar_high,
            low=history.bar_low,
            close=history.bar_close,
            volume=history.bar_volume,
            trade_count=history.bar_trades,
            notional=history.bar_notional,
        )

    @staticmethod
    def _reset_bar(history: _InstrumentHistory) -> None:
        close = history.bar_close
        history.bar_open = history.bar_high = history.bar_low = close
        history.bar_volume = history.bar_trades = history.bar_notional = 0
