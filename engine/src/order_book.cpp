#include "udeshi/order_book.hpp"

#include <algorithm>
#include <sstream>
#include <stdexcept>
#include <string>

namespace udeshi {

namespace {

[[noreturn]] void invariant_violation(const std::string& what) {
  throw std::logic_error("OrderBook invariant violated: " + what);
}

}  // namespace

OrderBook::OrderBook(InstrumentId instrument) : instrument_(instrument) {}

std::optional<Price> OrderBook::best_bid() const noexcept { return best_price(Side::Buy); }

std::optional<Price> OrderBook::best_ask() const noexcept { return best_price(Side::Sell); }

std::optional<Price> OrderBook::best_price(Side side_to_query) const noexcept {
  const PriceLevel* level = side(side_to_query).best();
  if (level == nullptr) {
    return std::nullopt;
  }
  return level->price();
}

Quantity OrderBook::volume_at(Side side_to_query, Price price) const noexcept {
  const PriceLevel* level = side(side_to_query).find(price);
  return level == nullptr ? 0 : level->total_quantity();
}

Quantity OrderBook::total_volume(Side side_to_query) const noexcept {
  Quantity total = 0;
  for (const auto& [price, level] : side(side_to_query)) {
    total += level.total_quantity();
  }
  return total;
}

std::size_t OrderBook::level_count(Side side_to_query) const noexcept {
  return side(side_to_query).level_count();
}

const Order* OrderBook::find(OrderId id) const noexcept { return index_.find(id); }

Order* OrderBook::find_mutable(OrderId id) noexcept { return index_.find(id); }

std::vector<LevelView> OrderBook::depth(Side side_to_query, std::size_t max_levels) const {
  const BookSide& book_side = side(side_to_query);
  std::vector<LevelView> levels;
  levels.reserve(std::min(max_levels, book_side.level_count()));
  for (const auto& [price, level] : book_side) {
    if (levels.size() == max_levels) {
      break;
    }
    levels.push_back(LevelView{price, level.total_quantity(), level.order_count()});
  }
  return levels;
}

BookSnapshot OrderBook::snapshot(std::size_t max_levels) const {
  return BookSnapshot{instrument_, depth(Side::Buy, max_levels), depth(Side::Sell, max_levels)};
}

std::vector<const Order*> OrderBook::orders_at(Side side_to_query, Price price) const {
  std::vector<const Order*> orders;
  const PriceLevel* level = side(side_to_query).find(price);
  if (level == nullptr) {
    return orders;
  }
  orders.reserve(level->order_count());
  for (const Order* order = level->front(); order != nullptr; order = order->next()) {
    orders.push_back(order);
  }
  return orders;
}

Order& OrderBook::insert(Order order) {
  // Every structural property that check_invariants() verifies is enforced here, so the
  // public primitive cannot be used to build a book the checker would reject.
  if (order.instrument != instrument_) {
    throw std::invalid_argument("OrderBook::insert: order belongs to another instrument");
  }
  if (order.original_quantity <= 0 || order.original_quantity > kMaxOrderQuantity ||
      order.remaining_quantity <= 0 || order.remaining_quantity > order.original_quantity) {
    throw std::invalid_argument("OrderBook::insert: quantity out of range");
  }
  if (!is_valid(order.side) || order.price <= 0 || order.price > kMaxPrice) {
    throw std::invalid_argument("OrderBook::insert: invalid side or price");
  }
  if (index_.find(order.id) != nullptr) {
    throw std::invalid_argument("OrderBook::insert: duplicate order id " +
                                std::to_string(order.id));
  }
  // Room in the index first (growing it changes no entry), so that nothing can fail once the
  // order is in its level.
  index_.reserve_one();
  PriceLevel& level = mutable_side(order.side).find_or_create(order.price);
  // Time priority is list order, so a new order must be younger than the current tail. (If
  // this throws, the level already existed and is unchanged.)
  if (!level.empty() && level.back()->sequence >= order.sequence) {
    throw std::invalid_argument("OrderBook::insert: sequence not after the level's tail");
  }
  Order* node = nullptr;
  try {
    node = pool_.allocate();
  } catch (...) {
    if (level.empty()) {
      mutable_side(order.side).erase(order.price);  // Drop the level we just created.
    }
    throw;
  }
  *node = order;
  level.push_back(node);
  index_.insert(order.id, node);
  return *node;
}

bool OrderBook::execute(Order& order, Quantity quantity) {
  PriceLevel* level = order.level();
  level->reduce(&order, quantity);
  if (order.remaining_quantity > 0) {
    return false;
  }
  remove(order);
  return true;
}

void OrderBook::remove(Order& order) {
  PriceLevel* level = order.level();
  const Price price = level->price();
  const Side order_side = order.side;
  level->erase(&order);
  if (level->empty()) {
    mutable_side(order_side).erase(price);
  }
  index_.erase(order.id);
  pool_.release(&order);
}

void OrderBook::check_side_invariants(const BookSide& book_side,
                                      std::size_t& indexed_orders_seen) const {
  for (const auto& [price, level] : book_side) {
    std::ostringstream where;
    where << to_string(book_side.side()) << " level " << price << ": ";
    if (level.price() != price) {
      invariant_violation(where.str() + "level price does not match its key");
    }
    if (price <= 0 || price > kMaxPrice) {
      invariant_violation(where.str() + "price out of range");
    }
    if (level.empty() || level.order_count() == 0) {
      invariant_violation(where.str() + "empty level left in the book");
    }
    Quantity summed_quantity = 0;
    std::size_t counted_orders = 0;
    const Order* previous = nullptr;
    for (const Order* order = level.front(); order != nullptr; order = order->next()) {
      if (order->prev() != previous) {
        invariant_violation(where.str() + "broken back-link in FIFO queue");
      }
      if (order->level() != &level) {
        invariant_violation(where.str() + "order points at the wrong level");
      }
      if (order->side != book_side.side() || order->price != price ||
          order->instrument != instrument_) {
        invariant_violation(where.str() + "order side/price/instrument mismatch");
      }
      if (order->remaining_quantity <= 0 ||
          order->remaining_quantity > order->original_quantity) {
        invariant_violation(where.str() + "order remaining quantity out of range");
      }
      if (previous != nullptr && previous->sequence >= order->sequence) {
        invariant_violation(where.str() + "FIFO queue not in strictly increasing sequence order");
      }
      if (index_.find(order->id) != order) {
        invariant_violation(where.str() + "resting order missing from id index");
      }
      summed_quantity += order->remaining_quantity;
      ++counted_orders;
      previous = order;
    }
    if (previous != level.back()) {
      invariant_violation(where.str() + "tail pointer does not reference last order");
    }
    if (summed_quantity != level.total_quantity()) {
      invariant_violation(where.str() + "cached total quantity disagrees with orders");
    }
    if (counted_orders != level.order_count()) {
      invariant_violation(where.str() + "cached order count disagrees with orders");
    }
    indexed_orders_seen += counted_orders;
  }
}

void OrderBook::check_invariants() const {
  std::size_t orders_seen = 0;
  check_side_invariants(bids_, orders_seen);
  check_side_invariants(asks_, orders_seen);
  if (orders_seen != index_.size()) {
    invariant_violation("id index holds orders that are not in any level");
  }
  if (orders_seen != pool_.live_count()) {
    invariant_violation("order pool live count disagrees with resting orders");
  }
  const auto bid = best_bid();
  const auto ask = best_ask();
  if (bid && ask && *bid >= *ask) {
    invariant_violation("book is crossed or locked (best bid " + std::to_string(*bid) +
                        " >= best ask " + std::to_string(*ask) + ")");
  }
}

}  // namespace udeshi
