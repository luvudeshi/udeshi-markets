cmake_minimum_required(VERSION 3.24)

project(udeshi_markets
  VERSION 0.1.0
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
option(UDESHI_WARNINGS_AS_ERRORS "Treat compiler warnings as errors" ON)
option(UDESHI_SANITIZE "Build with AddressSanitizer and UndefinedBehaviorSanitizer" OFF)

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
if(UDESHI_SANITIZE)
  target_compile_options(udeshi_compile_options INTERFACE
    -fsanitize=address,undefined -fno-omit-frame-pointer -fno-sanitize-recover=all)
  target_link_options(udeshi_compile_options INTERFACE -fsanitize=address,undefined)
endif()

add_subdirectory(engine)
