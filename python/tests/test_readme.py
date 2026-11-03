"""The README's Python example must keep running and keep printing the same deterministic result."""

from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path

README = Path(__file__).resolve().parents[2] / "README.md"


def test_readme_example_runs_and_is_deterministic() -> None:
    blocks = re.findall(r"```python\n(.*?)```", README.read_text(encoding="utf-8"), re.DOTALL)
    assert len(blocks) == 1, "expected exactly one Python example in the README"
    outputs = []
    for _ in range(2):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exec(compile(blocks[0], str(README), "exec"), {"__name__": "readme_example"})
        outputs.append(stdout.getvalue())
    assert outputs[0] == outputs[1]
    assert outputs[0] == "9263 28424 dbcb62714eb57ef0c337fed2bbec35d5\n"
