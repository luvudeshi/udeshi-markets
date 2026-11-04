#include <gtest/gtest.h>

#include <stdexcept>
#include <type_traits>
#include <utility>
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
  book_.insert(make_order(2, Side::Buy, 100, 1, 6));
  book_.find_mutable(2)->sequence = 3;  // Corrupt time priority behind insert()'s back.
  EXPECT_THROW(book_.check_invariants(), std::logic_error);
  book_.find_mutable(2)->sequence = 6;
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

// Read-only views of the book must not hand out mutable access to its internals.
static_assert(std::is_same_v<decltype(std::declval<const PriceLevel&>().front()), const Order*>);
static_assert(std::is_same_v<decltype(std::declval<const PriceLevel&>().back()), const Order*>);
static_assert(std::is_same_v<decltype(std::declval<const Order&>().next()), const Order*>);
static_assert(std::is_same_v<decltype(std::declval<const Order&>().prev()), const Order*>);
static_assert(std::is_same_v<decltype(std::declval<const Order&>().level()), const PriceLevel*>);
static_assert(std::is_same_v<decltype(std::declval<const BookSide&>().best()), const PriceLevel*>);
static_assert(std::is_same_v<decltype(std::declval<const OrderBook&>().find(1)), const Order*>);

TEST_F(OrderBookTest, InsertRejectsMalformedOrdersWithoutSideEffects) {
  Order bad_side = make_order(1, Side::Buy, 100, 5, 1);
  bad_side.side = static_cast<Side>(2);
  Order zero_price = make_order(2, Side::Buy, 0, 5, 2);
  Order huge_price = make_order(3, Side::Sell, kMaxPrice + 1, 5, 3);
  Order overfilled = make_order(4, Side::Buy, 100, 5, 4);
  overfilled.remaining_quantity = 6;
  Order wrong_instrument = make_order(5, Side::Buy, 100, 5, 5);
  wrong_instrument.instrument = 1;
  Order too_large = make_order(6, Side::Buy, 100, kMaxOrderQuantity + 1, 6);
  // Id 0 is never assigned, and the id index uses it to mark an empty slot.
  Order zero_id = make_order(kInvalidOrderId, Side::Buy, 100, 5, 7);
  for (const Order& order :
       {bad_side, zero_price, huge_price, overfilled, wrong_instrument, too_large, zero_id}) {
    EXPECT_THROW(book_.insert(order), std::invalid_argument) << order.id;
  }
  EXPECT_TRUE(book_.empty());
  EXPECT_EQ(book_.level_count(Side::Buy), 0u);
  EXPECT_EQ(book_.level_count(Side::Sell), 0u);
}

TEST_F(OrderBookTest, InsertRejectsOrdersThatWouldBreakTimePriority) {
  book_.insert(make_order(1, Side::Sell, 100, 5, 10));
  EXPECT_THROW(book_.insert(make_order(2, Side::Sell, 100, 5, 9)), std::invalid_argument);
  EXPECT_THROW(book_.insert(make_order(3, Side::Sell, 100, 5, 10)), std::invalid_argument);
  EXPECT_EQ(book_.order_count(), 1u);
  EXPECT_EQ(book_.volume_at(Side::Sell, 100), 5);
  EXPECT_EQ(book_.find(2), nullptr);
  // An older sequence is fine at a different (new) level, and a younger one at this level.
  EXPECT_NO_THROW(book_.insert(make_order(4, Side::Sell, 101, 5, 3)));
  EXPECT_NO_THROW(book_.insert(make_order(5, Side::Sell, 100, 5, 11)));
}

TEST_F(OrderBookTest, CopiesOfRestingOrdersAreDetachedSnapshots) {
  book_.insert(make_order(1, Side::Buy, 100, 5, 1));
  book_.insert(make_order(2, Side::Buy, 100, 7, 2));
  book_.insert(make_order(3, Side::Buy, 100, 9, 3));
  const Order copy = *book_.find(2);  // A middle order, linked on both sides.
  EXPECT_EQ(copy.id, 2u);
  EXPECT_EQ(copy.remaining_quantity, 7);
  EXPECT_EQ(copy.next(), nullptr);
  EXPECT_EQ(copy.prev(), nullptr);
  EXPECT_EQ(copy.level(), nullptr);
  EXPECT_FALSE(copy.is_linked());
  EXPECT_TRUE(book_.find(2)->is_linked());

  Order assigned;
  assigned = *book_.find(1);  // Assignment copies data but never links.
  EXPECT_EQ(assigned.id, 1u);
  EXPECT_FALSE(assigned.is_linked());
  // A non-const copy cannot reach the live book.
  Order mutable_copy = *book_.find(3);
  EXPECT_EQ(mutable_copy.prev(), nullptr);
  EXPECT_EQ(mutable_copy.level(), nullptr);
}

TEST_F(OrderBookTest, ManyOrdersAcrossFewLevelsSpanSeveralPoolChunks) {
  // Pool growth must never move nodes that are linked into live FIFO queues.
  const auto count = static_cast<OrderId>(OrderPool::kChunkSize * 3 + 11);
  for (OrderId id = 1; id <= count; ++id) {
    book_.insert(make_order(id, Side::Sell, 100 + static_cast<Price>(id % 7), 1, id));
  }
  book_.check_invariants();
  for (OrderId id = 1; id <= count; id += 3) {
    book_.remove(*book_.find_mutable(id));
  }
  book_.check_invariants();
  EXPECT_EQ(book_.order_count(), count - (count + 2) / 3);
  for (OrderId id = count + 1; id <= count + 500; ++id) {  // Reuses released nodes.
    book_.insert(make_order(id, Side::Sell, 100, 1, id));
  }
  book_.check_invariants();
}

#if defined(UDESHI_ASAN_ENABLED)
// Released nodes are poisoned, so a stale Order* is reported like a heap use-after-free even
// though the pool never returns the memory to the allocator.
TEST(OrderPoolDeathTest, AccessToReleasedNodeIsReportedUnderAsan) {
  EXPECT_DEATH(
      {
        OrderPool pool;
        Order* order = pool.allocate();
        order->id = 7;
        pool.release(order);
        volatile OrderId stale = order->id;
        static_cast<void>(stale);
      },
      "use-after-poison");
}
#endif

}  // namespace
}  // namespace udeshi
