#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>
#include <string_view>
#include <vector>

#include "udeshi/matching_engine.hpp"
#include "udeshi/order.hpp"

namespace udeshi::bench {

// What an operation is, for the per-kind latency breakdown.
enum class OpKind : std::uint8_t {
  Add,            // A limit order that rests without trading (a maker).
  Cancel,         // Cancellation of a resting order.
  CancelMissing,  // Cancellation of an order that is not on the book.
  Take,           // A marketable IOC limit order (a taker).
  Market,         // A market order.
  Sweep,          // A market order that empties several price levels.
};
inline constexpr std::size_t kOpKinds = 6;
[[nodiscard]] std::string_view to_string(OpKind kind) noexcept;

struct Op {
  OpKind kind = OpKind::Add;
  bool is_cancel = false;
  OrderId cancel_id = kInvalidOrderId;  // For cancels (instrument 0).
  OrderRequest request;                 // For submissions.
};

// One benchmark: the operations that build the starting book (replayed before every
// measurement, untimed), the operations that are measured, and the state the engine must end
// in. Workloads are recorded against a real engine (see Planner), so every cancel names an
// order that is resting at that moment; the expected end state checks that the replay
// reproduced the recording exactly.
struct Workload {
  std::vector<Op> setup;
  std::vector<Op> measured;
  Sequence final_sequence = 0;
  std::size_t final_order_count = 0;
  std::size_t start_order_count = 0;
  std::size_t start_levels_per_side = 0;
};

struct WorkloadSpec {
  std::string name;   // e.g. "book_size/100000"
  std::string group;  // book_size, price_levels, patterns, cancellation, exchange
  std::string description;
  bool through_exchange = false;  // Replay through Exchange (market data) instead of the engine.
  std::function<Workload()> build;
};

// Every workload, at full size or (quick) at a size for smoke tests in CI.
[[nodiscard]] std::vector<WorkloadSpec> workload_specs(bool quick);

// The requests that build a book of `orders` resting orders, alternately bids and asks, over
// `levels` prices per side next to the mid (what every workload's setup starts from). Built
// directly, without a planning engine, so measuring memory sees nothing else.
[[nodiscard]] std::vector<OrderRequest> resting_orders(std::size_t orders, std::size_t levels);

// The mid price of every workload, in ticks.
inline constexpr Price kMidPrice = 100'000;

}  // namespace udeshi::bench
