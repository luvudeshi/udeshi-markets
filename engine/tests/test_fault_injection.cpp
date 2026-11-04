// Allocation-failure injection.
//
// This file is built as its own test executable (udeshi_fault_injection_tests) because it
// replaces the global operator new / delete, which affects every allocation in the program.
// Keeping it out of the main suite leaves ASan's own operator new/delete interception (and its
// new/delete mismatch checks) in force there. The replacements behave normally unless a test
// arms a failure countdown, in which case the N-th subsequent allocation throws
// std::bad_alloc. That lets the tests drive every allocation point of an operation and check
// its exception-safety guarantee.

#include <gtest/gtest.h>

#include <cstddef>
#include <cstdlib>
#include <new>
#include <utility>

#include "test_helpers.hpp"
#include "udeshi/exchange.hpp"
#include "udeshi/matching_engine.hpp"

namespace {

// Number of allocations to allow before failing; <= 0 means disarmed. The tests are
// single-threaded, so a plain global is sufficient.
long g_allocations_before_failure = 0;

void* allocate_or_fail(std::size_t size) {
  if (g_allocations_before_failure > 0 && --g_allocations_before_failure == 0) {
    throw std::bad_alloc();
  }
  if (void* memory = std::malloc(size == 0 ? 1 : size)) {
    return memory;
  }
  throw std::bad_alloc();
}

// Arms the countdown for its lifetime: the `nth` allocation from now fails (once).
class FailNthAllocation {
 public:
  explicit FailNthAllocation(long nth) { g_allocations_before_failure = nth; }
  FailNthAllocation(const FailNthAllocation&) = delete;
  FailNthAllocation& operator=(const FailNthAllocation&) = delete;
  ~FailNthAllocation() { g_allocations_before_failure = 0; }
  [[nodiscard]] static bool fired() { return g_allocations_before_failure == 0; }
};

// Counts the allocations `operation` performs, without failing any of them.
template <typename Operation>
long count_allocations(Operation&& operation) {
  constexpr long kUnreachable = 1L << 40;
  g_allocations_before_failure = kUnreachable;
  operation();
  const long used = kUnreachable - g_allocations_before_failure;
  g_allocations_before_failure = 0;
  return used;
}

}  // namespace

// Every non-aligned replaceable form must be replaced together. If any form were left to the
// runtime (e.g. the nothrow new that std::stable_sort's temporary buffer uses), memory could be
// allocated by the runtime's operator new and released by our delete, which ASan reports as an
// alloc-dealloc mismatch. The align_val_t forms are left to the runtime as a consistent pair.
void* operator new(std::size_t size) { return allocate_or_fail(size); }
void* operator new[](std::size_t size) { return allocate_or_fail(size); }
void* operator new(std::size_t size, const std::nothrow_t&) noexcept {
  try {
    return allocate_or_fail(size);
  } catch (const std::bad_alloc&) {
    return nullptr;
  }
}
void* operator new[](std::size_t size, const std::nothrow_t&) noexcept {
  try {
    return allocate_or_fail(size);
  } catch (const std::bad_alloc&) {
    return nullptr;
  }
}
void operator delete(void* memory) noexcept { std::free(memory); }
void operator delete[](void* memory) noexcept { std::free(memory); }
void operator delete(void* memory, std::size_t) noexcept { std::free(memory); }
void operator delete[](void* memory, std::size_t) noexcept { std::free(memory); }
void operator delete(void* memory, const std::nothrow_t&) noexcept { std::free(memory); }
void operator delete[](void* memory, const std::nothrow_t&) noexcept { std::free(memory); }

namespace udeshi {
namespace {

using testing::limit;
using testing::market;

// The fail-fast contract: no exception can escape order entry. (cancel() never allocates, so
// only a compile-time check can pin it.)
static_assert(noexcept(std::declval<MatchingEngine&>().submit(std::declval<const OrderRequest&>())));
static_assert(noexcept(std::declval<MatchingEngine&>().cancel(InstrumentId{}, OrderId{})));
static_assert(noexcept(std::declval<Exchange&>().submit(std::declval<const OrderRequest&>())));
static_assert(noexcept(std::declval<Exchange&>().cancel(InstrumentId{}, OrderId{})));

TEST(FaultInjection, AddInstrumentIsFailureAtomicAtEveryAllocationPoint) {
  int failure_points = 0;
  for (long nth = 1; nth < 100; ++nth) {
    Exchange exchange;
    exchange.add_instrument({"AAA", 0.01, 100});
    bool threw = false;
    {
      FailNthAllocation inject(nth);
      try {
        exchange.add_instrument({"BBB", 0.01, 200});
      } catch (const std::bad_alloc&) {
        threw = true;
      }
      if (!threw) {
        EXPECT_FALSE(FailNthAllocation::fired());
      }
    }
    if (!threw) {
      break;  // `nth` is past the last allocation: every failure point has been exercised.
    }
    ++failure_points;
    SCOPED_TRACE(::testing::Message() << "allocation " << nth << " failed");
    EXPECT_EQ(exchange.instrument_count(), 1u);
    EXPECT_EQ(exchange.engine().instrument_count(), 1u);
    EXPECT_FALSE(exchange.find_instrument("BBB").has_value());

    // The exchange is intact: the symbol can be registered again and its book trades and
    // records market data at the right index.
    const InstrumentId bbb = exchange.add_instrument({"BBB", 0.01, 200});
    EXPECT_EQ(bbb, 1u);
    exchange.submit(limit(Side::Sell, 200, 5, 1, bbb));
    const auto buy = exchange.submit(limit(Side::Buy, 200, 5, 2, bbb));
    EXPECT_EQ(buy.status, OrderStatus::Filled);
    EXPECT_EQ(exchange.statistics(bbb).volume, 5);
    EXPECT_EQ(exchange.statistics(0).volume, 0);
  }
  EXPECT_GE(failure_points, 2) << "expected several allocation points in add_instrument";
}

Order resting_buy(OrderId id, Price price, Sequence sequence) {
  Order order;
  order.id = id;
  order.side = Side::Buy;
  order.price = price;
  order.original_quantity = 5;
  order.remaining_quantity = 5;
  order.sequence = sequence;
  return order;
}

// Fails each allocation of `insert(order)` in turn on a book prepared by `prepare`, and checks
// that the book is unchanged and still usable each time. Returns the number of failure points.
template <typename Prepare>
int check_insert_failure_atomicity(Prepare&& prepare, const Order& order) {
  int failure_points = 0;
  for (long nth = 1; nth < 100; ++nth) {
    OrderBook book(0);
    prepare(book);
    const BookSnapshot before = book.snapshot(100);
    const std::size_t orders_before = book.order_count();
    bool threw = false;
    {
      FailNthAllocation inject(nth);
      try {
        book.insert(order);
      } catch (const std::bad_alloc&) {
        threw = true;
      }
    }
    if (!threw) {
      break;
    }
    ++failure_points;
    EXPECT_EQ(book.snapshot(100), before) << nth;
    EXPECT_EQ(book.order_count(), orders_before) << nth;
    EXPECT_EQ(book.find(order.id), nullptr) << nth;
    EXPECT_NO_THROW(book.check_invariants()) << nth;
    EXPECT_NO_THROW(book.insert(order)) << nth;  // Retrying succeeds.
    EXPECT_NO_THROW(book.check_invariants()) << nth;
  }
  return failure_points;
}

TEST(FaultInjection, OrderBookInsertIntoEmptyBookIsFailureAtomic) {
  // Allocations: the id index's first table, the map node for the new level, a pool chunk and
  // the free-list buffer. A floor guards against the test silently exercising nothing.
  const int failure_points =
      check_insert_failure_atomicity([](OrderBook&) {}, resting_buy(1, 100, 1));
  EXPECT_GE(failure_points, 3);
}

TEST(FaultInjection, OrderBookInsertIntoExistingLevelAllocatesNothing) {
  // The level exists, the pool has free nodes and the id index has room: an insert then
  // allocates nothing at all, so there is no failure point (the index used to allocate a node
  // per order).
  const int failure_points = check_insert_failure_atomicity(
      [](OrderBook& book) {
        book.insert(resting_buy(1, 100, 1));
        book.insert(resting_buy(2, 101, 2));
      },
      resting_buy(3, 100, 3));
  EXPECT_EQ(failure_points, 0);
}

TEST(FaultInjection, OrderBookInsertThatGrowsTheIndexIsFailureAtomic) {
  // The index doubles before it is half full: with 32 orders in a table of 64 slots, the next
  // insert must grow it, and that allocation is the only one that can fail.
  const int failure_points = check_insert_failure_atomicity(
      [](OrderBook& book) {
        for (OrderId id = 1; id <= 32; ++id) {
          book.insert(resting_buy(id, 100, id));
        }
      },
      resting_buy(33, 100, 33));
  EXPECT_EQ(failure_points, 1);
}

// Allocation failure after matching has modified the book cannot be reported consistently,
// so the engine fails fast instead of returning or throwing with partial state.
TEST(FaultInjectionDeathTest, AllocationFailureDuringMatchingTerminates) {
  MatchingEngine engine;
  engine.add_instrument();
  for (Price price = 100; price < 110; ++price) {
    engine.submit(limit(Side::Sell, price, 10, 1));
  }
  EXPECT_DEATH(
      {
        FailNthAllocation inject(2);  // The trade vector's first allocation succeeds, its growth fails.
        static_cast<void>(engine.submit(market(Side::Buy, 100, 2)));
      },
      "bad_alloc");
}

// The Exchange records market data after the engine has committed a match, so an allocation
// failure there (e.g. while the recent-trades ring grows) must also terminate rather than
// return a result that omits the trade. Every allocation point is exercised.
TEST(FaultInjectionDeathTest, AllocationFailureAnywhereInExchangeSubmitTerminates) {
  const auto prepare = [](Exchange& exchange) {
    const InstrumentId id = exchange.add_instrument({"ACME", 0.01, 10'000});
    exchange.submit(limit(Side::Sell, 10'000, 5, 1, id));
    exchange.submit(limit(Side::Sell, 10'001, 5, 1, id));
    return id;
  };
  const auto crossing_buy = [](InstrumentId id) { return limit(Side::Buy, 10'001, 15, 2, id); };

  Exchange probe;
  const InstrumentId probe_id = prepare(probe);
  const long allocations = count_allocations([&] { probe.submit(crossing_buy(probe_id)); });
  ASSERT_GE(allocations, 3) << "trades vector, ring growth and the resting remainder";

  for (long nth = 1; nth <= allocations; ++nth) {
    SCOPED_TRACE(::testing::Message() << "failing allocation " << nth << " of " << allocations);
    Exchange exchange;
    const InstrumentId id = prepare(exchange);
    EXPECT_DEATH(
        {
          FailNthAllocation inject(nth);
          static_cast<void>(exchange.submit(crossing_buy(id)));
        },
        "bad_alloc");
  }
}

}  // namespace
}  // namespace udeshi
