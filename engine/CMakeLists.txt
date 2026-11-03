add_library(udeshi_engine
  src/types.cpp
  src/order_book.cpp
  src/matching_engine.cpp
  src/exchange.cpp)
add_library(udeshi::engine ALIAS udeshi_engine)

target_include_directories(udeshi_engine PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>)
target_compile_features(udeshi_engine PUBLIC cxx_std_20)
target_link_libraries(udeshi_engine PRIVATE udeshi_compile_options)
# Position-independent so the same static library can be linked into the Python extension.
set_target_properties(udeshi_engine PROPERTIES POSITION_INDEPENDENT_CODE ON)

if(UDESHI_BUILD_TESTS)
  enable_testing()
  add_subdirectory(tests)
endif()
