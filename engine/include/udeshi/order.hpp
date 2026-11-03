#pragma once

#include "udeshi/types.hpp"

namespace udeshi {

class PriceLevel;

// What a trader submits. The engine assigns the id, sequence number and resulting state.
struct OrderRequest {
  InstrumentId instrument = 0;
  TraderId owner = kAnonymousTrader;
  Side side = Side::Buy;
  OrderType type = OrderType::Limit;
  TimeInForce time_in_force = TimeInForce::GoodTillCancel;
  SelfTradePrevention self_trade_prevention = SelfTradePrevention::Allow;
  Price price = 0;  // Limit price in ticks; must be 0 for market orders.
  Quantity quantity = 0;
  Timestamp timestamp = 0;  // Simulated submission time; informational, not used for priority.
};

// A resting order. Doubles as an intrusive doubly-linked-list node inside its PriceLevel so
// that cancellation from the middle of a FIFO queue is O(1) with no extra allocation.
//
// Orders are owned by an OrderPool; the PriceLevel and the book's id index hold non-owning
// pointers. Time priority is the order of the intrusive list, which is the order of arrival
// (strictly increasing `sequence`) because orders are only ever appended at the tail.
struct Order {
  OrderId id = kInvalidOrderId;
  TraderId owner = kAnonymousTrader;
  InstrumentId instrument = 0;
  Side side = Side::Buy;
  SelfTradePrevention self_trade_prevention = SelfTradePrevention::Allow;
  Price price = 0;
  Quantity original_quantity = 0;
  Quantity remaining_quantity = 0;
  Sequence sequence = 0;
  Timestamp timestamp = 0;

 private:
  friend class PriceLevel;
  Order* prev_ = nullptr;
  Order* next_ = nullptr;
  PriceLevel* level_ = nullptr;  // The level this order rests in; null when not resting.

 public:
  // Deep-const navigation: a const Order only yields const neighbours, so read-only views of
  // the book (OrderBook::find, BookSide iteration) cannot be used to mutate it.
  [[nodiscard]] const Order* next() const noexcept { return next_; }
  [[nodiscard]] Order* next() noexcept { return next_; }
  [[nodiscard]] const Order* prev() const noexcept { return prev_; }
  [[nodiscard]] Order* prev() noexcept { return prev_; }
  [[nodiscard]] const PriceLevel* level() const noexcept { return level_; }
  [[nodiscard]] PriceLevel* level() noexcept { return level_; }
  [[nodiscard]] Quantity filled_quantity() const noexcept {
    return original_quantity - remaining_quantity;
  }
};

}  // namespace udeshi
