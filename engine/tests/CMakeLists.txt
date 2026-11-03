include(FetchContent)
FetchContent_Declare(
  googletest
  URL https://github.com/google/googletest/releases/download/v1.17.0/googletest-1.17.0.tar.gz
  URL_HASH SHA256=65fab701d9829d38cb77c14acdc431d2108bfdbf8979e40eb8ae567edf10b27c
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
set(INSTALL_GTEST OFF CACHE BOOL "" FORCE)
set(BUILD_GMOCK OFF CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(googletest)

add_executable(udeshi_engine_tests
  test_price_level.cpp
  test_order_book.cpp
  test_matching_engine.cpp
  test_exchange.cpp
  test_properties.cpp)
target_include_directories(udeshi_engine_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(udeshi_engine_tests PRIVATE
  udeshi::engine udeshi_compile_options GTest::gtest_main)

include(GoogleTest)
gtest_discover_tests(udeshi_engine_tests DISCOVERY_TIMEOUT 60)
