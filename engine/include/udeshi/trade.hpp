#pragma once

#include "udeshi/types.hpp"

namespace udeshi {

// A single execution between one aggressive (incoming) order and one resting order.
// Trades always execute at the resting order's price.
struct Trade {
  TradeId id = 0;
  Sequence sequence = 0;
  Timestamp timestamp = 0;  // Timestamp of the aggressive order that caused the trade.
  InstrumentId instrument = 0;
  Price price = 0;
  Quantity quantity = 0;
  Side aggressor_side = Side::Buy;
  OrderId buy_order_id = kInvalidOrderId;
  OrderId sell_order_id = kInvalidOrderId;
  TraderId buyer = kAnonymousTrader;
  TraderId seller = kAnonymousTrader;

  [[nodiscard]] OrderId aggressor_order_id() const noexcept {
    return aggressor_side == Side::Buy ? buy_order_id : sell_order_id;
  }
  [[nodiscard]] OrderId resting_order_id() const noexcept {
    return aggressor_side == Side::Buy ? sell_order_id : buy_order_id;
  }

  friend bool operator==(const Trade&, const Trade&) = default;
};

}  // namespace udeshi
