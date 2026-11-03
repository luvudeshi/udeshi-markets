// Randomised property-based and differential tests for the matching engine.
//
// Each stream drives the engine with a long, seeded pseudo-random sequence of submissions and
// cancellations across one or more instruments: limit / market / IOC orders on both sides, all
// self-trade-prevention modes, non-monotone timestamps, cancels of live, stale, never-issued
// and wrong-instrument ids, and malformed input (bad quantities and prices, out-of-range enum
// values, unknown instruments). After every operation it checks three independent things:
//
//  1. Differential equivalence with ReferenceEngine (reference_engine.hpp), a naive model of
//     the documented contract: every field of every result (ids, sequence numbers, status,
//     reasons, quantities, trades, self-trade-prevention cancels), and the full L3 book. The
//     book comparison, L2 aggregates, book-volume accounting and structural invariants run
//     after every operation, except in the deep-book profile, where they run every 25
//     operations to keep the naive model's O(n log n) book dump affordable.
//
//  2. Contract properties checked directly, so that a bug shared by engine and oracle would
//     still be caught:
//       * sequencing: each submit/cancel takes the next sequence number, and its trades and
//         STP cancels take the following ones contiguously; order and trade ids are dense;
//       * no execution at a price worse than either party's limit, and every execution at the
//         resting order's price;
//       * quantity accounting: for every order, filled + cancelled + still-resting quantity
//         equals the submitted quantity, and per instrument and side the book's resting volume
//         equals rested - filled-while-resting - cancelled. Shares can be neither created nor
//         destroyed without one of these equations failing;
//       * finished (filled or cancelled) orders never trade again, and the book contains an
//         order exactly when the engine reported it as resting.
//
//  3. Structural invariants via OrderBook::check_invariants() and L2 aggregates.
//
// Generators use SplitMix64 rather than <random> distributions, whose output is
// implementation-defined; streams (and the golden digests below) are identical on every
// platform.

#include <gtest/gtest.h>

#include <algorithm>
#include <array>
#include <cstdint>
#include <limits>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

#include "reference_engine.hpp"
#include "udeshi/matching_engine.hpp"

namespace udeshi {
namespace {

using testing::RefCancel;
using testing::ReferenceEngine;
using testing::RefRow;
using testing::RefStpCancel;
using testing::RefSubmit;
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
  // Integer in [lo, hi]. The modulo bias is irrelevant for test generation.
  std::int64_t between(std::int64_t lo, std::int64_t hi) {
    return lo + static_cast<std::int64_t>(next() % static_cast<std::uint64_t>(hi - lo + 1));
  }
  // True with probability per_mille / 1000.
  bool chance(int per_mille) { return between(0, 999) < per_mille; }

 private:
  std::uint64_t state_;
};

// ---- Stream generation ---------------------------------------------------------------------

enum class Profile {
  Narrow,    // Prices within a few ticks: deep FIFO queues, frequent self-trade prevention.
  Moderate,  // Two instruments, moderate dispersion.
  Wide,      // Three instruments, sparse books with many levels.
  Deep,      // Passive-heavy flow growing a book of thousands of orders (several pool chunks),
             // boundary prices (1 and kMaxPrice) and rare maximum-size market sweeps.
};

struct StreamConfig {
  Profile profile;
  std::uint64_t seed;
  int operations;
};

struct ProfileParams {
  const char* name;
  std::int64_t price_half_width;
  int owners;       // Owner ids are drawn from [0, owners]; 0 is anonymous.
  int instruments;
  int cancel_per_mille;
  int market_per_mille;
  int ioc_per_mille;
  int check_every;  // Full L3 / invariant check cadence.
};

ProfileParams params(Profile profile) {
  switch (profile) {
    case Profile::Narrow: return {"narrow", 3, 3, 1, 250, 80, 70, 1};
    case Profile::Moderate: return {"moderate", 25, 8, 2, 250, 80, 70, 1};
    case Profile::Wide: return {"wide", 400, 20, 3, 250, 80, 70, 1};
    case Profile::Deep: return {"deep", 300, 30, 1, 50, 30, 20, 25};
  }
  return {"?", 0, 0, 0, 0, 0, 0, 1};
}

std::ostream& operator<<(std::ostream& os, const StreamConfig& c) {
  return os << params(c.profile).name << "/seed=" << c.seed << "/ops=" << c.operations;
}

enum class OpKind { Submit, Cancel };

struct Operation {
  OpKind kind = OpKind::Submit;
  OrderRequest request;
  InstrumentId cancel_instrument = 0;
  OrderId cancel_id = kInvalidOrderId;
};

class StreamGenerator {
 public:
  explicit StreamGenerator(const StreamConfig& config)
      : config_(config),
        params_(params(config.profile)),
        rng_(config.seed),
        mids_(static_cast<std::size_t>(params_.instruments), 1000) {}

  Operation next(const ReferenceEngine& reference) {
    Operation op;
    const auto instrument =
        static_cast<InstrumentId>(rng_.between(0, params_.instruments - 1));
    std::int64_t& mid = mids_[instrument];
    mid = std::clamp<std::int64_t>(mid + rng_.between(-1, 1), 400, 5000);

    if (rng_.chance(params_.cancel_per_mille)) {
      op.kind = OpKind::Cancel;
      op.cancel_instrument = instrument;
      const auto resting = reference.resting_ids(instrument);
      if (!resting.empty() && rng_.chance(850)) {
        op.cancel_id = resting[static_cast<std::size_t>(
            rng_.between(0, static_cast<std::int64_t>(resting.size()) - 1))];
        if (params_.instruments > 1 && rng_.chance(100)) {  // Right id, wrong instrument.
          op.cancel_instrument = (instrument + 1) % static_cast<InstrumentId>(params_.instruments);
        }
      } else {
        op.cancel_id = static_cast<OrderId>(
            rng_.between(0, static_cast<std::int64_t>(reference.next_order_id()) + 3));
      }
      if (rng_.chance(10)) {
        op.cancel_instrument = static_cast<InstrumentId>(params_.instruments + 5);
      }
      return op;
    }

    OrderRequest& r = op.request;
    r.instrument = instrument;
    r.owner = static_cast<TraderId>(rng_.between(0, params_.owners));
    r.side = rng_.chance(500) ? Side::Buy : Side::Sell;
    r.quantity = rng_.chance(50) ? rng_.between(200, 2000) : rng_.between(1, 100);
    const int stp_roll = static_cast<int>(rng_.between(0, 99));
    r.self_trade_prevention = stp_roll < 60   ? SelfTradePrevention::Allow
                              : stp_roll < 80 ? SelfTradePrevention::CancelResting
                                              : SelfTradePrevention::CancelIncoming;
    // Deliberately unrelated to arrival order: priority must follow arrival, not timestamps.
    r.timestamp = rng_.between(0, 1'000'000'000);

    const int type_roll = static_cast<int>(rng_.between(0, 999));
    if (type_roll < params_.market_per_mille) {
      r.type = OrderType::Market;
      r.time_in_force =
          rng_.chance(500) ? TimeInForce::ImmediateOrCancel : TimeInForce::GoodTillCancel;
      r.price = 0;
      if (config_.profile == Profile::Deep && rng_.chance(5)) {
        r.quantity = kMaxOrderQuantity;  // Sweeps the entire opposite side.
      }
    } else {
      r.type = OrderType::Limit;
      r.time_in_force = type_roll < params_.market_per_mille + params_.ioc_per_mille
                            ? TimeInForce::ImmediateOrCancel
                            : TimeInForce::GoodTillCancel;
      r.price = limit_price(r.side, mid);
    }

    if (rng_.chance(30)) {
      inject_invalid(r);
      if (rng_.chance(300)) {
        inject_invalid(r);  // Two independent faults: exercises the validation order.
      }
    }
    return op;
  }

 private:
  std::int64_t limit_price(Side side, std::int64_t mid) {
    const std::int64_t width = params_.price_half_width;
    if (config_.profile == Profile::Deep) {
      if (rng_.chance(5)) {  // Far-away boundary prices that only a full sweep reaches.
        return side == Side::Buy ? 1 : kMaxPrice;
      }
      // Mostly passive: buys below and sells above mid, so the book keeps growing; a small
      // fraction crosses to keep trades flowing.
      const bool aggressive = rng_.chance(30);
      const std::int64_t offset = rng_.between(1, width);
      if (side == Side::Buy) {
        return aggressive ? mid + offset : std::max<std::int64_t>(2, mid - offset);
      }
      return aggressive ? std::max<std::int64_t>(2, mid - offset) : mid + offset;
    }
    // Buys centred slightly below mid and sells slightly above, overlapping enough that a large
    // fraction of orders cross.
    const std::int64_t skew = side == Side::Buy ? -1 : 1;
    return mid + skew + rng_.between(-width, width);
  }

  void inject_invalid(OrderRequest& r) {
    switch (rng_.between(0, 9)) {
      case 0: r.quantity = -rng_.between(0, 5); break;
      case 1: r.quantity = kMaxOrderQuantity + rng_.between(1, 10); break;
      case 2: r.type = OrderType::Limit; r.price = -rng_.between(0, 5); break;
      case 3: r.type = OrderType::Limit; r.price = kMaxPrice + rng_.between(1, 10); break;
      case 4:
        r.type = OrderType::Market;
        r.price = rng_.chance(500) ? rng_.between(1, 50) : -rng_.between(1, 50);
        break;
      case 5: r.side = static_cast<Side>(rng_.between(2, 255)); break;
      case 6: r.type = static_cast<OrderType>(rng_.between(2, 255)); break;
      case 7: r.time_in_force = static_cast<TimeInForce>(rng_.between(2, 255)); break;
      case 8:
        r.self_trade_prevention = static_cast<SelfTradePrevention>(rng_.between(3, 255));
        break;
      default: r.instrument = static_cast<InstrumentId>(params_.instruments + rng_.between(0, 3));
    }
  }

  StreamConfig config_;
  ProfileParams params_;
  SplitMix64 rng_;
  std::vector<std::int64_t> mids_;
};

// ---- Engine -> reference-model conversions -------------------------------------------------

RefSubmit to_ref(const SubmitResult& r) {
  RefSubmit out;
  out.sequence = r.sequence;
  out.order_id = r.order_id;
  out.status = r.status;
  out.reject_reason = r.reject_reason;
  out.cancel_reason = r.cancel_reason;
  out.requested = r.requested_quantity;
  out.filled = r.filled_quantity;
  out.rested = r.resting_quantity;
  out.cancelled = r.cancelled_quantity;
  for (const Trade& t : r.trades) {
    out.trades.push_back(RefTrade{t.id, t.sequence, t.instrument, t.price, t.quantity,
                                  t.aggressor_side, t.buy_order_id, t.sell_order_id, t.buyer,
                                  t.seller, t.timestamp});
  }
  for (const RestingCancel& c : r.resting_cancels) {
    out.stp_cancels.push_back(
        RefStpCancel{c.order_id, c.sequence, c.owner, c.side, c.price, c.cancelled_quantity});
  }
  return out;
}

RefCancel to_ref(const CancelResult& r) {
  return RefCancel{r.outcome, r.sequence, r.order_id, r.owner, r.side, r.price,
                   r.cancelled_quantity};
}

std::vector<RefRow> engine_rows(const OrderBook& book) {
  std::vector<RefRow> rows;
  for (const Side side : {Side::Buy, Side::Sell}) {
    for (const auto& [price, level] : book.side(side)) {
      for (const Order* o = level.front(); o != nullptr; o = o->next()) {
        rows.push_back(RefRow{side, price, o->id, o->owner, o->sequence, o->original_quantity,
                              o->remaining_quantity, o->timestamp});
      }
    }
  }
  return rows;
}

// L2 view aggregated from reference rows, for checking the engine's cached level aggregates.
std::vector<LevelView> aggregate(const std::vector<RefRow>& rows, Side side) {
  std::vector<LevelView> levels;
  for (const RefRow& row : rows) {
    if (row.side != side) {
      continue;
    }
    if (levels.empty() || levels.back().price != row.price) {
      levels.push_back(LevelView{row.price, 0, 0});
    }
    levels.back().quantity += row.remaining;
    levels.back().order_count += 1;
  }
  return levels;
}

// ---- Direct contract checks (independent of the reference model) --------------------------

class ContractChecker {
 public:
  explicit ContractChecker(int instruments)
      : expected_resting_(static_cast<std::size_t>(instruments)) {}

  void on_submit(const MatchingEngine& engine, const OrderRequest& request,
                 const SubmitResult& result) {
    ASSERT_EQ(result.sequence, last_sequence_ + 1) << "submit must take the next sequence";
    last_sequence_ = result.sequence;
    ASSERT_EQ(result.requested_quantity, request.quantity);
    if (!result.accepted()) {
      ASSERT_EQ(result.order_id, kInvalidOrderId);
      ASSERT_TRUE(result.trades.empty());
      ASSERT_TRUE(result.resting_cancels.empty());
      ASSERT_EQ(result.filled_quantity + result.resting_quantity + result.cancelled_quantity, 0);
      ASSERT_EQ(engine.last_sequence(), last_sequence_);
      return;
    }
    ASSERT_EQ(result.order_id, next_order_id_) << "order ids must be dense";
    ++next_order_id_;
    ASSERT_TRUE(orders_.emplace(result.order_id, OrderInfo{request, 0, 0, false}).second);

    // Trades and STP cancels must occupy the next sequence numbers with no gaps, in the order
    // the matching loop produced them.
    std::vector<Sequence> event_sequences;
    for (const Trade& t : result.trades) event_sequences.push_back(t.sequence);
    for (const RestingCancel& c : result.resting_cancels) event_sequences.push_back(c.sequence);
    std::sort(event_sequences.begin(), event_sequences.end());
    for (const Sequence s : event_sequences) {
      ASSERT_EQ(s, last_sequence_ + 1) << "event sequence numbers must be contiguous";
      last_sequence_ = s;
    }
    ASSERT_EQ(engine.last_sequence(), last_sequence_);

    auto& resting_volume = expected_resting_.at(request.instrument);
    for (const RestingCancel& c : result.resting_cancels) {
      ASSERT_EQ(c.reason, CancelReason::SelfTradePrevention);
      ASSERT_EQ(request.self_trade_prevention, SelfTradePrevention::CancelResting);
      ASSERT_NE(request.owner, kAnonymousTrader) << "STP applied to an anonymous order";
      ASSERT_EQ(c.owner, request.owner);
      ASSERT_EQ(c.side, opposite(request.side));
      OrderInfo& victim = orders_.at(c.order_id);
      ASSERT_EQ(victim.request.owner, request.owner) << "STP cancelled another owner's order";
      ASSERT_TRUE(marketable(request, victim.request.price))
          << "STP cancelled an own order that the incoming order could not have traded with";
      ASSERT_FALSE(victim.finished) << "STP cancelled an order that was no longer live";
      ASSERT_EQ(victim.request.instrument, request.instrument);
      ASSERT_EQ(c.price, victim.request.price);
      ASSERT_EQ(c.cancelled_quantity,
                victim.request.quantity - victim.filled - victim.cancelled);
      victim.cancelled += c.cancelled_quantity;
      victim.finished = true;
      resting_volume[side_index(c.side)] -= c.cancelled_quantity;
    }

    Quantity traded = 0;
    for (const Trade& t : result.trades) {
      ASSERT_EQ(t.id, next_trade_id_) << "trade ids must be dense";
      ++next_trade_id_;
      ASSERT_EQ(t.instrument, request.instrument);
      ASSERT_EQ(t.aggressor_side, request.side);
      ASSERT_EQ(t.aggressor_order_id(), result.order_id);
      ASSERT_EQ(t.timestamp, request.timestamp);
      ASSERT_GT(t.quantity, 0);
      OrderInfo& buy = orders_.at(t.buy_order_id);
      OrderInfo& sell = orders_.at(t.sell_order_id);
      ASSERT_EQ(buy.request.side, Side::Buy);
      ASSERT_EQ(sell.request.side, Side::Sell);
      ASSERT_EQ(t.buyer, buy.request.owner);
      ASSERT_EQ(t.seller, sell.request.owner);
      ASSERT_FALSE(buy.finished) << "finished buy order traded";
      ASSERT_FALSE(sell.finished) << "finished sell order traded";
      if (request.self_trade_prevention != SelfTradePrevention::Allow &&
          request.owner != kAnonymousTrader) {
        ASSERT_NE(t.buyer, t.seller) << "self-trade despite self-trade prevention";
      }
      if (buy.request.type == OrderType::Limit) {
        ASSERT_LE(t.price, buy.request.price) << "buy executed above its limit";
      }
      if (sell.request.type == OrderType::Limit) {
        ASSERT_GE(t.price, sell.request.price) << "sell executed below its limit";
      }
      OrderInfo& resting = orders_.at(t.resting_order_id());
      ASSERT_EQ(t.price, resting.request.price) << "trade not at the resting order's price";
      buy.filled += t.quantity;
      sell.filled += t.quantity;
      ASSERT_LE(buy.filled, buy.request.quantity) << "buy over-filled";
      ASSERT_LE(sell.filled, sell.request.quantity) << "sell over-filled";
      if (resting.filled == resting.request.quantity) {
        resting.finished = true;
      }
      resting_volume[side_index(opposite(request.side))] -= t.quantity;
      traded += t.quantity;
    }

    ASSERT_EQ(traded, result.filled_quantity);
    ASSERT_EQ(result.filled_quantity + result.resting_quantity + result.cancelled_quantity,
              request.quantity);
    OrderInfo& incoming = orders_.at(result.order_id);
    incoming.cancelled += result.cancelled_quantity;
    const bool rests = result.status == OrderStatus::Resting;
    ASSERT_EQ(rests, result.resting_quantity > 0);
    const OrderBook& book = engine.book(request.instrument);
    ASSERT_EQ(book.find(result.order_id) != nullptr, rests);

    // Why matching stopped must agree with what is left on the opposite side.
    const PriceLevel* opposite_best = book.side(opposite(request.side)).best();
    if (result.cancel_reason == CancelReason::SelfTradePrevention) {
      ASSERT_EQ(request.self_trade_prevention, SelfTradePrevention::CancelIncoming);
      ASSERT_NE(request.owner, kAnonymousTrader);
      ASSERT_NE(opposite_best, nullptr);
      // CancelIncoming leaves the blocking own order at the front of the best level.
      ASSERT_EQ(opposite_best->front()->owner, request.owner);
      ASSERT_TRUE(marketable(request, opposite_best->price()));
    } else if (result.filled_quantity < request.quantity) {
      // Rested, or cancelled for lack of liquidity: nothing marketable may remain.
      ASSERT_TRUE(opposite_best == nullptr || !marketable(request, opposite_best->price()))
          << "remainder not matched although marketable liquidity remains";
      ASSERT_EQ(result.cancel_reason,
                rests ? CancelReason::None : CancelReason::NoLiquidity);
    }
    if (rests) {
      resting_volume[side_index(request.side)] += result.resting_quantity;
    } else {
      incoming.finished = true;
    }
  }

  void on_cancel(const MatchingEngine& engine, InstrumentId instrument, OrderId id,
                 const CancelResult& result) {
    ASSERT_EQ(result.sequence, last_sequence_ + 1) << "cancel must take the next sequence";
    last_sequence_ = result.sequence;
    ASSERT_EQ(engine.last_sequence(), last_sequence_);
    ASSERT_EQ(result.order_id, id);
    const auto it = orders_.find(id);
    const bool live = it != orders_.end() && !it->second.finished &&
                      it->second.request.instrument == instrument;
    if (!engine.has_instrument(instrument)) {
      ASSERT_EQ(result.outcome, CancelOutcome::UnknownInstrument);
      return;
    }
    ASSERT_EQ(result.cancelled(), live) << to_string(result.outcome);
    if (!live) {
      ASSERT_EQ(result.outcome, CancelOutcome::OrderNotFound);
      ASSERT_EQ(result.cancelled_quantity, 0);
      return;
    }
    OrderInfo& info = it->second;
    ASSERT_EQ(result.owner, info.request.owner);
    ASSERT_EQ(result.side, info.request.side);
    ASSERT_EQ(result.price, info.request.price);
    ASSERT_EQ(result.cancelled_quantity, info.request.quantity - info.filled - info.cancelled);
    ASSERT_EQ(engine.book(instrument).find(id), nullptr);
    info.cancelled += result.cancelled_quantity;
    info.finished = true;
    expected_resting_.at(instrument)[side_index(result.side)] -= result.cancelled_quantity;
  }

  // Book volume per side must equal what the accounting says is still resting.
  void check_book_volume(const MatchingEngine& engine) const {
    for (std::size_t i = 0; i < expected_resting_.size(); ++i) {
      const OrderBook& book = engine.book(static_cast<InstrumentId>(i));
      ASSERT_EQ(book.total_volume(Side::Buy), expected_resting_[i][0]) << "instrument " << i;
      ASSERT_EQ(book.total_volume(Side::Sell), expected_resting_[i][1]) << "instrument " << i;
    }
  }

  // Lifetime conservation for every order ever accepted.
  void check_all_orders(const MatchingEngine& engine) const {
    for (const auto& [id, info] : orders_) {
      const Order* resting = engine.book(info.request.instrument).find(id);
      const Quantity still_resting = resting == nullptr ? 0 : resting->remaining_quantity;
      ASSERT_EQ(info.filled + info.cancelled + still_resting, info.request.quantity)
          << "order " << id << " quantity not conserved";
      ASSERT_EQ(info.finished, resting == nullptr) << "order " << id;
    }
  }

 private:
  struct OrderInfo {
    OrderRequest request;
    Quantity filled;
    Quantity cancelled;
    bool finished;
  };
  static std::size_t side_index(Side side) { return side == Side::Buy ? 0 : 1; }
  // Whether the incoming order could trade against a resting order at `resting_price`.
  static bool marketable(const OrderRequest& request, Price resting_price) {
    if (request.type == OrderType::Market) {
      return true;
    }
    return request.side == Side::Buy ? resting_price <= request.price
                                     : resting_price >= request.price;
  }

  std::unordered_map<OrderId, OrderInfo> orders_;
  std::vector<std::array<Quantity, 2>> expected_resting_;
  Sequence last_sequence_ = 0;
  OrderId next_order_id_ = 1;
  TradeId next_trade_id_ = 1;
};

// What a stream actually exercised, so that a generator regression cannot silently stop
// testing the interesting paths.
struct Coverage {
  int trades = 0;
  int multi_level_sweeps = 0;  // Submissions that traded at >= 2 distinct prices.
  int deep_sweeps = 0;         // Submissions that traded at >= 10 distinct prices.
  int stp_resting_cancels = 0;
  int stp_incoming_stops = 0;
  int market_partial = 0;      // Market orders with both fills and a cancelled remainder.
  int successful_cancels = 0;
  int failed_cancels = 0;
  int rejects = 0;
  std::size_t peak_resting_orders = 0;
  std::set<RejectReason> reject_reasons;
};

void record(Coverage& coverage, const OrderRequest& request, const SubmitResult& result) {
  if (!result.accepted()) {
    ++coverage.rejects;
    coverage.reject_reasons.insert(result.reject_reason);
    return;
  }
  coverage.trades += static_cast<int>(result.trades.size());
  std::set<Price> prices;
  for (const Trade& t : result.trades) prices.insert(t.price);
  coverage.multi_level_sweeps += prices.size() >= 2 ? 1 : 0;
  coverage.deep_sweeps += prices.size() >= 10 ? 1 : 0;
  coverage.stp_resting_cancels += static_cast<int>(result.resting_cancels.size());
  coverage.stp_incoming_stops += result.cancel_reason == CancelReason::SelfTradePrevention;
  coverage.market_partial += request.type == OrderType::Market && result.filled_quantity > 0 &&
                             result.cancelled_quantity > 0;
}

// ---- The differential / property test ------------------------------------------------------

class RandomizedMatchingTest : public ::testing::TestWithParam<StreamConfig> {};

TEST_P(RandomizedMatchingTest, MatchesReferenceAndPreservesContract) {
  const StreamConfig config = GetParam();
  const ProfileParams p = params(config.profile);
  StreamGenerator generator(config);
  MatchingEngine engine;
  ReferenceEngine reference;
  for (int i = 0; i < p.instruments; ++i) {
    ASSERT_EQ(engine.add_instrument(), reference.add_instrument());
  }
  ContractChecker checker(p.instruments);
  Coverage coverage;

  for (int step = 0; step < config.operations; ++step) {
    SCOPED_TRACE(::testing::Message() << config << " step=" << step);
    const Operation op = generator.next(reference);

    if (op.kind == OpKind::Cancel) {
      const CancelResult result = engine.cancel(op.cancel_instrument, op.cancel_id);
      ASSERT_EQ(to_ref(result), reference.cancel(op.cancel_instrument, op.cancel_id));
      checker.on_cancel(engine, op.cancel_instrument, op.cancel_id, result);
      ++(result.cancelled() ? coverage.successful_cancels : coverage.failed_cancels);
    } else {
      const SubmitResult result = engine.submit(op.request);
      ASSERT_EQ(to_ref(result), reference.submit(op.request));
      checker.on_submit(engine, op.request, result);
      record(coverage, op.request, result);
    }
    if (::testing::Test::HasFatalFailure()) {
      return;
    }
    for (int i = 0; i < p.instruments; ++i) {
      coverage.peak_resting_orders = std::max(
          coverage.peak_resting_orders, engine.book(static_cast<InstrumentId>(i)).order_count());
    }

    if (step % p.check_every == 0 || step + 1 == config.operations) {
      checker.check_book_volume(engine);
      for (int i = 0; i < p.instruments; ++i) {
        const auto instrument = static_cast<InstrumentId>(i);
        const OrderBook& book = engine.book(instrument);
        ASSERT_NO_THROW(book.check_invariants());
        const std::vector<RefRow> expected = reference.rows(instrument);
        ASSERT_EQ(engine_rows(book), expected);
        const auto max_levels = std::numeric_limits<std::size_t>::max();
        ASSERT_EQ(book.depth(Side::Buy, max_levels), aggregate(expected, Side::Buy));
        ASSERT_EQ(book.depth(Side::Sell, max_levels), aggregate(expected, Side::Sell));
        ASSERT_EQ(book.order_count(), expected.size());
      }
    }
  }
  checker.check_all_orders(engine);

  // Coverage floors, set well below what every seed of each profile actually reaches.
  const int ops = config.operations;
  EXPECT_GT(coverage.trades, ops / 10);
  EXPECT_GT(coverage.successful_cancels, ops / 50);
  EXPECT_GT(coverage.failed_cancels, 0);
  EXPECT_GT(coverage.rejects, 0);
  const std::set<RejectReason> every_reject_reason = {
      RejectReason::UnknownInstrument,          RejectReason::InvalidSide,
      RejectReason::InvalidOrderType,           RejectReason::InvalidTimeInForce,
      RejectReason::InvalidSelfTradePrevention, RejectReason::InvalidQuantity,
      RejectReason::InvalidPrice,               RejectReason::MarketOrderWithPrice};
  for (const RejectReason reason : every_reject_reason) {
    EXPECT_TRUE(coverage.reject_reasons.contains(reason))
        << "stream never produced reject reason " << to_string(reason);
  }
  EXPECT_GT(coverage.multi_level_sweeps, 0);
  EXPECT_GT(coverage.market_partial, 0);
  EXPECT_GT(coverage.stp_resting_cancels, 0);
  EXPECT_GT(coverage.stp_incoming_stops, 0);
  if (config.profile == Profile::Deep) {
    EXPECT_GT(coverage.peak_resting_orders, OrderPool::kChunkSize)
        << "deep profile must grow the book past one pool chunk";
    EXPECT_GT(coverage.deep_sweeps, 0);
  }
  RecordProperty("trades", coverage.trades);
  RecordProperty("multi_level_sweeps", coverage.multi_level_sweeps);
  RecordProperty("deep_sweeps", coverage.deep_sweeps);
  RecordProperty("stp_resting_cancels", coverage.stp_resting_cancels);
  RecordProperty("stp_incoming_stops", coverage.stp_incoming_stops);
  RecordProperty("market_partial", coverage.market_partial);
  RecordProperty("successful_cancels", coverage.successful_cancels);
  RecordProperty("failed_cancels", coverage.failed_cancels);
  RecordProperty("rejects", coverage.rejects);
  RecordProperty("reject_reasons", static_cast<int>(coverage.reject_reasons.size()));
  RecordProperty("peak_resting_orders", static_cast<int>(coverage.peak_resting_orders));
}

std::vector<StreamConfig> stream_configs() {
  std::vector<StreamConfig> configs;
  for (std::uint64_t seed = 1; seed <= 12; ++seed) {
    configs.push_back({Profile::Narrow, seed, 6000});
    configs.push_back({Profile::Moderate, seed + 100, 6000});
    configs.push_back({Profile::Wide, seed + 200, 4000});
  }
  for (std::uint64_t seed = 1; seed <= 3; ++seed) {
    configs.push_back({Profile::Deep, seed + 300, 20000});
  }
  return configs;
}

std::string config_name(const ::testing::TestParamInfo<StreamConfig>& info) {
  return std::string(params(info.param.profile).name) + "_" + std::to_string(info.param.seed);
}

INSTANTIATE_TEST_SUITE_P(Streams, RandomizedMatchingTest, ::testing::ValuesIn(stream_configs()),
                         config_name);

// ---- Deterministic replay ------------------------------------------------------------------

// 64-bit FNV-1a over an explicit, padding-free serialisation of every observable output.
class Digest {
 public:
  void add(std::uint64_t value) {
    for (int byte = 0; byte < 8; ++byte) {
      hash_ ^= (value >> (8 * byte)) & 0xFFU;
      hash_ *= 0x100000001B3ULL;
    }
  }
  void add_signed(std::int64_t value) { add(static_cast<std::uint64_t>(value)); }
  template <typename Enum>
  void add_enum(Enum value) {
    add(static_cast<std::uint64_t>(value));
  }
  [[nodiscard]] std::uint64_t value() const { return hash_; }

 private:
  std::uint64_t hash_ = 0xCBF29CE484222325ULL;
};

void digest(Digest& d, const RefSubmit& r) {
  d.add(r.sequence);
  d.add(r.order_id);
  d.add_enum(r.status);
  d.add_enum(r.reject_reason);
  d.add_enum(r.cancel_reason);
  d.add_signed(r.requested);
  d.add_signed(r.filled);
  d.add_signed(r.rested);
  d.add_signed(r.cancelled);
  for (const RefTrade& t : r.trades) {
    d.add(t.id);
    d.add(t.sequence);
    d.add(t.instrument);
    d.add_signed(t.price);
    d.add_signed(t.quantity);
    d.add_enum(t.aggressor_side);
    d.add(t.buy_order_id);
    d.add(t.sell_order_id);
    d.add(t.buyer);
    d.add(t.seller);
    d.add_signed(t.timestamp);
  }
  for (const RefStpCancel& c : r.stp_cancels) {
    d.add(c.order_id);
    d.add(c.sequence);
    d.add(c.owner);
    d.add_enum(c.side);
    d.add_signed(c.price);
    d.add_signed(c.quantity);
  }
}

void digest(Digest& d, const RefCancel& c) {
  d.add_enum(c.outcome);
  d.add(c.sequence);
  d.add(c.order_id);
  d.add(c.owner);
  d.add_enum(c.side);
  d.add_signed(c.price);
  d.add_signed(c.quantity);
}

void digest(Digest& d, const std::vector<RefRow>& rows) {
  for (const RefRow& row : rows) {
    d.add_enum(row.side);
    d.add_signed(row.price);
    d.add(row.id);
    d.add(row.owner);
    d.add(row.sequence);
    d.add_signed(row.original);
    d.add_signed(row.remaining);
    d.add_signed(row.timestamp);
  }
}

// Runs a stream against `engine` and digests every output plus the final L3 books. The
// reference model is only used by the generator to choose realistic cancel targets.
std::uint64_t run_and_digest(MatchingEngine& engine, const StreamConfig& config) {
  const ProfileParams p = params(config.profile);
  StreamGenerator generator(config);
  ReferenceEngine reference;
  for (int i = 0; i < p.instruments; ++i) {
    engine.add_instrument();
    reference.add_instrument();
  }
  Digest d;
  for (int step = 0; step < config.operations; ++step) {
    const Operation op = generator.next(reference);
    if (op.kind == OpKind::Cancel) {
      digest(d, to_ref(engine.cancel(op.cancel_instrument, op.cancel_id)));
      reference.cancel(op.cancel_instrument, op.cancel_id);
    } else {
      digest(d, to_ref(engine.submit(op.request)));
      reference.submit(op.request);
    }
  }
  for (int i = 0; i < p.instruments; ++i) {
    digest(d, engine_rows(engine.book(static_cast<InstrumentId>(i))));
  }
  return d.value();
}

struct GoldenRun {
  StreamConfig config;
  std::uint64_t digest;
};

// Golden digests pin the complete observable behaviour (every id, sequence number, status,
// reason, trade and final book) of fixed streams. The streams and the engine use only integer
// arithmetic, so these values must be identical on every platform and compiler; CI checks
// them on Linux/libstdc++ and macOS/libc++. If the engine's behaviour is changed on purpose,
// update the constants and explain the change in the commit message.
const GoldenRun kGoldenRuns[] = {
    {{Profile::Narrow, 7, 20'000}, 0x1f7b7ad73d50dd68ULL},
    {{Profile::Moderate, 99, 20'000}, 0x53b0a176b4662774ULL},
    {{Profile::Wide, 12345, 10'000}, 0xfd7e7ba03d2bfbb5ULL},
};

TEST(DeterministicReplay, IndependentRunsMatchGoldenDigests) {
  for (const GoldenRun& golden : kGoldenRuns) {
    SCOPED_TRACE(::testing::Message() << golden.config);
    // Keep the first engine alive while the second runs, so the two use different absolute
    // heap addresses: dependence on address values (e.g. hashing pointers) makes the digests
    // differ. Both engines allocate in the same pattern, so dependence on the relative order
    // of addresses is not detected here; the golden values and the reference model catch it.
    MatchingEngine first;
    const std::uint64_t first_digest = run_and_digest(first, golden.config);
    MatchingEngine second;
    const std::uint64_t second_digest = run_and_digest(second, golden.config);
    EXPECT_EQ(first_digest, second_digest);
    EXPECT_EQ(first_digest, golden.digest)
        << "golden digest changed; new value 0x" << std::hex << first_digest;
  }
}

TEST(DeterministicReplay, DifferentSeedsDiverge) {
  MatchingEngine a;
  MatchingEngine b;
  EXPECT_NE(run_and_digest(a, {Profile::Narrow, 1, 2'000}),
            run_and_digest(b, {Profile::Narrow, 2, 2'000}));
}

}  // namespace
}  // namespace udeshi
