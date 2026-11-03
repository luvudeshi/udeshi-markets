#pragma once

// A deliberately naive, obviously-correct order book used as a test oracle.
//
// It stores resting orders in a flat vector and finds the best counterparty by a linear scan
// over (price, sequence). It shares no code with the production engine, so agreement between
// the two on long random order streams is strong evidence that the optimised data structures
// implement the same price-time-priority semantics.

#include <algorithm>
#include <cstdint>
#include <optional>
#include <tuple>
#include <vector>

#include "udeshi/types.hpp"

namespace udeshi::testing {

struct RefOrder {
  std::uint64_t id;
  std::uint64_t owner;
  Side side;
  std::int64_t price;
  std::int64_t remaining;
  std::uint64_t arrival;  // Monotonic arrival counter (time priority).
};

struct RefTrade {
  std::int64_t price;
  std::int64_t quantity;
  std::uint64_t buy_id;
  std::uint64_t sell_id;
  std::uint64_t buyer;
  std::uint64_t seller;
  friend bool operator==(const RefTrade&, const RefTrade&) = default;
};

struct RefResult {
  bool rejected = false;
  std::uint64_t id = 0;
  std::int64_t filled = 0;
  std::int64_t rested = 0;
  std::int64_t cancelled = 0;
  std::vector<RefTrade> trades;
  std::vector<std::uint64_t> stp_cancelled_resting;
};

// L3 row used to compare full book state: (side, price, arrival-order position, id, remaining).
struct RefRow {
  Side side;
  std::int64_t price;
  std::uint64_t id;
  std::int64_t remaining;
  friend bool operator==(const RefRow&, const RefRow&) = default;
};

class ReferenceBook {
 public:
  RefResult submit(std::uint64_t id, std::uint64_t owner, Side side, bool is_market, bool ioc,
                   SelfTradePrevention stp, std::int64_t price, std::int64_t quantity) {
    RefResult result;
    const bool valid_quantity = quantity > 0 && quantity <= kMaxOrderQuantity;
    const bool valid_price = is_market ? price == 0 : (price > 0 && price <= kMaxPrice);
    if (!valid_quantity || !valid_price) {
      result.rejected = true;
      return result;
    }
    result.id = id;
    std::int64_t remaining = quantity;
    bool stp_stop = false;
    while (remaining > 0 && !stp_stop) {
      auto best = find_best(opposite(side));
      if (!best) {
        break;
      }
      RefOrder& resting = orders_[*best];
      const bool marketable =
          is_market || (side == Side::Buy ? resting.price <= price : resting.price >= price);
      if (!marketable) {
        break;
      }
      if (stp != SelfTradePrevention::Allow && owner != kAnonymousTrader &&
          resting.owner == owner) {
        if (stp == SelfTradePrevention::CancelIncoming) {
          stp_stop = true;
          break;
        }
        result.stp_cancelled_resting.push_back(resting.id);
        orders_.erase(orders_.begin() + static_cast<std::ptrdiff_t>(*best));
        continue;
      }
      const std::int64_t fill = std::min(remaining, resting.remaining);
      RefTrade trade{resting.price, fill, 0, 0, 0, 0};
      if (side == Side::Buy) {
        trade.buy_id = id;
        trade.buyer = owner;
        trade.sell_id = resting.id;
        trade.seller = resting.owner;
      } else {
        trade.sell_id = id;
        trade.seller = owner;
        trade.buy_id = resting.id;
        trade.buyer = resting.owner;
      }
      result.trades.push_back(trade);
      remaining -= fill;
      resting.remaining -= fill;
      if (resting.remaining == 0) {
        orders_.erase(orders_.begin() + static_cast<std::ptrdiff_t>(*best));
      }
    }
    result.filled = quantity - remaining;
    if (remaining > 0) {
      if (!is_market && !ioc && !stp_stop) {
        orders_.push_back(RefOrder{id, owner, side, price, remaining, next_arrival_++});
        result.rested = remaining;
      } else {
        result.cancelled = remaining;
      }
    }
    return result;
  }

  // Returns the cancelled quantity, or nullopt if the order is not resting.
  std::optional<std::int64_t> cancel(std::uint64_t id) {
    for (std::size_t i = 0; i < orders_.size(); ++i) {
      if (orders_[i].id == id) {
        const std::int64_t remaining = orders_[i].remaining;
        orders_.erase(orders_.begin() + static_cast<std::ptrdiff_t>(i));
        return remaining;
      }
    }
    return std::nullopt;
  }

  // Full book state ordered best-price-first, then by time priority, bids before asks.
  [[nodiscard]] std::vector<RefRow> rows() const {
    std::vector<RefOrder> sorted = orders_;
    std::sort(sorted.begin(), sorted.end(), [](const RefOrder& a, const RefOrder& b) {
      if (a.side != b.side) {
        return a.side == Side::Buy;
      }
      if (a.price != b.price) {
        return a.side == Side::Buy ? a.price > b.price : a.price < b.price;
      }
      return a.arrival < b.arrival;
    });
    std::vector<RefRow> out;
    out.reserve(sorted.size());
    for (const auto& order : sorted) {
      out.push_back(RefRow{order.side, order.price, order.id, order.remaining});
    }
    return out;
  }

  [[nodiscard]] const std::vector<RefOrder>& orders() const { return orders_; }

 private:
  [[nodiscard]] std::optional<std::size_t> find_best(Side side) const {
    std::optional<std::size_t> best;
    for (std::size_t i = 0; i < orders_.size(); ++i) {
      const RefOrder& candidate = orders_[i];
      if (candidate.side != side) {
        continue;
      }
      if (!best) {
        best = i;
        continue;
      }
      const RefOrder& current = orders_[*best];
      const bool better_price =
          side == Side::Buy ? candidate.price > current.price : candidate.price < current.price;
      const bool same_price_earlier =
          candidate.price == current.price && candidate.arrival < current.arrival;
      if (better_price || same_price_earlier) {
        best = i;
      }
    }
    return best;
  }

  std::vector<RefOrder> orders_;
  std::uint64_t next_arrival_ = 0;
};

}  // namespace udeshi::testing
