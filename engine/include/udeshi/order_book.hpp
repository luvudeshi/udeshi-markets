#pragma once

#include <cstddef>
#include <map>
#include <optional>
#include <unordered_map>
#include <vector>

#include "udeshi/order.hpp"
#include "udeshi/order_pool.hpp"
#include "udeshi/price_level.hpp"
#include "udeshi/types.hpp"

namespace udeshi {

// Aggregated (L2) view of one price level.
struct LevelView {
  Price price = 0;
  Quantity quantity = 0;
  std::size_t order_count = 0;

  friend bool operator==(const LevelView&, const LevelView&) = default;
};

// Aggregated depth for both sides, best price first on each side.
struct BookSnapshot {
  InstrumentId instrument = 0;
  std::vector<LevelView> bids;
  std::vector<LevelView> asks;

  friend bool operator==(const BookSnapshot&, const BookSnapshot&) = default;
};

// One side of a book: price levels ordered from best to worst.
//
// Bids are ordered by descending price and asks by ascending price, so begin() is always the
// best level and best-price access is O(1). A new level costs O(log L) to insert, where L is
// the number of distinct prices on this side (typically small relative to the order count).
class BookSide {
 public:
  struct BestFirst {
    Side side;
    [[nodiscard]] bool operator()(Price lhs, Price rhs) const noexcept {
      return side == Side::Buy ? lhs > rhs : lhs < rhs;
    }
  };
  using LevelMap = std::map<Price, PriceLevel, BestFirst>;

  explicit BookSide(Side side) : side_(side), levels_(BestFirst{side}) {}

  [[nodiscard]] Side side() const noexcept { return side_; }
  [[nodiscard]] bool empty() const noexcept { return levels_.empty(); }
  [[nodiscard]] std::size_t level_count() const noexcept { return levels_.size(); }

  [[nodiscard]] PriceLevel* best() noexcept {
    return levels_.empty() ? nullptr : &levels_.begin()->second;
  }
  [[nodiscard]] const PriceLevel* best() const noexcept {
    return levels_.empty() ? nullptr : &levels_.begin()->second;
  }
  [[nodiscard]] const PriceLevel* find(Price price) const noexcept {
    const auto it = levels_.find(price);
    return it == levels_.end() ? nullptr : &it->second;
  }

  [[nodiscard]] PriceLevel& find_or_create(Price price) {
    return levels_.try_emplace(price, price).first->second;
  }
  void erase(Price price) noexcept { levels_.erase(price); }

  [[nodiscard]] LevelMap::const_iterator begin() const noexcept { return levels_.begin(); }
  [[nodiscard]] LevelMap::const_iterator end() const noexcept { return levels_.end(); }

 private:
  Side side_;
  LevelMap levels_;
};

// Limit order book for a single instrument: resting orders only, no matching logic.
//
// Owns every resting Order (via an OrderPool) and indexes them by id for O(1) cancellation.
// The mutation primitives each preserve the structural invariants checked by
// check_invariants(); the MatchingEngine is responsible for the economic invariant that the
// book is never left crossed (best bid < best ask).
class OrderBook {
 public:
  explicit OrderBook(InstrumentId instrument);

  // Non-copyable and non-movable: resting orders hold pointers into this object's levels.
  OrderBook(const OrderBook&) = delete;
  OrderBook& operator=(const OrderBook&) = delete;
  OrderBook(OrderBook&&) = delete;
  OrderBook& operator=(OrderBook&&) = delete;
  ~OrderBook() = default;

  // ---- Queries -----------------------------------------------------------------------------
  [[nodiscard]] InstrumentId instrument() const noexcept { return instrument_; }
  [[nodiscard]] std::optional<Price> best_bid() const noexcept;
  [[nodiscard]] std::optional<Price> best_ask() const noexcept;
  [[nodiscard]] std::optional<Price> best_price(Side side) const noexcept;
  [[nodiscard]] Quantity volume_at(Side side, Price price) const noexcept;
  [[nodiscard]] Quantity total_volume(Side side) const noexcept;
  [[nodiscard]] std::size_t level_count(Side side) const noexcept;
  [[nodiscard]] std::size_t order_count() const noexcept { return index_.size(); }
  [[nodiscard]] bool empty() const noexcept { return index_.empty(); }
  [[nodiscard]] const Order* find(OrderId id) const noexcept;

  // Up to `max_levels` aggregated levels per side, best first.
  [[nodiscard]] std::vector<LevelView> depth(Side side, std::size_t max_levels) const;
  [[nodiscard]] BookSnapshot snapshot(std::size_t max_levels) const;
  // Resting orders at one price in time-priority order (L3 view). Empty if no such level.
  [[nodiscard]] std::vector<const Order*> orders_at(Side side, Price price) const;

  [[nodiscard]] const BookSide& side(Side side) const noexcept {
    return side == Side::Buy ? bids_ : asks_;
  }

  // ---- Mutation primitives (used by MatchingEngine) ----------------------------------------
  // Inserts a new resting order at the tail of its price level. The id must be unused.
  // Takes the order by value so the argument may safely alias a node just released to the
  // pool (e.g. remove-then-reinsert), which allocation would otherwise overwrite.
  Order& insert(Order order);
  // Executes `quantity` of a resting order. If it becomes fully filled it is removed from the
  // book and the pointer is invalidated; returns true in that case.
  bool execute(Order& order, Quantity quantity);
  // Removes a resting order from the book; the pointer is invalidated.
  void remove(Order& order);
  [[nodiscard]] Order* find_mutable(OrderId id) noexcept;
  [[nodiscard]] PriceLevel* best_level(Side side) noexcept {
    return mutable_side(side).best();
  }

  // Verifies every structural invariant, throwing std::logic_error describing the first
  // violation. O(number of resting orders); intended for tests and debug builds.
  void check_invariants() const;

 private:
  [[nodiscard]] BookSide& mutable_side(Side side) noexcept {
    return side == Side::Buy ? bids_ : asks_;
  }
  void check_side_invariants(const BookSide& side, std::size_t& indexed_orders_seen) const;

  InstrumentId instrument_;
  BookSide bids_{Side::Buy};
  BookSide asks_{Side::Sell};
  OrderPool pool_;
  std::unordered_map<OrderId, Order*> index_;
};

}  // namespace udeshi
