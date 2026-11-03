// Python bindings for the matching engine: the `udeshi_markets._engine` extension module.
//
// Design rules:
//  * Everything crosses the language boundary by value. Python never holds a pointer or
//    reference into an order book, so no Python object can dangle or alias engine memory when
//    the book changes. (Order snapshots are detached copies; see Order::Links.)
//  * Enums are real Python `enum.Enum` types, so Python callers cannot construct an
//    out-of-range value; the engine still validates enum fields as a second line of defence.
//  * C++ exceptions map to the closest Python exception: std::invalid_argument -> ValueError,
//    std::out_of_range (unknown symbol / instrument) -> KeyError.
//  * Value types with a C++ operator== compare by value in Python, return NotImplemented for
//    other types (so `trade == None` is simply False), and are unhashable.

#include <pybind11/native_enum.h>
#include <pybind11/operators.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "udeshi/exchange.hpp"

namespace py = pybind11;

namespace udeshi {
namespace {

template <typename Enum>
std::string enum_name(Enum value) {
  return std::string(to_string(value));
}

std::string repr(const OrderRequest& r) {
  std::ostringstream os;
  os << "OrderRequest(instrument=" << r.instrument << ", side=" << enum_name(r.side)
     << ", type=" << enum_name(r.type) << ", price=" << r.price << ", quantity=" << r.quantity
     << ", owner=" << r.owner << ")";
  return os.str();
}

std::string repr(const Trade& t) {
  std::ostringstream os;
  os << "Trade(id=" << t.id << ", instrument=" << t.instrument << ", " << t.quantity << "@"
     << t.price << ", aggressor=" << enum_name(t.aggressor_side) << ", buy_order=" << t.buy_order_id
     << ", sell_order=" << t.sell_order_id << ", sequence=" << t.sequence << ")";
  return os.str();
}

std::string repr(const SubmitResult& r) {
  std::ostringstream os;
  os << "SubmitResult(order_id=" << r.order_id << ", status=" << enum_name(r.status);
  if (r.reject_reason != RejectReason::None) {
    os << ", reject_reason=" << enum_name(r.reject_reason);
  }
  os << ", filled=" << r.filled_quantity << ", resting=" << r.resting_quantity
     << ", cancelled=" << r.cancelled_quantity << ", trades=" << r.trades.size() << ")";
  return os.str();
}

// Copies of resting orders, never pointers into the book.
std::optional<Order> find_order(const Exchange& exchange, InstrumentId instrument, OrderId id) {
  const Order* order = exchange.order_book(instrument).find(id);
  return order == nullptr ? std::nullopt : std::optional<Order>(*order);
}

std::vector<Order> orders_at(const Exchange& exchange, InstrumentId instrument, Side side,
                             Price price) {
  std::vector<Order> copies;
  for (const Order* order : exchange.order_book(instrument).orders_at(side, price)) {
    copies.push_back(*order);
  }
  return copies;
}

void bind_enums(py::module_& m) {
  py::native_enum<Side>(m, "Side", "enum.Enum")
      .value("BUY", Side::Buy)
      .value("SELL", Side::Sell)
      .finalize();
  py::native_enum<OrderType>(m, "OrderType", "enum.Enum")
      .value("LIMIT", OrderType::Limit)
      .value("MARKET", OrderType::Market)
      .finalize();
  py::native_enum<TimeInForce>(m, "TimeInForce", "enum.Enum")
      .value("GOOD_TILL_CANCEL", TimeInForce::GoodTillCancel)
      .value("IMMEDIATE_OR_CANCEL", TimeInForce::ImmediateOrCancel)
      .finalize();
  py::native_enum<SelfTradePrevention>(m, "SelfTradePrevention", "enum.Enum")
      .value("ALLOW", SelfTradePrevention::Allow)
      .value("CANCEL_RESTING", SelfTradePrevention::CancelResting)
      .value("CANCEL_INCOMING", SelfTradePrevention::CancelIncoming)
      .finalize();
  py::native_enum<OrderStatus>(m, "OrderStatus", "enum.Enum")
      .value("REJECTED", OrderStatus::Rejected)
      .value("RESTING", OrderStatus::Resting)
      .value("FILLED", OrderStatus::Filled)
      .value("CANCELLED", OrderStatus::Cancelled)
      .finalize();
  py::native_enum<RejectReason>(m, "RejectReason", "enum.Enum")
      .value("NONE", RejectReason::None)
      .value("UNKNOWN_INSTRUMENT", RejectReason::UnknownInstrument)
      .value("INVALID_SIDE", RejectReason::InvalidSide)
      .value("INVALID_ORDER_TYPE", RejectReason::InvalidOrderType)
      .value("INVALID_TIME_IN_FORCE", RejectReason::InvalidTimeInForce)
      .value("INVALID_SELF_TRADE_PREVENTION", RejectReason::InvalidSelfTradePrevention)
      .value("INVALID_QUANTITY", RejectReason::InvalidQuantity)
      .value("INVALID_PRICE", RejectReason::InvalidPrice)
      .value("MARKET_ORDER_WITH_PRICE", RejectReason::MarketOrderWithPrice)
      .finalize();
  py::native_enum<CancelReason>(m, "CancelReason", "enum.Enum")
      .value("NONE", CancelReason::None)
      .value("USER_REQUESTED", CancelReason::UserRequested)
      .value("NO_LIQUIDITY", CancelReason::NoLiquidity)
      .value("SELF_TRADE_PREVENTION", CancelReason::SelfTradePrevention)
      .finalize();
  py::native_enum<CancelOutcome>(m, "CancelOutcome", "enum.Enum")
      .value("CANCELLED", CancelOutcome::Cancelled)
      .value("UNKNOWN_INSTRUMENT", CancelOutcome::UnknownInstrument)
      .value("ORDER_NOT_FOUND", CancelOutcome::OrderNotFound)
      .finalize();
}

void bind_value_types(py::module_& m) {
  py::class_<OrderRequest>(m, "OrderRequest", "An order as submitted by a trader (prices in ticks).")
      .def(py::init([](InstrumentId instrument, Side side, Quantity quantity, OrderType type,
                       Price price, TimeInForce time_in_force, TraderId owner,
                       SelfTradePrevention self_trade_prevention, Timestamp timestamp) {
             OrderRequest r;
             r.instrument = instrument;
             r.side = side;
             r.quantity = quantity;
             r.type = type;
             r.price = price;
             r.time_in_force = time_in_force;
             r.owner = owner;
             r.self_trade_prevention = self_trade_prevention;
             r.timestamp = timestamp;
             return r;
           }),
           py::kw_only(), py::arg("instrument"), py::arg("side"), py::arg("quantity"),
           py::arg("type") = OrderType::Limit, py::arg("price") = Price{0},
           py::arg("time_in_force") = TimeInForce::GoodTillCancel,
           py::arg("owner") = kAnonymousTrader,
           py::arg("self_trade_prevention") = SelfTradePrevention::Allow,
           py::arg("timestamp") = Timestamp{0})
      .def_readwrite("instrument", &OrderRequest::instrument)
      .def_readwrite("side", &OrderRequest::side)
      .def_readwrite("quantity", &OrderRequest::quantity)
      .def_readwrite("type", &OrderRequest::type)
      .def_readwrite("price", &OrderRequest::price)
      .def_readwrite("time_in_force", &OrderRequest::time_in_force)
      .def_readwrite("owner", &OrderRequest::owner)
      .def_readwrite("self_trade_prevention", &OrderRequest::self_trade_prevention)
      .def_readwrite("timestamp", &OrderRequest::timestamp)
      .def("__repr__", [](const OrderRequest& r) { return repr(r); });

  py::class_<Order>(m, "Order", "A detached snapshot of a resting order.")
      .def_readonly("id", &Order::id)
      .def_readonly("owner", &Order::owner)
      .def_readonly("instrument", &Order::instrument)
      .def_readonly("side", &Order::side)
      .def_readonly("self_trade_prevention", &Order::self_trade_prevention)
      .def_readonly("price", &Order::price)
      .def_readonly("original_quantity", &Order::original_quantity)
      .def_readonly("remaining_quantity", &Order::remaining_quantity)
      .def_readonly("sequence", &Order::sequence)
      .def_readonly("timestamp", &Order::timestamp)
      .def_property_readonly("filled_quantity", &Order::filled_quantity);

  py::class_<Trade>(m, "Trade", "One execution, at the resting order's price (in ticks).")
      .def_readonly("id", &Trade::id)
      .def_readonly("sequence", &Trade::sequence)
      .def_readonly("timestamp", &Trade::timestamp)
      .def_readonly("instrument", &Trade::instrument)
      .def_readonly("price", &Trade::price)
      .def_readonly("quantity", &Trade::quantity)
      .def_readonly("aggressor_side", &Trade::aggressor_side)
      .def_readonly("buy_order_id", &Trade::buy_order_id)
      .def_readonly("sell_order_id", &Trade::sell_order_id)
      .def_readonly("buyer", &Trade::buyer)
      .def_readonly("seller", &Trade::seller)
      .def_property_readonly("aggressor_order_id", &Trade::aggressor_order_id)
      .def_property_readonly("resting_order_id", &Trade::resting_order_id)
      .def(py::self == py::self)
      .def("__repr__", [](const Trade& t) { return repr(t); });

  py::class_<LevelView>(m, "LevelView", "Aggregated quantity at one price level.")
      .def_readonly("price", &LevelView::price)
      .def_readonly("quantity", &LevelView::quantity)
      .def_readonly("order_count", &LevelView::order_count)
      .def(py::self == py::self);

  py::class_<BookSnapshot>(m, "BookSnapshot", "Aggregated depth, best price first per side.")
      .def_readonly("instrument", &BookSnapshot::instrument)
      .def_readonly("bids", &BookSnapshot::bids)
      .def_readonly("asks", &BookSnapshot::asks)
      .def(py::self == py::self);

  py::class_<RestingCancel>(m, "RestingCancel", "A resting order removed by self-trade prevention.")
      .def_readonly("order_id", &RestingCancel::order_id)
      .def_readonly("sequence", &RestingCancel::sequence)
      .def_readonly("owner", &RestingCancel::owner)
      .def_readonly("side", &RestingCancel::side)
      .def_readonly("price", &RestingCancel::price)
      .def_readonly("cancelled_quantity", &RestingCancel::cancelled_quantity)
      .def_readonly("reason", &RestingCancel::reason)
      .def(py::self == py::self);

  py::class_<SubmitResult>(m, "SubmitResult", "Everything that happened because of one submission.")
      .def_readonly("order_id", &SubmitResult::order_id)
      .def_readonly("sequence", &SubmitResult::sequence)
      .def_readonly("status", &SubmitResult::status)
      .def_readonly("reject_reason", &SubmitResult::reject_reason)
      .def_readonly("cancel_reason", &SubmitResult::cancel_reason)
      .def_readonly("requested_quantity", &SubmitResult::requested_quantity)
      .def_readonly("filled_quantity", &SubmitResult::filled_quantity)
      .def_readonly("resting_quantity", &SubmitResult::resting_quantity)
      .def_readonly("cancelled_quantity", &SubmitResult::cancelled_quantity)
      .def_readonly("trades", &SubmitResult::trades)
      .def_readonly("resting_cancels", &SubmitResult::resting_cancels)
      .def_property_readonly("accepted", &SubmitResult::accepted)
      .def("__repr__", [](const SubmitResult& r) { return repr(r); });

  py::class_<CancelResult>(m, "CancelResult", "The outcome of a cancel request.")
      .def_readonly("outcome", &CancelResult::outcome)
      .def_readonly("sequence", &CancelResult::sequence)
      .def_readonly("order_id", &CancelResult::order_id)
      .def_readonly("owner", &CancelResult::owner)
      .def_readonly("side", &CancelResult::side)
      .def_readonly("price", &CancelResult::price)
      .def_readonly("cancelled_quantity", &CancelResult::cancelled_quantity)
      .def_property_readonly("cancelled", &CancelResult::cancelled);

  py::class_<InstrumentSpec>(m, "InstrumentSpec", "Static description of an instrument.")
      .def(py::init([](std::string symbol, double tick_size, Price reference_price) {
             return InstrumentSpec{std::move(symbol), tick_size, reference_price};
           }),
           py::kw_only(), py::arg("symbol"), py::arg("tick_size"), py::arg("reference_price"))
      .def_readonly("symbol", &InstrumentSpec::symbol)
      .def_readonly("tick_size", &InstrumentSpec::tick_size)
      .def_readonly("reference_price", &InstrumentSpec::reference_price);

  py::class_<MarketStatistics>(m, "MarketStatistics",
                               "Trade- and book-derived market data (prices in ticks).")
      .def_readonly("instrument", &MarketStatistics::instrument)
      .def_readonly("reference_price", &MarketStatistics::reference_price)
      .def_readonly("last_price", &MarketStatistics::last_price)
      .def_readonly("open_price", &MarketStatistics::open_price)
      .def_readonly("high_price", &MarketStatistics::high_price)
      .def_readonly("low_price", &MarketStatistics::low_price)
      .def_readonly("volume", &MarketStatistics::volume)
      .def_readonly("volume_saturated", &MarketStatistics::volume_saturated)
      .def_readonly("trade_count", &MarketStatistics::trade_count)
      .def_readonly("notional", &MarketStatistics::notional)
      .def_readonly("vwap", &MarketStatistics::vwap)
      .def_readonly("best_bid", &MarketStatistics::best_bid)
      .def_readonly("best_ask", &MarketStatistics::best_ask)
      .def_readonly("midprice", &MarketStatistics::midprice)
      .def_readonly("spread", &MarketStatistics::spread)
      .def_readonly("bid_depth", &MarketStatistics::bid_depth)
      .def_readonly("ask_depth", &MarketStatistics::ask_depth)
      .def_readonly("resting_orders", &MarketStatistics::resting_orders)
      .def_property_readonly("current_price", &MarketStatistics::current_price);
}

void bind_exchange(py::module_& m) {
  py::class_<Exchange>(m, "Exchange",
                       "Multi-instrument exchange: matching engine, symbology and market data.\n\n"
                       "Not thread-safe; drive each instance from one thread.")
      .def(py::init<std::size_t>(),
           py::arg("recent_trade_capacity") = Exchange::kDefaultRecentTradeCapacity)
      .def(
          "add_instrument",
          [](Exchange& exchange, std::string symbol, double tick_size, Price reference_price) {
            return exchange.add_instrument({std::move(symbol), tick_size, reference_price});
          },
          py::kw_only(), py::arg("symbol"), py::arg("tick_size"), py::arg("reference_price"))
      .def_property_readonly("instrument_count", &Exchange::instrument_count)
      .def("symbols", &Exchange::symbols)
      .def("find_instrument", &Exchange::find_instrument, py::arg("symbol"))
      .def("instrument_id", &Exchange::instrument_id, py::arg("symbol"))
      .def("instrument", &Exchange::instrument, py::arg("instrument"),
           py::return_value_policy::copy)
      .def("submit", &Exchange::submit, py::arg("request"))
      .def("cancel", &Exchange::cancel, py::arg("instrument"), py::arg("order_id"))
      .def("book_snapshot", &Exchange::book_snapshot, py::arg("instrument"),
           py::arg("depth") = std::size_t{10})
      .def("recent_trades", &Exchange::recent_trades, py::arg("instrument"),
           py::arg("limit") = std::size_t{100})
      .def("best_bid", &Exchange::best_bid, py::arg("instrument"))
      .def("best_ask", &Exchange::best_ask, py::arg("instrument"))
      .def("midprice", &Exchange::midprice, py::arg("instrument"))
      .def("spread", &Exchange::spread, py::arg("instrument"))
      .def("statistics", &Exchange::statistics, py::arg("instrument"))
      .def("to_ticks", &Exchange::to_ticks, py::arg("instrument"), py::arg("price"))
      .def("to_currency", &Exchange::to_currency, py::arg("instrument"), py::arg("ticks"))
      .def("order", &find_order, py::arg("instrument"), py::arg("order_id"),
           "A snapshot of a resting order, or None if it is not resting.")
      .def("orders_at", &orders_at, py::arg("instrument"), py::arg("side"), py::arg("price"),
           "Snapshots of the resting orders at one price, in time priority.")
      .def(
          "resting_order_count",
          [](const Exchange& exchange, InstrumentId instrument) {
            return exchange.order_book(instrument).order_count();
          },
          py::arg("instrument"))
      .def_property_readonly(
          "last_sequence", [](const Exchange& exchange) { return exchange.engine().last_sequence(); })
      .def(
          "check_invariants",
          [](const Exchange& exchange, InstrumentId instrument) {
            exchange.order_book(instrument).check_invariants();
          },
          py::arg("instrument"), "Raises RuntimeError if the book's internal invariants fail.");
}

}  // namespace
}  // namespace udeshi

PYBIND11_MODULE(_engine, m) {
  using namespace udeshi;
  m.doc() = "Udeshi Markets matching engine (C++20) bindings.";
  m.attr("__version__") = UDESHI_VERSION;
  m.attr("MAX_PRICE") = kMaxPrice;
  m.attr("MAX_ORDER_QUANTITY") = kMaxOrderQuantity;
  m.attr("MIN_TICK_SIZE") = kMinTickSize;
  m.attr("MAX_TICK_SIZE") = kMaxTickSize;
  m.attr("ANONYMOUS_TRADER") = kAnonymousTrader;
  m.attr("INVALID_ORDER_ID") = kInvalidOrderId;

  // An unknown symbol or instrument id is a lookup failure: KeyError reads better than the
  // IndexError pybind11 would map std::out_of_range to by default. The translator is local to
  // this module. A global one would live in pybind11's internals, which every pybind11 module
  // of the same ABI in the process shares, and would turn their std::out_of_range into KeyError
  // too (breaking, for example, iteration that relies on IndexError). That cross-module effect
  // can only be observed with a second extension module, so it is checked by hand rather than
  // in the test suite.
  py::register_local_exception_translator([](std::exception_ptr exception) {
    try {
      if (exception) {
        std::rethrow_exception(exception);
      }
    } catch (const std::out_of_range& error) {
      py::set_error(PyExc_KeyError, error.what());
    }
  });

  bind_enums(m);
  bind_value_types(m);
  bind_exchange(m);
  m.def("price_to_ticks", &price_to_ticks, py::arg("price"), py::arg("tick_size"),
        "Converts a currency price to ticks with the exchange's exact rules; raises ValueError "
        "for an off-grid, negative, non-finite or out-of-range price or an invalid tick size.");
}
