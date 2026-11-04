#pragma once

#include <cstdint>

namespace udeshi::bench {

// SplitMix64: a fast, well-mixed 64-bit generator (Steele, Lea and Flood, 2014). Workloads are
// generated from a fixed seed, so every run replays the same orders.
class SplitMix64 {
 public:
  explicit SplitMix64(std::uint64_t seed) noexcept : state_(seed) {}

  std::uint64_t next() noexcept {
    std::uint64_t z = (state_ += 0x9E3779B97F4A7C15ULL);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
  }

  // In [0, bound) for bound > 0. Modulo bias is below 2^-32 for the bounds used here, which
  // matters nowhere in a benchmark.
  std::uint64_t below(std::uint64_t bound) noexcept { return next() % bound; }

  // Uniform in [low, high].
  std::int64_t between(std::int64_t low, std::int64_t high) noexcept {
    return low + static_cast<std::int64_t>(below(static_cast<std::uint64_t>(high - low) + 1));
  }

  bool chance(std::uint64_t numerator, std::uint64_t denominator) noexcept {
    return below(denominator) < numerator;
  }

 private:
  std::uint64_t state_;
};

}  // namespace udeshi::bench
