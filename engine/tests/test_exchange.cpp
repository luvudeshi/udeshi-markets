#include <gtest/gtest.h>

#include <cmath>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

#include "test_helpers.hpp"
#include "udeshi/exchange.hpp"

namespace udeshi {
namespace {

using testing::limit;
using testing::market;

class ExchangeTest : public ::testing::Test {
 protected:
  void SetUp() override {
    acme_ = exchange_.add_instrument({"ACME", 0.01, 10'000});
    nova_ = exchange_.add_instrument({"NOVA", 0.05, 2'000});
  }

  Exchange exchange_{5};  // Small recent-trade buffer to exercise eviction.
  InstrumentId acme_ = 0;
  InstrumentId nova_ = 0;
};

TEST_F(ExchangeTest, RegistersInstrumentsWithDenseIds) {
  EXPECT_EQ(acme_, 0u);
  EXPECT_EQ(nova_, 1u);
  EXPECT_EQ(exchange_.instrument_count(), 2u);
  EXPECT_EQ(exchange_.symbols(), (std::vector<std::string>{"ACME", "NOVA"}));
  EXPECT_EQ(exchange_.find_instrument("NOVA"), nova_);
  EXPECT_FALSE(exchange_.find_instrument("ZZZ").has_value());
  EXPECT_EQ(exchange_.instrument_id("ACME"), acme_);
  EXPECT_THROW(static_cast<void>(exchange_.instrument_id("ZZZ")), std::out_of_range);
  EXPECT_EQ(exchange_.instrument(nova_).tick_size, 0.05);
  EXPECT_EQ(exchange_.engine().instrument_count(), 2u);
}

TEST_F(ExchangeTest, RejectsInvalidInstrumentSpecs) {
  EXPECT_THROW(exchange_.add_instrument({"ACME", 0.01, 100}), std::invalid_argument);
  EXPECT_THROW(exchange_.add_instrument({"", 0.01, 100}), std::invalid_argument);
  EXPECT_THROW(exchange_.add_instrument({"lower", 0.01, 100}), std::invalid_argument);
  EXPECT_THROW(exchange_.add_instrument({"WAY_TOO_LONG_SYMBOL", 0.01, 100}),
               std::invalid_argument);
  EXPECT_THROW(exchange_.add_instrument({"BAD", 0.0, 100}), std::invalid_argument);
  EXPECT_THROW(exchange_.add_instrument({"BAD", -0.01, 100}), std::invalid_argument);
  EXPECT_THROW(exchange_.add_instrument({"BAD", std::nan(""), 100}), std::invalid_argument);
  EXPECT_THROW(exchange_.add_instrument({"BAD", 0.01, 0}), std::invalid_argument);
  EXPECT_THROW(exchange_.add_instrument({"BAD", 0.01, kMaxPrice + 1}), std::invalid_argument);
  // Failed registrations leave no trace in either the exchange or the engine.
  EXPECT_EQ(exchange_.instrument_count(), 2u);
  EXPECT_EQ(exchange_.engine().instrument_count(), 2u);
  EXPECT_FALSE(exchange_.find_instrument("BAD").has_value());
}

TEST_F(ExchangeTest, StatisticsBeforeAnyTradeUseReferencePrice) {
  const auto stats = exchange_.statistics(acme_);
  EXPECT_EQ(stats.reference_price, 10'000);
  EXPECT_EQ(stats.current_price(), 10'000);
  EXPECT_FALSE(stats.last_price.has_value());
  EXPECT_FALSE(stats.vwap.has_value());
  EXPECT_FALSE(stats.midprice.has_value());
  EXPECT_FALSE(stats.spread.has_value());
  EXPECT_EQ(stats.volume, 0);
  EXPECT_EQ(stats.trade_count, 0u);
}

TEST_F(ExchangeTest, TopOfBookMidAndSpread) {
  exchange_.submit(limit(Side::Buy, 9'990, 10, 1, acme_));
  EXPECT_EQ(exchange_.best_bid(acme_), 9'990);
  EXPECT_FALSE(exchange_.midprice(acme_).has_value());  // One-sided book.
  EXPECT_FALSE(exchange_.spread(acme_).has_value());
  exchange_.submit(limit(Side::Sell, 10'005, 10, 2, acme_));
  EXPECT_EQ(exchange_.best_ask(acme_), 10'005);
  EXPECT_DOUBLE_EQ(*exchange_.midprice(acme_), 9'997.5);
  EXPECT_EQ(exchange_.spread(acme_), 15);
  const auto snapshot = exchange_.book_snapshot(acme_, 10);
  EXPECT_EQ(snapshot.instrument, acme_);
  ASSERT_EQ(snapshot.bids.size(), 1u);
  ASSERT_EQ(snapshot.asks.size(), 1u);
}

TEST_F(ExchangeTest, MarketDataDerivesFromExecutedTradesOnly) {
  exchange_.submit(limit(Side::Sell, 10'010, 5, 1, acme_));
  exchange_.submit(limit(Side::Sell, 10'020, 5, 1, acme_));
  exchange_.submit(limit(Side::Buy, 9'900, 5, 3, acme_));
  // Resting orders alone do not move the traded price.
  EXPECT_FALSE(exchange_.statistics(acme_).last_price.has_value());

  exchange_.submit(market(Side::Buy, 7, 2, acme_));  // 5 @ 10010, 2 @ 10020
  const auto stats = exchange_.statistics(acme_);
  EXPECT_EQ(stats.last_price, 10'020);
  EXPECT_EQ(stats.open_price, 10'010);
  EXPECT_EQ(stats.high_price, 10'020);
  EXPECT_EQ(stats.low_price, 10'010);
  EXPECT_EQ(stats.volume, 7);
  EXPECT_EQ(stats.trade_count, 2u);
  EXPECT_DOUBLE_EQ(stats.notional, 5.0 * 10'010 + 2.0 * 10'020);
  ASSERT_TRUE(stats.vwap.has_value());
  EXPECT_DOUBLE_EQ(*stats.vwap, (5.0 * 10'010 + 2.0 * 10'020) / 7.0);
  EXPECT_EQ(stats.best_bid, 9'900);
  EXPECT_EQ(stats.best_ask, 10'020);
  EXPECT_EQ(stats.bid_depth, 5);
  EXPECT_EQ(stats.ask_depth, 3);
  EXPECT_EQ(stats.resting_orders, 2u);
  EXPECT_EQ(stats.current_price(), 10'020);

  exchange_.submit(market(Side::Sell, 1, 4, acme_));
  const auto after = exchange_.statistics(acme_);
  EXPECT_EQ(after.last_price, 9'900);
  EXPECT_EQ(after.low_price, 9'900);
  EXPECT_EQ(after.high_price, 10'020);
  EXPECT_EQ(after.open_price, 10'010);
}

TEST_F(ExchangeTest, StatisticsArePerInstrument) {
  exchange_.submit(limit(Side::Sell, 2'000, 5, 1, nova_));
  exchange_.submit(limit(Side::Buy, 2'000, 5, 2, nova_));
  EXPECT_EQ(exchange_.statistics(nova_).volume, 5);
  EXPECT_EQ(exchange_.statistics(acme_).volume, 0);
  EXPECT_TRUE(exchange_.recent_trades(acme_, 10).empty());
  EXPECT_EQ(exchange_.recent_trades(nova_, 10).size(), 1u);
}

TEST_F(ExchangeTest, RecentTradesAreBoundedAndChronological) {
  for (int i = 0; i < 8; ++i) {
    exchange_.submit(limit(Side::Sell, 10'000 + i, 1, 1, acme_));
    exchange_.submit(limit(Side::Buy, 10'000 + i, 1, 2, acme_));
  }
  const auto all = exchange_.recent_trades(acme_, 100);
  ASSERT_EQ(all.size(), 5u);  // Capacity is 5.
  for (std::size_t i = 0; i < all.size(); ++i) {
    EXPECT_EQ(all[i].price, 10'003 + static_cast<Price>(i));
  }
  const auto last_two = exchange_.recent_trades(acme_, 2);
  ASSERT_EQ(last_two.size(), 2u);
  EXPECT_EQ(last_two[0].price, 10'006);
  EXPECT_EQ(last_two[1].price, 10'007);
  EXPECT_TRUE(exchange_.recent_trades(acme_, 0).empty());
  // Statistics are cumulative and unaffected by recent-trade eviction.
  EXPECT_EQ(exchange_.statistics(acme_).trade_count, 8u);
}

TEST_F(ExchangeTest, CancelDelegatesToEngine) {
  const auto order = exchange_.submit(limit(Side::Buy, 9'000, 3, 1, acme_));
  EXPECT_EQ(exchange_.cancel(nova_, order.order_id).outcome, CancelOutcome::OrderNotFound);
  EXPECT_TRUE(exchange_.cancel(acme_, order.order_id).cancelled());
  EXPECT_FALSE(exchange_.best_bid(acme_).has_value());
}

TEST_F(ExchangeTest, RejectedOrdersDoNotAffectMarketData) {
  const auto result = exchange_.submit(limit(Side::Buy, 0, 3, 1, acme_));
  EXPECT_FALSE(result.accepted());
  const auto unknown = exchange_.submit(limit(Side::Buy, 100, 3, 1, 77));
  EXPECT_EQ(unknown.reject_reason, RejectReason::UnknownInstrument);
  EXPECT_EQ(exchange_.statistics(acme_).resting_orders, 0u);
}

TEST_F(ExchangeTest, UnknownInstrumentQueriesThrow) {
  EXPECT_THROW(static_cast<void>(exchange_.statistics(9)), std::out_of_range);
  EXPECT_THROW(static_cast<void>(exchange_.order_book(9)), std::out_of_range);
  EXPECT_THROW(static_cast<void>(exchange_.recent_trades(9, 1)), std::out_of_range);
  EXPECT_THROW(static_cast<void>(exchange_.instrument(9)), std::out_of_range);
  EXPECT_THROW(static_cast<void>(exchange_.to_currency(9, 1)), std::out_of_range);
}

TEST_F(ExchangeTest, TickConversionRoundTrips) {
  EXPECT_EQ(exchange_.to_ticks(acme_, 100.01), 10'001);
  EXPECT_EQ(exchange_.to_ticks(acme_, 0.07), 7);
  EXPECT_DOUBLE_EQ(exchange_.to_currency(acme_, 10'001), 100.01);
  EXPECT_EQ(exchange_.to_ticks(nova_, 100.05), 2'001);
  EXPECT_EQ(exchange_.to_ticks(nova_, 0.0), 0);
  for (Price ticks = 1; ticks < 100'000; ticks += 997) {
    EXPECT_EQ(exchange_.to_ticks(acme_, exchange_.to_currency(acme_, ticks)), ticks);
  }
}

TEST_F(ExchangeTest, TickConversionRejectsOffGridAndNonFinite) {
  EXPECT_THROW(static_cast<void>(exchange_.to_ticks(acme_, 100.005)), std::invalid_argument);
  EXPECT_THROW(static_cast<void>(exchange_.to_ticks(nova_, 100.01)), std::invalid_argument);
  EXPECT_THROW(static_cast<void>(exchange_.to_ticks(acme_, std::nan(""))),
               std::invalid_argument);
  EXPECT_THROW(static_cast<void>(exchange_.to_ticks(
                   acme_, std::numeric_limits<double>::infinity())),
               std::invalid_argument);
  EXPECT_THROW(static_cast<void>(exchange_.to_ticks(acme_, 1e300)), std::invalid_argument);
}

TEST(ExchangeConstruction, RejectsZeroTradeCapacity) {
  EXPECT_THROW(Exchange{0}, std::invalid_argument);
}

}  // namespace
}  // namespace udeshi
