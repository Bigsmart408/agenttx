"""Synthetic coding-agent trajectory (>=24 tool calls)."""

from __future__ import annotations

from pathlib import Path
from typing import List

from agenttx.harness import TrajectoryStep


def seed_repo(workdir: Path) -> None:
    workdir = Path(workdir)
    (workdir / "src").mkdir(parents=True, exist_ok=True)
    (workdir / "tests").mkdir(parents=True, exist_ok=True)
    (workdir / "src" / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\ndef mul(a, b):\n    return a * b\n",
        encoding="utf-8",
    )
    (workdir / "tests" / "test_calc.py").write_text(
        "from src.calc import add, mul\n\ndef test_add():\n    assert add(1, 2) == 3\n\ndef test_mul():\n    assert mul(2, 3) == 6\n",
        encoding="utf-8",
    )
    (workdir / "README.md").write_text("# toy calc\n", encoding="utf-8")


def build_coding_trajectory() -> List[TrajectoryStep]:
    """Emulate: explore -> edit -> test -> fix -> refactor -> test (>=24 steps)."""
    steps: List[TrajectoryStep] = []
    # explore
    steps.append(TrajectoryStep("run_shell", {"cmd": "ls -la && find . -type f | sort"}))
    steps.append(TrajectoryStep("read_file", {"path": "src/calc.py"}))
    steps.append(TrajectoryStep("read_file", {"path": "tests/test_calc.py"}))
    steps.append(TrajectoryStep("read_file", {"path": "README.md"}))
    # feature: add sub + pow
    steps.append(TrajectoryStep("write_file", {
        "path": "src/calc.py",
        "content": (
            "def add(a, b):\n    return a + b\n\n"
            "def mul(a, b):\n    return a * b\n\n"
            "def sub(a, b):\n    return a - b\n\n"
            "def pow2(a):\n    return a * a\n"
        ),
    }))
    steps.append(TrajectoryStep("append_file", {
        "path": "tests/test_calc.py",
        "content": (
            "\nfrom src.calc import sub, pow2\n\n"
            "def test_sub():\n    assert sub(5, 2) == 3\n\n"
            "def test_pow2():\n    assert pow2(4) == 16\n"
        ),
    }))
    # run tests (may fail if import path wrong — use PYTHONPATH)
    steps.append(TrajectoryStep("run_tests", {
        "cmd": "PYTHONPATH=. python3 -m pytest -q tests/test_calc.py",
        "ignore_errors": True,
    }))
    # fix packaging
    steps.append(TrajectoryStep("write_file", {"path": "src/__init__.py", "content": ""}))
    steps.append(TrajectoryStep("run_tests", {
        "cmd": "PYTHONPATH=. python3 -m pytest -q tests/test_calc.py",
        "ignore_errors": True,
    }))
    # refactor into modules
    for name, body in [
        ("src/ops_add.py", "def add(a, b):\n    return a + b\n"),
        ("src/ops_mul.py", "def mul(a, b):\n    return a * b\n"),
        ("src/ops_sub.py", "def sub(a, b):\n    return a - b\n"),
        ("src/ops_pow.py", "def pow2(a):\n    return a * a\n"),
    ]:
        steps.append(TrajectoryStep("write_file", {"path": name, "content": body}))
    steps.append(TrajectoryStep("write_file", {
        "path": "src/calc.py",
        "content": (
            "from src.ops_add import add\n"
            "from src.ops_mul import mul\n"
            "from src.ops_sub import sub\n"
            "from src.ops_pow import pow2\n"
        ),
    }))
    steps.append(TrajectoryStep("run_tests", {
        "cmd": "PYTHONPATH=. python3 -m pytest -q tests/test_calc.py",
        "ignore_errors": True,
    }))
    # docs + misc edits to lengthen trajectory
    for i in range(1, 6):
        steps.append(TrajectoryStep("write_file", {
            "path": f"notes/step{i}.md",
            "content": f"# note {i}\nrefactor pass {i}\n",
        }))
        steps.append(TrajectoryStep("read_file", {"path": f"notes/step{i}.md"}))
    steps.append(TrajectoryStep("append_file", {
        "path": "README.md",
        "content": "\n## API\nadd/mul/sub/pow2\n",
    }))
    steps.append(TrajectoryStep("run_shell", {"cmd": "wc -l src/*.py tests/*.py README.md"}))
    steps.append(TrajectoryStep("run_tests", {
        "cmd": "PYTHONPATH=. python3 -m pytest -q tests/test_calc.py",
        "ignore_errors": True,
    }))
    return steps
