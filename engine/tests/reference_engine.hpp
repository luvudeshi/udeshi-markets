#pragma once

// A deliberately naive, obviously-correct model of the documented MatchingEngine contract,
// used as a test oracle.
//
// Each instrument's resting orders live in a flat vector, and the best counterparty is found
// by a linear scan over (price, arrival sequence). The model assigns its own order ids, trade
// ids and sequence numbers from the documented rules, validates input with its own checks on
// the raw enum values, and derives every reported status and reason independently. It shares
// no code with the production engine (only the plain request struct and enum vocabulary), so
// agreement between the two on long random order streams is strong evidence that the
// optimised data structures implement exactly the specified semantics.

#include <algorithm>
#include <cstdint>
#include <optional>
#include <ostream>
#include <vector>

#include "udeshi/order.hpp"
#include "udeshi/types.hpp"

namespace udeshi::testing {

struct RefTrade {
  std::uint64_t id = 0;
  std::uint64_t sequence = 0;
  std::uint32_t instrument = 0;
  std::int64_t price = 0;
  std::int64_t quantity = 0;
  Side aggressor_side = Side::Buy;
  std::uint64_t buy_order_id = 0;
  std::uint64_t sell_order_id = 0;
  std::uint64_t buyer = 0;
  std::uint64_t seller = 0;
  std::int64_t timestamp = 0;
  friend bool operator==(const RefTrade&, const RefTrade&) = default;
};

struct RefStpCancel {
  std::uint64_t order_id = 0;
  std::uint64_t sequence = 0;
  std::uint64_t owner = 0;
  Side side = Side::Buy;
  std::int64_t price = 0;
  std::int64_t quantity = 0;
  friend bool operator==(const RefStpCancel&, const RefStpCancel&) = default;
};

struct RefSubmit {
  std::uint64_t sequence = 0;
  std::uint64_t order_id = 0;
  OrderStatus status = OrderStatus::Rejected;
  RejectReason reject_reason = RejectReason::None;
  CancelReason cancel_reason = CancelReason::None;
  std::int64_t requested = 0;
  std::int64_t filled = 0;
  std::int64_t rested = 0;
  std::int64_t cancelled = 0;
  std::vector<RefTrade> trades;
  std::vector<RefStpCancel> stp_cancels;
  friend bool operator==(const RefSubmit&, const RefSubmit&) = default;
};

struct RefCancel {
  CancelOutcome outcome = CancelOutcome::OrderNotFound;
  std::uint64_t sequence = 0;
  std::uint64_t order_id = 0;
  std::uint64_t owner = 0;
  Side side = Side::Buy;
  std::int64_t price = 0;
  std::int64_t quantity = 0;
  friend bool operator==(const RefCancel&, const RefCancel&) = default;
};

// One resting order, as it should appear in the L3 book (best price first, then time).
struct RefRow {
  Side side = Side::Buy;
  std::int64_t price = 0;
  std::uint64_t id = 0;
  std::uint64_t owner = 0;
  std::uint64_t sequence = 0;
  std::int64_t original = 0;
  std::int64_t remaining = 0;
  std::int64_t timestamp = 0;
  friend bool operator==(const RefRow&, const RefRow&) = default;
};

inline std::ostream& operator<<(std::ostream& os, const RefTrade& t) {
  return os << "Trade{id=" << t.id << " seq=" << t.sequence << " inst=" << t.instrument << " "
            << t.quantity << "@" << t.price << " aggr=" << to_string(t.aggressor_side)
            << " buy=" << t.buy_order_id << "/" << t.buyer << " sell=" << t.sell_order_id << "/"
            << t.seller << " ts=" << t.timestamp << "}";
}
inline std::ostream& operator<<(std::ostream& os, const RefStpCancel& c) {
  return os << "StpCancel{id=" << c.order_id << " seq=" << c.sequence << " owner=" << c.owner
            << " " << to_string(c.side) << " " << c.quantity << "@" << c.price << "}";
}
inline std::ostream& operator<<(std::ostream& os, const RefSubmit& r) {
  os << "Submit{seq=" << r.sequence << " id=" << r.order_id << " " << to_string(r.status)
     << " reject=" << to_string(r.reject_reason) << " cancel=" << to_string(r.cancel_reason)
     << " req=" << r.requested << " filled=" << r.filled << " rested=" << r.rested
     << " cancelled=" << r.cancelled << " trades=[";
  for (const auto& t : r.trades) os << t << " ";
  os << "] stp=[";
  for (const auto& c : r.stp_cancels) os << c << " ";
  return os << "]}";
}
inline std::ostream& operator<<(std::ostream& os, const RefCancel& c) {
  return os << "Cancel{" << to_string(c.outcome) << " seq=" << c.sequence << " id=" << c.order_id
            << " owner=" << c.owner << " " << to_string(c.side) << " " << c.quantity << "@"
            << c.price << "}";
}
inline std::ostream& operator<<(std::ostream& os, const RefRow& r) {
  return os << "Row{" << to_string(r.side) << " " << r.price << " id=" << r.id
            << " owner=" << r.owner << " seq=" << r.sequence << " " << r.remaining << "/"
            << r.original << " ts=" << r.timestamp << "}";
}

class ReferenceEngine {
 public:
  std::uint32_t add_instrument() {
    books_.emplace_back();
    return static_cast<std::uint32_t>(books_.size() - 1);
  }

  [[nodiscard]] std::uint64_t last_sequence() const { return next_sequence_ - 1; }

  RefSubmit submit(const OrderRequest& request) {
    RefSubmit result;
    result.sequence = next_sequence_++;
    result.requested = request.quantity;
    result.reject_reason = validate(request);
    if (result.reject_reason != RejectReason::None) {
      result.status = OrderStatus::Rejected;
      return result;
    }
    result.order_id = next_order_id_++;
    std::vector<RefOrder>& orders = books_[request.instrument];
    const bool is_market = request.type == OrderType::Market;
    const bool is_buy = request.side == Side::Buy;

    std::int64_t remaining = request.quantity;
    bool stopped_by_stp = false;
    while (remaining > 0) {
      const auto best = find_best(orders, is_buy ? Side::Sell : Side::Buy);
      if (!best) {
        break;
      }
      RefOrder& resting = orders[*best];
      const bool marketable =
          is_market || (is_buy ? resting.price <= request.price : resting.price >= request.price);
      if (!marketable) {
        break;
      }
      const bool same_owner = request.owner != 0 && resting.owner == request.owner;
      if (same_owner && request.self_trade_prevention == SelfTradePrevention::CancelIncoming) {
        stopped_by_stp = true;
        break;
      }
      if (same_owner && request.self_trade_prevention == SelfTradePrevention::CancelResting) {
        result.stp_cancels.push_back(RefStpCancel{resting.id, next_sequence_++, resting.owner,
                                                  resting.side, resting.price,
                                                  resting.remaining});
        orders.erase(orders.begin() + static_cast<std::ptrdiff_t>(*best));
        continue;
      }
      const std::int64_t fill = std::min(remaining, resting.remaining);
      RefTrade trade;
      trade.id = next_trade_id_++;
      trade.sequence = next_sequence_++;
      trade.instrument = request.instrument;
      trade.price = resting.price;
      trade.quantity = fill;
      trade.aggressor_side = request.side;
      trade.buy_order_id = is_buy ? result.order_id : resting.id;
      trade.sell_order_id = is_buy ? resting.id : result.order_id;
      trade.buyer = is_buy ? request.owner : resting.owner;
      trade.seller = is_buy ? resting.owner : request.owner;
      trade.timestamp = request.timestamp;
      result.trades.push_back(trade);
      remaining -= fill;
      resting.remaining -= fill;
      if (resting.remaining == 0) {
        orders.erase(orders.begin() + static_cast<std::ptrdiff_t>(*best));
      }
    }

    result.filled = request.quantity - remaining;
    if (remaining == 0) {
      result.status = OrderStatus::Filled;
    } else if (stopped_by_stp) {
      result.status = OrderStatus::Cancelled;
      result.cancel_reason = CancelReason::SelfTradePrevention;
      result.cancelled = remaining;
    } else if (!is_market && request.time_in_force == TimeInForce::GoodTillCancel) {
      orders.push_back(RefOrder{result.order_id, request.owner, request.side, request.price,
                                request.quantity, remaining, result.sequence, request.timestamp});
      result.status = OrderStatus::Resting;
      result.rested = remaining;
    } else {
      result.status = OrderStatus::Cancelled;
      result.cancel_reason = CancelReason::NoLiquidity;
      result.cancelled = remaining;
    }
    return result;
  }

  RefCancel cancel(std::uint32_t instrument, std::uint64_t order_id) {
    RefCancel result;
    result.sequence = next_sequence_++;
    result.order_id = order_id;
    if (instrument >= books_.size()) {
      result.outcome = CancelOutcome::UnknownInstrument;
      return result;
    }
    std::vector<RefOrder>& orders = books_[instrument];
    for (std::size_t i = 0; i < orders.size(); ++i) {
      if (orders[i].id == order_id) {
        result.outcome = CancelOutcome::Cancelled;
        result.owner = orders[i].owner;
        result.side = orders[i].side;
        result.price = orders[i].price;
        result.quantity = orders[i].remaining;
        orders.erase(orders.begin() + static_cast<std::ptrdiff_t>(i));
        return result;
      }
    }
    result.outcome = CancelOutcome::OrderNotFound;
    return result;
  }

  // Full book state ordered bids then asks, best price first, then by arrival.
  [[nodiscard]] std::vector<RefRow> rows(std::uint32_t instrument) const {
    std::vector<RefOrder> sorted = books_.at(instrument);
    std::sort(sorted.begin(), sorted.end(), [](const RefOrder& a, const RefOrder& b) {
      if (a.side != b.side) {
        return a.side == Side::Buy;
      }
      if (a.price != b.price) {
        return a.side == Side::Buy ? a.price > b.price : a.price < b.price;
      }
      return a.sequence < b.sequence;
    });
    std::vector<RefRow> out;
    out.reserve(sorted.size());
    for (const RefOrder& o : sorted) {
      out.push_back(RefRow{o.side, o.price, o.id, o.owner, o.sequence, o.original, o.remaining,
                           o.timestamp});
    }
    return out;
  }

  // Ids of resting orders on one instrument, in storage order (used to pick cancel targets).
  [[nodiscard]] std::vector<std::uint64_t> resting_ids(std::uint32_t instrument) const {
    std::vector<std::uint64_t> ids;
    for (const RefOrder& o : books_.at(instrument)) {
      ids.push_back(o.id);
    }
    return ids;
  }

  [[nodiscard]] std::uint64_t next_order_id() const { return next_order_id_; }

 private:
  struct RefOrder {
    std::uint64_t id;
    std::uint64_t owner;
    Side side;
    std::int64_t price;
    std::int64_t original;
    std::int64_t remaining;
    std::uint64_t sequence;
    std::int64_t timestamp;
  };

  // Validation re-derived from the documented contract, in the documented order, using raw
  // integer comparisons rather than the engine's is_valid() helpers.
  [[nodiscard]] RejectReason validate(const OrderRequest& r) const {
    if (r.instrument >= books_.size()) return RejectReason::UnknownInstrument;
    if (static_cast<int>(r.side) > 1) return RejectReason::InvalidSide;
    if (static_cast<int>(r.type) > 1) return RejectReason::InvalidOrderType;
    if (static_cast<int>(r.time_in_force) > 1) return RejectReason::InvalidTimeInForce;
    if (static_cast<int>(r.self_trade_prevention) > 2) {
      return RejectReason::InvalidSelfTradePrevention;
    }
    if (r.quantity < 1 || r.quantity > 1'000'000'000) return RejectReason::InvalidQuantity;
    if (r.type == OrderType::Market) {
      return r.price == 0 ? RejectReason::None : RejectReason::MarketOrderWithPrice;
    }
    if (r.price < 1 || r.price > 1'000'000'000) return RejectReason::InvalidPrice;
    return RejectReason::None;
  }

  [[nodiscard]] static std::optional<std::size_t> find_best(const std::vector<RefOrder>& orders,
                                                            Side side) {
    std::optional<std::size_t> best;
    for (std::size_t i = 0; i < orders.size(); ++i) {
      const RefOrder& candidate = orders[i];
      if (candidate.side != side) {
        continue;
      }
      if (!best) {
        best = i;
        continue;
      }
      const RefOrder& current = orders[*best];
      const bool better_price =
          side == Side::Buy ? candidate.price > current.price : candidate.price < current.price;
      const bool same_price_earlier =
          candidate.price == current.price && candidate.sequence < current.sequence;
      if (better_price || same_price_earlier) {
        best = i;
      }
    }
    return best;
  }

  std::vector<std::vector<RefOrder>> books_;
  std::uint64_t next_sequence_ = 1;
  std::uint64_t next_order_id_ = 1;
  std::uint64_t next_trade_id_ = 1;
};

}  // namespace udeshi::testing
