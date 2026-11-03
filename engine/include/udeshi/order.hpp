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

  // Intrusive list hook. Copying never transfers links: a copy-constructed Order is detached,
  // and assigning to an Order leaves its own links untouched. A copy of a resting order is
  // therefore a plain snapshot that cannot reach (or corrupt) the live book and cannot dangle
  // when the book changes, and assigning into a pool node never disturbs a list.
  struct Links {
    Order* prev = nullptr;
    Order* next = nullptr;
    PriceLevel* level = nullptr;  // The level this order rests in; null when not resting.

    Links() = default;
    Links(const Links& /*other*/) noexcept {}
    Links& operator=(const Links& /*other*/) noexcept { return *this; }
    ~Links() = default;
  };
  Links links_;

 public:
  // Deep-const navigation: a const Order only yields const neighbours, so read-only views of
  // the book (OrderBook::find, BookSide iteration) cannot be used to mutate it.
  [[nodiscard]] const Order* next() const noexcept { return links_.next; }
  [[nodiscard]] Order* next() noexcept { return links_.next; }
  [[nodiscard]] const Order* prev() const noexcept { return links_.prev; }
  [[nodiscard]] Order* prev() noexcept { return links_.prev; }
  [[nodiscard]] const PriceLevel* level() const noexcept { return links_.level; }
  [[nodiscard]] PriceLevel* level() noexcept { return links_.level; }
  [[nodiscard]] bool is_linked() const noexcept { return links_.level != nullptr; }
  [[nodiscard]] Quantity filled_quantity() const noexcept {
    return original_quantity - remaining_quantity;
  }
};

}  // namespace udeshi
