"""Keeps the hand-written type stubs (_engine.pyi) in sync with the compiled module."""

import ast
import enum
import inspect
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
