// Allocation-failure injection.
//
// This translation unit replaces the global operator new / delete for the whole test binary
// with versions that behave normally unless a test arms a failure countdown, in which case
// the N-th subsequent allocation throws std::bad_alloc. That lets the tests drive every
// allocation point of an operation and check its exception-safety guarantee.

#include <gtest/gtest.h>

#include <cstddef>
#include <cstdlib>
#include <new>

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

TEST(FaultInjection, OrderBookInsertIsFailureAtomic) {
  for (long nth = 1; nth < 100; ++nth) {
    OrderBook book(0);
    Order order;
    order.id = 1;
    order.side = Side::Buy;
    order.price = 100;
    order.original_quantity = 5;
    order.remaining_quantity = 5;
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
    EXPECT_TRUE(book.empty()) << nth;
    EXPECT_EQ(book.level_count(Side::Buy), 0u) << nth;
    EXPECT_NO_THROW(book.check_invariants()) << nth;
    EXPECT_NO_THROW(book.insert(order)) << nth;  // Retrying succeeds.
  }
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
        FailNthAllocation inject(2);  // The trade vector's first growth succeeds, the second fails.
        static_cast<void>(engine.submit(market(Side::Buy, 100, 2)));
      },
      "bad_alloc");
}

}  // namespace
}  // namespace udeshi
