// Randomised property-based and differential tests for the matching engine.
//
// Each test case drives the engine with a long, seeded pseudo-random stream of submissions and
// cancellations (limit / market / IOC, both sides, self-trade-prevention modes, invalid input)
// and checks, after every operation:
//
//   * Differential equivalence: every trade, every order outcome, and (periodically) the full
//     L3 book state match a naive reference implementation exactly.
//   * Economic invariants: no execution at a price worse than either party's limit; trades
//     execute at the resting order's price; an order never executes more than its quantity;
//     cancelled orders never trade; shares and cash are conserved across all participants.
//   * Structural invariants: OrderBook::check_invariants() (cached totals, FIFO links,
//     sequence ordering, id index, not crossed).
//   * Sequencing: ids and sequence numbers are strictly increasing.
//
// Generators use SplitMix64 rather than <random> distributions, whose output is
// implementation-defined; this keeps failures reproducible on every platform.

#include <gtest/gtest.h>

#include <cstdint>
#include <map>
#include <set>
#include <sstream>
#include <tuple>
#include <unordered_map>
#include <vector>

#include "reference_book.hpp"
#include "test_helpers.hpp"
#include "udeshi/matching_engine.hpp"

namespace udeshi {
namespace {

using testing::ReferenceBook;
using testing::RefRow;
using testing::RefTrade;

class SplitMix64 {
 public:
  explicit SplitMix64(std::uint64_t seed) : state_(seed) {}
  std::uint64_t next() {
    std::uint64_t z = (state_ += 0x9E3779B97F4A7C15ULL);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
  }
  // Uniform-ish integer in [lo, hi]. The modulo bias is irrelevant for test generation.
  std::int64_t between(std::int64_t lo, std::int64_t hi) {
    return lo + static_cast<std::int64_t>(next() % static_cast<std::uint64_t>(hi - lo + 1));
  }
  bool chance(int percent) { return between(0, 99) < percent; }

 private:
  std::uint64_t state_;
};

struct StreamConfig {
  std::uint64_t seed;
  int operations;
  std::int64_t price_half_width;  // Narrow => deep FIFO queues; wide => many levels.
  int owners;                     // Few owners => frequent self-trade-prevention events.
};

std::ostream& operator<<(std::ostream& os, const StreamConfig& c) {
  return os << "seed=" << c.seed << " ops=" << c.operations << " width=" << c.price_half_width
            << " owners=" << c.owners;
}

enum class OpKind { Submit, Cancel };

struct Operation {
  OpKind kind = OpKind::Submit;
  OrderRequest request;
  OrderId cancel_id = kInvalidOrderId;
};

// Generates the next operation. Cancels mostly target orders that are actually resting (taken
// from the reference book) but sometimes target stale or never-issued ids.
Operation next_operation(SplitMix64& rng, const StreamConfig& config, std::int64_t& mid,
                         const ReferenceBook& reference, OrderId next_id_hint) {
  Operation op;
  mid = std::clamp<std::int64_t>(mid + rng.between(-1, 1), 200, 5000);
  const int roll = static_cast<int>(rng.between(0, 99));
  if (roll < 25) {
    op.kind = OpKind::Cancel;
    const auto& resting = reference.orders();
    if (!resting.empty() && rng.chance(85)) {
      op.cancel_id = resting[static_cast<std::size_t>(
                                 rng.between(0, static_cast<std::int64_t>(resting.size()) - 1))]
                         .id;
    } else {
      op.cancel_id = static_cast<OrderId>(rng.between(0, static_cast<std::int64_t>(next_id_hint) + 3));
    }
    return op;
  }

  OrderRequest& r = op.request;
  r.owner = static_cast<TraderId>(rng.between(0, config.owners));  // 0 = anonymous.
  r.side = rng.chance(50) ? Side::Buy : Side::Sell;
  r.quantity = rng.chance(5) ? rng.between(200, 2000) : rng.between(1, 100);
  const int stp_roll = static_cast<int>(rng.between(0, 99));
  r.self_trade_prevention = stp_roll < 60   ? SelfTradePrevention::Allow
                            : stp_roll < 80 ? SelfTradePrevention::CancelResting
                                            : SelfTradePrevention::CancelIncoming;
  r.timestamp = static_cast<Timestamp>(next_id_hint) * 1000;

  if (roll < 33) {
    r.type = OrderType::Market;
    r.time_in_force = rng.chance(50) ? TimeInForce::ImmediateOrCancel : TimeInForce::GoodTillCancel;
    r.price = 0;
  } else {
    r.type = OrderType::Limit;
    r.time_in_force = roll < 40 ? TimeInForce::ImmediateOrCancel : TimeInForce::GoodTillCancel;
    // Buys centred slightly below mid and sells slightly above, overlapping enough that a
    // large fraction of orders cross.
    const std::int64_t skew = r.side == Side::Buy ? -1 : 1;
    r.price = mid + skew + rng.between(-config.price_half_width, config.price_half_width);
  }

  if (rng.chance(2)) {  // Occasionally inject invalid input.
    switch (rng.between(0, 3)) {
      case 0: r.quantity = -rng.between(0, 5); break;
      case 1: r.quantity = kMaxOrderQuantity + 1; break;
      case 2: r.type = OrderType::Limit; r.price = -rng.between(0, 5); break;
      default: r.type = OrderType::Market; r.price = 7; break;
    }
  }
  return op;
}

std::vector<RefRow> engine_rows(const OrderBook& book) {
  std::vector<RefRow> rows;
  for (const Side side : {Side::Buy, Side::Sell}) {
    for (const auto& [price, level] : book.side(side)) {
      for (const Order* order = level.front(); order != nullptr; order = order->next()) {
        rows.push_back(RefRow{side, price, order->id, order->remaining_quantity});
      }
    }
  }
  return rows;
}

RefTrade to_ref(const Trade& t) {
  return RefTrade{t.price, t.quantity, t.buy_order_id, t.sell_order_id, t.buyer, t.seller};
}

// Tracks every accepted order and every execution to check economic invariants.
class Ledger {
 public:
  void on_accepted(OrderId id, const OrderRequest& request) {
    ASSERT_TRUE(orders_.emplace(id, Info{request, 0}).second) << "duplicate order id " << id;
  }

  void on_cancelled(OrderId id) { cancelled_.insert(id); }

  void on_trade(const Trade& trade, const std::unordered_map<OrderId, Price>& resting_prices) {
    ASSERT_GT(trade.quantity, 0);
    ASSERT_FALSE(cancelled_.contains(trade.buy_order_id)) << "cancelled buy order traded";
    ASSERT_FALSE(cancelled_.contains(trade.sell_order_id)) << "cancelled sell order traded";
    ASSERT_NE(trade.buy_order_id, trade.sell_order_id);

    Info& buy = orders_.at(trade.buy_order_id);
    Info& sell = orders_.at(trade.sell_order_id);
    ASSERT_EQ(buy.request.side, Side::Buy);
    ASSERT_EQ(sell.request.side, Side::Sell);
    if (buy.request.type == OrderType::Limit) {
      ASSERT_LE(trade.price, buy.request.price) << "buy executed above its limit";
    }
    if (sell.request.type == OrderType::Limit) {
      ASSERT_GE(trade.price, sell.request.price) << "sell executed below its limit";
    }
    // Execution price is always the resting (passive) order's limit price.
    ASSERT_EQ(trade.price, resting_prices.at(trade.resting_order_id()));

    buy.filled += trade.quantity;
    sell.filled += trade.quantity;
    ASSERT_LE(buy.filled, buy.request.quantity) << "buy over-filled";
    ASSERT_LE(sell.filled, sell.request.quantity) << "sell over-filled";

    ASSERT_GT(trade.sequence, last_trade_sequence_);
    ASSERT_GT(trade.id, last_trade_id_);
    last_trade_sequence_ = trade.sequence;
    last_trade_id_ = trade.id;

    const std::int64_t notional = trade.price * trade.quantity;
    shares_[trade.buyer] += trade.quantity;
    shares_[trade.seller] -= trade.quantity;
    cash_[trade.buyer] -= notional;
    cash_[trade.seller] += notional;
  }

  void check_conservation() const {
    std::int64_t total_shares = 0;
    std::int64_t total_cash = 0;
    for (const auto& [owner, shares] : shares_) {
      total_shares += shares;
    }
    for (const auto& [owner, cash] : cash_) {
      total_cash += cash;
    }
    EXPECT_EQ(total_shares, 0) << "shares created or destroyed by matching";
    EXPECT_EQ(total_cash, 0) << "cash created or destroyed by matching";
  }

 private:
  struct Info {
    OrderRequest request;
    Quantity filled;
  };
  std::unordered_map<OrderId, Info> orders_;
  std::set<OrderId> cancelled_;
  std::map<TraderId, std::int64_t> shares_;
  std::map<TraderId, std::int64_t> cash_;
  Sequence last_trade_sequence_ = 0;
  TradeId last_trade_id_ = 0;
};

class RandomizedMatchingTest : public ::testing::TestWithParam<StreamConfig> {};

TEST_P(RandomizedMatchingTest, MatchesReferenceAndPreservesInvariants) {
  const StreamConfig config = GetParam();
  SplitMix64 rng(config.seed);
  MatchingEngine engine;
  const InstrumentId instrument = engine.add_instrument();
  ReferenceBook reference;
  Ledger ledger;
  std::unordered_map<OrderId, Price> resting_prices;  // Limit price of every accepted order.
  std::int64_t mid = 1000;
  OrderId next_id_hint = 1;
  Sequence last_sequence = 0;
  std::size_t total_trades = 0;
  std::size_t successful_cancels = 0;

  for (int step = 0; step < config.operations; ++step) {
    SCOPED_TRACE(::testing::Message() << config << " step=" << step);
    Operation op = next_operation(rng, config, mid, reference, next_id_hint);

    if (op.kind == OpKind::Cancel) {
      const CancelResult result = engine.cancel(instrument, op.cancel_id);
      const auto expected = reference.cancel(op.cancel_id);
      ASSERT_GT(result.sequence, last_sequence);
      last_sequence = result.sequence;
      ASSERT_EQ(result.cancelled(), expected.has_value());
      if (expected) {
        ASSERT_EQ(result.cancelled_quantity, *expected);
        ledger.on_cancelled(op.cancel_id);
        ++successful_cancels;
      }
    } else {
      op.request.instrument = instrument;
      const SubmitResult result = engine.submit(op.request);
      const bool is_market = op.request.type == OrderType::Market;
      const bool is_ioc = op.request.time_in_force == TimeInForce::ImmediateOrCancel;
      const auto expected =
          reference.submit(next_id_hint, op.request.owner, op.request.side, is_market,
                           is_ioc, op.request.self_trade_prevention, op.request.price,
                           op.request.quantity);

      ASSERT_GT(result.sequence, last_sequence);
      last_sequence = result.sequence;
      ASSERT_EQ(!result.accepted(), expected.rejected);
      if (!result.accepted()) {
        ASSERT_TRUE(result.trades.empty());
        ASSERT_EQ(result.filled_quantity + result.resting_quantity + result.cancelled_quantity, 0);
        continue;
      }
      ASSERT_EQ(result.order_id, next_id_hint) << "order ids must be dense and sequential";
      ++next_id_hint;

      ledger.on_accepted(result.order_id, op.request);
      resting_prices[result.order_id] = op.request.price;

      std::vector<std::uint64_t> stp_ids;
      for (const RestingCancel& cancel : result.resting_cancels) {
        stp_ids.push_back(cancel.order_id);
        ledger.on_cancelled(cancel.order_id);
      }
      ASSERT_EQ(stp_ids, expected.stp_cancelled_resting);

      std::vector<RefTrade> actual_trades;
      for (const Trade& trade : result.trades) {
        actual_trades.push_back(to_ref(trade));
        ledger.on_trade(trade, resting_prices);
        ASSERT_GT(trade.sequence, last_sequence);
        last_sequence = trade.sequence;
        ASSERT_EQ(trade.timestamp, op.request.timestamp);
        ASSERT_EQ(trade.aggressor_side, op.request.side);
      }
      ASSERT_EQ(actual_trades, expected.trades);
      total_trades += actual_trades.size();

      ASSERT_EQ(result.filled_quantity, expected.filled);
      ASSERT_EQ(result.resting_quantity, expected.rested);
      ASSERT_EQ(result.cancelled_quantity, expected.cancelled);
      ASSERT_EQ(result.filled_quantity + result.resting_quantity + result.cancelled_quantity,
                op.request.quantity);
      if (result.resting_quantity == 0) {
        // An order that does not rest is finished; it must never trade again.
        ledger.on_cancelled(result.order_id);
      }
    }

    if (step % 64 == 0 || step + 1 == config.operations) {
      const OrderBook& book = engine.book(instrument);
      ASSERT_NO_THROW(book.check_invariants());
      ASSERT_EQ(engine_rows(book), reference.rows());
    }
  }

  ledger.check_conservation();
  // Guard against a generator that silently stops exercising the interesting paths.
  EXPECT_GT(total_trades, static_cast<std::size_t>(config.operations) / 10);
  EXPECT_GT(successful_cancels, static_cast<std::size_t>(config.operations) / 20);
}

std::vector<StreamConfig> stream_configs() {
  std::vector<StreamConfig> configs;
  for (std::uint64_t seed = 1; seed <= 12; ++seed) {
    configs.push_back({seed, 6000, 3, 3});      // Narrow: deep queues, heavy STP.
    configs.push_back({seed + 100, 6000, 25, 8});   // Moderate.
    configs.push_back({seed + 200, 4000, 400, 20});  // Wide: many sparse levels.
  }
  return configs;
}

INSTANTIATE_TEST_SUITE_P(Streams, RandomizedMatchingTest, ::testing::ValuesIn(stream_configs()));

// ---- Deterministic replay ------------------------------------------------------------------

struct RunTranscript {
  std::vector<std::tuple<OrderId, Sequence, OrderStatus, Quantity, Quantity, Quantity>> outcomes;
  std::vector<Trade> trades;
  BookSnapshot final_book;
};

RunTranscript run_stream(std::uint64_t seed, int operations) {
  const StreamConfig config{seed, operations, 10, 6};
  SplitMix64 rng(seed);
  MatchingEngine engine;
  const InstrumentId instrument = engine.add_instrument();
  ReferenceBook reference;  // Only used to choose realistic cancel targets.
  std::int64_t mid = 1000;
  OrderId next_id_hint = 1;
  RunTranscript transcript;
  for (int step = 0; step < operations; ++step) {
    Operation op = next_operation(rng, config, mid, reference, next_id_hint);
    if (op.kind == OpKind::Cancel) {
      const auto result = engine.cancel(instrument, op.cancel_id);
      reference.cancel(op.cancel_id);
      transcript.outcomes.emplace_back(op.cancel_id, result.sequence, OrderStatus::Cancelled,
                                       result.cancelled_quantity, 0, 0);
      continue;
    }
    op.request.instrument = instrument;
    const auto result = engine.submit(op.request);
    reference.submit(next_id_hint, op.request.owner, op.request.side,
                     op.request.type == OrderType::Market,
                     op.request.time_in_force == TimeInForce::ImmediateOrCancel,
                     op.request.self_trade_prevention, op.request.price, op.request.quantity);
    if (result.accepted()) {
      ++next_id_hint;
    }
    transcript.outcomes.emplace_back(result.order_id, result.sequence, result.status,
                                     result.filled_quantity, result.resting_quantity,
                                     result.cancelled_quantity);
    transcript.trades.insert(transcript.trades.end(), result.trades.begin(), result.trades.end());
  }
  transcript.final_book = engine.book(instrument).snapshot(1'000'000);
  return transcript;
}

TEST(DeterministicReplay, IdenticalInputsProduceIdenticalOutputs) {
  for (const std::uint64_t seed : {7ULL, 99ULL, 12345ULL}) {
    const RunTranscript first = run_stream(seed, 20'000);
    const RunTranscript second = run_stream(seed, 20'000);
    EXPECT_EQ(first.outcomes, second.outcomes) << "seed " << seed;
    EXPECT_EQ(first.trades, second.trades) << "seed " << seed;
    EXPECT_EQ(first.final_book, second.final_book) << "seed " << seed;
    EXPECT_FALSE(first.trades.empty());
  }
}

TEST(DeterministicReplay, DifferentSeedsDiverge) {
  const RunTranscript a = run_stream(1, 2'000);
  const RunTranscript b = run_stream(2, 2'000);
  EXPECT_NE(a.trades, b.trades);
}

}  // namespace
}  // namespace udeshi
