"""The README's Python examples must keep running and keep printing the same deterministic
results."""

from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path

README = Path(__file__).resolve().parents[2] / "README.md"

EXPECTED = [
    "9263 28424 dbcb62714eb57ef0c337fed2bbec35d5\n",
    "3463 {'fundamental': 2038, 'market_maker': -1453, 'momentum': 206, "
    "'pairs': -31, 'random': -760}\n",
    "spread 10.48 -> 8.83 bps, Welch p = 0.0286\n",
]


def run(block: str) -> str:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        exec(compile(block, str(README), "exec"), {"__name__": "readme_example"})
    return stdout.getvalue()


def test_readme_examples_run_and_are_deterministic() -> None:
    blocks = re.findall(r"```python\n(.*?)```", README.read_text(encoding="utf-8"), re.DOTALL)
    assert len(blocks) == len(EXPECTED), "an example was added or removed: update EXPECTED"
    for block, expected in zip(blocks, EXPECTED, strict=True):
        first = run(block)
        assert first == run(block)
        assert first == expected
