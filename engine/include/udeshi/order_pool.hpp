#pragma once

#include <cstddef>
#include <memory>
#include <vector>

#include "udeshi/order.hpp"

namespace udeshi {

// Slab allocator for Order nodes.
//
// Orders are allocated in fixed-size chunks that are never moved or freed while the pool is
// alive, so Order* pointers stay valid for as long as the order rests. Released nodes go onto
// a LIFO free list and are reused first, which keeps the working set small and cache-warm.
// This avoids a general-purpose heap allocation per order on the hot path.
class OrderPool {
 public:
  static constexpr std::size_t kChunkSize = 4096;

  OrderPool() = default;
  OrderPool(const OrderPool&) = delete;
  OrderPool& operator=(const OrderPool&) = delete;
  OrderPool(OrderPool&&) noexcept = default;
  OrderPool& operator=(OrderPool&&) noexcept = default;
  ~OrderPool() = default;

  [[nodiscard]] Order* allocate() {
    if (free_list_.empty()) {
      grow();
    }
    Order* order = free_list_.back();
    free_list_.pop_back();
    *order = Order{};
    ++live_count_;
    return order;
  }

  void release(Order* order) noexcept {
    // The free list's capacity always equals the total number of nodes (reserved in grow()),
    // so this push_back never reallocates and therefore cannot throw.
    free_list_.push_back(order);
    --live_count_;
  }

  [[nodiscard]] std::size_t live_count() const noexcept { return live_count_; }
  [[nodiscard]] std::size_t capacity() const noexcept { return chunks_.size() * kChunkSize; }

 private:
  void grow() {
    // Every step that can throw happens before any state is modified (strong guarantee).
    auto chunk = std::make_unique<Order[]>(kChunkSize);
    free_list_.reserve(capacity() + kChunkSize);
    Order* base = chunk.get();
    chunks_.push_back(std::move(chunk));
    // Push in reverse so nodes are handed out in ascending address order.
    for (std::size_t i = kChunkSize; i > 0; --i) {
      free_list_.push_back(&base[i - 1]);
    }
  }

  std::vector<std::unique_ptr<Order[]>> chunks_;
  std::vector<Order*> free_list_;
  std::size_t live_count_ = 0;
};

}  // namespace udeshi
