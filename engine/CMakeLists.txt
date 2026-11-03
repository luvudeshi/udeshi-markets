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
# Hidden visibility keeps the engine's symbols private to whatever links it: the extension
# module then exports only PyInit__engine. Exported, they could be interposed by another copy of
# the engine loaded in the same process, and under -fPIC GCC would have to assume that they
# might be, which stops it inlining calls between them.
set_target_properties(udeshi_engine PROPERTIES
  POSITION_INDEPENDENT_CODE ON
  CXX_VISIBILITY_PRESET hidden
  VISIBILITY_INLINES_HIDDEN ON)

if(UDESHI_BUILD_TESTS)
  add_subdirectory(tests)
endif()
