# The pybind11 extension module. Normally built through scikit-build-core (see pyproject.toml),
# which locates Python and pybind11 and installs the module into the udeshi_markets package.
find_package(Python 3.12 REQUIRED COMPONENTS Interpreter Development.Module)
find_package(pybind11 3.0 CONFIG REQUIRED)

pybind11_add_module(_engine MODULE bindings/module.cpp)
target_link_libraries(_engine PRIVATE udeshi::engine udeshi_compile_options)
target_compile_definitions(_engine PRIVATE UDESHI_VERSION="${PROJECT_VERSION}")

install(TARGETS _engine LIBRARY DESTINATION udeshi_markets)
