"""Harder multi-file refactor workload for live agents."""
from __future__ import annotations
from pathlib import Path

REFACTOR_TASK = """You are refactoring a small Python package.

Goals:
1. Split src/calc.py into modules: src/ops_add.py, src/ops_mul.py, src/ops_sub.py (move add/mul/sub).
2. Make src/calc.py a thin re-export facade importing those functions.
3. Add src/ops_div.py with div(a,b) that raises ZeroDivisionError on b==0.
4. Update tests/test_calc.py to cover add/mul/sub/div (including zero-division).
5. Add notes/REFACTOR.md describing the new layout in <=10 lines.
6. Run: PYTHONPATH=. python -m pytest -q
7. Call finish when tests pass. Prefer commit=false.

Constraints: stay inside the workspace; do not touch files outside it.
"""


def seed_refactor_repo(workdir: Path) -> None:
    workdir = Path(workdir)
    (workdir / "src").mkdir(parents=True, exist_ok=True)
    (workdir / "tests").mkdir(parents=True, exist_ok=True)
    (workdir / "src" / "__init__.py").write_text("", encoding="utf-8")
    (workdir / "src" / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n"
        "def mul(a, b):\n    return a * b\n\n"
        "def sub(a, b):\n    return a - b\n",
        encoding="utf-8",
    )
    (workdir / "tests" / "test_calc.py").write_text(
        "from src.calc import add, mul, sub\n\n"
        "def test_add():\n    assert add(1, 2) == 3\n\n"
        "def test_mul():\n    assert mul(2, 3) == 6\n\n"
        "def test_sub():\n    assert sub(5, 2) == 3\n",
        encoding="utf-8",
    )
    (workdir / "README.md").write_text("# calc package\n", encoding="utf-8")