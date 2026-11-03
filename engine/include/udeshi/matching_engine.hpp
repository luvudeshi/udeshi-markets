#pragma once

#include <cstddef>
#include <memory>
#include <vector>

#include "udeshi/order.hpp"
#include "udeshi/order_book.hpp"
#include "udeshi/trade.hpp"
#include "udeshi/types.hpp"

namespace udeshi {

// A resting order removed by the engine as a side effect of someone else's submission
// (currently only self-trade prevention). The owner of that order needs to be told.
struct RestingCancel {
  OrderId order_id = kInvalidOrderId;
  Sequence sequence = 0;  // Position of the cancellation in the global event order.
  TraderId owner = kAnonymousTrader;
  Side side = Side::Buy;
  Price price = 0;
  Quantity cancelled_quantity = 0;
  CancelReason reason = CancelReason::None;

  friend bool operator==(const RestingCancel&, const RestingCancel&) = default;
};

// Everything that happened as a consequence of one submission.
//
// For an accepted order: filled_quantity + resting_quantity + cancelled_quantity equals the
// requested quantity exactly. For a rejected order all three are zero.
struct SubmitResult {
  OrderId order_id = kInvalidOrderId;  // kInvalidOrderId if rejected.
  Sequence sequence = 0;               // Sequence number of the submission event itself.
  OrderStatus status = OrderStatus::Rejected;
  RejectReason reject_reason = RejectReason::None;
  CancelReason cancel_reason = CancelReason::None;  // Why any remainder was cancelled.
  Quantity requested_quantity = 0;
  Quantity filled_quantity = 0;
  Quantity resting_quantity = 0;
  Quantity cancelled_quantity = 0;
  // Trades and self-trade-prevention cancels in the order they happened. Each consumes one
  // sequence number, so merging the two vectors by `sequence` reproduces the exact interleaving.
  std::vector<Trade> trades;
  std::vector<RestingCancel> resting_cancels;

  [[nodiscard]] bool accepted() const noexcept { return status != OrderStatus::Rejected; }
};

struct CancelResult {
  CancelOutcome outcome = CancelOutcome::OrderNotFound;
  Sequence sequence = 0;
  OrderId order_id = kInvalidOrderId;
  TraderId owner = kAnonymousTrader;
  Side side = Side::Buy;
  Price price = 0;
  Quantity cancelled_quantity = 0;  // Quantity that was still resting when cancelled.

  [[nodiscard]] bool cancelled() const noexcept { return outcome == CancelOutcome::Cancelled; }
};

// Deterministic continuous-auction matching engine over a set of independent instruments.
//
// Responsibilities: input validation, assignment of order ids / trade ids / sequence numbers,
// price-time priority matching, and cancellation. Given the same sequence of calls it produces
// exactly the same ids, trades and book states: there is no wall-clock time, randomness, or
// iteration over unordered containers anywhere on the matching path.
//
// Sequencing contract: every submit() and cancel() call consumes exactly one sequence number
// (including rejected submissions and failed cancels), and so does every trade and every
// self-trade-prevention cancel, in the order they occur. Order ids are assigned densely to
// accepted orders only; trade ids are dense. All counters are global across instruments.
//
// Failure model: submit() and cancel() are noexcept. Invalid input is reported through the
// result, never by throwing. The only possible exception is allocation failure in the middle
// of matching, after the book has already been modified; continuing would leave reported
// results inconsistent with the book, so the process terminates instead (fail-fast).
//
// Not thread-safe: callers serialise access (the simulation drives it from a single thread,
// which is also what real exchanges do per matching shard).
class MatchingEngine {
 public:
  MatchingEngine() = default;
  MatchingEngine(const MatchingEngine&) = delete;
  MatchingEngine& operator=(const MatchingEngine&) = delete;
  MatchingEngine(MatchingEngine&&) noexcept = default;
  MatchingEngine& operator=(MatchingEngine&&) noexcept = default;
  ~MatchingEngine() = default;

  // Creates a new empty order book and returns its dense instrument id (0, 1, 2, ...).
  InstrumentId add_instrument();
  [[nodiscard]] std::size_t instrument_count() const noexcept { return books_.size(); }
  [[nodiscard]] bool has_instrument(InstrumentId instrument) const noexcept {
    return instrument < books_.size();
  }

  // Validates and processes an order. Problems with the input are reported through
  // SubmitResult::reject_reason.
  SubmitResult submit(const OrderRequest& request) noexcept;

  // Cancels the remaining quantity of a resting order.
  CancelResult cancel(InstrumentId instrument, OrderId order_id) noexcept;

  // Throws std::out_of_range for an unknown instrument.
  [[nodiscard]] const OrderBook& book(InstrumentId instrument) const;

  [[nodiscard]] Sequence last_sequence() const noexcept { return next_sequence_ - 1; }

 private:
  [[nodiscard]] RejectReason validate(const OrderRequest& request) const noexcept;
  void match(OrderBook& book, const OrderRequest& request, OrderId order_id,
             SubmitResult& result);

  std::vector<std::unique_ptr<OrderBook>> books_;
  OrderId next_order_id_ = 1;
  TradeId next_trade_id_ = 1;
  Sequence next_sequence_ = 1;
};

}  // namespace udeshi
