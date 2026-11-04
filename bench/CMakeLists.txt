# Benchmarks of the matching engine (see docs/benchmarking.md). Built with the engine's own
# warnings and flags; run the Release build:
#   cmake --preset release && cmake --build --preset release
#   build/release/bench/udeshi_bench --json bench-results.json
add_executable(udeshi_bench
  main.cpp
  allocation_counter.cpp
  measure.cpp
  workloads.cpp)
target_link_libraries(udeshi_bench PRIVATE udeshi::engine udeshi_compile_options)
# Recorded in every report, so results always say how they were built.
string(TOUPPER "${CMAKE_BUILD_TYPE}" udeshi_build_type_upper)
target_compile_definitions(udeshi_bench PRIVATE
  UDESHI_BUILD_TYPE="${CMAKE_BUILD_TYPE}"
  UDESHI_BUILD_FLAGS="${CMAKE_CXX_FLAGS} ${CMAKE_CXX_FLAGS_${udeshi_build_type_upper}}")
