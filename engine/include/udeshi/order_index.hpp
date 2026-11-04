#pragma once

#include <cassert>
#include <cstddef>
#include <cstdint>
#include <vector>

#include "udeshi/order.hpp"
#include "udeshi/types.hpp"

namespace udeshi {

// The book's index of resting orders by id, for O(1) cancellation.
//
// An open-addressing hash table (linear probing, deletion by backward shift, no tombstones)
// over one contiguous array of {id, Order*} slots. A node-based std::unordered_map allocates on
// every insert and frees on every erase; measured, those calls were most of the cost of adding
// and cancelling orders, and following its node pointers misses the cache once a book holds
// many orders (docs/benchmarking.md). Here an insert or erase allocates nothing except when the
// table doubles, and a lookup reads one or two adjacent slots.
//
// Ids are hashed by Fibonacci hashing (multiplication by 2^64 / golden ratio, keeping the top
// bits), which spreads the engine's sequential ids evenly. The table doubles before it is half
// full, so probe sequences stay short. Id 0 (kInvalidOrderId, never assigned) marks an empty
// slot.
class OrderIndex {
 public:
  OrderIndex() = default;

  [[nodiscard]] std::size_t size() const noexcept { return size_; }
  [[nodiscard]] bool empty() const noexcept { return size_ == 0; }
  [[nodiscard]] std::size_t capacity() const noexcept { return slots_.size(); }

  // The order with this id, or null (always for id 0, which marks an empty slot).
  [[nodiscard]] Order* find(OrderId id) const noexcept {
    if (slots_.empty() || id == kInvalidOrderId) {
      return nullptr;
    }
    for (std::size_t i = home(id);; i = next(i)) {
      if (slots_[i].id == id) {
        return slots_[i].order;
      }
      if (slots_[i].id == kInvalidOrderId) {
        return nullptr;
      }
    }
  }

  // Makes room for one more entry, so that the next insert() cannot fail. Throws
  // std::bad_alloc if the table must grow and cannot; the index is then unchanged.
  void reserve_one() {
    if ((size_ + 1) * 2 > slots_.size()) {
      grow();
    }
  }

  // Adds an entry for an id that is not in the index and is not 0. Call reserve_one() first.
  void insert(OrderId id, Order* order) noexcept {
    assert(id != kInvalidOrderId && size_ * 2 < slots_.size());
    std::size_t i = home(id);
    while (slots_[i].id != kInvalidOrderId) {
      i = next(i);
    }
    slots_[i] = Slot{id, order};
    ++size_;
  }

  // Removes the entry for `id`; returns false if there is none.
  bool erase(OrderId id) noexcept {
    if (slots_.empty() || id == kInvalidOrderId) {
      return false;
    }
    std::size_t hole = home(id);
    while (slots_[hole].id != id) {
      if (slots_[hole].id == kInvalidOrderId) {
        return false;
      }
      hole = next(hole);
    }
    // Backward shift: move later entries of the probe run into the hole whenever the hole lies
    // on their probe path (between their home slot and their current slot), so that every
    // remaining entry stays reachable from its home without tombstones.
    for (std::size_t i = next(hole); slots_[i].id != kInvalidOrderId; i = next(i)) {
      const std::size_t wanted = home(slots_[i].id);
      const bool hole_on_path =
          hole <= i ? (wanted <= hole || wanted > i) : (wanted <= hole && wanted > i);
      if (hole_on_path) {
        slots_[hole] = slots_[i];
        hole = i;
      }
    }
    slots_[hole] = Slot{};
    --size_;
    return true;
  }

 private:
  struct Slot {
    OrderId id = kInvalidOrderId;
    Order* order = nullptr;
  };

  static constexpr std::size_t kInitialCapacity = 64;  // A power of two.

  [[nodiscard]] std::size_t home(OrderId id) const noexcept {
    return static_cast<std::size_t>((id * 0x9E3779B97F4A7C15ULL) >> shift_);
  }
  [[nodiscard]] std::size_t next(std::size_t i) const noexcept {
    return (i + 1) & (slots_.size() - 1);
  }

  void grow() {
    const std::size_t capacity = slots_.empty() ? kInitialCapacity : slots_.size() * 2;
    std::vector<Slot> old(capacity);  // May throw; nothing has changed yet.
    old.swap(slots_);
    shift_ = 64 - log2(capacity);
    for (const Slot& slot : old) {
      if (slot.id != kInvalidOrderId) {
        std::size_t i = home(slot.id);
        while (slots_[i].id != kInvalidOrderId) {
          i = next(i);
        }
        slots_[i] = slot;
      }
    }
  }

  [[nodiscard]] static unsigned log2(std::size_t power_of_two) noexcept {
    unsigned bits = 0;
    while ((std::size_t{1} << bits) < power_of_two) {
      ++bits;
    }
    return bits;
  }

  std::vector<Slot> slots_;
  std::size_t size_ = 0;
  unsigned shift_ = 64;
};

}  // namespace udeshi
