#pragma once

#include <cstdint>

namespace udeshi::bench {

// Heap activity of the whole program, recorded by the benchmark binary's replacement of the
// global operator new / delete (allocation_counter.cpp). Counting is off by default, so timed
// runs pay nothing for it; the memory and allocation passes switch it on.
struct AllocationCounters {
  std::uint64_t allocations = 0;
  std::uint64_t deallocations = 0;
  // Usable bytes of the blocks currently allocated through operator new (malloc_size on
  // macOS, malloc_usable_size on Linux): what the allocator actually reserved, not just what
  // was requested.
  std::int64_t live_bytes = 0;
};

void set_allocation_counting(bool enabled) noexcept;
[[nodiscard]] AllocationCounters allocation_counters() noexcept;

}  // namespace udeshi::bench
