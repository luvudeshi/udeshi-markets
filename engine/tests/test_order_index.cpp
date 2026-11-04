// OrderIndex: the open-addressing id index of a book. Checked directly here (including the
// wrap-around cases of backward-shift deletion) and differentially against std::unordered_map.

#include <gtest/gtest.h>

#include <cstdint>
#include <random>
#include <unordered_map>
#include <vector>

#include "udeshi/order_index.hpp"

namespace udeshi {
namespace {

// Distinct, recognisable node addresses for the entries (never dereferenced).
Order* node(std::vector<Order>& storage, OrderId id) {
  return &storage[static_cast<std::size_t>(id % storage.size())];
}

// The home slot of `id` in a table of `capacity` slots, by the index's own hash.
std::size_t home(OrderId id, std::size_t capacity) {
  unsigned bits = 0;
  while ((std::size_t{1} << bits) < capacity) {
    ++bits;
  }
  return static_cast<std::size_t>((id * 0x9E3779B97F4A7C15ULL) >> (64 - bits));
}

// The first `count` ids whose home slot is `slot` in a table of `capacity` slots.
std::vector<OrderId> ids_at_home(std::size_t slot, std::size_t capacity, std::size_t count) {
  std::vector<OrderId> ids;
  for (OrderId id = 1; ids.size() < count; ++id) {
    if (home(id, capacity) == slot) {
      ids.push_back(id);
    }
  }
  return ids;
}

void insert(OrderIndex& index, OrderId id, Order* order) {
  index.reserve_one();
  index.insert(id, order);
}

TEST(OrderIndex, EmptyIndexFindsAndErasesNothing) {
  OrderIndex index;
  EXPECT_EQ(index.size(), 0U);
  EXPECT_TRUE(index.empty());
  EXPECT_EQ(index.find(1), nullptr);
  EXPECT_FALSE(index.erase(1));
}

// Id 0 marks an empty slot: it is never found and never erased, whatever the table holds.
TEST(OrderIndex, IdZeroIsNeverAnEntry) {
  std::vector<Order> storage(16);
  OrderIndex index;
  insert(index, 7, node(storage, 7));
  EXPECT_EQ(index.find(kInvalidOrderId), nullptr);
  EXPECT_FALSE(index.erase(kInvalidOrderId));
  EXPECT_EQ(index.size(), 1U);
  EXPECT_EQ(index.find(7), node(storage, 7));
}

TEST(OrderIndex, InsertsFindsAndErases) {
  std::vector<Order> storage(16);
  OrderIndex index;
  insert(index, 7, node(storage, 7));
  insert(index, 8, node(storage, 8));
  EXPECT_EQ(index.size(), 2U);
  EXPECT_EQ(index.find(7), node(storage, 7));
  EXPECT_EQ(index.find(8), node(storage, 8));
  EXPECT_EQ(index.find(9), nullptr);
  EXPECT_TRUE(index.erase(7));
  EXPECT_FALSE(index.erase(7));
  EXPECT_EQ(index.find(7), nullptr);
  EXPECT_EQ(index.find(8), node(storage, 8));
  EXPECT_EQ(index.size(), 1U);
}

// Entries that share a home slot at the end of the table wrap around to its start. Erasing the
// first must shift the others back, across the wrap, so they stay reachable; erasing an entry
// whose run passes over others' home slots must leave those where they are.
TEST(OrderIndex, BackwardShiftAcrossTheWrapAround) {
  std::vector<Order> storage(64);
  OrderIndex index;
  index.reserve_one();
  const std::size_t capacity = index.capacity();
  ASSERT_EQ(capacity, 64U);
  const std::vector<OrderId> last = ids_at_home(capacity - 1, capacity, 3);  // Run 63, 0, 1.
  const std::vector<OrderId> first = ids_at_home(0, capacity, 1);            // Home 0, at 2.
  for (const OrderId id : last) {
    insert(index, id, node(storage, id));
  }
  insert(index, first[0], node(storage, first[0]));
  ASSERT_EQ(index.capacity(), capacity);  // No growth: the layout is as described.

  EXPECT_TRUE(index.erase(last[0]));  // Everything after it shifts back by one.
  for (const OrderId id : {last[1], last[2], first[0]}) {
    EXPECT_EQ(index.find(id), node(storage, id)) << id;
  }
  EXPECT_TRUE(index.erase(last[2]));
  EXPECT_EQ(index.find(last[1]), node(storage, last[1]));
  EXPECT_EQ(index.find(first[0]), node(storage, first[0]));
  EXPECT_TRUE(index.erase(last[1]));
  EXPECT_EQ(index.find(first[0]), node(storage, first[0]));
  EXPECT_EQ(index.size(), 1U);
}

// An entry already at its home slot must not be moved into an earlier hole.
TEST(OrderIndex, BackwardShiftLeavesEntriesAtTheirHome) {
  std::vector<Order> storage(64);
  OrderIndex index;
  index.reserve_one();
  const std::size_t capacity = index.capacity();
  const OrderId a = ids_at_home(10, capacity, 1)[0];
  const OrderId b = ids_at_home(11, capacity, 1)[0];
  insert(index, a, node(storage, a));
  insert(index, b, node(storage, b));
  EXPECT_TRUE(index.erase(a));
  EXPECT_EQ(index.find(b), node(storage, b));
  EXPECT_FALSE(index.erase(a));
}

// The same across the wrap-around: a hole in the last slot must not pull in the entry that
// sits at its own home in slot 0.
TEST(OrderIndex, BackwardShiftLeavesEntriesAtTheirHomeAcrossTheWrapAround) {
  std::vector<Order> storage(64);
  OrderIndex index;
  index.reserve_one();
  const std::size_t capacity = index.capacity();
  const OrderId last = ids_at_home(capacity - 1, capacity, 1)[0];
  const OrderId first = ids_at_home(0, capacity, 1)[0];
  insert(index, last, node(storage, last));
  insert(index, first, node(storage, first));
  EXPECT_TRUE(index.erase(last));
  EXPECT_EQ(index.find(first), node(storage, first));
  EXPECT_TRUE(index.erase(first));
  EXPECT_TRUE(index.empty());
}

TEST(OrderIndex, GrowsBeforeHalfFullAndKeepsEveryEntry) {
  std::vector<Order> storage(1024);
  OrderIndex index;
  for (OrderId id = 1; id <= 10'000; ++id) {
    insert(index, id, node(storage, id));
    ASSERT_LE(index.size() * 2, index.capacity());
  }
  for (OrderId id = 1; id <= 10'000; ++id) {
    ASSERT_EQ(index.find(id), node(storage, id)) << id;
  }
  EXPECT_EQ(index.find(10'001), nullptr);
}

// Random inserts (sequential ids, as the engine assigns them, and scattered ones), erases of
// present and absent ids, and lookups, checked against std::unordered_map after every step.
TEST(OrderIndex, AgreesWithAReferenceMap) {
  std::vector<Order> storage(4096);
  std::mt19937_64 rng(2026);
  OrderIndex index;
  std::unordered_map<OrderId, Order*> reference;
  std::vector<OrderId> present;
  OrderId next_sequential = 1;
  for (int step = 0; step < 200'000; ++step) {
    const auto roll = rng() % 100;
    if (roll < 45 || present.empty()) {
      const OrderId id =
          roll % 3 == 0 ? (rng() | 1ULL) : (next_sequential += 1 + rng() % 3);  // Never 0.
      if (reference.contains(id)) {
        continue;
      }
      insert(index, id, node(storage, id));
      reference.emplace(id, node(storage, id));
      present.push_back(id);
    } else if (roll < 85) {
      const std::size_t at = rng() % present.size();
      const OrderId id = present[at];
      present[at] = present.back();
      present.pop_back();
      ASSERT_TRUE(index.erase(id));
      reference.erase(id);
    } else if (roll < 90) {
      const OrderId absent = next_sequential + 1 + rng() % 1000;
      ASSERT_EQ(index.erase(absent), reference.contains(absent)) << absent;
      reference.erase(absent);
      std::erase(present, absent);
    } else {
      const OrderId probe = rng() % 2 == 0 && !present.empty() ? present[rng() % present.size()]
                                                                : rng() % (next_sequential + 2);
      const auto it = reference.find(probe);
      ASSERT_EQ(index.find(probe), it == reference.end() ? nullptr : it->second) << probe;
    }
    ASSERT_EQ(index.size(), reference.size());
  }
  for (const auto& [id, order] : reference) {
    ASSERT_EQ(index.find(id), order) << id;
  }
}

}  // namespace
}  // namespace udeshi
