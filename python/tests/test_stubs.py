"""Keeps the hand-written type stubs (_engine.pyi) in sync with the compiled module."""

import ast
import enum
import inspect
import re
import types
from pathlib import Path

import pytest

import udeshi_markets
from udeshi_markets import _engine

STUB = Path(udeshi_markets.__file__).with_name("_engine.pyi")


def _stub_tree() -> ast.Module:
    return ast.parse(STUB.read_text())


def _public(names: list[str]) -> set[str]:
    return {name for name in names if not name.startswith("_") or name == "__version__"}


def _stub_classes() -> list[tuple[ast.ClassDef, type]]:
    return [
        (node, getattr(_engine, node.name))
        for node in _stub_tree().body
        if isinstance(node, ast.ClassDef)
    ]


def _members(node: ast.ClassDef) -> dict[str, ast.stmt]:
    members: dict[str, ast.stmt] = {}
    for item in node.body:
        if isinstance(item, ast.FunctionDef):
            members[item.name] = item
        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            members[item.target.id] = item
    return members


def _is_uninstantiable(init: ast.FunctionDef) -> bool:
    """The stub's marker for a class without a Python constructor: a required parameter of
    type Never, which no argument can satisfy."""
    parameters = [*init.args.posonlyargs, *init.args.args, *init.args.kwonlyargs]
    return any(
        isinstance(p.annotation, ast.Name) and p.annotation.id == "Never" for p in parameters
    )


def test_package_ships_its_type_information() -> None:
    assert STUB.is_file()
    assert STUB.with_name("py.typed").is_file()


def test_every_module_attribute_has_a_stub_and_vice_versa() -> None:
    stub_names: set[str] = set()
    for node in _stub_tree().body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef):
            stub_names.add(node.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            stub_names.add(node.target.id)
    module_names = _public(dir(_engine))
    assert stub_names == module_names


def test_class_members_match() -> None:
    for node, runtime in _stub_classes():
        if issubclass(runtime, enum.Enum):
            stub_members = {
                target.id
                for item in node.body
                if isinstance(item, ast.Assign)
                for target in item.targets
                if isinstance(target, ast.Name)
            }
            assert stub_members == {member.name for member in runtime}, node.name
            for item in node.body:
                if isinstance(item, ast.Assign) and isinstance(item.value, ast.Constant):
                    name = item.targets[0]
                    assert isinstance(name, ast.Name)
                    assert runtime[name.id].value == item.value.value, f"{node.name}.{name.id}"
            continue
        stub_public = {name for name in _members(node) if not name.startswith("_")}
        runtime_public = {name for name, _ in inspect.getmembers(runtime) if name[0] != "_"}
        assert stub_public == runtime_public, node.name


def test_constructors_match() -> None:
    """Every stub class states whether Python can construct it, and the module agrees: classes
    without a bound constructor (instances come only from the engine) are uninstantiable."""
    for node, runtime in _stub_classes():
        if issubclass(runtime, enum.Enum):
            continue
        init = _members(node).get("__init__")
        assert isinstance(init, ast.FunctionDef), f"{node.name}: the stub must declare __init__"
        # pybind11 binds a constructor as a method; a class without one keeps the base type's
        # __init__ slot, which raises "No constructor defined!".
        constructible = not isinstance(vars(runtime)["__init__"], types.WrapperDescriptorType)
        assert _is_uninstantiable(init) == (not constructible), node.name
        if not constructible:
            with pytest.raises(TypeError, match="No constructor defined"):
                runtime()


def test_equality_and_hashing_match() -> None:
    """A class that binds __eq__ is unhashable at runtime (pybind11 sets __hash__ = None), and
    its stub must say both, or mypy would accept it as a set member or a dict key."""
    for node, runtime in _stub_classes():
        if issubclass(runtime, enum.Enum):
            continue
        members = _members(node)
        assert ("__eq__" in members) == ("__eq__" in vars(runtime)), node.name
        assert ("__hash__" in members) == (runtime.__hash__ is None), node.name


# ---- Signatures ------------------------------------------------------------------------------
#
# pybind11 writes each function's signature as the first line of its docstring, e.g.
#   add_instrument(self: udeshi_markets._engine.Exchange, *, symbol: str, ...) -> int
# The line is parsed as Python and compared with the stub: parameter names, kinds, types and
# defaults, and the return type. Before parsing, enum defaults (<Side.BUY: 0>) are spelled as
# Side.BUY, and the protocols pybind11 names for numeric arguments as the int and float that the
# stub promises.

_ENUM_REPR = re.compile(r"<(\w+\.\w+): [^<>]*>")
_SPELLINGS = (
    ("udeshi_markets._engine.", ""),
    ("typing.SupportsInt | typing.SupportsIndex", "int"),
    ("typing.SupportsFloat | typing.SupportsIndex", "float"),
)

Parameter = tuple[str, str, str | None, str | None]  # Name, kind, annotation, default.


def _source(node: ast.expr | None) -> str | None:
    return None if node is None else ast.unparse(node)


def _runtime_signature(function: object) -> ast.FunctionDef:
    doc = function.__doc__ or ""
    line = doc.splitlines()[0] if doc else ""
    match = re.fullmatch(r"\w*\((.*)\) -> (.+)", line)
    assert match is not None, f"no pybind11 signature in {line!r}"
    parameters = _ENUM_REPR.sub(r"\1", match[1])
    source = f"def f({parameters}) -> {match[2]}: ..."
    for old, new in _SPELLINGS:
        source = source.replace(old, new)
    parsed = ast.parse(source).body[0]
    assert isinstance(parsed, ast.FunctionDef)
    return parsed


def _signature(function: ast.FunctionDef, *, method: bool) -> tuple[list[Parameter], str | None]:
    """Parameters (without ``self`` for a method, whose annotation only the runtime spells out)
    and the return annotation."""
    arguments = function.args
    assert arguments.vararg is None, f"{function.name}: unexpected *args"
    assert arguments.kwarg is None, f"{function.name}: unexpected **kwargs"
    positional = [*arguments.posonlyargs, *arguments.args]
    kinds = ["positional-only"] * len(arguments.posonlyargs) + ["positional"] * len(arguments.args)
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(arguments.defaults))
    defaults += arguments.defaults
    parameters: list[Parameter] = [
        (arg.arg, kind, _source(arg.annotation), _source(default))
        for arg, kind, default in zip(positional, kinds, defaults, strict=True)
    ]
    parameters += [
        (arg.arg, "keyword-only", _source(arg.annotation), _source(default))
        for arg, default in zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True)
    ]
    return (parameters[1:] if method else parameters), _source(function.returns)


def _is_property(function: ast.FunctionDef) -> bool:
    return any(isinstance(d, ast.Name) and d.id == "property" for d in function.decorator_list)


def test_function_signatures_match() -> None:
    for node in _stub_tree().body:
        if isinstance(node, ast.FunctionDef):
            runtime = _runtime_signature(getattr(_engine, node.name))
            assert _signature(node, method=False) == _signature(runtime, method=False), node.name


def test_constant_types_match() -> None:
    for node in _stub_tree().body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            value = getattr(_engine, node.target.id)
            assert _source(node.annotation) == type(value).__name__, node.target.id


def test_methods_and_properties_match() -> None:
    """Methods match signature for signature. A stub @property is read-only at runtime and a
    stub attribute read-write; both have the getter's type."""
    for node, runtime in _stub_classes():
        if issubclass(runtime, enum.Enum):
            continue
        for name, member in _members(node).items():
            if name.startswith("_") and name != "__init__":
                continue  # __eq__ (typed against object in the stub) and __hash__: see above.
            label = f"{node.name}.{name}"
            attribute = inspect.getattr_static(runtime, name)
            if isinstance(member, ast.AnnAssign):
                assert isinstance(attribute, property), f"{label}: not a property at runtime"
                assert attribute.fset is not None, f"{label}: read-only at runtime"
                getter = _runtime_signature(attribute.fget)
                assert _source(member.annotation) == _source(getter.returns), label
                continue
            assert isinstance(member, ast.FunctionDef)
            if _is_property(member):
                assert isinstance(attribute, property), f"{label}: not a property at runtime"
                assert attribute.fset is None, f"{label}: writable at runtime"
                getter = _runtime_signature(attribute.fget)
                assert _signature(member, method=True) == _signature(getter, method=True), label
            elif name == "__init__" and _is_uninstantiable(member):
                continue  # No constructor: test_constructors_match.
            else:
                assert not isinstance(attribute, property), f"{label}: a property at runtime"
                runtime_signature = _runtime_signature(attribute)
                assert _signature(member, method=True) == _signature(
                    runtime_signature, method=True
                ), label
