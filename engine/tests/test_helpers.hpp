#pragma once

#include <vector>

#include "udeshi/matching_engine.hpp"
#include "udeshi/order.hpp"
#include "udeshi/types.hpp"

namespace udeshi::testing {

inline OrderRequest limit(Side side, Price price, Quantity quantity,
                          TraderId owner = kAnonymousTrader, InstrumentId instrument = 0) {
  OrderRequest request;
  request.instrument = instrument;
  request.owner = owner;
  request.side = side;
  request.type = OrderType::Limit;
  request.time_in_force = TimeInForce::GoodTillCancel;
  request.price = price;
  request.quantity = quantity;
  return request;
}

inline OrderRequest ioc(Side side, Price price, Quantity quantity,
                        TraderId owner = kAnonymousTrader, InstrumentId instrument = 0) {
  OrderRequest request = limit(side, price, quantity, owner, instrument);
  request.time_in_force = TimeInForce::ImmediateOrCancel;
  return request;
}

inline OrderRequest market(Side side, Quantity quantity, TraderId owner = kAnonymousTrader,
                           InstrumentId instrument = 0) {
  OrderRequest request;
  request.instrument = instrument;
  request.owner = owner;
  request.side = side;
  request.type = OrderType::Market;
  request.time_in_force = TimeInForce::ImmediateOrCancel;
  request.quantity = quantity;
  return request;
}

inline OrderRequest with_stp(OrderRequest request, SelfTradePrevention mode) {
  request.self_trade_prevention = mode;
  return request;
}

// (price, quantity) pairs make trade expectations compact and readable.
struct Fill {
  Price price;
  Quantity quantity;
  friend bool operator==(const Fill&, const Fill&) = default;
};

inline std::vector<Fill> fills(const SubmitResult& result) {
  std::vector<Fill> out;
  out.reserve(result.trades.size());
  for (const Trade& trade : result.trades) {
    out.push_back(Fill{trade.price, trade.quantity});
  }
  return out;
}

inline void PrintTo(const Fill& fill, std::ostream* os) {
  *os << fill.quantity << "@" << fill.price;
}

}  // namespace udeshi::testing
