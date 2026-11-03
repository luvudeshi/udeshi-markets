#pragma once

#include <cstddef>
#include <memory>
#include <vector>

#include "udeshi/order.hpp"

// AddressSanitizer cannot see use-after-release of pooled objects, because pool memory is never
// returned to the allocator. When ASan is active we poison released nodes manually so that any
// access through a stale Order* is reported exactly like a heap use-after-free.
#if defined(__SANITIZE_ADDRESS__)
#define UDESHI_ASAN_ENABLED 1
#elif defined(__has_feature)
#if __has_feature(address_sanitizer)
#define UDESHI_ASAN_ENABLED 1
#endif
#endif

#if defined(UDESHI_ASAN_ENABLED)
#include <sanitizer/asan_interface.h>
#define UDESHI_POISON(address, size) ASAN_POISON_MEMORY_REGION((address), (size))
#define UDESHI_UNPOISON(address, size) ASAN_UNPOISON_MEMORY_REGION((address), (size))
#else
#define UDESHI_POISON(address, size) static_cast<void>(0)
#define UDESHI_UNPOISON(address, size) static_cast<void>(0)
#endif

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
  OrderPool(OrderPool&&) = delete;
  OrderPool& operator=(OrderPool&&) = delete;
  ~OrderPool() {
#if defined(UDESHI_ASAN_ENABLED)
    for (const auto& chunk : chunks_) {
      UDESHI_UNPOISON(chunk.get(), kChunkSize * sizeof(Order));
    }
#endif
  }

  [[nodiscard]] Order* allocate() {
    if (free_list_.empty()) {
      grow();
    }
    Order* order = free_list_.back();
    free_list_.pop_back();
    UDESHI_UNPOISON(order, sizeof(Order));
    *order = Order{};
    ++live_count_;
    return order;
  }

  void release(Order* order) noexcept {
    // The free list's capacity always equals the total number of nodes (reserved in grow()),
    // so this push_back never reallocates and therefore cannot throw.
    free_list_.push_back(order);
    UDESHI_POISON(order, sizeof(Order));
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
    UDESHI_POISON(base, kChunkSize * sizeof(Order));
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
