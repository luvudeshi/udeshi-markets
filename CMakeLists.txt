cmake_minimum_required(VERSION 3.24)

project(udeshi_markets
  VERSION 0.2.0
  DESCRIPTION "Simulated electronic exchange and market simulation engine"
  LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

option(UDESHI_BUILD_TESTS "Build the GoogleTest suite" ON)
option(UDESHI_BUILD_PYTHON "Build the pybind11 extension module (udeshi_markets._engine)" OFF)
option(UDESHI_WARNINGS_AS_ERRORS "Treat compiler warnings as errors" ON)
option(UDESHI_SANITIZE "Build with AddressSanitizer and UndefinedBehaviorSanitizer" OFF)
option(UDESHI_BUILD_BENCHMARKS "Build the engine benchmarks (bench/)" ON)

# Applied to every first-party target (not to third-party dependencies).
# -ffp-contract=off: never fuse a*b+c into an FMA. Whether a fused instruction exists differs
# between targets (arm64 always, x86-64 only with -mfma), so contraction would make
# floating-point results platform-dependent and break cross-platform reproducibility.
add_library(udeshi_compile_options INTERFACE)
target_compile_options(udeshi_compile_options INTERFACE
  -ffp-contract=off
  -Wall -Wextra -Wpedantic -Wshadow -Wconversion -Wsign-conversion
  -Wnon-virtual-dtor -Wold-style-cast -Wcast-align -Woverloaded-virtual
  $<$<BOOL:${UDESHI_WARNINGS_AS_ERRORS}>:-Werror>)
# Sanitizers apply to every target, third-party ones included: mixing instrumented and
# uninstrumented copies of the same templates (e.g. libc++ containers in googletest) produces
# false ASan container-overflow reports.
if(UDESHI_SANITIZE)
  add_compile_options(-fsanitize=address,undefined -fno-omit-frame-pointer
                      -fno-sanitize-recover=all)
  add_link_options(-fsanitize=address,undefined)
endif()

# Must be called in the top-level directory so that `ctest` run from the build root finds
# tests registered in subdirectories.
if(UDESHI_BUILD_TESTS)
  enable_testing()
endif()

add_subdirectory(engine)

# The benchmark replaces the global operator new to count allocations, which would hide ASan's
# own new/delete checks, and sanitized timings mean nothing: it is not built with sanitizers.
if(UDESHI_BUILD_BENCHMARKS AND NOT UDESHI_SANITIZE)
  add_subdirectory(bench)
endif()

if(UDESHI_BUILD_PYTHON)
  add_subdirectory(python)
endif()
