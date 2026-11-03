# The pybind11 extension module. Normally built through scikit-build-core (see pyproject.toml),
# which locates Python and pybind11 and installs the module into the udeshi_markets package.
find_package(Python 3.12 REQUIRED COMPONENTS Interpreter Development.Module)
find_package(pybind11 3.0 CONFIG REQUIRED)

pybind11_add_module(_engine MODULE bindings/module.cpp)
target_link_libraries(_engine PRIVATE udeshi::engine udeshi_compile_options)
# The module reports the version of the distribution it ships in: scikit-build-core passes the
# version from pyproject.toml. A plain CMake build falls back to the CMake project version.
if(DEFINED SKBUILD_PROJECT_VERSION_FULL)
  set(UDESHI_MODULE_VERSION "${SKBUILD_PROJECT_VERSION_FULL}")
elseif(DEFINED SKBUILD_PROJECT_VERSION)
  set(UDESHI_MODULE_VERSION "${SKBUILD_PROJECT_VERSION}")
else()
  set(UDESHI_MODULE_VERSION "${PROJECT_VERSION}")
endif()
target_compile_definitions(_engine PRIVATE UDESHI_VERSION="${UDESHI_MODULE_VERSION}")
# Hidden visibility is not enough under GCC: type information for the engine's enum types is
# still exported. On ELF platforms a version script exports the init function and nothing else.
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
  set(UDESHI_EXPORTS_MAP "${CMAKE_CURRENT_SOURCE_DIR}/bindings/exports.map")
  target_link_options(_engine PRIVATE "LINKER:--version-script=${UDESHI_EXPORTS_MAP}")
  set_property(TARGET _engine APPEND PROPERTY LINK_DEPENDS "${UDESHI_EXPORTS_MAP}")
endif()

install(TARGETS _engine LIBRARY DESTINATION udeshi_markets)
