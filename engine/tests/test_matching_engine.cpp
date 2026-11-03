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

class MatchingEngineTest : public ::testing::Test {
 protected:
  void SetUp() override { instrument_ = engine_.add_instrument(); }
  void TearDown() override { engine_.book(instrument_).check_invariants(); }

  [[nodiscard]] const OrderBook& book() const { return engine_.book(instrument_); }

  MatchingEngine engine_;
  InstrumentId instrument_ = 0;
};

// ---- Resting / single orders ---------------------------------------------------------------

TEST_F(MatchingEngineTest, LimitOrderIntoEmptyBookRests) {
  const auto result = engine_.submit(limit(Side::Buy, 100, 10, 1));
  EXPECT_EQ(result.status, OrderStatus::Resting);
  EXPECT_NE(result.order_id, kInvalidOrderId);
  EXPECT_TRUE(result.trades.empty());
  EXPECT_EQ(result.filled_quantity, 0);
  EXPECT_EQ(result.resting_quantity, 10);
  EXPECT_EQ(result.cancelled_quantity, 0);
  EXPECT_EQ(book().best_bid(), 100);
  EXPECT_FALSE(book().best_ask().has_value());
  EXPECT_EQ(book().volume_at(Side::Buy, 100), 10);

  const Order* order = book().find(result.order_id);
  ASSERT_NE(order, nullptr);
  EXPECT_EQ(order->owner, 1u);
  EXPECT_EQ(order->remaining_quantity, 10);
  EXPECT_EQ(order->original_quantity, 10);
  EXPECT_EQ(order->sequence, result.sequence);
}

TEST_F(MatchingEngineTest, NonCrossingOrdersBothRest) {
  engine_.submit(limit(Side::Buy, 99, 5));
  engine_.submit(limit(Side::Sell, 101, 7));
  EXPECT_EQ(book().best_bid(), 99);
  EXPECT_EQ(book().best_ask(), 101);
  EXPECT_EQ(book().order_count(), 2u);
}

TEST_F(MatchingEngineTest, OrderIdsAndSequencesAreStrictlyIncreasing) {
  const auto a = engine_.submit(limit(Side::Buy, 99, 5));
  const auto b = engine_.submit(limit(Side::Buy, 98, 5));
  const auto c = engine_.submit(limit(Side::Sell, 99, 1));  // Trades: consumes a sequence too.
  const auto d = engine_.submit(limit(Side::Sell, 120, 1));
  EXPECT_LT(a.order_id, b.order_id);
  EXPECT_LT(b.order_id, c.order_id);
  EXPECT_LT(c.order_id, d.order_id);
  EXPECT_LT(a.sequence, b.sequence);
  EXPECT_LT(b.sequence, c.sequence);
  ASSERT_EQ(c.trades.size(), 1u);
  EXPECT_LT(c.sequence, c.trades[0].sequence);
  EXPECT_LT(c.trades[0].sequence, d.sequence);
  EXPECT_EQ(engine_.last_sequence(), d.sequence);
}

// ---- The canonical price-time priority example from the specification ----------------------

TEST_F(MatchingEngineTest, SpecExampleBuySweepsTwoAskLevels) {
  const auto s1 = engine_.submit(limit(Side::Sell, 101, 10, 1));
  const auto s2 = engine_.submit(limit(Side::Sell, 102, 5, 2));
  const auto buy = engine_.submit(limit(Side::Buy, 102, 12, 3));

  EXPECT_EQ(buy.status, OrderStatus::Filled);
  EXPECT_EQ(fills(buy), (std::vector<Fill>{{101, 10}, {102, 2}}));
  EXPECT_EQ(buy.filled_quantity, 12);
  EXPECT_EQ(buy.resting_quantity, 0);

  ASSERT_EQ(buy.trades.size(), 2u);
  EXPECT_EQ(buy.trades[0].sell_order_id, s1.order_id);
  EXPECT_EQ(buy.trades[1].sell_order_id, s2.order_id);
  for (const Trade& trade : buy.trades) {
    EXPECT_EQ(trade.buy_order_id, buy.order_id);
    EXPECT_EQ(trade.buyer, 3u);
    EXPECT_EQ(trade.aggressor_side, Side::Buy);
  }

  // The remaining 3 shares at 102 stay in the ask book; 101 is gone; no bid rests.
  EXPECT_EQ(book().best_ask(), 102);
  EXPECT_EQ(book().volume_at(Side::Sell, 102), 3);
  EXPECT_EQ(book().volume_at(Side::Sell, 101), 0);
  EXPECT_EQ(book().level_count(Side::Sell), 1u);
  EXPECT_FALSE(book().best_bid().has_value());
  EXPECT_EQ(book().find(s1.order_id), nullptr);
  ASSERT_NE(book().find(s2.order_id), nullptr);
  EXPECT_EQ(book().find(s2.order_id)->remaining_quantity, 3);
}

// ---- Price priority ------------------------------------------------------------------------

TEST_F(MatchingEngineTest, BuyMatchesLowestAskFirstRegardlessOfArrival) {
  engine_.submit(limit(Side::Sell, 105, 1));
  engine_.submit(limit(Side::Sell, 103, 1));
  engine_.submit(limit(Side::Sell, 104, 1));
  const auto buy = engine_.submit(limit(Side::Buy, 105, 3));
  EXPECT_EQ(fills(buy), (std::vector<Fill>{{103, 1}, {104, 1}, {105, 1}}));
}

TEST_F(MatchingEngineTest, SellMatchesHighestBidFirstRegardlessOfArrival) {
  engine_.submit(limit(Side::Buy, 95, 1));
  engine_.submit(limit(Side::Buy, 97, 1));
  engine_.submit(limit(Side::Buy, 96, 1));
  const auto sell = engine_.submit(limit(Side::Sell, 95, 3));
  EXPECT_EQ(fills(sell), (std::vector<Fill>{{97, 1}, {96, 1}, {95, 1}}));
}

TEST_F(MatchingEngineTest, TradesExecuteAtRestingPriceGivingPriceImprovement) {
  engine_.submit(limit(Side::Sell, 100, 5));
  const auto buy = engine_.submit(limit(Side::Buy, 110, 5));
  EXPECT_EQ(fills(buy), (std::vector<Fill>{{100, 5}}));

  engine_.submit(limit(Side::Buy, 90, 5));
  const auto sell = engine_.submit(limit(Side::Sell, 80, 5));
  EXPECT_EQ(fills(sell), (std::vector<Fill>{{90, 5}}));
}

TEST_F(MatchingEngineTest, LimitPriceStopsTheSweep) {
  engine_.submit(limit(Side::Sell, 100, 5));
  engine_.submit(limit(Side::Sell, 101, 5));
  engine_.submit(limit(Side::Sell, 102, 5));
  const auto buy = engine_.submit(limit(Side::Buy, 101, 20));
  EXPECT_EQ(fills(buy), (std::vector<Fill>{{100, 5}, {101, 5}}));
  EXPECT_EQ(buy.status, OrderStatus::Resting);
  EXPECT_EQ(buy.filled_quantity, 10);
  EXPECT_EQ(buy.resting_quantity, 10);
  EXPECT_EQ(book().best_bid(), 101);
  EXPECT_EQ(book().best_ask(), 102);
}

TEST_F(MatchingEngineTest, OrderAtExactlyTheOppositeBestPriceMatches) {
  engine_.submit(limit(Side::Buy, 100, 5));
  const auto sell = engine_.submit(limit(Side::Sell, 100, 5));
  EXPECT_EQ(sell.status, OrderStatus::Filled);
  EXPECT_TRUE(book().empty());
}

TEST_F(MatchingEngineTest, OneTickAwayDoesNotMatch) {
  engine_.submit(limit(Side::Buy, 100, 5));
  const auto sell = engine_.submit(limit(Side::Sell, 101, 5));
  EXPECT_EQ(sell.status, OrderStatus::Resting);
  EXPECT_TRUE(sell.trades.empty());
  EXPECT_EQ(book().best_bid(), 100);
  EXPECT_EQ(book().best_ask(), 101);
}

// ---- Time priority (FIFO within a level) ---------------------------------------------------

TEST_F(MatchingEngineTest, FifoAtSamePrice) {
  const auto first = engine_.submit(limit(Side::Sell, 100, 3, 1));
  const auto second = engine_.submit(limit(Side::Sell, 100, 3, 2));
  const auto third = engine_.submit(limit(Side::Sell, 100, 3, 3));
  const auto buy = engine_.submit(limit(Side::Buy, 100, 7, 9));
  ASSERT_EQ(buy.trades.size(), 3u);
  EXPECT_EQ(buy.trades[0].sell_order_id, first.order_id);
  EXPECT_EQ(buy.trades[0].quantity, 3);
  EXPECT_EQ(buy.trades[1].sell_order_id, second.order_id);
  EXPECT_EQ(buy.trades[1].quantity, 3);
  EXPECT_EQ(buy.trades[2].sell_order_id, third.order_id);
  EXPECT_EQ(buy.trades[2].quantity, 1);
  EXPECT_EQ(book().find(third.order_id)->remaining_quantity, 2);
}

TEST_F(MatchingEngineTest, PartiallyFilledRestingOrderKeepsTimePriority) {
  const auto first = engine_.submit(limit(Side::Buy, 100, 10, 1));
  const auto second = engine_.submit(limit(Side::Buy, 100, 10, 2));
  engine_.submit(limit(Side::Sell, 100, 4));  // Partially fills `first`.

  const auto orders = book().orders_at(Side::Buy, 100);
  ASSERT_EQ(orders.size(), 2u);
  EXPECT_EQ(orders[0]->id, first.order_id);
  EXPECT_EQ(orders[0]->remaining_quantity, 6);
  EXPECT_EQ(orders[1]->id, second.order_id);

  const auto sell = engine_.submit(limit(Side::Sell, 100, 7));
  ASSERT_EQ(sell.trades.size(), 2u);
  EXPECT_EQ(sell.trades[0].buy_order_id, first.order_id);
  EXPECT_EQ(sell.trades[0].quantity, 6);
  EXPECT_EQ(sell.trades[1].buy_order_id, second.order_id);
  EXPECT_EQ(sell.trades[1].quantity, 1);
}

TEST_F(MatchingEngineTest, NewOrderAtExistingLevelJoinsTheBackOfTheQueue) {
  const auto a = engine_.submit(limit(Side::Sell, 100, 1));
  const auto b = engine_.submit(limit(Side::Sell, 100, 1));
  engine_.submit(limit(Side::Buy, 100, 1));  // Consumes `a`.
  const auto c = engine_.submit(limit(Side::Sell, 100, 1));
  const auto orders = book().orders_at(Side::Sell, 100);
  ASSERT_EQ(orders.size(), 2u);
  EXPECT_EQ(orders[0]->id, b.order_id);
  EXPECT_EQ(orders[1]->id, c.order_id);
  EXPECT_EQ(book().find(a.order_id), nullptr);
}

TEST_F(MatchingEngineTest, CancelledOrderLosesPlaceAndOthersAdvance) {
  const auto a = engine_.submit(limit(Side::Sell, 100, 1));
  const auto b = engine_.submit(limit(Side::Sell, 100, 1));
  const auto c = engine_.submit(limit(Side::Sell, 100, 1));
  EXPECT_TRUE(engine_.cancel(instrument_, b.order_id).cancelled());
  const auto buy = engine_.submit(limit(Side::Buy, 100, 2));
  ASSERT_EQ(buy.trades.size(), 2u);
  EXPECT_EQ(buy.trades[0].sell_order_id, a.order_id);
  EXPECT_EQ(buy.trades[1].sell_order_id, c.order_id);
}

// ---- Partial and full fills ----------------------------------------------------------------

TEST_F(MatchingEngineTest, IncomingPartiallyFilledRemainderRests) {
  engine_.submit(limit(Side::Sell, 100, 4));
  const auto buy = engine_.submit(limit(Side::Buy, 100, 10));
  EXPECT_EQ(buy.status, OrderStatus::Resting);
  EXPECT_EQ(buy.filled_quantity, 4);
  EXPECT_EQ(buy.resting_quantity, 6);
  EXPECT_EQ(book().best_bid(), 100);
  EXPECT_EQ(book().volume_at(Side::Buy, 100), 6);
  EXPECT_FALSE(book().best_ask().has_value());
  const Order* resting = book().find(buy.order_id);
  ASSERT_NE(resting, nullptr);
  EXPECT_EQ(resting->original_quantity, 10);
  EXPECT_EQ(resting->remaining_quantity, 6);
  EXPECT_EQ(resting->filled_quantity(), 4);
}

TEST_F(MatchingEngineTest, ExactQuantityMatchFullyFillsBoth) {
  const auto sell = engine_.submit(limit(Side::Sell, 100, 10));
  const auto buy = engine_.submit(limit(Side::Buy, 100, 10));
  EXPECT_EQ(buy.status, OrderStatus::Filled);
  EXPECT_EQ(fills(buy), (std::vector<Fill>{{100, 10}}));
  EXPECT_EQ(book().find(sell.order_id), nullptr);
  EXPECT_TRUE(book().empty());
  EXPECT_EQ(book().level_count(Side::Sell), 0u);
}

TEST_F(MatchingEngineTest, LargeOrderCrossesManyLevelsAndRestsRemainder) {
  Quantity total = 0;
  for (Price price = 100; price < 150; ++price) {
    engine_.submit(limit(Side::Sell, price, price - 99));
    total += price - 99;
  }
  const auto buy = engine_.submit(limit(Side::Buy, 149, total + 25));
  EXPECT_EQ(buy.trades.size(), 50u);
  EXPECT_EQ(buy.filled_quantity, total);
  EXPECT_EQ(buy.resting_quantity, 25);
  EXPECT_EQ(book().level_count(Side::Sell), 0u);
  EXPECT_EQ(book().best_bid(), 149);
  for (std::size_t i = 1; i < buy.trades.size(); ++i) {
    EXPECT_LT(buy.trades[i - 1].price, buy.trades[i].price);
  }
}

// ---- Market orders -------------------------------------------------------------------------

TEST_F(MatchingEngineTest, MarketOrderSweepsBestPrices) {
  engine_.submit(limit(Side::Sell, 101, 10));
  engine_.submit(limit(Side::Sell, 102, 5));
  const auto buy = engine_.submit(market(Side::Buy, 12));
  EXPECT_EQ(buy.status, OrderStatus::Filled);
  EXPECT_EQ(fills(buy), (std::vector<Fill>{{101, 10}, {102, 2}}));
  EXPECT_EQ(book().volume_at(Side::Sell, 102), 3);
}

TEST_F(MatchingEngineTest, MarketOrderRemainderIsCancelledNotRested) {
  engine_.submit(limit(Side::Buy, 99, 4));
  engine_.submit(limit(Side::Buy, 98, 4));
  const auto sell = engine_.submit(market(Side::Sell, 10));
  EXPECT_EQ(sell.status, OrderStatus::Cancelled);
  EXPECT_EQ(sell.cancel_reason, CancelReason::NoLiquidity);
  EXPECT_EQ(fills(sell), (std::vector<Fill>{{99, 4}, {98, 4}}));
  EXPECT_EQ(sell.filled_quantity, 8);
  EXPECT_EQ(sell.cancelled_quantity, 2);
  EXPECT_EQ(sell.resting_quantity, 0);
  EXPECT_TRUE(book().empty());
  EXPECT_EQ(book().find(sell.order_id), nullptr);
}

TEST_F(MatchingEngineTest, MarketOrderAgainstEmptyBookIsCancelled) {
  const auto buy = engine_.submit(market(Side::Buy, 5));
  EXPECT_TRUE(buy.accepted());
  EXPECT_EQ(buy.status, OrderStatus::Cancelled);
  EXPECT_EQ(buy.cancel_reason, CancelReason::NoLiquidity);
  EXPECT_EQ(buy.filled_quantity, 0);
  EXPECT_EQ(buy.cancelled_quantity, 5);
  EXPECT_TRUE(book().empty());
}

TEST_F(MatchingEngineTest, MarketOrderIgnoresSameSideLiquidity) {
  engine_.submit(limit(Side::Buy, 100, 5));
  const auto buy = engine_.submit(market(Side::Buy, 5));
  EXPECT_EQ(buy.status, OrderStatus::Cancelled);
  EXPECT_EQ(buy.filled_quantity, 0);
  EXPECT_EQ(book().volume_at(Side::Buy, 100), 5);
}

TEST_F(MatchingEngineTest, MarketOrderTimeInForceDoesNotMakeItRest) {
  auto request = market(Side::Buy, 5);
  request.time_in_force = TimeInForce::GoodTillCancel;
  const auto result = engine_.submit(request);
  EXPECT_EQ(result.status, OrderStatus::Cancelled);
  EXPECT_TRUE(book().empty());
}

// ---- Immediate-or-cancel limit orders ------------------------------------------------------

TEST_F(MatchingEngineTest, IocLimitFillsWhatItCanAndCancelsTheRest) {
  engine_.submit(limit(Side::Sell, 100, 3));
  engine_.submit(limit(Side::Sell, 102, 3));
  const auto buy = engine_.submit(ioc(Side::Buy, 101, 10));
  EXPECT_EQ(buy.status, OrderStatus::Cancelled);
  EXPECT_EQ(fills(buy), (std::vector<Fill>{{100, 3}}));
  EXPECT_EQ(buy.cancelled_quantity, 7);
  EXPECT_FALSE(book().best_bid().has_value());
  EXPECT_EQ(book().best_ask(), 102);
}

TEST_F(MatchingEngineTest, IocLimitFullyFilledReportsFilled) {
  engine_.submit(limit(Side::Sell, 100, 10));
  const auto buy = engine_.submit(ioc(Side::Buy, 100, 10));
  EXPECT_EQ(buy.status, OrderStatus::Filled);
  EXPECT_EQ(buy.cancelled_quantity, 0);
}

// ---- Cancellation --------------------------------------------------------------------------

TEST_F(MatchingEngineTest, CancelRemovesRestingOrder) {
  const auto order = engine_.submit(limit(Side::Buy, 100, 10, 4));
  const auto cancel = engine_.cancel(instrument_, order.order_id);
  EXPECT_EQ(cancel.outcome, CancelOutcome::Cancelled);
  EXPECT_EQ(cancel.cancelled_quantity, 10);
  EXPECT_EQ(cancel.owner, 4u);
  EXPECT_EQ(cancel.side, Side::Buy);
  EXPECT_EQ(cancel.price, 100);
  EXPECT_TRUE(book().empty());
  EXPECT_EQ(book().level_count(Side::Buy), 0u);
}

TEST_F(MatchingEngineTest, CancelReportsOnlyRemainingQuantityOfPartiallyFilledOrder) {
  const auto order = engine_.submit(limit(Side::Buy, 100, 10));
  engine_.submit(limit(Side::Sell, 100, 4));
  const auto cancel = engine_.cancel(instrument_, order.order_id);
  EXPECT_TRUE(cancel.cancelled());
  EXPECT_EQ(cancel.cancelled_quantity, 6);
}

TEST_F(MatchingEngineTest, CancelledOrderNeverExecutes) {
  const auto order = engine_.submit(limit(Side::Sell, 100, 10));
  engine_.cancel(instrument_, order.order_id);
  const auto buy = engine_.submit(limit(Side::Buy, 100, 10));
  EXPECT_TRUE(buy.trades.empty());
  EXPECT_EQ(buy.status, OrderStatus::Resting);
}

TEST_F(MatchingEngineTest, CancelTwiceFailsTheSecondTime) {
  const auto order = engine_.submit(limit(Side::Buy, 100, 10));
  EXPECT_TRUE(engine_.cancel(instrument_, order.order_id).cancelled());
  const auto again = engine_.cancel(instrument_, order.order_id);
  EXPECT_EQ(again.outcome, CancelOutcome::OrderNotFound);
  EXPECT_EQ(again.cancelled_quantity, 0);
}

TEST_F(MatchingEngineTest, CancelFilledOrderFails) {
  const auto order = engine_.submit(limit(Side::Buy, 100, 10));
  engine_.submit(limit(Side::Sell, 100, 10));
  EXPECT_EQ(engine_.cancel(instrument_, order.order_id).outcome, CancelOutcome::OrderNotFound);
}

TEST_F(MatchingEngineTest, CancelUnknownIdsAndInstruments) {
  EXPECT_EQ(engine_.cancel(instrument_, 12345).outcome, CancelOutcome::OrderNotFound);
  EXPECT_EQ(engine_.cancel(instrument_, kInvalidOrderId).outcome, CancelOutcome::OrderNotFound);
  EXPECT_EQ(engine_.cancel(99, 1).outcome, CancelOutcome::UnknownInstrument);
}

TEST_F(MatchingEngineTest, CancelWithWrongInstrumentFails) {
  const InstrumentId other = engine_.add_instrument();
  const auto order = engine_.submit(limit(Side::Buy, 100, 10));
  EXPECT_EQ(engine_.cancel(other, order.order_id).outcome, CancelOutcome::OrderNotFound);
  EXPECT_EQ(book().order_count(), 1u);
}

TEST_F(MatchingEngineTest, CancelMiddleOfQueueKeepsLevel) {
  engine_.submit(limit(Side::Buy, 100, 1));
  const auto mid = engine_.submit(limit(Side::Buy, 100, 2));
  engine_.submit(limit(Side::Buy, 100, 3));
  engine_.cancel(instrument_, mid.order_id);
  EXPECT_EQ(book().volume_at(Side::Buy, 100), 4);
  EXPECT_EQ(book().side(Side::Buy).best()->order_count(), 2u);
}

TEST_F(MatchingEngineTest, CancellingLastOrderAtBestRevealsNextLevel) {
  const auto best = engine_.submit(limit(Side::Sell, 100, 1));
  engine_.submit(limit(Side::Sell, 105, 1));
  engine_.cancel(instrument_, best.order_id);
  EXPECT_EQ(book().best_ask(), 105);
  EXPECT_EQ(book().level_count(Side::Sell), 1u);
}

// ---- Validation ----------------------------------------------------------------------------

TEST_F(MatchingEngineTest, RejectsInvalidQuantities) {
  for (const Quantity quantity : {Quantity{0}, Quantity{-1}, kMaxOrderQuantity + 1}) {
    const auto result = engine_.submit(limit(Side::Buy, 100, quantity));
    EXPECT_EQ(result.status, OrderStatus::Rejected) << quantity;
    EXPECT_EQ(result.reject_reason, RejectReason::InvalidQuantity) << quantity;
    EXPECT_EQ(result.order_id, kInvalidOrderId);
  }
  EXPECT_EQ(engine_.submit(market(Side::Buy, 0)).reject_reason, RejectReason::InvalidQuantity);
  EXPECT_TRUE(book().empty());
}

TEST_F(MatchingEngineTest, AcceptsBoundaryQuantityAndPrice) {
  const auto result = engine_.submit(limit(Side::Buy, kMaxPrice, kMaxOrderQuantity));
  EXPECT_EQ(result.status, OrderStatus::Resting);
  const auto at_max = engine_.submit(limit(Side::Sell, kMaxPrice, 1));
  EXPECT_EQ(at_max.status, OrderStatus::Filled);
  const auto min_price = engine_.submit(limit(Side::Buy, 1, 1));
  EXPECT_EQ(min_price.status, OrderStatus::Resting);
}

TEST_F(MatchingEngineTest, RejectsInvalidLimitPrices) {
  for (const Price price : {Price{0}, Price{-5}, kMaxPrice + 1}) {
    const auto result = engine_.submit(limit(Side::Sell, price, 1));
    EXPECT_EQ(result.reject_reason, RejectReason::InvalidPrice) << price;
  }
  EXPECT_TRUE(book().empty());
}

TEST_F(MatchingEngineTest, RejectsMarketOrderWithPrice) {
  auto request = market(Side::Buy, 1);
  request.price = 100;
  EXPECT_EQ(engine_.submit(request).reject_reason, RejectReason::MarketOrderWithPrice);
}

TEST_F(MatchingEngineTest, RejectsUnknownInstrument) {
  const auto result = engine_.submit(limit(Side::Buy, 100, 1, 1, 42));
  EXPECT_EQ(result.reject_reason, RejectReason::UnknownInstrument);
  EXPECT_FALSE(result.accepted());
}

TEST_F(MatchingEngineTest, RejectedOrderDoesNotConsumeAnOrderId) {
  const auto a = engine_.submit(limit(Side::Buy, 100, 1));
  engine_.submit(limit(Side::Buy, 0, 1));
  const auto b = engine_.submit(limit(Side::Buy, 100, 1));
  EXPECT_EQ(b.order_id, a.order_id + 1);
}

TEST_F(MatchingEngineTest, BookAccessorRejectsUnknownInstrument) {
  EXPECT_THROW(static_cast<void>(engine_.book(7)), std::out_of_range);
}

// ---- Instrument isolation ------------------------------------------------------------------

TEST_F(MatchingEngineTest, InstrumentsHaveIndependentBooks) {
  const InstrumentId other = engine_.add_instrument();
  engine_.submit(limit(Side::Sell, 100, 5, 1, instrument_));
  const auto buy = engine_.submit(limit(Side::Buy, 100, 5, 2, other));
  EXPECT_TRUE(buy.trades.empty());
  EXPECT_EQ(engine_.book(other).best_bid(), 100);
  EXPECT_EQ(book().best_ask(), 100);
  engine_.book(other).check_invariants();
}

// ---- Self-trade prevention -----------------------------------------------------------------

TEST_F(MatchingEngineTest, SelfTradesAllowedByDefault) {
  engine_.submit(limit(Side::Sell, 100, 5, 7));
  const auto buy = engine_.submit(limit(Side::Buy, 100, 5, 7));
  ASSERT_EQ(buy.trades.size(), 1u);
  EXPECT_EQ(buy.trades[0].buyer, 7u);
  EXPECT_EQ(buy.trades[0].seller, 7u);
}

TEST_F(MatchingEngineTest, StpCancelRestingRemovesOwnOrderAndKeepsMatching) {
  const auto own = engine_.submit(limit(Side::Sell, 100, 5, 7));
  const auto other = engine_.submit(limit(Side::Sell, 100, 5, 8));
  const auto buy = engine_.submit(
      with_stp(limit(Side::Buy, 100, 8, 7), SelfTradePrevention::CancelResting));
  ASSERT_EQ(buy.resting_cancels.size(), 1u);
  EXPECT_EQ(buy.resting_cancels[0].order_id, own.order_id);
  EXPECT_EQ(buy.resting_cancels[0].cancelled_quantity, 5);
  EXPECT_EQ(buy.resting_cancels[0].reason, CancelReason::SelfTradePrevention);
  ASSERT_EQ(buy.trades.size(), 1u);
  EXPECT_EQ(buy.trades[0].sell_order_id, other.order_id);
  EXPECT_EQ(buy.filled_quantity, 5);
  EXPECT_EQ(buy.status, OrderStatus::Resting);
  EXPECT_EQ(buy.resting_quantity, 3);
  EXPECT_EQ(book().find(own.order_id), nullptr);
  EXPECT_EQ(book().best_bid(), 100);
}

TEST_F(MatchingEngineTest, StpCancelRestingClearsWholeLevelOfOwnOrders) {
  engine_.submit(limit(Side::Sell, 100, 5, 7));
  engine_.submit(limit(Side::Sell, 100, 5, 7));
  engine_.submit(limit(Side::Sell, 101, 5, 8));
  const auto buy = engine_.submit(
      with_stp(limit(Side::Buy, 101, 5, 7), SelfTradePrevention::CancelResting));
  EXPECT_EQ(buy.resting_cancels.size(), 2u);
  EXPECT_EQ(fills(buy), (std::vector<Fill>{{101, 5}}));
  EXPECT_EQ(buy.status, OrderStatus::Filled);
  EXPECT_TRUE(book().empty());
}

TEST_F(MatchingEngineTest, StpCancelRestingReportsRemainingNotOriginalQuantity) {
  const auto own = engine_.submit(limit(Side::Sell, 100, 10, 7));
  engine_.submit(limit(Side::Buy, 100, 4, 8));  // Partially fills `own`: 6 remain.
  const auto buy = engine_.submit(
      with_stp(limit(Side::Buy, 100, 3, 7), SelfTradePrevention::CancelResting));
  ASSERT_EQ(buy.resting_cancels.size(), 1u);
  EXPECT_EQ(buy.resting_cancels[0].order_id, own.order_id);
  EXPECT_EQ(buy.resting_cancels[0].cancelled_quantity, 6);
  EXPECT_EQ(buy.resting_cancels[0].owner, 7u);
  EXPECT_EQ(buy.resting_cancels[0].side, Side::Sell);
  EXPECT_EQ(buy.resting_cancels[0].price, 100);
  EXPECT_TRUE(buy.trades.empty());
  EXPECT_EQ(buy.status, OrderStatus::Resting);
}

TEST_F(MatchingEngineTest, StpCancelIncomingStopsMatchingAndCancelsRemainder) {
  engine_.submit(limit(Side::Sell, 100, 5, 8));
  const auto own = engine_.submit(limit(Side::Sell, 100, 5, 7));
  const auto buy = engine_.submit(
      with_stp(limit(Side::Buy, 101, 10, 7), SelfTradePrevention::CancelIncoming));
  EXPECT_EQ(fills(buy), (std::vector<Fill>{{100, 5}}));
  EXPECT_EQ(buy.status, OrderStatus::Cancelled);
  EXPECT_EQ(buy.cancel_reason, CancelReason::SelfTradePrevention);
  EXPECT_EQ(buy.cancelled_quantity, 5);
  EXPECT_EQ(buy.resting_quantity, 0);
  EXPECT_TRUE(buy.resting_cancels.empty());
  ASSERT_NE(book().find(own.order_id), nullptr);
  EXPECT_FALSE(book().best_bid().has_value());  // GTC remainder must not rest crossed.
}

TEST_F(MatchingEngineTest, StpDoesNotApplyToAnonymousOrders) {
  engine_.submit(limit(Side::Sell, 100, 5, kAnonymousTrader));
  const auto buy = engine_.submit(with_stp(limit(Side::Buy, 100, 5, kAnonymousTrader),
                                           SelfTradePrevention::CancelIncoming));
  EXPECT_EQ(buy.status, OrderStatus::Filled);
}

TEST_F(MatchingEngineTest, StpOnlyAffectsSameOwner) {
  engine_.submit(limit(Side::Sell, 100, 5, 8));
  const auto buy = engine_.submit(
      with_stp(limit(Side::Buy, 100, 5, 7), SelfTradePrevention::CancelIncoming));
  EXPECT_EQ(buy.status, OrderStatus::Filled);
}

// ---- Accounting identity -------------------------------------------------------------------

TEST_F(MatchingEngineTest, QuantityPartitionHoldsForEveryOutcome) {
  engine_.submit(limit(Side::Sell, 100, 3));
  const std::vector<SubmitResult> results = {
      engine_.submit(limit(Side::Buy, 100, 5)),         // partial fill, rests
      engine_.submit(market(Side::Sell, 9)),            // partial fill, cancelled
      engine_.submit(limit(Side::Sell, 110, 4)),        // rests
      engine_.submit(ioc(Side::Buy, 110, 4)),           // fills
      engine_.submit(ioc(Side::Buy, 90, 4)),            // nothing, cancelled
  };
  for (const auto& r : results) {
    EXPECT_EQ(r.filled_quantity + r.resting_quantity + r.cancelled_quantity,
              r.requested_quantity);
    Quantity traded = 0;
    for (const Trade& t : r.trades) {
      traded += t.quantity;
    }
    EXPECT_EQ(traded, r.filled_quantity);
  }
}

TEST_F(MatchingEngineTest, TradeTimestampsComeFromTheAggressor) {
  auto sell = limit(Side::Sell, 100, 5);
  sell.timestamp = 1'000;
  engine_.submit(sell);
  auto buy = limit(Side::Buy, 100, 5);
  buy.timestamp = 2'500;
  const auto result = engine_.submit(buy);
  ASSERT_EQ(result.trades.size(), 1u);
  EXPECT_EQ(result.trades[0].timestamp, 2'500);
}

}  // namespace
}  // namespace udeshi
