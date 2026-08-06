"""Deterministic mistake -> rollback -> recover coding trajectory."""
from __future__ import annotations

from pathlib import Path
from typing import List

from agenttx.harness import TrajectoryStep


def seed_repo(workdir: Path) -> None:
    workdir = Path(workdir)
    (workdir / "src").mkdir(parents=True, exist_ok=True)
    (workdir / "tests").mkdir(parents=True, exist_ok=True)
    (workdir / "src" / "__init__.py").write_text("", encoding="utf-8")
    (workdir / "src" / "mathy.py").write_text(
        "def add(a, b):\n    return a + b\n",
        encoding="utf-8",
    )
    (workdir / "tests" / "test_mathy.py").write_text(
        "from src.mathy import add\n\n"
        "def test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )


def build_bad_then_good() -> List[TrajectoryStep]:
    """Write a broken mul, observe test fail (caller rolls back), then good path."""
    return [
        TrajectoryStep("read_file", {"path": "src/mathy.py"}),
        TrajectoryStep(
            "write_file",
            {
                "path": "src/mathy.py",
                "content": (
                    "def add(a, b):\n    return a + b\n\n"
                    "def mul(a, b):\n    return a + b  # BUG: should multiply\n"
                ),
            },
        ),
        TrajectoryStep(
            "write_file",
            {
                "path": "tests/test_mathy.py",
                "content": (
                    "from src.mathy import add, mul\n\n"
                    "def test_add():\n    assert add(1, 2) == 3\n\n"
                    "def test_mul():\n    assert mul(2, 3) == 6\n"
                ),
            },
        ),
        TrajectoryStep(
            "run_tests",
            {
                "cmd": "PYTHONPATH=. python3 -m pytest -q tests/test_mathy.py",
                "ignore_errors": True,
            },
        ),
    ]


def build_good_fix() -> List[TrajectoryStep]:
    return [
        TrajectoryStep(
            "write_file",
            {
                "path": "src/mathy.py",
                "content": (
                    "def add(a, b):\n    return a + b\n\n"
                    "def mul(a, b):\n    return a * b\n"
                ),
            },
        ),
        TrajectoryStep(
            "write_file",
            {
                "path": "tests/test_mathy.py",
                "content": (
                    "from src.mathy import add, mul\n\n"
                    "def test_add():\n    assert add(1, 2) == 3\n\n"
                    "def test_mul():\n    assert mul(2, 3) == 6\n"
                ),
            },
        ),
        TrajectoryStep(
            "run_tests",
            {
                "cmd": "PYTHONPATH=. python3 -m pytest -q tests/test_mathy.py",
                "ignore_errors": True,
            },
        ),
        TrajectoryStep(
            "write_file",
            {"path": "notes/RECOVERY.md", "content": "Rolled back buggy mul; committed fixed mul.\n"},
        ),
    ]
