#include <gtest/gtest.h>

#include <stdexcept>
#include <vector>

#include "udeshi/order_book.hpp"

namespace udeshi {
namespace {

Order make_order(OrderId id, Side side, Price price, Quantity quantity, Sequence sequence) {
  Order order;
  order.id = id;
  order.side = side;
  order.price = price;
  order.original_quantity = quantity;
  order.remaining_quantity = quantity;
  order.sequence = sequence;
  return order;
}

class OrderBookTest : public ::testing::Test {
 protected:
  void TearDown() override { book_.check_invariants(); }
  OrderBook book_{0};
};

TEST_F(OrderBookTest, EmptyBook) {
  EXPECT_TRUE(book_.empty());
  EXPECT_FALSE(book_.best_bid().has_value());
  EXPECT_FALSE(book_.best_ask().has_value());
  EXPECT_EQ(book_.order_count(), 0u);
  EXPECT_EQ(book_.level_count(Side::Buy), 0u);
  EXPECT_EQ(book_.volume_at(Side::Sell, 100), 0);
  EXPECT_EQ(book_.find(1), nullptr);
  EXPECT_TRUE(book_.depth(Side::Buy, 10).empty());
  EXPECT_TRUE(book_.orders_at(Side::Buy, 100).empty());
  EXPECT_EQ(book_.best_level(Side::Buy), nullptr);
}

TEST_F(OrderBookTest, BidsSortDescendingAsksAscending) {
  book_.insert(make_order(1, Side::Buy, 98, 1, 1));
  book_.insert(make_order(2, Side::Buy, 100, 2, 2));
  book_.insert(make_order(3, Side::Buy, 99, 3, 3));
  book_.insert(make_order(4, Side::Sell, 103, 4, 4));
  book_.insert(make_order(5, Side::Sell, 101, 5, 5));
  book_.insert(make_order(6, Side::Sell, 102, 6, 6));

  EXPECT_EQ(book_.best_bid(), 100);
  EXPECT_EQ(book_.best_ask(), 101);
  EXPECT_EQ(book_.depth(Side::Buy, 10),
            (std::vector<LevelView>{{100, 2, 1}, {99, 3, 1}, {98, 1, 1}}));
  EXPECT_EQ(book_.depth(Side::Sell, 10),
            (std::vector<LevelView>{{101, 5, 1}, {102, 6, 1}, {103, 4, 1}}));
  EXPECT_EQ(book_.depth(Side::Sell, 2), (std::vector<LevelView>{{101, 5, 1}, {102, 6, 1}}));
  EXPECT_TRUE(book_.depth(Side::Sell, 0).empty());
  EXPECT_EQ(book_.total_volume(Side::Buy), 6);
  EXPECT_EQ(book_.total_volume(Side::Sell), 15);
}

TEST_F(OrderBookTest, LevelsAggregateQuantityAndCount) {
  book_.insert(make_order(1, Side::Buy, 100, 5, 1));
  book_.insert(make_order(2, Side::Buy, 100, 7, 2));
  EXPECT_EQ(book_.level_count(Side::Buy), 1u);
  EXPECT_EQ(book_.volume_at(Side::Buy, 100), 12);
  const auto snapshot = book_.snapshot(5);
  ASSERT_EQ(snapshot.bids.size(), 1u);
  EXPECT_EQ(snapshot.bids[0], (LevelView{100, 12, 2}));
  EXPECT_TRUE(snapshot.asks.empty());
}

TEST_F(OrderBookTest, OrdersAtReturnsFifoOrder) {
  book_.insert(make_order(10, Side::Sell, 100, 1, 1));
  book_.insert(make_order(11, Side::Sell, 100, 1, 2));
  book_.insert(make_order(12, Side::Sell, 100, 1, 3));
  const auto orders = book_.orders_at(Side::Sell, 100);
  ASSERT_EQ(orders.size(), 3u);
  EXPECT_EQ(orders[0]->id, 10u);
  EXPECT_EQ(orders[1]->id, 11u);
  EXPECT_EQ(orders[2]->id, 12u);
}

TEST_F(OrderBookTest, ExecutePartialKeepsOrderFullRemovesIt) {
  Order& order = book_.insert(make_order(1, Side::Buy, 100, 10, 1));
  EXPECT_FALSE(book_.execute(order, 3));
  EXPECT_EQ(book_.volume_at(Side::Buy, 100), 7);
  EXPECT_EQ(book_.find(1)->remaining_quantity, 7);
  EXPECT_TRUE(book_.execute(*book_.find_mutable(1), 7));
  EXPECT_EQ(book_.find(1), nullptr);
  EXPECT_TRUE(book_.empty());
  EXPECT_EQ(book_.level_count(Side::Buy), 0u);
}

TEST_F(OrderBookTest, RemoveDropsEmptyLevelsOnly) {
  book_.insert(make_order(1, Side::Sell, 100, 1, 1));
  book_.insert(make_order(2, Side::Sell, 100, 1, 2));
  book_.insert(make_order(3, Side::Sell, 101, 1, 3));
  book_.remove(*book_.find_mutable(1));
  EXPECT_EQ(book_.level_count(Side::Sell), 2u);
  book_.remove(*book_.find_mutable(2));
  EXPECT_EQ(book_.level_count(Side::Sell), 1u);
  EXPECT_EQ(book_.best_ask(), 101);
}

TEST_F(OrderBookTest, InsertRejectsDuplicateIdWithoutSideEffects) {
  book_.insert(make_order(1, Side::Buy, 100, 5, 1));
  EXPECT_THROW(book_.insert(make_order(1, Side::Buy, 99, 5, 2)), std::invalid_argument);
  EXPECT_EQ(book_.order_count(), 1u);
  EXPECT_EQ(book_.level_count(Side::Buy), 1u);
}

TEST_F(OrderBookTest, InsertRejectsNonPositiveQuantity) {
  EXPECT_THROW(book_.insert(make_order(1, Side::Buy, 100, 0, 1)), std::invalid_argument);
  EXPECT_TRUE(book_.empty());
}

TEST_F(OrderBookTest, InvariantCheckerDetectsCrossedBook) {
  book_.insert(make_order(1, Side::Buy, 101, 1, 1));
  book_.insert(make_order(2, Side::Sell, 100, 1, 2));
  EXPECT_THROW(book_.check_invariants(), std::logic_error);
  book_.remove(*book_.find_mutable(2));  // Restore a valid state for TearDown.
}

TEST_F(OrderBookTest, InvariantCheckerDetectsOutOfOrderSequences) {
  book_.insert(make_order(1, Side::Buy, 100, 1, 5));
  book_.insert(make_order(2, Side::Buy, 100, 1, 3));
  EXPECT_THROW(book_.check_invariants(), std::logic_error);
  book_.remove(*book_.find_mutable(2));
}

TEST_F(OrderBookTest, ManyOrdersAcrossManyLevels) {
  OrderId id = 1;
  for (Price price = 1; price <= 500; ++price) {
    for (int k = 0; k < 4; ++k) {
      book_.insert(make_order(id, Side::Buy, price, k + 1, id));
      ++id;
    }
  }
  EXPECT_EQ(book_.order_count(), 2000u);
  EXPECT_EQ(book_.level_count(Side::Buy), 500u);
  EXPECT_EQ(book_.best_bid(), 500);
  EXPECT_EQ(book_.volume_at(Side::Buy, 250), 10);
  book_.check_invariants();
  for (OrderId remove_id = 1; remove_id < id; remove_id += 2) {
    book_.remove(*book_.find_mutable(remove_id));
  }
  EXPECT_EQ(book_.order_count(), 1000u);
  EXPECT_EQ(book_.volume_at(Side::Buy, 250), 6);
}

}  // namespace
}  // namespace udeshi
