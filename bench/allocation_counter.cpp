#include "allocation_counter.hpp"

#include <cstddef>
#include <cstdlib>
#include <new>

#if defined(__APPLE__)
#include <malloc/malloc.h>
#else
#include <malloc.h>
#endif

namespace udeshi::bench {

namespace {

// The benchmark is single-threaded, so plain globals suffice.
bool g_counting = false;
AllocationCounters g_counters;

std::int64_t usable_size(void* memory) noexcept {
#if defined(__APPLE__)
  return static_cast<std::int64_t>(malloc_size(memory));
#else
  return static_cast<std::int64_t>(malloc_usable_size(memory));
#endif
}

void* allocate(std::size_t size) {
  void* memory = std::malloc(size == 0 ? 1 : size);
  if (memory == nullptr) {
    throw std::bad_alloc();
  }
  if (g_counting) {
    ++g_counters.allocations;
    g_counters.live_bytes += usable_size(memory);
  }
  return memory;
}

void release(void* memory) noexcept {
  if (memory == nullptr) {
    return;
  }
  if (g_counting) {
    ++g_counters.deallocations;
    g_counters.live_bytes -= usable_size(memory);
  }
  std::free(memory);
}

}  // namespace

void set_allocation_counting(bool enabled) noexcept { g_counting = enabled; }

AllocationCounters allocation_counters() noexcept { return g_counters; }

}  // namespace udeshi::bench

// Every non-aligned replaceable form is replaced together, so memory is never allocated by the
// runtime's operator new and released by this one (or the reverse). The align_val_t forms are
// left to the runtime as a consistent pair; the engine does not use over-aligned types.
void* operator new(std::size_t size) { return udeshi::bench::allocate(size); }
void* operator new[](std::size_t size) { return udeshi::bench::allocate(size); }
void* operator new(std::size_t size, const std::nothrow_t&) noexcept {
  try {
    return udeshi::bench::allocate(size);
  } catch (const std::bad_alloc&) {
    return nullptr;
  }
}
void* operator new[](std::size_t size, const std::nothrow_t&) noexcept {
  try {
    return udeshi::bench::allocate(size);
  } catch (const std::bad_alloc&) {
    return nullptr;
  }
}
void operator delete(void* memory) noexcept { udeshi::bench::release(memory); }
void operator delete[](void* memory) noexcept { udeshi::bench::release(memory); }
void operator delete(void* memory, std::size_t) noexcept { udeshi::bench::release(memory); }
void operator delete[](void* memory, std::size_t) noexcept { udeshi::bench::release(memory); }
void operator delete(void* memory, const std::nothrow_t&) noexcept {
  udeshi::bench::release(memory);
}
void operator delete[](void* memory, const std::nothrow_t&) noexcept {
  udeshi::bench::release(memory);
}
