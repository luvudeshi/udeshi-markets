#pragma once

#include <cassert>
#include <cstddef>

#include "udeshi/order.hpp"
#include "udeshi/types.hpp"

namespace udeshi {

// All resting orders at one price on one side, in strict FIFO (time-priority) order.
//
// Implemented as an intrusive doubly linked list over Order nodes: append, pop-front and
// unlink-from-anywhere are all O(1) and never allocate. The aggregate quantity and order count
// are maintained incrementally so depth queries never walk the queue.
class PriceLevel {
 public:
  explicit PriceLevel(Price price) noexcept : price_(price) {}

  // Non-copyable and non-movable: a level holds raw pointers into its order list, so two live
  // copies would alias the same nodes. Levels are constructed in place inside std::map nodes,
  // whose addresses are stable.
  PriceLevel(const PriceLevel&) = delete;
  PriceLevel& operator=(const PriceLevel&) = delete;
  PriceLevel(PriceLevel&&) = delete;
  PriceLevel& operator=(PriceLevel&&) = delete;
  ~PriceLevel() = default;

  [[nodiscard]] Price price() const noexcept { return price_; }
  [[nodiscard]] Quantity total_quantity() const noexcept { return total_quantity_; }
  [[nodiscard]] std::size_t order_count() const noexcept { return order_count_; }
  [[nodiscard]] bool empty() const noexcept { return head_ == nullptr; }
  [[nodiscard]] const Order* front() const noexcept { return head_; }
  [[nodiscard]] Order* front() noexcept { return head_; }
  [[nodiscard]] const Order* back() const noexcept { return tail_; }
  [[nodiscard]] Order* back() noexcept { return tail_; }

  // Appends at the tail: the newest order has the lowest time priority.
  void push_back(Order* order) noexcept {
    assert(order != nullptr && order->price == price_);
    order->prev_ = tail_;
    order->next_ = nullptr;
    order->level_ = this;
    if (tail_ != nullptr) {
      tail_->next_ = order;
    } else {
      head_ = order;
    }
    tail_ = order;
    total_quantity_ += order->remaining_quantity;
    ++order_count_;
  }

  // Unlinks an order that is currently in this level. Its remaining quantity is removed from
  // the level total, so callers must not modify remaining_quantity before calling this
  // without also calling reduce().
  void erase(Order* order) noexcept {
    assert(order != nullptr && order->level_ == this && order_count_ > 0);
    if (order->prev_ != nullptr) {
      order->prev_->next_ = order->next_;
    } else {
      head_ = order->next_;
    }
    if (order->next_ != nullptr) {
      order->next_->prev_ = order->prev_;
    } else {
      tail_ = order->prev_;
    }
    order->prev_ = nullptr;
    order->next_ = nullptr;
    order->level_ = nullptr;
    total_quantity_ -= order->remaining_quantity;
    --order_count_;
  }

  // Records that `quantity` of an order in this level executed. Does not change priority.
  void reduce(Order* order, Quantity quantity) noexcept {
    assert(order->level_ == this && quantity > 0 && quantity <= order->remaining_quantity);
    order->remaining_quantity -= quantity;
    total_quantity_ -= quantity;
  }

 private:
  Price price_;
  Quantity total_quantity_ = 0;
  std::size_t order_count_ = 0;
  Order* head_ = nullptr;
  Order* tail_ = nullptr;
};

}  // namespace udeshi
