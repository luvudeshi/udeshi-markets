include(FetchContent)
FetchContent_Declare(
  googletest
  URL https://github.com/google/googletest/releases/download/v1.17.0/googletest-1.17.0.tar.gz
  URL_HASH SHA256=65fab701d9829d38cb77c14acdc431d2108bfdbf8979e40eb8ae567edf10b27c
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
set(INSTALL_GTEST OFF CACHE BOOL "" FORCE)
set(BUILD_GMOCK OFF CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(googletest)

include(GoogleTest)

function(udeshi_add_test_executable name)
  add_executable(${name} ${ARGN})
  target_include_directories(${name} PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
  target_link_libraries(${name} PRIVATE udeshi::engine udeshi_compile_options GTest::gtest_main)
  gtest_discover_tests(${name} DISCOVERY_TIMEOUT 60)
endfunction()

udeshi_add_test_executable(udeshi_engine_tests
  test_price_level.cpp
  test_order_book.cpp
  test_matching_engine.cpp
  test_exchange.cpp
  test_engine_contract.cpp
  test_properties.cpp)

# Replaces the global operator new/delete, so it must not share a binary with the main suite:
# there the replacement would hide ASan's new/delete mismatch checks from every test.
udeshi_add_test_executable(udeshi_fault_injection_tests test_fault_injection.cpp)
