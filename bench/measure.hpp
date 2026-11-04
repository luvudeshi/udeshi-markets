#pragma once

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace udeshi::bench {

using Clock = std::chrono::steady_clock;

[[nodiscard]] inline std::int64_t nanoseconds_between(Clock::time_point start,
                                                      Clock::time_point end) noexcept {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(end - start).count();
}

// Latency distribution of a set of operations, in nanoseconds. Percentiles use the
// nearest-rank method: the p-th percentile is the smallest sample that at least p% of the
// samples do not exceed.
struct LatencyStats {
  std::size_t count = 0;
  double mean_ns = 0;
  std::int64_t p50_ns = 0;
  std::int64_t p95_ns = 0;
  std::int64_t p99_ns = 0;
  std::int64_t p999_ns = 0;
  std::int64_t max_ns = 0;
};

// Sorts `samples` in place.
[[nodiscard]] LatencyStats summarize(std::vector<std::int64_t>& samples);

// What the clock contributes to a per-operation latency. `tick_ns` is the step of the
// underlying counter (the timebase on macOS, clock_getres on Linux): every interval is a
// multiple of it. The gaps between two consecutive readings show the cost of reading it.
struct ClockInfo {
  double tick_ns = 0;
  std::int64_t min_read_gap_ns = 0;  // Smallest non-zero gap between consecutive readings.
  std::int64_t read_gap_p50_ns = 0;  // Median gap: the floor of any measurement.
};
[[nodiscard]] ClockInfo measure_clock();

// Resident set size of this process, in bytes (0 where it cannot be read).
[[nodiscard]] std::int64_t resident_bytes();

// The machine and build, for the report.
struct Environment {
  std::string cpu;
  std::string os;
  std::string compiler;
  std::string build_type;
  unsigned logical_cpus = 0;
  std::int64_t memory_bytes = 0;
};
[[nodiscard]] Environment describe_environment();

// On macOS, asks the scheduler for a performance core (the highest quality-of-service class);
// elsewhere a no-op. Returns a note for the report.
[[nodiscard]] std::string prefer_performance_cores();

}  // namespace udeshi::bench
