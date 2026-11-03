"""Keeps the hand-written type stubs (_engine.pyi) in sync with the compiled module."""

import ast
import enum
import inspect
from pathlib import Path

import udeshi_markets
from udeshi_markets import _engine

STUB = Path(udeshi_markets.__file__).with_name("_engine.pyi")


def _stub_tree() -> ast.Module:
    return ast.parse(STUB.read_text())


def _public(names: list[str]) -> set[str]:
    return {name for name in names if not name.startswith("_") or name == "__version__"}


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
    for node in _stub_tree().body:
        if not isinstance(node, ast.ClassDef):
            continue
        runtime = getattr(_engine, node.name)
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
        stub_members = set()
        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                stub_members.add(item.name)
            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                stub_members.add(item.target.id)
        runtime_members = {
            name
            for name, _ in inspect.getmembers(runtime)
            if not name.startswith("_") or name == "__init__"
        }
        if "__init__" not in stub_members:
            runtime_members.discard("__init__")
        assert stub_members == runtime_members, node.name
