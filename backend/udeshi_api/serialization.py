"""Conversion of engine and simulation objects into API models.

All values are read from the engine and the recorder; nothing is recomputed here except unit
conversion (ticks to currency) and rounding for display.
"""

from collections.abc import Sequence
from decimal import Decimal

from udeshi_api.schemas import (
    BarOut,
    LevelOut,
    MarketDetailOut,
    MarketOut,
    OpenOrderOut,
    OrderBookOut,
    TradeOut,
)
from udeshi_markets import Exchange, LevelView, MarketStatistics, Order, Side
from udeshi_markets.simulation import Bar, PublicTrade, to_seconds


class PriceFormat:
    """Ticks to currency for one instrument, rounded so that binary floating point never shows
    artefacts such as 0.15000000000000002. Integer-tick prices get the tick's decimal places;
    fractional-tick values (midprice, VWAP) get two more."""

    __slots__ = ("places", "tick_size")

    def __init__(self, tick_size: float) -> None:
        exponent = Decimal(repr(tick_size)).normalize().as_tuple().exponent
        assert isinstance(exponent, int)  # Finite tick sizes only (validated upstream).
        self.tick_size = tick_size
        self.places = max(0, -exponent)

    def price(self, ticks: int) -> float:
        return round(ticks * self.tick_size, self.places)

    def maybe_price(self, ticks: int | None) -> float | None:
        return None if ticks is None else self.price(ticks)

    def fractional(self, ticks: float | None) -> float | None:
        return None if ticks is None else round(ticks * self.tick_size, self.places + 2)


def side_name(side: Side) -> str:
    return "buy" if side is Side.BUY else "sell"


def trade_out(trade: PublicTrade, fmt: PriceFormat) -> TradeOut:
    return TradeOut(
        trade_id=trade.trade_id,
        time_seconds=to_seconds(trade.time),
        price=fmt.price(trade.price),
        price_ticks=trade.price,
        quantity=trade.quantity,
        aggressor_side="buy" if trade.aggressor_side is Side.BUY else "sell",
    )


def bar_out(bar: Bar, fmt: PriceFormat) -> BarOut:
    return BarOut(
        start_seconds=to_seconds(bar.start),
        end_seconds=to_seconds(bar.end),
        open=fmt.price(bar.open),
        high=fmt.price(bar.high),
        low=fmt.price(bar.low),
        close=fmt.price(bar.close),
        volume=bar.volume,
        trade_count=bar.trade_count,
        vwap=fmt.fractional(bar.vwap),
    )


def _market_fields(
    symbol: str, stats: MarketStatistics, fmt: PriceFormat
) -> dict[str, float | int | str | None]:
    reference = fmt.price(stats.reference_price)
    last = fmt.maybe_price(stats.last_price)
    change = None if last is None else round(last - reference, fmt.places)
    change_percent = (
        None
        if stats.last_price is None
        else round((stats.last_price / stats.reference_price - 1.0) * 100.0, 4)
    )
    return {
        "symbol": symbol,
        "tick_size": fmt.tick_size,
        "reference_price": reference,
        "last_price": last,
        "change": change,
        "change_percent": change_percent,
        "open": fmt.maybe_price(stats.open_price),
        "high": fmt.maybe_price(stats.high_price),
        "low": fmt.maybe_price(stats.low_price),
        "volume": stats.volume,
        "trade_count": stats.trade_count,
        "vwap": fmt.fractional(stats.vwap),
        "best_bid": fmt.maybe_price(stats.best_bid),
        "best_ask": fmt.maybe_price(stats.best_ask),
        "midprice": fmt.fractional(stats.midprice),
        "spread": fmt.maybe_price(stats.spread),
    }


def market_out(exchange: Exchange, instrument: int, fmt: PriceFormat) -> MarketOut:
    symbol = exchange.instrument(instrument).symbol
    return MarketOut.model_validate(_market_fields(symbol, exchange.statistics(instrument), fmt))


def market_detail_out(exchange: Exchange, instrument: int, fmt: PriceFormat) -> MarketDetailOut:
    stats = exchange.statistics(instrument)
    fields = _market_fields(exchange.instrument(instrument).symbol, stats, fmt)
    fields.update(
        bid_depth=stats.bid_depth,
        ask_depth=stats.ask_depth,
        resting_orders=stats.resting_orders,
        notional=round(stats.notional * fmt.tick_size, fmt.places),
    )
    return MarketDetailOut.model_validate(fields)


def book_out(
    exchange: Exchange, instrument: int, depth: int, fmt: PriceFormat, now: int
) -> OrderBookOut:
    snapshot = exchange.book_snapshot(instrument, depth)

    def levels(side: Sequence[LevelView]) -> list[LevelOut]:
        return [
            LevelOut(
                price=fmt.price(level.price),
                price_ticks=level.price,
                quantity=level.quantity,
                orders=level.order_count,
            )
            for level in side
        ]

    return OrderBookOut(
        symbol=exchange.instrument(instrument).symbol,
        time_seconds=to_seconds(now),
        bids=levels(snapshot.bids),
        asks=levels(snapshot.asks),
        best_bid=fmt.maybe_price(exchange.best_bid(instrument)),
        best_ask=fmt.maybe_price(exchange.best_ask(instrument)),
        midprice=fmt.fractional(exchange.midprice(instrument)),
        spread=fmt.maybe_price(exchange.spread(instrument)),
    )


def open_order_out(order: Order, symbol: str, fmt: PriceFormat) -> OpenOrderOut:
    return OpenOrderOut(
        order_id=order.id,
        symbol=symbol,
        side="buy" if order.side is Side.BUY else "sell",
        price=fmt.price(order.price),
        price_ticks=order.price,
        original_quantity=order.original_quantity,
        remaining_quantity=order.remaining_quantity,
        time_seconds=to_seconds(order.timestamp),
    )


def recent_bars(bars: Sequence[Bar], base_width: int, factor: int, limit: int) -> list[Bar]:
    """The ``limit`` most recent bars ``factor`` times wider than the contiguous base bars.
    Costs O(limit * factor), however long the history: only the needed tail is aggregated."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    first = max(0, len(bars) - (limit + 1) * factor)
    merged = aggregate_bars(bars[first:], base_width, factor)
    if first > 0 and bars[first].start % (base_width * factor) != 0:
        merged = merged[1:]  # Its earlier base bars were cut off by the slice.
    return merged[-limit:]


def aggregate_bars(bars: Sequence[Bar], base_width: int, factor: int) -> list[Bar]:
    """Merges consecutive base bars into bars ``factor`` times wider, aligned to multiples of
    the wider interval. The last group may be partial (the bar still forming)."""
    if factor < 1:
        raise ValueError("aggregation factor must be at least 1")
    if factor == 1 or not bars:
        return list(bars)
    width = base_width * factor
    merged: list[Bar] = []
    group: list[Bar] = []
    for bar in bars:
        if group and bar.start // width != group[0].start // width:
            merged.append(_merge(group, width))
            group = []
        group.append(bar)
    if group:
        merged.append(_merge(group, width))
    return merged


def _merge(group: Sequence[Bar], width: int) -> Bar:
    start = group[0].start // width * width
    return Bar(
        instrument=group[0].instrument,
        start=start,
        end=group[-1].end,
        open=group[0].open,
        high=max(bar.high for bar in group),
        low=min(bar.low for bar in group),
        close=group[-1].close,
        volume=sum(bar.volume for bar in group),
        trade_count=sum(bar.trade_count for bar in group),
        notional=sum(bar.notional for bar in group),
    )
