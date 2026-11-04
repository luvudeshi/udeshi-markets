#pragma once

#include <cstdint>

#include "udeshi/matching_engine.hpp"
#include "udeshi/order_book.hpp"

namespace udeshi::bench {

// Fingerprints of a replay, compared with those the recording produced.
//
// add_light() costs a few instructions per operation: the timed replays use it, so that no
// result can be optimised away and every replay is checked without slowing the measurement.
// add_full() covers every field of every result, trades included: the verification replay
// uses it, together with book_digest() of the final book, so that a replay that differs from
// the recording in any way (another price, quantity or counterparty order) fails.
class Checksum {
 public:
  [[nodiscard]] std::uint64_t value() const noexcept { return value_; }

  void add_light(const SubmitResult& result) noexcept {
    mix(result.sequence);
    mix(result.order_id);
    mix(static_cast<std::uint64_t>(result.filled_quantity));
    mix(result.trades.size());
  }
  void add_light(const CancelResult& result) noexcept {
    mix(result.sequence);
    mix(static_cast<std::uint64_t>(result.cancelled_quantity));
  }

  void add_full(const SubmitResult& result) noexcept {
    mix(result.order_id);
    mix(result.sequence);
    mix(static_cast<std::uint64_t>(result.status));
    mix(static_cast<std::uint64_t>(result.reject_reason));
    mix(static_cast<std::uint64_t>(result.cancel_reason));
    mix(static_cast<std::uint64_t>(result.requested_quantity));
    mix(static_cast<std::uint64_t>(result.filled_quantity));
    mix(static_cast<std::uint64_t>(result.resting_quantity));
    mix(static_cast<std::uint64_t>(result.cancelled_quantity));
    for (const Trade& trade : result.trades) {
      mix(trade.id);
      mix(trade.sequence);
      mix(static_cast<std::uint64_t>(trade.price));
      mix(static_cast<std::uint64_t>(trade.quantity));
      mix(trade.buy_order_id);
      mix(trade.sell_order_id);
      mix(trade.buyer);
      mix(trade.seller);
    }
    for (const RestingCancel& cancel : result.resting_cancels) {
      mix(cancel.order_id);
      mix(static_cast<std::uint64_t>(cancel.cancelled_quantity));
    }
  }
  void add_full(const CancelResult& result) noexcept {
    mix(static_cast<std::uint64_t>(result.outcome));
    mix(result.sequence);
    mix(result.order_id);
    mix(result.owner);
    mix(static_cast<std::uint64_t>(result.side));
    mix(static_cast<std::uint64_t>(result.price));
    mix(static_cast<std::uint64_t>(result.cancelled_quantity));
  }

  // Every price level of both sides: price, quantity and order count.
  void add_book(const OrderBook& book) {
    for (const Side side : {Side::Buy, Side::Sell}) {
      mix(book.level_count(side));
      for (const auto& [price, level] : book.side(side)) {
        mix(static_cast<std::uint64_t>(price));
        mix(static_cast<std::uint64_t>(level.total_quantity()));
        mix(level.order_count());
      }
    }
  }

 private:
  // FNV-1a style mixing of whole words: cheap, and order-sensitive.
  void mix(std::uint64_t word) noexcept { value_ = (value_ ^ word) * 0x100000001B3ULL; }

  std::uint64_t value_ = 0xCBF29CE484222325ULL;
};

}  // namespace udeshi::bench
