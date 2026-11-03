"""Integration tests for the pybind11 bindings (udeshi_markets._engine)."""

from __future__ import annotations

import enum
import importlib.metadata
import math
import operator
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import udeshi_markets as um
from udeshi_markets import (
    CancelOutcome,
    Exchange,
    OrderRequest,
    OrderStatus,
    OrderType,
    RejectReason,
    SelfTradePrevention,
    Side,
    TimeInForce,
    _engine,
)


@pytest.fixture
def exchange() -> Exchange:
    ex = Exchange()
    ex.add_instrument(symbol="ACME", tick_size=0.01, reference_price=10_000)
    ex.add_instrument(symbol="NOVA", tick_size=0.05, reference_price=2_000)
    return ex


def limit(
    side: Side, price: int, quantity: int, owner: int = 0, instrument: int = 0
) -> OrderRequest:
    return OrderRequest(
        instrument=instrument, side=side, price=price, quantity=quantity, owner=owner
    )


# ---- Types and conversions -------------------------------------------------------------------


def test_enums_are_native_python_enums() -> None:
    for enum_type in (
        Side,
        OrderType,
        TimeInForce,
        SelfTradePrevention,
        OrderStatus,
        RejectReason,
        um.CancelReason,
        CancelOutcome,
    ):
        assert issubclass(enum_type, enum.Enum)
    assert [member.name for member in Side] == ["BUY", "SELL"]
    assert len(RejectReason) == 9


def test_enum_fields_reject_integers_and_invalid_values() -> None:
    with pytest.raises(ValueError, match="not a valid Side"):
        Side(5)
    bad_side: Any = 1
    with pytest.raises(TypeError):
        OrderRequest(instrument=0, side=bad_side, quantity=1)
    request = limit(Side.BUY, 100, 1)
    with pytest.raises(TypeError):
        request.side = bad_side


def test_order_request_is_keyword_only_with_engine_defaults() -> None:
    request = OrderRequest(instrument=0, side=Side.SELL, quantity=7)
    assert request.type is OrderType.LIMIT
    assert request.time_in_force is TimeInForce.GOOD_TILL_CANCEL
    assert request.self_trade_prevention is SelfTradePrevention.ALLOW
    assert request.price == 0
    assert request.owner == um.ANONYMOUS_TRADER
    positional: Any = OrderRequest
    with pytest.raises(TypeError):
        positional(0, Side.BUY, 1)


def test_constants() -> None:
    assert um.MAX_PRICE == 1_000_000_000
    assert um.MAX_ORDER_QUANTITY == 1_000_000_000
    assert um.MIN_TICK_SIZE == 1e-12
    assert um.MAX_TICK_SIZE == 1e12
    assert um.INVALID_ORDER_ID == 0


def test_module_version_is_the_distribution_version() -> None:
    assert um.__version__ == importlib.metadata.version("udeshi-markets")


def test_the_extension_exports_only_its_init_function() -> None:
    """The statically linked engine must not leak udeshi:: symbols from the shared object,
    where another library's copy of the same symbols could interpose on them."""
    nm = shutil.which("nm")
    if nm is None:
        pytest.skip("nm is not available")
    flags = ["-gU"] if sys.platform == "darwin" else ["-D", "--defined-only"]
    listing = subprocess.run(
        [nm, *flags, _engine.__file__], check=True, capture_output=True, text=True
    ).stdout
    names = [line.split()[-1] for line in listing.splitlines() if line.strip()]
    if sys.platform == "darwin":
        names = [name.removeprefix("_") for name in names]
    assert "PyInit__engine" in names
    assert [name for name in names if "udeshi" in name] == []


def test_price_to_ticks_uses_the_exchange_rules() -> None:
    assert um.price_to_ticks(100.01, 0.01) == 10_001
    assert um.price_to_ticks(30000.05, 0.05) == 600_001


@pytest.mark.parametrize(
    ("price", "tick", "message"),
    [
        (100.005, 0.01, "not a multiple of the tick size"),
        (-0.01, 0.01, "non-negative"),
        (math.nan, 0.01, "finite"),
        (1.0, 0.0, "tick size"),
    ],
)
def test_price_to_ticks_rejects_invalid_input(price: float, tick: float, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        um.price_to_ticks(price, tick)


# ---- Matching through the bindings -----------------------------------------------------------


def test_spec_example(exchange: Exchange) -> None:
    exchange.submit(limit(Side.SELL, 101, 10, owner=1))
    exchange.submit(limit(Side.SELL, 102, 5, owner=2))
    result = exchange.submit(limit(Side.BUY, 102, 12, owner=3))
    assert result.status is OrderStatus.FILLED
    assert result.accepted
    assert [(t.quantity, t.price) for t in result.trades] == [(10, 101), (2, 102)]
    assert all(t.aggressor_side is Side.BUY and t.buyer == 3 for t in result.trades)
    snapshot = exchange.book_snapshot(0)
    assert [(level.price, level.quantity, level.order_count) for level in snapshot.asks] == [
        (102, 3, 1)
    ]
    assert snapshot.bids == []


def test_market_and_ioc_remainders_are_cancelled(exchange: Exchange) -> None:
    exchange.submit(limit(Side.BUY, 99, 4))
    market = exchange.submit(
        OrderRequest(instrument=0, side=Side.SELL, type=OrderType.MARKET, quantity=10)
    )
    assert market.status is OrderStatus.CANCELLED
    assert market.cancel_reason is um.CancelReason.NO_LIQUIDITY
    assert (market.filled_quantity, market.cancelled_quantity) == (4, 6)
    ioc = exchange.submit(
        OrderRequest(
            instrument=0,
            side=Side.BUY,
            price=200,
            quantity=3,
            time_in_force=TimeInForce.IMMEDIATE_OR_CANCEL,
        )
    )
    assert ioc.status is OrderStatus.CANCELLED
    assert exchange.best_bid(0) is None


def test_rejections_are_results_not_exceptions(exchange: Exchange) -> None:
    assert exchange.submit(limit(Side.BUY, 0, 1)).reject_reason is RejectReason.INVALID_PRICE
    assert exchange.submit(limit(Side.BUY, 1, 0)).reject_reason is RejectReason.INVALID_QUANTITY
    unknown = exchange.submit(limit(Side.BUY, 1, 1, instrument=9))
    assert unknown.reject_reason is RejectReason.UNKNOWN_INSTRUMENT
    assert unknown.status is OrderStatus.REJECTED
    assert not unknown.accepted
    assert unknown.order_id == um.INVALID_ORDER_ID


def test_cancel_outcomes(exchange: Exchange) -> None:
    order = exchange.submit(limit(Side.BUY, 100, 5, owner=4))
    result = exchange.cancel(0, order.order_id)
    assert result.cancelled
    assert (result.owner, result.side, result.price, result.cancelled_quantity) == (
        4,
        Side.BUY,
        100,
        5,
    )
    assert exchange.cancel(0, order.order_id).outcome is CancelOutcome.ORDER_NOT_FOUND
    assert exchange.cancel(7, 1).outcome is CancelOutcome.UNKNOWN_INSTRUMENT


def test_order_snapshots_are_detached_copies(exchange: Exchange) -> None:
    first = exchange.submit(limit(Side.SELL, 100, 5, owner=1))
    second = exchange.submit(limit(Side.SELL, 100, 7, owner=2))
    snapshot = exchange.order(0, first.order_id)
    assert snapshot is not None
    assert (snapshot.remaining_quantity, snapshot.owner, snapshot.side) == (5, 1, Side.SELL)
    exchange.submit(limit(Side.BUY, 100, 3))  # Partially fills `first`.
    assert snapshot.remaining_quantity == 5  # The copy does not change...
    live = exchange.order(0, first.order_id)
    assert live is not None
    assert live.remaining_quantity == 2  # ...but a fresh snapshot does.
    assert live.filled_quantity == 3
    queue = exchange.orders_at(0, Side.SELL, 100)
    assert [order.id for order in queue] == [first.order_id, second.order_id]
    exchange.cancel(0, first.order_id)
    assert exchange.order(0, first.order_id) is None


def test_market_statistics_use_none_for_undefined_values(exchange: Exchange) -> None:
    stats = exchange.statistics(0)
    assert stats.last_price is None
    assert stats.vwap is None
    assert stats.midprice is None
    assert stats.current_price == 10_000
    exchange.submit(limit(Side.SELL, 10_010, 5, owner=1))
    exchange.submit(limit(Side.BUY, 9_990, 5, owner=2))
    exchange.submit(OrderRequest(instrument=0, side=Side.BUY, type=OrderType.MARKET, quantity=2))
    stats = exchange.statistics(0)
    assert stats.last_price == 10_010
    assert stats.volume == 2
    assert stats.vwap == pytest.approx(10_010)
    assert stats.midprice == pytest.approx(10_000)
    assert stats.spread == 20
    assert (stats.bid_depth, stats.ask_depth, stats.resting_orders) == (5, 3, 2)
    assert not stats.volume_saturated


def test_recent_trades_are_oldest_first(exchange: Exchange) -> None:
    for price in (100, 101, 102):
        exchange.submit(limit(Side.SELL, price, 1, owner=1))
        exchange.submit(limit(Side.BUY, price, 1, owner=2))
    assert [t.price for t in exchange.recent_trades(0)] == [100, 101, 102]
    assert [t.price for t in exchange.recent_trades(0, limit=2)] == [101, 102]
    first = exchange.recent_trades(0)[0]
    assert first == exchange.recent_trades(0)[0]


def _self_trade_prevention_run(resting: int = 5) -> tuple[um.SubmitResult, um.BookSnapshot]:
    """The same input every time: owner 1 rests two sells, then crosses its own best one."""
    ex = Exchange()
    ex.add_instrument(symbol="ACME", tick_size=0.01, reference_price=10_000)
    ex.submit(limit(Side.SELL, 101, resting, owner=1))
    ex.submit(limit(Side.SELL, 102, 4, owner=2))
    ex.submit(limit(Side.BUY, 99, 3, owner=3))
    result = ex.submit(
        OrderRequest(
            instrument=0,
            side=Side.BUY,
            price=102,
            quantity=6,
            owner=1,
            self_trade_prevention=SelfTradePrevention.CANCEL_RESTING,
        )
    )
    return result, ex.book_snapshot(0)


def test_value_types_compare_by_value() -> None:
    first, first_book = _self_trade_prevention_run()
    second, second_book = _self_trade_prevention_run()
    assert len(first.resting_cancels) == 1
    assert len(first.trades) == 1
    assert first.resting_cancels == second.resting_cancels
    assert first.resting_cancels[0] is not second.resting_cancels[0]
    assert first.trades == second.trades
    assert first_book == second_book
    assert first_book.bids[0] == second_book.bids[0]
    assert first.trades[0] != second.resting_cancels[0]  # Different types are never equal.


def test_value_types_follow_the_comparison_protocol() -> None:
    result, book = _self_trade_prevention_run()
    values: list[object] = [result.trades[0], result.resting_cancels[0], book, book.bids[0]]
    for value in values:
        assert operator.eq(value, None) is False, type(value).__name__
        assert operator.ne(value, None) is True, type(value).__name__
        assert operator.eq(value, "text") is False, type(value).__name__
        assert None not in [value]
        assert value in [None, value]
        with pytest.raises(TypeError, match="unhashable"):
            hash(value)


def test_lookup_errors_are_key_errors(exchange: Exchange) -> None:
    assert exchange.find_instrument("NOPE") is None
    assert exchange.instrument_id("NOVA") == 1
    with pytest.raises(KeyError):
        exchange.instrument_id("NOPE")
    with pytest.raises(KeyError):
        exchange.statistics(5)
    with pytest.raises(KeyError):
        exchange.order(5, 1)


def test_invalid_registration_and_conversion_raise_value_error(exchange: Exchange) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        exchange.add_instrument(symbol="ACME", tick_size=0.01, reference_price=1)
    with pytest.raises(ValueError, match="invalid symbol"):
        exchange.add_instrument(symbol="bad", tick_size=0.01, reference_price=1)
    with pytest.raises(ValueError, match="not a multiple of the tick size"):
        exchange.to_ticks(0, 100.005)
    assert exchange.to_ticks(1, 100.05) == 2_001
    assert exchange.to_currency(1, 2_001) == pytest.approx(100.05)
    assert exchange.symbols() == ["ACME", "NOVA"]
    assert exchange.instrument(1).tick_size == 0.05


def test_sequence_numbers_are_exposed(exchange: Exchange) -> None:
    first = exchange.submit(limit(Side.SELL, 100, 1))
    second = exchange.submit(limit(Side.BUY, 100, 1))
    assert (first.sequence, second.sequence, second.trades[0].sequence) == (1, 2, 3)
    assert exchange.last_sequence == 3


# ---- Every bound field, after a known scenario -----------------------------------------------
#
# The scenario is built so that the fields compared with each other differ, so a binding that
# exposes the wrong C++ member (e.g. sequence as timestamp, high as low, bid as ask) fails.
# Three ACME events first make order ids, trade ids and sequence numbers diverge.


@dataclass
class _Scenario:
    exchange: Exchange
    results: dict[str, um.SubmitResult]
    cancel: um.CancelResult
    book: um.BookSnapshot  # NOVA, before the self-trade-prevention step.
    stats: um.MarketStatistics  # NOVA, before the self-trade-prevention step.
    order: um.Order  # C, partially filled, before the self-trade-prevention step.


def _nova_order(
    owner: int, side: Side, price: int, quantity: int, timestamp: int, **extra: Any
) -> OrderRequest:
    return OrderRequest(
        instrument=1,
        side=side,
        price=price,
        quantity=quantity,
        owner=owner,
        timestamp=timestamp,
        **extra,
    )


@pytest.fixture
def scenario(exchange: Exchange) -> _Scenario:
    results: dict[str, um.SubmitResult] = {}
    results["P1"] = exchange.submit(
        OrderRequest(instrument=0, side=Side.BUY, price=9_000, quantity=3, owner=31, timestamp=100)
    )  # id 1, sequence 1
    cancel = exchange.cancel(0, results["P1"].order_id)  # sequence 2
    results["P3"] = exchange.submit(
        OrderRequest(instrument=0, side=Side.BUY, price=9_000, quantity=0, owner=31)
    )  # rejected: sequence 3, no id
    stp_incoming = SelfTradePrevention.CANCEL_INCOMING
    ioc = TimeInForce.IMMEDIATE_OR_CANCEL
    results["A"] = exchange.submit(_nova_order(11, Side.SELL, 2_010, 7, 5_001))  # id 2, seq 4
    results["B"] = exchange.submit(_nova_order(12, Side.SELL, 2_030, 4, 5_002))  # id 3, seq 5
    results["C"] = exchange.submit(
        _nova_order(13, Side.BUY, 1_990, 7, 5_003, self_trade_prevention=stp_incoming)
    )  # id 4, seq 6
    results["D"] = exchange.submit(_nova_order(14, Side.BUY, 2_030, 13, 5_004, time_in_force=ioc))
    results["E"] = exchange.submit(_nova_order(15, Side.SELL, 1_990, 2, 5_005))  # id 6
    results["F"] = exchange.submit(_nova_order(16, Side.SELL, 2_005, 3, 5_006))  # id 7
    results["G"] = exchange.submit(_nova_order(17, Side.BUY, 2_005, 3, 5_007))  # id 8
    results["H"] = exchange.submit(_nova_order(18, Side.BUY, 1_996, 1, 5_008))  # id 9
    results["J"] = exchange.submit(_nova_order(19, Side.BUY, 1_990, 3, 5_009))  # id 10
    results["K"] = exchange.submit(_nova_order(23, Side.SELL, 2_040, 6, 5_010))  # id 11
    results["L"] = exchange.submit(_nova_order(21, Side.BUY, 1_980, 2, 5_011))  # id 12
    book, stats, order = exchange.book_snapshot(1), exchange.statistics(1), exchange.order(1, 4)
    assert order is not None
    results["M"] = exchange.submit(
        _nova_order(
            23,
            Side.BUY,
            2_040,
            1,
            5_012,
            self_trade_prevention=SelfTradePrevention.CANCEL_RESTING,
        )
    )  # id 13, seq 19: cancels K (seq 20), then rests
    return _Scenario(exchange, results, cancel, book, stats, order)


def _trade_fields(trade: um.Trade) -> tuple[object, ...]:
    return (
        trade.id,
        trade.sequence,
        trade.timestamp,
        trade.instrument,
        trade.price,
        trade.quantity,
        trade.aggressor_side,
        trade.buy_order_id,
        trade.sell_order_id,
        trade.buyer,
        trade.seller,
        trade.aggressor_order_id,
        trade.resting_order_id,
    )


def test_trade_fields(scenario: _Scenario) -> None:
    buy, sell = Side.BUY, Side.SELL
    trades = [*scenario.results["D"].trades, *scenario.results["E"].trades]
    trades += scenario.results["G"].trades
    assert [_trade_fields(t) for t in trades] == [
        (1, 8, 5_004, 1, 2_010, 7, buy, 5, 2, 14, 11, 5, 2),
        (2, 9, 5_004, 1, 2_030, 4, buy, 5, 3, 14, 12, 5, 3),
        (3, 11, 5_005, 1, 1_990, 2, sell, 4, 6, 13, 15, 6, 4),
        (4, 14, 5_007, 1, 2_005, 3, buy, 8, 7, 17, 16, 8, 7),
    ]
    assert scenario.exchange.recent_trades(1) == trades


def _submit_fields(result: um.SubmitResult) -> tuple[object, ...]:
    return (
        result.order_id,
        result.sequence,
        result.status,
        result.reject_reason,
        result.cancel_reason,
        result.requested_quantity,
        result.filled_quantity,
        result.resting_quantity,
        result.cancelled_quantity,
        len(result.trades),
        len(result.resting_cancels),
        result.accepted,
    )


def test_submit_result_fields(scenario: _Scenario) -> None:
    rejected, resting = OrderStatus.REJECTED, OrderStatus.RESTING
    filled, cancelled = OrderStatus.FILLED, OrderStatus.CANCELLED
    ok, bad_quantity = RejectReason.NONE, RejectReason.INVALID_QUANTITY
    none, no_liquidity = um.CancelReason.NONE, um.CancelReason.NO_LIQUIDITY
    # order id, sequence, status, reject and cancel reasons, requested / filled / resting /
    # cancelled quantity, trade count, resting-cancel count, accepted
    expected = {
        "P3": (0, 3, rejected, bad_quantity, none, 0, 0, 0, 0, 0, 0, False),
        "A": (2, 4, resting, ok, none, 7, 0, 7, 0, 0, 0, True),
        "D": (5, 7, cancelled, ok, no_liquidity, 13, 11, 0, 2, 2, 0, True),
        "E": (6, 10, filled, ok, none, 2, 2, 0, 0, 1, 0, True),
        "M": (13, 19, resting, ok, none, 1, 0, 1, 0, 0, 1, True),
    }
    assert {name: _submit_fields(scenario.results[name]) for name in expected} == expected


def test_resting_cancel_fields(scenario: _Scenario) -> None:
    (cancel,) = scenario.results["M"].resting_cancels
    assert (
        cancel.order_id,
        cancel.sequence,
        cancel.owner,
        cancel.side,
        cancel.price,
        cancel.cancelled_quantity,
        cancel.reason,
    ) == (11, 20, 23, Side.SELL, 2_040, 6, um.CancelReason.SELF_TRADE_PREVENTION)
    assert scenario.exchange.last_sequence == 20


def test_cancel_result_fields(scenario: _Scenario) -> None:
    cancel = scenario.cancel
    assert (
        cancel.outcome,
        cancel.sequence,
        cancel.order_id,
        cancel.owner,
        cancel.side,
        cancel.price,
        cancel.cancelled_quantity,
        cancel.cancelled,
    ) == (CancelOutcome.CANCELLED, 2, 1, 31, Side.BUY, 9_000, 3, True)


def test_order_fields(scenario: _Scenario) -> None:
    order = scenario.order
    assert (
        order.id,
        order.owner,
        order.instrument,
        order.side,
        order.self_trade_prevention,
        order.price,
        order.original_quantity,
        order.remaining_quantity,
        order.sequence,
        order.timestamp,
        order.filled_quantity,
    ) == (4, 13, 1, Side.BUY, SelfTradePrevention.CANCEL_INCOMING, 1_990, 7, 5, 6, 5_003, 2)


def test_market_statistics_fields(scenario: _Scenario) -> None:
    stats = scenario.stats
    assert (
        stats.instrument,
        stats.reference_price,
        stats.last_price,
        stats.open_price,
        stats.high_price,
        stats.low_price,
        stats.volume,
        stats.volume_saturated,
        stats.trade_count,
        stats.best_bid,
        stats.best_ask,
        stats.spread,
        stats.bid_depth,
        stats.ask_depth,
        stats.resting_orders,
        stats.current_price,
    ) == (1, 2_000, 2_005, 2_010, 2_030, 1_990, 16, False, 4, 1_996, 2_040, 44, 11, 6, 5, 2_005)
    notional = 2_010 * 7 + 2_030 * 4 + 1_990 * 2 + 2_005 * 3
    assert stats.notional == notional
    assert stats.vwap == notional / 16
    assert stats.midprice == 2_018.0


def test_book_snapshot_and_level_fields(scenario: _Scenario) -> None:
    book = scenario.book
    assert book.instrument == 1
    levels = [
        [(v.price, v.quantity, v.order_count) for v in side] for side in (book.bids, book.asks)
    ]
    assert levels == [[(1_996, 1, 1), (1_990, 8, 2), (1_980, 2, 1)], [(2_040, 6, 1)]]
    spec = scenario.exchange.instrument(1)
    assert (spec.symbol, spec.tick_size, spec.reference_price) == ("NOVA", 0.05, 2_000)


def test_value_types_with_different_fields_are_unequal(scenario: _Scenario) -> None:
    first, second = scenario.results["D"].trades
    assert first != second
    assert scenario.book.bids[0] != scenario.book.bids[1]
    assert scenario.book != scenario.exchange.book_snapshot(1)  # M changed the book.
    smaller, _ = _self_trade_prevention_run(resting=4)
    larger, _ = _self_trade_prevention_run(resting=5)
    assert smaller.resting_cancels[0] != larger.resting_cancels[0]


def test_order_request_fields_round_trip() -> None:
    request = OrderRequest(
        instrument=3,
        side=Side.SELL,
        quantity=11,
        type=OrderType.MARKET,
        price=17,
        time_in_force=TimeInForce.IMMEDIATE_OR_CANCEL,
        owner=19,
        self_trade_prevention=SelfTradePrevention.CANCEL_RESTING,
        timestamp=23,
    )
    fields = (
        "instrument",
        "side",
        "quantity",
        "type",
        "price",
        "time_in_force",
        "owner",
        "self_trade_prevention",
        "timestamp",
    )
    expected: tuple[object, ...] = (
        3,
        Side.SELL,
        11,
        OrderType.MARKET,
        17,
        TimeInForce.IMMEDIATE_OR_CANCEL,
        19,
        SelfTradePrevention.CANCEL_RESTING,
        23,
    )
    assert tuple(getattr(request, name) for name in fields) == expected
    request.instrument, request.quantity, request.price = 4, 12, 18
    request.owner, request.timestamp = 20, 24
    request.side, request.type = Side.BUY, OrderType.LIMIT
    request.time_in_force = TimeInForce.GOOD_TILL_CANCEL
    request.self_trade_prevention = SelfTradePrevention.CANCEL_INCOMING
    assert tuple(getattr(request, name) for name in fields) == (
        4,
        Side.BUY,
        12,
        OrderType.LIMIT,
        18,
        TimeInForce.GOOD_TILL_CANCEL,
        20,
        SelfTradePrevention.CANCEL_INCOMING,
        24,
    )


def test_default_depth_and_trade_limit(exchange: Exchange) -> None:
    for tick in range(12):
        exchange.submit(limit(Side.BUY, 1_000 - tick, 1))
        exchange.submit(limit(Side.SELL, 2_000 + tick, 1))
    snapshot = exchange.book_snapshot(0)
    assert (len(snapshot.bids), len(snapshot.asks)) == (10, 10)
    assert [level.price for level in snapshot.bids[:2]] == [1_000, 999]
    deeper = exchange.book_snapshot(0, depth=12)
    assert (len(deeper.bids), len(deeper.asks)) == (12, 12)
    for _ in range(101):
        exchange.submit(limit(Side.SELL, 1_500, 1))
        exchange.submit(limit(Side.BUY, 1_500, 1))
    recent = exchange.recent_trades(0)
    assert len(recent) == 100
    assert recent[-1].id == 101
    assert recent[0].id == 2


# ---- Property-based test through the bindings ------------------------------------------------


@dataclass
class _Ledger:
    """Per-order accounting kept by the test, independently of the engine."""

    requested: dict[int, int] = field(default_factory=dict)
    filled: dict[int, int] = field(default_factory=dict)
    cancelled: dict[int, int] = field(default_factory=dict)


_order = st.tuples(
    st.sampled_from([Side.BUY, Side.SELL]),
    st.sampled_from([OrderType.LIMIT, OrderType.LIMIT, OrderType.LIMIT, OrderType.MARKET]),
    st.integers(min_value=95, max_value=105),
    st.integers(min_value=1, max_value=50),
    st.sampled_from(list(TimeInForce)),
    st.sampled_from(list(SelfTradePrevention)),
    st.integers(min_value=0, max_value=3),
)
_operation = st.one_of(
    st.tuples(st.just("submit"), _order),
    st.tuples(st.just("cancel"), st.integers(min_value=0, max_value=60)),
)


@settings(max_examples=150, deadline=None)
@given(st.lists(_operation, min_size=1, max_size=80))
def test_random_order_flow_preserves_engine_invariants(
    operations: list[tuple[str, Any]],
) -> None:
    exchange = Exchange()
    instrument = exchange.add_instrument(symbol="PROP", tick_size=0.01, reference_price=100)
    ledger = _Ledger()
    for kind, payload in operations:
        if kind == "cancel":
            result = exchange.cancel(instrument, payload)
            if result.cancelled:
                ledger.cancelled[payload] = (
                    ledger.cancelled.get(payload, 0) + result.cancelled_quantity
                )
            continue
        side, order_type, price, quantity, tif, stp, owner = payload
        submit = exchange.submit(
            OrderRequest(
                instrument=instrument,
                side=side,
                type=order_type,
                price=0 if order_type is OrderType.MARKET else price,
                quantity=quantity,
                time_in_force=tif,
                self_trade_prevention=stp,
                owner=owner,
            )
        )
        assert submit.accepted
        assert (
            submit.filled_quantity + submit.resting_quantity + submit.cancelled_quantity == quantity
        )
        assert sum(t.quantity for t in submit.trades) == submit.filled_quantity
        ledger.requested[submit.order_id] = quantity
        ledger.cancelled[submit.order_id] = submit.cancelled_quantity
        for trade in submit.trades:
            if order_type is OrderType.LIMIT:
                assert trade.price <= price if side is Side.BUY else trade.price >= price
            for order_id in (trade.buy_order_id, trade.sell_order_id):
                ledger.filled[order_id] = ledger.filled.get(order_id, 0) + trade.quantity
        for removed in submit.resting_cancels:
            ledger.cancelled[removed.order_id] = (
                ledger.cancelled.get(removed.order_id, 0) + removed.cancelled_quantity
            )
        exchange.check_invariants(instrument)
        bid, ask = exchange.best_bid(instrument), exchange.best_ask(instrument)
        assert bid is None or ask is None or bid < ask

    for order_id, requested in ledger.requested.items():
        resting = exchange.order(instrument, order_id)
        still_resting = 0 if resting is None else resting.remaining_quantity
        accounted = ledger.filled.get(order_id, 0) + ledger.cancelled.get(order_id, 0)
        assert accounted + still_resting == requested, f"order {order_id}"
