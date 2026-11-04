#include "measure.hpp"

#include <sys/utsname.h>
#include <unistd.h>

#include <algorithm>
#include <cmath>
#include <fstream>
#include <numeric>
#include <thread>

#if defined(__APPLE__)
#include <mach/mach.h>
#include <pthread.h>
#include <sys/sysctl.h>
#endif

#ifndef UDESHI_BUILD_TYPE
#define UDESHI_BUILD_TYPE "unknown"
#endif
#ifndef UDESHI_BUILD_FLAGS
#define UDESHI_BUILD_FLAGS ""
#endif

namespace udeshi::bench {

namespace {

// The nearest-rank percentile of sorted samples: the value at rank ceil(p * n), 1-based.
std::int64_t percentile(const std::vector<std::int64_t>& sorted, double p) {
  const auto n = static_cast<double>(sorted.size());
  const auto rank = static_cast<std::size_t>(std::ceil(p * n));
  return sorted[std::clamp<std::size_t>(rank, 1, sorted.size()) - 1];
}

#if defined(__APPLE__)
std::string sysctl_string(const char* name) {
  std::size_t size = 0;
  if (sysctlbyname(name, nullptr, &size, nullptr, 0) != 0 || size == 0) {
    return {};
  }
  std::string value(size, '\0');
  if (sysctlbyname(name, value.data(), &size, nullptr, 0) != 0) {
    return {};
  }
  value.resize(value.find('\0') == std::string::npos ? size : value.find('\0'));
  return value;
}
#endif

}  // namespace

LatencyStats summarize(std::vector<std::int64_t>& samples) {
  LatencyStats stats;
  stats.count = samples.size();
  if (samples.empty()) {
    return stats;
  }
  std::sort(samples.begin(), samples.end());
  const long double total = std::accumulate(samples.begin(), samples.end(), 0.0L);
  stats.mean_ns = static_cast<double>(total / static_cast<long double>(samples.size()));
  stats.p50_ns = percentile(samples, 0.50);
  stats.p95_ns = percentile(samples, 0.95);
  stats.p99_ns = percentile(samples, 0.99);
  stats.p999_ns = percentile(samples, 0.999);
  stats.max_ns = samples.back();
  return stats;
}

ClockInfo measure_clock() {
  constexpr int kReadings = 200'000;
  std::vector<std::int64_t> gaps;
  gaps.reserve(kReadings);
  std::int64_t resolution = 0;
  for (int i = 0; i < kReadings; ++i) {
    const auto first = Clock::now();
    const auto second = Clock::now();
    const std::int64_t gap = nanoseconds_between(first, second);
    gaps.push_back(gap);
    if (gap > 0 && (resolution == 0 || gap < resolution)) {
      resolution = gap;
    }
  }
  ClockInfo info;
  info.resolution_ns = resolution;
  info.read_overhead_p50_ns = summarize(gaps).p50_ns;
  return info;
}

std::int64_t resident_bytes() {
#if defined(__APPLE__)
  mach_task_basic_info_data_t info{};
  mach_msg_type_number_t count = MACH_TASK_BASIC_INFO_COUNT;
  if (task_info(mach_task_self(), MACH_TASK_BASIC_INFO, reinterpret_cast<task_info_t>(&info),
                &count) != KERN_SUCCESS) {
    return 0;
  }
  return static_cast<std::int64_t>(info.resident_size);
#else
  std::ifstream statm("/proc/self/statm");
  std::int64_t total_pages = 0;
  std::int64_t resident_pages = 0;
  if (!(statm >> total_pages >> resident_pages)) {
    return 0;
  }
  return resident_pages * static_cast<std::int64_t>(sysconf(_SC_PAGESIZE));
#endif
}

Environment describe_environment() {
  Environment environment;
  environment.logical_cpus = std::thread::hardware_concurrency();
  utsname name{};
  if (uname(&name) == 0) {
    environment.os = std::string(name.sysname) + " " + name.release + " " + name.machine;
  }
#if defined(__APPLE__)
  environment.cpu = sysctl_string("machdep.cpu.brand_string");
  const std::string product = sysctl_string("kern.osproductversion");
  if (!product.empty()) {
    environment.os = "macOS " + product + " (" + environment.os + ")";
  }
  std::int64_t memory = 0;
  std::size_t size = sizeof(memory);
  if (sysctlbyname("hw.memsize", &memory, &size, nullptr, 0) == 0) {
    environment.memory_bytes = memory;
  }
#else
  std::ifstream cpuinfo("/proc/cpuinfo");
  for (std::string line; std::getline(cpuinfo, line);) {
    if (line.rfind("model name", 0) == 0) {
      environment.cpu = line.substr(line.find(':') + 2);
      break;
    }
  }
  environment.memory_bytes =
      static_cast<std::int64_t>(sysconf(_SC_PHYS_PAGES)) * sysconf(_SC_PAGESIZE);
#endif
#if defined(__clang__)
  environment.compiler = "Clang " __clang_version__;
#elif defined(__GNUC__)
  environment.compiler = "GCC " __VERSION__;
#endif
  environment.build_type = std::string(UDESHI_BUILD_TYPE) + " (" + UDESHI_BUILD_FLAGS + ")";
  return environment;
}

std::string prefer_performance_cores() {
#if defined(__APPLE__)
  if (pthread_set_qos_class_self_np(QOS_CLASS_USER_INTERACTIVE, 0) == 0) {
    return "QoS user-interactive (macOS schedules it on a performance core)";
  }
  return "QoS request failed: the scheduler may use an efficiency core";
#else
  return "no core pinning";
#endif
}

}  // namespace udeshi::bench
