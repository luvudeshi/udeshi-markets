// Exact-value tests of the MatchingEngine's documented contract: sequencing and id
// assignment, validation order, status / reason reporting, timestamps, boundary prices, and
// multi-instrument behaviour. The randomized differential test covers these statistically;
// these tests pin them with hand-checked values.

#include <gtest/gtest.h>

#include <vector>

#include "test_helpers.hpp"
#include "udeshi/matching_engine.hpp"

namespace udeshi {
namespace {

using testing::fills;
using testing::Fill;
using testing::ioc;
using testing::limit;
using testing::market;
using testing::with_stp;

class EngineContractTest : public ::testing::Test {
 protected:
  void SetUp() override { instrument_ = engine_.add_instrument(); }
  void TearDown() override {
    for (InstrumentId i = 0; i < engine_.instrument_count(); ++i) {
      engine_.book(i).check_invariants();
    }
  }
  [[nodiscard]] const OrderBook& book() const { return engine_.book(instrument_); }

  MatchingEngine engine_;
  InstrumentId instrument_ = 0;
};

// ---- Sequencing and identifiers ------------------------------------------------------------

TEST_F(EngineContractTest, SpecExampleHasExactIdsAndSequences) {
  const auto s1 = engine_.submit(limit(Side::Sell, 101, 10, 1));
  const auto s2 = engine_.submit(limit(Side::Sell, 102, 5, 2));
  const auto buy = engine_.submit(limit(Side::Buy, 102, 12, 3));
  EXPECT_EQ(s1.order_id, 1u);
  EXPECT_EQ(s2.order_id, 2u);
  EXPECT_EQ(buy.order_id, 3u);
  EXPECT_EQ(s1.sequence, 1u);
  EXPECT_EQ(s2.sequence, 2u);
  EXPECT_EQ(buy.sequence, 3u);
  ASSERT_EQ(buy.trades.size(), 2u);
  EXPECT_EQ(buy.trades[0].id, 1u);
  EXPECT_EQ(buy.trades[0].sequence, 4u);
  EXPECT_EQ(buy.trades[1].id, 2u);
  EXPECT_EQ(buy.trades[1].sequence, 5u);
  EXPECT_EQ(engine_.last_sequence(), 5u);
  EXPECT_EQ(book().find(s2.order_id)->sequence, 2u);
}

TEST_F(EngineContractTest, EveryCallAndEveryEventTakesExactlyOneSequenceNumber) {
  const auto own = engine_.submit(limit(Side::Sell, 100, 5, 7));    // seq 1
  const auto other = engine_.submit(limit(Side::Sell, 100, 5, 8));  // seq 2
  // seq 3 = submission, seq 4 = STP cancel of `own`, seq 5 = trade with `other`.
  const auto buy = engine_.submit(
      with_stp(limit(Side::Buy, 100, 5, 7), SelfTradePrevention::CancelResting));
  EXPECT_EQ(buy.sequence, 3u);
  ASSERT_EQ(buy.resting_cancels.size(), 1u);
  EXPECT_EQ(buy.resting_cancels[0].order_id, own.order_id);
  EXPECT_EQ(buy.resting_cancels[0].sequence, 4u);
  ASSERT_EQ(buy.trades.size(), 1u);
  EXPECT_EQ(buy.trades[0].sell_order_id, other.order_id);
  EXPECT_EQ(buy.trades[0].sequence, 5u);
  EXPECT_EQ(buy.trades[0].id, 1u);

  const auto rejected = engine_.submit(limit(Side::Buy, 0, 5));  // seq 6, no order id
  EXPECT_EQ(rejected.sequence, 6u);
  EXPECT_EQ(rejected.order_id, kInvalidOrderId);
  const auto failed_cancel = engine_.cancel(instrument_, 999);  // seq 7
  EXPECT_EQ(failed_cancel.sequence, 7u);
  const auto unknown_cancel = engine_.cancel(42, 1);  // seq 8
  EXPECT_EQ(unknown_cancel.sequence, 8u);
  EXPECT_EQ(engine_.last_sequence(), 8u);

  const auto next = engine_.submit(limit(Side::Buy, 90, 1));  // seq 9, order id 4
  EXPECT_EQ(next.sequence, 9u);
  EXPECT_EQ(next.order_id, 4u);
}

TEST_F(EngineContractTest, StpCancelsAndTradesInterleaveInExecutionOrder) {
  engine_.submit(limit(Side::Sell, 100, 1, 8));  // id 1
  engine_.submit(limit(Side::Sell, 100, 1, 7));  // id 2 (own)
  engine_.submit(limit(Side::Sell, 101, 1, 8));  // id 3
  engine_.submit(limit(Side::Sell, 101, 1, 7));  // id 4 (own)
  const auto buy = engine_.submit(
      with_stp(limit(Side::Buy, 101, 5, 7), SelfTradePrevention::CancelResting));  // seq 5
  // Execution order: trade id1 (6), cancel id2 (7), trade id3 (8), cancel id4 (9).
  ASSERT_EQ(buy.trades.size(), 2u);
  ASSERT_EQ(buy.resting_cancels.size(), 2u);
  EXPECT_EQ(buy.trades[0].sell_order_id, 1u);
  EXPECT_EQ(buy.trades[0].sequence, 6u);
  EXPECT_EQ(buy.resting_cancels[0].order_id, 2u);
  EXPECT_EQ(buy.resting_cancels[0].sequence, 7u);
  EXPECT_EQ(buy.trades[1].sell_order_id, 3u);
  EXPECT_EQ(buy.trades[1].sequence, 8u);
  EXPECT_EQ(buy.resting_cancels[1].order_id, 4u);
  EXPECT_EQ(buy.resting_cancels[1].sequence, 9u);
  EXPECT_EQ(buy.status, OrderStatus::Resting);
  EXPECT_EQ(buy.resting_quantity, 3);
}

TEST_F(EngineContractTest, IdsAndSequencesAreGlobalAcrossInstruments) {
  const InstrumentId second = engine_.add_instrument();
  const InstrumentId third = engine_.add_instrument();
  std::vector<OrderId> order_ids;
  std::vector<TradeId> trade_ids;
  Sequence last = 0;
  for (int round = 0; round < 3; ++round) {
    for (const InstrumentId instrument : {instrument_, second, third}) {
      const auto rest = engine_.submit(limit(Side::Sell, 100 + round, 5, 1, instrument));
      const auto take = engine_.submit(limit(Side::Buy, 100 + round, 5, 2, instrument));
      for (const auto* result : {&rest, &take}) {
        EXPECT_EQ(result->sequence, last + 1);
        last = result->sequence;
        order_ids.push_back(result->order_id);
        for (const Trade& trade : result->trades) {
          EXPECT_EQ(trade.instrument, instrument);
          EXPECT_EQ(trade.sequence, last + 1);
          last = trade.sequence;
          trade_ids.push_back(trade.id);
        }
      }
    }
  }
  for (std::size_t i = 0; i < order_ids.size(); ++i) {
    EXPECT_EQ(order_ids[i], i + 1) << "order ids must be dense across instruments";
  }
  ASSERT_EQ(trade_ids.size(), 9u);
  for (std::size_t i = 0; i < trade_ids.size(); ++i) {
    EXPECT_EQ(trade_ids[i], i + 1) << "trade ids must be dense across instruments";
  }
}

TEST_F(EngineContractTest, CancelOnWrongInstrumentLeavesOrderResting) {
  const InstrumentId second = engine_.add_instrument();
  const auto order = engine_.submit(limit(Side::Buy, 100, 5, 1, instrument_));
  EXPECT_EQ(engine_.cancel(second, order.order_id).outcome, CancelOutcome::OrderNotFound);
  ASSERT_NE(book().find(order.order_id), nullptr);
  const auto trade = engine_.submit(limit(Side::Sell, 100, 5, 2, instrument_));
  EXPECT_EQ(trade.status, OrderStatus::Filled);
}

// ---- Validation ----------------------------------------------------------------------------

TEST_F(EngineContractTest, RejectsOutOfRangeEnumValues) {
  auto bad_side = limit(Side::Buy, 100, 1);
  bad_side.side = static_cast<Side>(2);
  auto bad_type = limit(Side::Buy, 100, 1);
  bad_type.type = static_cast<OrderType>(7);
  auto bad_tif = limit(Side::Buy, 100, 1);
  bad_tif.time_in_force = static_cast<TimeInForce>(9);
  auto bad_stp = limit(Side::Buy, 100, 1);
  bad_stp.self_trade_prevention = static_cast<SelfTradePrevention>(3);
  auto bad_market_tif = market(Side::Buy, 1);
  bad_market_tif.time_in_force = static_cast<TimeInForce>(200);

  const std::vector<std::pair<OrderRequest, RejectReason>> cases = {
      {bad_side, RejectReason::InvalidSide},
      {bad_type, RejectReason::InvalidOrderType},
      {bad_tif, RejectReason::InvalidTimeInForce},
      {bad_stp, RejectReason::InvalidSelfTradePrevention},
      {bad_market_tif, RejectReason::InvalidTimeInForce},
  };
  engine_.submit(limit(Side::Sell, 100, 1));  // Something they would otherwise trade with.
  for (const auto& [request, reason] : cases) {
    const auto result = engine_.submit(request);
    EXPECT_EQ(result.status, OrderStatus::Rejected) << to_string(reason);
    EXPECT_EQ(result.reject_reason, reason);
    EXPECT_EQ(result.order_id, kInvalidOrderId);
    EXPECT_TRUE(result.trades.empty());
  }
  EXPECT_EQ(book().order_count(), 1u);
  EXPECT_EQ(book().volume_at(Side::Sell, 100), 1);
}

TEST_F(EngineContractTest, ValidationReportsTheFirstFailingCheck) {
  auto request = limit(Side::Buy, 0, 0, 1, 99);  // Everything wrong at once.
  request.side = static_cast<Side>(5);
  request.type = static_cast<OrderType>(5);
  request.time_in_force = static_cast<TimeInForce>(5);
  request.self_trade_prevention = static_cast<SelfTradePrevention>(5);
  const std::vector<RejectReason> expected_order = {
      RejectReason::UnknownInstrument,          RejectReason::InvalidSide,
      RejectReason::InvalidOrderType,           RejectReason::InvalidTimeInForce,
      RejectReason::InvalidSelfTradePrevention, RejectReason::InvalidQuantity,
      RejectReason::InvalidPrice};
  for (const RejectReason expected : expected_order) {
    EXPECT_EQ(engine_.submit(request).reject_reason, expected);
    // Fix the field that was just reported and check that the next one is reported.
    switch (expected) {
      case RejectReason::UnknownInstrument: request.instrument = instrument_; break;
      case RejectReason::InvalidSide: request.side = Side::Buy; break;
      case RejectReason::InvalidOrderType: request.type = OrderType::Limit; break;
      case RejectReason::InvalidTimeInForce:
        request.time_in_force = TimeInForce::GoodTillCancel;
        break;
      case RejectReason::InvalidSelfTradePrevention:
        request.self_trade_prevention = SelfTradePrevention::Allow;
        break;
      case RejectReason::InvalidQuantity: request.quantity = 1; break;
      default: request.price = 100; break;
    }
  }
  EXPECT_EQ(engine_.submit(request).status, OrderStatus::Resting);
}

// ---- Status and reason reporting -----------------------------------------------------------

TEST_F(EngineContractTest, IocPartialFillReportsNoLiquidity) {
  engine_.submit(limit(Side::Sell, 100, 2));
  const auto result = engine_.submit(ioc(Side::Buy, 100, 5));
  EXPECT_EQ(result.status, OrderStatus::Cancelled);
  EXPECT_EQ(result.cancel_reason, CancelReason::NoLiquidity);
  EXPECT_EQ(result.filled_quantity, 2);
  EXPECT_EQ(result.cancelled_quantity, 3);
}

TEST_F(EngineContractTest, RestingAndFilledOrdersReportNoCancelReason) {
  engine_.submit(limit(Side::Sell, 100, 2));
  EXPECT_EQ(engine_.submit(limit(Side::Buy, 100, 5)).cancel_reason, CancelReason::None);
  EXPECT_EQ(engine_.submit(limit(Side::Sell, 100, 3)).cancel_reason, CancelReason::None);
}

TEST_F(EngineContractTest, MarketOrderStoppedBySelfTradePreventionReportsIt) {
  engine_.submit(limit(Side::Sell, 100, 2, 8));
  engine_.submit(limit(Side::Sell, 101, 2, 7));
  const auto result =
      engine_.submit(with_stp(market(Side::Buy, 10, 7), SelfTradePrevention::CancelIncoming));
  EXPECT_EQ(fills(result), (std::vector<Fill>{{100, 2}}));
  EXPECT_EQ(result.status, OrderStatus::Cancelled);
  EXPECT_EQ(result.cancel_reason, CancelReason::SelfTradePrevention);
  EXPECT_EQ(result.cancelled_quantity, 8);
}

TEST_F(EngineContractTest, IocStoppedBySelfTradePreventionReportsIt) {
  engine_.submit(limit(Side::Buy, 100, 2, 7));
  const auto result =
      engine_.submit(with_stp(ioc(Side::Sell, 99, 3, 7), SelfTradePrevention::CancelIncoming));
  EXPECT_TRUE(result.trades.empty());
  EXPECT_EQ(result.cancel_reason, CancelReason::SelfTradePrevention);
  EXPECT_EQ(result.cancelled_quantity, 3);
  EXPECT_EQ(book().volume_at(Side::Buy, 100), 2);
}

TEST_F(EngineContractTest, StpCancelReportsTheRestingOrdersOwnPrice) {
  const auto own = engine_.submit(limit(Side::Sell, 100, 4, 7));
  const auto buy = engine_.submit(
      with_stp(limit(Side::Buy, 103, 1, 7), SelfTradePrevention::CancelResting));
  ASSERT_EQ(buy.resting_cancels.size(), 1u);
  EXPECT_EQ(buy.resting_cancels[0].order_id, own.order_id);
  EXPECT_EQ(buy.resting_cancels[0].price, 100);
  EXPECT_EQ(buy.resting_cancels[0].side, Side::Sell);
  EXPECT_EQ(book().best_bid(), 103);
}

TEST_F(EngineContractTest, CancelOfRestingSellReportsItsDetails) {
  const auto order = engine_.submit(limit(Side::Sell, 105, 9, 4));
  const auto cancel = engine_.cancel(instrument_, order.order_id);
  EXPECT_EQ(cancel.outcome, CancelOutcome::Cancelled);
  EXPECT_EQ(cancel.order_id, order.order_id);
  EXPECT_EQ(cancel.owner, 4u);
  EXPECT_EQ(cancel.side, Side::Sell);
  EXPECT_EQ(cancel.price, 105);
  EXPECT_EQ(cancel.cancelled_quantity, 9);
}

// ---- Timestamps ----------------------------------------------------------------------------

TEST_F(EngineContractTest, TimePriorityFollowsArrivalNotTimestamp) {
  auto a = limit(Side::Sell, 100, 1, 1);
  a.timestamp = 2'000;
  auto b = limit(Side::Sell, 100, 1, 2);
  b.timestamp = 1'000;
  auto c = limit(Side::Sell, 100, 1, 3);
  c.timestamp = 0;
  const auto ra = engine_.submit(a);
  const auto rb = engine_.submit(b);
  const auto rc = engine_.submit(c);

  const auto queue = book().orders_at(Side::Sell, 100);
  ASSERT_EQ(queue.size(), 3u);
  EXPECT_EQ(queue[0]->id, ra.order_id);
  EXPECT_EQ(queue[1]->id, rb.order_id);
  EXPECT_EQ(queue[2]->id, rc.order_id);
  EXPECT_EQ(queue[0]->timestamp, 2'000);
  EXPECT_EQ(queue[1]->timestamp, 1'000);

  const auto buy = engine_.submit(limit(Side::Buy, 100, 3));
  ASSERT_EQ(buy.trades.size(), 3u);
  EXPECT_EQ(buy.trades[0].sell_order_id, ra.order_id);
  EXPECT_EQ(buy.trades[1].sell_order_id, rb.order_id);
  EXPECT_EQ(buy.trades[2].sell_order_id, rc.order_id);
}

// ---- Boundary prices -----------------------------------------------------------------------

TEST_F(EngineContractTest, MarketBuyReachesAskAtMaximumPrice) {
  engine_.submit(limit(Side::Sell, kMaxPrice, kMaxOrderQuantity, 1));
  const auto buy = engine_.submit(market(Side::Buy, kMaxOrderQuantity, 2));
  EXPECT_EQ(buy.status, OrderStatus::Filled);
  EXPECT_EQ(fills(buy), (std::vector<Fill>{{kMaxPrice, kMaxOrderQuantity}}));
  EXPECT_TRUE(book().empty());
}

TEST_F(EngineContractTest, MarketSellReachesBidAtMinimumPrice) {
  engine_.submit(limit(Side::Buy, 1, 5, 1));
  engine_.submit(limit(Side::Buy, 2, 5, 1));
  const auto sell = engine_.submit(market(Side::Sell, 10, 2));
  EXPECT_EQ(fills(sell), (std::vector<Fill>{{2, 5}, {1, 5}}));
  EXPECT_TRUE(book().empty());
}

TEST_F(EngineContractTest, MaximumSizeMarketOrderSweepsThousandsOfLevels) {
  constexpr int kLevels = 5000;
  for (int i = 0; i < kLevels; ++i) {
    engine_.submit(limit(Side::Sell, 1000 + i, 1, 1));
  }
  engine_.submit(limit(Side::Sell, kMaxPrice, 1, 1));
  const auto buy = engine_.submit(market(Side::Buy, kMaxOrderQuantity, 2));
  ASSERT_EQ(buy.trades.size(), static_cast<std::size_t>(kLevels + 1));
  EXPECT_EQ(buy.trades.back().price, kMaxPrice);
  EXPECT_EQ(buy.filled_quantity, kLevels + 1);
  EXPECT_EQ(buy.cancelled_quantity, kMaxOrderQuantity - kLevels - 1);
  EXPECT_TRUE(book().empty());
  EXPECT_EQ(book().level_count(Side::Sell), 0u);
}

}  // namespace
}  // namespace udeshi
