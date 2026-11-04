// Benchmarks of the matching engine. See docs/benchmarking.md for the method.
//
//   udeshi_bench [--quick] [--repetitions N] [--filter TEXT] [--json PATH] [--list]
//
// Each workload is recorded once against a real engine (workloads.cpp), then replayed on fresh
// engines: once to warm up, `repetitions` times untimed per operation for throughput, once with
// every operation timed for the latency distribution, and once counting heap allocations.
// Every replay must end in the state the recording ended in, or the run fails.

#include <algorithm>
#include <array>
#include <cstdio>
#include <cstdlib>
#include <exception>
#include <fstream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#include "allocation_counter.hpp"
#include "measure.hpp"
#include "udeshi/exchange.hpp"
#include "udeshi/matching_engine.hpp"
#include "workloads.hpp"

namespace udeshi::bench {
namespace {

struct Options {
  bool quick = false;
  int repetitions = 5;
  std::string filter;
  std::string json_path;
  bool list = false;
};

constexpr const char* kUsage =
    "usage: udeshi_bench [--quick] [--repetitions N] [--filter TEXT] [--json PATH] [--list]\n";

Options parse_options(int argc, char** argv) {
  Options options;
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    const auto value = [&]() -> std::string {
      if (i + 1 >= argc) {
        throw std::invalid_argument(arg + " needs a value");
      }
      return argv[++i];
    };
    if (arg == "--quick") {
      options.quick = true;
      options.repetitions = 2;
    } else if (arg == "--repetitions") {
      options.repetitions = std::stoi(value());
      if (options.repetitions < 1) {
        throw std::invalid_argument("--repetitions must be at least 1");
      }
    } else if (arg == "--filter") {
      options.filter = value();
    } else if (arg == "--json") {
      options.json_path = value();
    } else if (arg == "--list") {
      options.list = true;
    } else if (arg == "--help" || arg == "-h") {
      std::fputs(kUsage, stdout);
      std::exit(0);
    } else {
      throw std::invalid_argument("unknown argument " + arg);
    }
  }
  return options;
}

// Every result feeds a checksum: the compiler cannot discard any of the work, and the
// replays of one workload must all produce the same value.
struct Checksum {
  std::uint64_t value = 0;
  void mix(std::uint64_t x) noexcept { value = (value ^ x) * 0x100000001B3ULL; }
  void add(const SubmitResult& result) noexcept {
    mix(result.sequence);
    mix(result.order_id);
    mix(static_cast<std::uint64_t>(result.filled_quantity));
    mix(result.trades.size());
  }
  void add(const CancelResult& result) noexcept {
    mix(result.sequence);
    mix(static_cast<std::uint64_t>(result.cancelled_quantity));
  }
};

// The two things a workload can be replayed against: the matching engine alone, or the
// Exchange that the simulation uses (matching plus market-data recording).
class EngineTarget {
 public:
  EngineTarget() { engine_.add_instrument(); }
  SubmitResult submit(const OrderRequest& request) noexcept { return engine_.submit(request); }
  CancelResult cancel(OrderId id) noexcept { return engine_.cancel(0, id); }
  [[nodiscard]] Sequence last_sequence() const noexcept { return engine_.last_sequence(); }
  [[nodiscard]] std::size_t order_count() const { return engine_.book(0).order_count(); }

 private:
  MatchingEngine engine_;
};

class ExchangeTarget {
 public:
  ExchangeTarget() { exchange_.add_instrument(InstrumentSpec{"BENCH", 0.01, kMidPrice}); }
  SubmitResult submit(const OrderRequest& request) noexcept { return exchange_.submit(request); }
  CancelResult cancel(OrderId id) noexcept { return exchange_.cancel(0, id); }
  [[nodiscard]] Sequence last_sequence() const noexcept {
    return exchange_.engine().last_sequence();
  }
  [[nodiscard]] std::size_t order_count() const { return exchange_.order_book(0).order_count(); }

 private:
  Exchange exchange_;
};

template <typename Target>
inline void apply(Target& target, const Op& op, Checksum& checksum) {
  if (op.is_cancel) {
    checksum.add(target.cancel(op.cancel_id));
  } else {
    checksum.add(target.submit(op.request));
  }
}

template <typename Target>
std::unique_ptr<Target> prepared(const Workload& workload) {
  auto target = std::make_unique<Target>();
  Checksum ignored;
  for (const Op& op : workload.setup) {
    apply(*target, op, ignored);
  }
  return target;
}

template <typename Target>
void verify(const Target& target, const Workload& workload) {
  if (target.last_sequence() != workload.final_sequence ||
      target.order_count() != workload.final_order_count) {
    throw std::runtime_error("a replay ended in a different state than its recording");
  }
}

struct Replay {
  std::int64_t elapsed_ns = 0;
  std::uint64_t checksum = 0;
};

template <typename Target>
Replay replay_untimed(const Workload& workload) {
  auto target = prepared<Target>(workload);
  Checksum checksum;
  const auto start = Clock::now();
  for (const Op& op : workload.measured) {
    apply(*target, op, checksum);
  }
  const auto end = Clock::now();
  verify(*target, workload);
  return {nanoseconds_between(start, end), checksum.value};
}

template <typename Target>
std::uint64_t replay_timed(const Workload& workload, std::vector<std::int64_t>& samples) {
  auto target = prepared<Target>(workload);
  samples.assign(workload.measured.size(), 0);
  Checksum checksum;
  for (std::size_t i = 0; i < workload.measured.size(); ++i) {
    const auto start = Clock::now();
    apply(*target, workload.measured[i], checksum);
    const auto end = Clock::now();
    samples[i] = nanoseconds_between(start, end);
  }
  verify(*target, workload);
  return checksum.value;
}

struct KindAllocations {
  std::array<std::uint64_t, kOpKinds> allocations{};
  std::array<std::uint64_t, kOpKinds> operations{};
};

template <typename Target>
KindAllocations replay_counting(const Workload& workload, std::uint64_t& checksum_out) {
  auto target = prepared<Target>(workload);
  KindAllocations result;
  Checksum checksum;
  set_allocation_counting(true);
  for (const Op& op : workload.measured) {
    const std::uint64_t before = allocation_counters().allocations;
    apply(*target, op, checksum);
    const auto kind = static_cast<std::size_t>(op.kind);
    result.allocations[kind] += allocation_counters().allocations - before;
    ++result.operations[kind];
  }
  set_allocation_counting(false);
  verify(*target, workload);
  checksum_out = checksum.value;
  return result;
}

struct WorkloadResult {
  const WorkloadSpec* spec = nullptr;
  std::size_t setup_ops = 0;
  std::size_t measured_ops = 0;
  std::size_t start_orders = 0;
  std::size_t start_levels = 0;
  std::size_t end_orders = 0;
  std::vector<double> ops_per_second;  // One per repetition.
  LatencyStats latency;
  std::array<LatencyStats, kOpKinds> latency_by_kind{};
  KindAllocations allocations;
};

template <typename Target>
WorkloadResult measure(const WorkloadSpec& spec, const Workload& workload, int repetitions) {
  WorkloadResult result;
  result.spec = &spec;
  result.setup_ops = workload.setup.size();
  result.measured_ops = workload.measured.size();
  result.start_orders = workload.start_order_count;
  result.start_levels = workload.start_levels_per_side;
  result.end_orders = workload.final_order_count;

  const std::uint64_t expected = replay_untimed<Target>(workload).checksum;  // Warm-up.
  const auto check = [&](std::uint64_t checksum) {
    if (checksum != expected) {
      throw std::runtime_error(spec.name + ": replays of one workload disagree");
    }
  };
  for (int i = 0; i < repetitions; ++i) {
    const Replay replay = replay_untimed<Target>(workload);
    check(replay.checksum);
    const double seconds = static_cast<double>(replay.elapsed_ns) / 1e9;
    result.ops_per_second.push_back(static_cast<double>(workload.measured.size()) / seconds);
  }

  std::vector<std::int64_t> samples;
  check(replay_timed<Target>(workload, samples));
  std::array<std::vector<std::int64_t>, kOpKinds> by_kind;
  for (std::size_t i = 0; i < samples.size(); ++i) {
    by_kind[static_cast<std::size_t>(workload.measured[i].kind)].push_back(samples[i]);
  }
  result.latency = summarize(samples);
  for (std::size_t kind = 0; kind < kOpKinds; ++kind) {
    result.latency_by_kind[kind] = summarize(by_kind[kind]);
  }

  std::uint64_t counted = 0;
  result.allocations = replay_counting<Target>(workload, counted);
  check(counted);
  return result;
}

double median(std::vector<double> values) {
  std::sort(values.begin(), values.end());
  const std::size_t n = values.size();
  return n % 2 == 1 ? values[n / 2] : (values[n / 2 - 1] + values[n / 2]) / 2.0;
}

struct MemoryResult {
  std::size_t orders = 0;
  std::size_t levels_per_side = 0;
  double heap_bytes_per_order = 0;
  double allocations_per_order = 0;
  double rss_bytes_per_order = 0;
};

// The heap a book of `orders` resting orders occupies: the engine's live heap bytes (the
// allocator's usable sizes) and its allocations, counted exactly, and the growth of the
// process's resident set while it is built.
MemoryResult measure_memory(std::size_t orders, std::size_t levels) {
  const std::vector<OrderRequest> requests = resting_orders(orders, levels);
  MemoryResult result;
  result.orders = orders;
  set_allocation_counting(true);
  const AllocationCounters before = allocation_counters();
  const std::int64_t rss_before = resident_bytes();
  {
    EngineTarget target;
    Checksum checksum;
    for (const OrderRequest& request : requests) {
      checksum.add(target.submit(request));
    }
    const AllocationCounters after = allocation_counters();
    const std::int64_t rss_after = resident_bytes();
    if (target.order_count() != orders) {
      throw std::runtime_error("memory: not every order rested");
    }
    const auto n = static_cast<double>(orders);
    result.heap_bytes_per_order = static_cast<double>(after.live_bytes - before.live_bytes) / n;
    result.allocations_per_order =
        static_cast<double>(after.allocations - before.allocations) / n;
    result.rss_bytes_per_order = static_cast<double>(rss_after - rss_before) / n;
    result.levels_per_side = levels;
  }
  set_allocation_counting(false);
  return result;
}

// ---- Output --------------------------------------------------------------------------------

std::string json_string(const std::string& text) {
  std::string out = "\"";
  for (const char c : text) {
    if (c == '"' || c == '\\') {
      out += '\\';
      out += c;
    } else if (static_cast<unsigned char>(c) < 0x20) {
      char escaped[8];
      std::snprintf(escaped, sizeof(escaped), "\\u%04x", c);
      out += escaped;
    } else {
      out += c;
    }
  }
  return out + "\"";
}

std::string json_latency(const LatencyStats& stats) {
  std::ostringstream out;
  out << "{\"count\": " << stats.count << ", \"mean_ns\": " << stats.mean_ns
      << ", \"p50_ns\": " << stats.p50_ns << ", \"p95_ns\": " << stats.p95_ns
      << ", \"p99_ns\": " << stats.p99_ns << ", \"p999_ns\": " << stats.p999_ns
      << ", \"max_ns\": " << stats.max_ns << "}";
  return out.str();
}

void write_json(const std::string& path, const Options& options, const Environment& environment,
                const ClockInfo& clock, const std::string& scheduling,
                const std::vector<MemoryResult>& memory,
                const std::vector<WorkloadResult>& results) {
  std::ofstream out(path);
  if (!out) {
    throw std::runtime_error("cannot write " + path);
  }
  out.precision(10);
  out << "{\n  \"environment\": {\"cpu\": " << json_string(environment.cpu)
      << ", \"os\": " << json_string(environment.os)
      << ", \"compiler\": " << json_string(environment.compiler)
      << ", \"build\": " << json_string(environment.build_type)
      << ", \"logical_cpus\": " << environment.logical_cpus
      << ", \"memory_bytes\": " << environment.memory_bytes
      << ", \"scheduling\": " << json_string(scheduling) << "},\n";
  out << "  \"clock\": {\"resolution_ns\": " << clock.resolution_ns
      << ", \"read_overhead_p50_ns\": " << clock.read_overhead_p50_ns << "},\n";
  out << "  \"config\": {\"quick\": " << (options.quick ? "true" : "false")
      << ", \"repetitions\": " << options.repetitions << "},\n";
  out << "  \"sizes\": {\"order_bytes\": " << sizeof(Order)
      << ", \"price_level_bytes\": " << sizeof(PriceLevel)
      << ", \"trade_bytes\": " << sizeof(Trade) << "},\n";
  out << "  \"memory\": [";
  for (std::size_t i = 0; i < memory.size(); ++i) {
    const MemoryResult& m = memory[i];
    out << (i == 0 ? "\n" : ",\n") << "    {\"orders\": " << m.orders
        << ", \"levels_per_side\": " << m.levels_per_side
        << ", \"heap_bytes_per_order\": " << m.heap_bytes_per_order
        << ", \"allocations_per_order\": " << m.allocations_per_order
        << ", \"rss_bytes_per_order\": " << m.rss_bytes_per_order << "}";
  }
  out << "\n  ],\n  \"workloads\": [";
  for (std::size_t i = 0; i < results.size(); ++i) {
    const WorkloadResult& r = results[i];
    out << (i == 0 ? "\n" : ",\n") << "    {\"name\": " << json_string(r.spec->name)
        << ", \"group\": " << json_string(r.spec->group)
        << ", \"description\": " << json_string(r.spec->description)
        << ", \"target\": " << json_string(r.spec->through_exchange ? "exchange" : "engine")
        << ", \"setup_ops\": " << r.setup_ops << ", \"measured_ops\": " << r.measured_ops
        << ", \"start_orders\": " << r.start_orders
        << ", \"start_levels_per_side\": " << r.start_levels
        << ", \"end_orders\": " << r.end_orders << ",\n      \"ops_per_second\": {\"median\": "
        << median(r.ops_per_second) << ", \"min\": "
        << *std::min_element(r.ops_per_second.begin(), r.ops_per_second.end())
        << ", \"max\": " << *std::max_element(r.ops_per_second.begin(), r.ops_per_second.end())
        << ", \"repetitions\": " << r.ops_per_second.size() << "},\n      \"latency\": "
        << json_latency(r.latency) << ",\n      \"by_kind\": {";
    bool first = true;
    for (std::size_t kind = 0; kind < kOpKinds; ++kind) {
      if (r.allocations.operations[kind] == 0) {
        continue;
      }
      const double per_op = static_cast<double>(r.allocations.allocations[kind]) /
                            static_cast<double>(r.allocations.operations[kind]);
      out << (first ? "" : ", ") << json_string(std::string(to_string(static_cast<OpKind>(kind))))
          << ": {\"latency\": " << json_latency(r.latency_by_kind[kind])
          << ", \"allocations_per_op\": " << per_op << "}";
      first = false;
    }
    out << "}}";
  }
  out << "\n  ]\n}\n";
}

void print_row(const WorkloadResult& r) {
  std::uint64_t allocations = 0;
  std::uint64_t operations = 0;
  for (std::size_t kind = 0; kind < kOpKinds; ++kind) {
    allocations += r.allocations.allocations[kind];
    operations += r.allocations.operations[kind];
  }
  std::printf("%-26s %9zu %9.2f %8.1f %7lld %7lld %7lld %8lld %9lld %7.2f\n",
              r.spec->name.c_str(), r.measured_ops, median(r.ops_per_second) / 1e6,
              r.latency.mean_ns, static_cast<long long>(r.latency.p50_ns),
              static_cast<long long>(r.latency.p95_ns), static_cast<long long>(r.latency.p99_ns),
              static_cast<long long>(r.latency.p999_ns), static_cast<long long>(r.latency.max_ns),
              static_cast<double>(allocations) / static_cast<double>(operations));
  std::fflush(stdout);
}

int run(int argc, char** argv) {
  const Options options = parse_options(argc, argv);
  const std::vector<WorkloadSpec> specs = workload_specs(options.quick);
  const auto selected = [&](const std::string& name) {
    return options.filter.empty() || name.find(options.filter) != std::string::npos;
  };
  if (options.list) {
    for (const WorkloadSpec& spec : specs) {
      std::printf("%-26s %s\n", spec.name.c_str(), spec.description.c_str());
    }
    return 0;
  }

  const std::string scheduling = prefer_performance_cores();
  const Environment environment = describe_environment();
  const ClockInfo clock = measure_clock();
  std::printf("cpu       %s (%u logical CPUs, %.0f GiB)\nos        %s\ncompiler  %s\n",
              environment.cpu.c_str(), environment.logical_cpus,
              static_cast<double>(environment.memory_bytes) / (1024.0 * 1024.0 * 1024.0),
              environment.os.c_str(), environment.compiler.c_str());
  std::printf("build     %s\nschedule  %s\nclock     resolution %lld ns, read overhead %lld ns "
              "(median)\nmode      %s, %d repetitions\n\n",
              environment.build_type.c_str(), scheduling.c_str(),
              static_cast<long long>(clock.resolution_ns),
              static_cast<long long>(clock.read_overhead_p50_ns),
              options.quick ? "quick" : "full", options.repetitions);

  // Memory first, in a fresh process, so the resident-set growth is not hidden by memory that
  // earlier workloads freed and the allocator kept.
  std::vector<MemoryResult> memory;
  if (selected("memory")) {
    const std::vector<std::size_t> sizes =
        options.quick ? std::vector<std::size_t>{10'000}
                      : std::vector<std::size_t>{10'000, 100'000, 1'000'000};
    std::printf("%-26s %12s %14s %12s\n", "memory (1,000 levels)", "heap B/order",
                "allocs/order", "RSS B/order");
    for (const std::size_t orders : sizes) {
      memory.push_back(measure_memory(orders, 1'000));
      const MemoryResult& m = memory.back();
      std::printf("%-26zu %12.1f %14.3f %12.1f\n", m.orders, m.heap_bytes_per_order,
                  m.allocations_per_order, m.rss_bytes_per_order);
    }
    std::printf("\n");
  }

  std::printf("%-26s %9s %9s %8s %7s %7s %7s %8s %9s %7s\n", "workload", "ops", "Mops/s",
              "mean ns", "p50", "p95", "p99", "p99.9", "max", "alloc/op");
  std::vector<WorkloadResult> results;
  for (const WorkloadSpec& spec : specs) {
    if (!selected(spec.name)) {
      continue;
    }
    const Workload workload = spec.build();
    results.push_back(spec.through_exchange
                          ? measure<ExchangeTarget>(spec, workload, options.repetitions)
                          : measure<EngineTarget>(spec, workload, options.repetitions));
    print_row(results.back());
  }

  if (!options.json_path.empty()) {
    write_json(options.json_path, options, environment, clock, scheduling, memory, results);
    std::printf("\nwrote %s\n", options.json_path.c_str());
  }
  return 0;
}

}  // namespace
}  // namespace udeshi::bench

int main(int argc, char** argv) {
  try {
    return udeshi::bench::run(argc, argv);
  } catch (const std::exception& error) {
    std::fprintf(stderr, "udeshi_bench: %s\n%s", error.what(), udeshi::bench::kUsage);
    return 1;
  }
}
