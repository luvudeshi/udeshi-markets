#include <gtest/gtest.h>

#include <array>
#include <set>
#include <vector>

#include "udeshi/order_pool.hpp"
#include "udeshi/price_level.hpp"

namespace udeshi {
namespace {

std::vector<OrderId> ids(const PriceLevel& level) {
  std::vector<OrderId> out;
  for (const Order* order = level.front(); order != nullptr; order = order->next()) {
    out.push_back(order->id);
  }
  return out;
}

class PriceLevelTest : public ::testing::Test {
 protected:
  void SetUp() override {
    for (std::size_t i = 0; i < orders_.size(); ++i) {
      orders_[i].id = i + 1;
      orders_[i].price = 100;
      orders_[i].original_quantity = static_cast<Quantity>(10 * (i + 1));
      orders_[i].remaining_quantity = orders_[i].original_quantity;
    }
  }
  std::array<Order, 4> orders_{};
  PriceLevel level_{100};
};

TEST_F(PriceLevelTest, StartsEmpty) {
  EXPECT_TRUE(level_.empty());
  EXPECT_EQ(level_.front(), nullptr);
  EXPECT_EQ(level_.back(), nullptr);
  EXPECT_EQ(level_.total_quantity(), 0);
  EXPECT_EQ(level_.order_count(), 0u);
  EXPECT_EQ(level_.price(), 100);
}

TEST_F(PriceLevelTest, PushBackPreservesArrivalOrderAndTotals) {
  for (auto& order : orders_) {
    level_.push_back(&order);
  }
  EXPECT_EQ(ids(level_), (std::vector<OrderId>{1, 2, 3, 4}));
  EXPECT_EQ(level_.total_quantity(), 100);
  EXPECT_EQ(level_.order_count(), 4u);
  EXPECT_EQ(level_.front(), &orders_[0]);
  EXPECT_EQ(level_.back(), &orders_[3]);
  EXPECT_EQ(orders_[2].level(), &level_);
}

TEST_F(PriceLevelTest, EraseHeadMiddleAndTail) {
  for (auto& order : orders_) {
    level_.push_back(&order);
  }
  level_.erase(&orders_[1]);  // middle
  EXPECT_EQ(ids(level_), (std::vector<OrderId>{1, 3, 4}));
  EXPECT_EQ(orders_[1].level(), nullptr);
  EXPECT_EQ(orders_[1].next(), nullptr);
  EXPECT_EQ(orders_[1].prev(), nullptr);
  level_.erase(&orders_[0]);  // head
  EXPECT_EQ(ids(level_), (std::vector<OrderId>{3, 4}));
  EXPECT_EQ(level_.front()->prev(), nullptr);
  level_.erase(&orders_[3]);  // tail
  EXPECT_EQ(ids(level_), (std::vector<OrderId>{3}));
  EXPECT_EQ(level_.back(), &orders_[2]);
  EXPECT_EQ(level_.total_quantity(), 30);
  level_.erase(&orders_[2]);  // last
  EXPECT_TRUE(level_.empty());
  EXPECT_EQ(level_.back(), nullptr);
  EXPECT_EQ(level_.total_quantity(), 0);
}

TEST_F(PriceLevelTest, ReduceKeepsPositionAndUpdatesTotal) {
  level_.push_back(&orders_[0]);
  level_.push_back(&orders_[1]);
  level_.reduce(&orders_[0], 4);
  EXPECT_EQ(orders_[0].remaining_quantity, 6);
  EXPECT_EQ(orders_[0].filled_quantity(), 4);
  EXPECT_EQ(level_.total_quantity(), 26);
  EXPECT_EQ(level_.front(), &orders_[0]);
}

TEST(OrderPoolTest, ReusesReleasedNodesWithoutGrowing) {
  OrderPool pool;
  std::vector<Order*> live;
  for (int i = 0; i < 100; ++i) {
    live.push_back(pool.allocate());
  }
  EXPECT_EQ(pool.live_count(), 100u);
  const std::size_t capacity = pool.capacity();
  for (int round = 0; round < 50; ++round) {
    for (Order* order : live) {
      pool.release(order);
    }
    live.clear();
    for (int i = 0; i < 100; ++i) {
      live.push_back(pool.allocate());
    }
  }
  EXPECT_EQ(pool.capacity(), capacity);
  EXPECT_EQ(pool.live_count(), 100u);
}

TEST(OrderPoolTest, GrowsAcrossChunksWithDistinctStableNodes) {
  OrderPool pool;
  std::set<Order*> distinct;
  std::vector<Order*> nodes;
  const std::size_t count = OrderPool::kChunkSize * 2 + 7;
  for (std::size_t i = 0; i < count; ++i) {
    Order* order = pool.allocate();
    order->id = i;
    nodes.push_back(order);
    distinct.insert(order);
  }
  EXPECT_EQ(distinct.size(), count);
  EXPECT_GE(pool.capacity(), count);
  for (std::size_t i = 0; i < count; ++i) {
    EXPECT_EQ(nodes[i]->id, i);  // Earlier nodes were not moved by later growth.
  }
}

TEST(OrderPoolTest, AllocateReturnsResetNode) {
  OrderPool pool;
  Order* order = pool.allocate();
  order->id = 42;
  order->remaining_quantity = 9;
  pool.release(order);
  Order* again = pool.allocate();
  EXPECT_EQ(again, order);
  EXPECT_EQ(again->id, kInvalidOrderId);
  EXPECT_EQ(again->remaining_quantity, 0);
  EXPECT_EQ(again->level(), nullptr);
}

}  // namespace
}  // namespace udeshi
