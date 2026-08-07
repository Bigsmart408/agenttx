"""Deterministic long Coding Agent workload for AgentTX evaluation.

The trajectory is intentionally more realistic than the original toy trace:
exploration, a feature addition, multi-file refactoring, a failing CI loop,
independent documentation/configuration edits, repair, and cleanup.  It is
parameterized by the requested number of tool calls so scaling runs can reuse
the same semantics without changing the causal fault.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Sequence

from agenttx.harness import TrajectoryStep

MIN_STEPS = 54
DEFAULT_STEPS = 64
FAULT_TAG = "faulty_formatting"
REPAIR_TAG = "repair_formatting"
INDEPENDENT_PATHS = (
    "docs/CHANGELOG.md",
    "config/feature.flags",
    "docs/attempt-1.md",
)


def seed_long_repo(workdir: Path) -> None:
    """Create the same small repository before every baseline run."""
    workdir = Path(workdir)
    for directory in ("src", "tests", "docs", "config", "notes"):
        (workdir / directory).mkdir(parents=True, exist_ok=True)
    (workdir / "src" / "__init__.py").write_text("", encoding="utf-8")
    (workdir / "src" / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n"
        "def mul(a, b):\n    return a * b\n",
        encoding="utf-8",
    )
    (workdir / "tests" / "test_calc.py").write_text(
        "from src.calc import add, mul\n\n"
        "def test_add():\n    assert add(1, 2) == 3\n\n"
        "def test_mul():\n    assert mul(2, 3) == 6\n",
        encoding="utf-8",
    )
    (workdir / "README.md").write_text(
        "# ledger calculator\n\nA small package used for coding-agent experiments.\n",
        encoding="utf-8",
    )
    (workdir / "config" / "defaults.ini").write_text(
        "[calculator]\nprecision=2\nformat=key-value\n",
        encoding="utf-8",
    )
    (workdir / "docs" / "DESIGN.md").write_text(
        "# Design\n\nThe initial package exposes add and mul.\n",
        encoding="utf-8",
    )


def _step(tool: str, **args: object) -> TrajectoryStep:
    return TrajectoryStep(tool, dict(args))


def build_long_coding_trajectory(length: int = DEFAULT_STEPS) -> List[TrajectoryStep]:
    """Build a deterministic trajectory with exactly ``length`` tool calls.

    The bad formatting write is intentionally followed by a failing test and
    independent docs/config writes.  A recovery benchmark executes the prefix,
    calls ``rollback_causal`` at that write, then continues with the repair
    suffix.  The normal trajectory executes the same steps without rollback.
    """
    if length < MIN_STEPS:
        raise ValueError(f"length must be >= {MIN_STEPS}")
    steps: List[TrajectoryStep] = []

    # 1. Explore repository state and record a negative lookup.
    steps.extend(
        [
            _step("run_shell", cmd="find . -maxdepth 3 -type f | sort"),
            _step("read_file", path="src/calc.py"),
            _step("read_file", path="tests/test_calc.py"),
            _step("read_file", path="README.md"),
            _step("read_file", path="config/defaults.ini"),
            _step("read_file", path="docs/ARCHITECTURE.md"),
            _step("run_shell", cmd="python3 --version"),
        ]
    )

    # 2. Add sub/pow2, test, then split the implementation into modules.
    steps.extend(
        [
            _step(
                "write_file",
                path="src/calc.py",
                content=(
                    "def add(a, b):\n    return a + b\n\n"
                    "def mul(a, b):\n    return a * b\n\n"
                    "def sub(a, b):\n    return a - b\n\n"
                    "def pow2(a):\n    return a * a\n"
                ),
            ),
            _step(
                "append_file",
                path="tests/test_calc.py",
                content=(
                    "\nfrom src.calc import sub, pow2\n\n"
                    "def test_sub():\n    assert sub(5, 2) == 3\n\n"
                    "def test_pow2():\n    assert pow2(4) == 16\n"
                ),
            ),
            _step(
                "run_tests",
                cmd="PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider",
                ignore_errors=True,
            ),
            _step("write_file", path="src/__init__.py", content=""),
            _step("write_file", path="src/ops_add.py", content="def add(a, b):\n    return a + b\n"),
            _step("write_file", path="src/ops_mul.py", content="def mul(a, b):\n    return a * b\n"),
            _step("write_file", path="src/ops_sub.py", content="def sub(a, b):\n    return a - b\n"),
            _step("write_file", path="src/ops_pow.py", content="def pow2(a):\n    return a * a\n"),
            _step(
                "write_file",
                path="src/calc.py",
                content=(
                    "from src.ops_add import add\n"
                    "from src.ops_mul import mul\n"
                    "from src.ops_sub import sub\n"
                    "from src.ops_pow import pow2\n"
                ),
            ),
            _step("run_tests", cmd="PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider", ignore_errors=True),
        ]
    )

    # 3. Add division and publish the first API/configuration pass.
    steps.extend(
        [
            _step(
                "write_file",
                path="src/ops_div.py",
                content=(
                    "def div(a, b):\n"
                    "    if b == 0:\n"
                    "        raise ZeroDivisionError('b must be non-zero')\n"
                    "    return a / b\n"
                ),
            ),
            _step(
                "write_file",
                path="src/calc.py",
                content=(
                    "from src.ops_add import add\n"
                    "from src.ops_mul import mul\n"
                    "from src.ops_sub import sub\n"
                    "from src.ops_pow import pow2\n"
                    "from src.ops_div import div\n"
                ),
            ),
            _step(
                "append_file",
                path="tests/test_calc.py",
                content=(
                    "\nfrom src.ops_div import div\n\n"
                    "def test_div():\n    assert div(6, 2) == 3\n\n"
                    "def test_div_zero():\n"
                    "    import pytest\n"
                    "    with pytest.raises(ZeroDivisionError):\n"
                    "        div(1, 0)\n"
                ),
            ),
            _step("run_tests", cmd="PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider", ignore_errors=True),
            _step(
                "write_file",
                path="config/agent.toml",
                content="[agent]\nmax_retries = 2\nrun_tests = true\n",
            ),
            _step(
                "write_file",
                path="docs/API.md",
                content=(
                    "# API\n\n"
                    "`src.calc` re-exports add, mul, sub, and pow2.\n"
                    "`src.ops_div.div` rejects a zero denominator.\n"
                ),
            ),
            _step("read_file", path="docs/API.md"),
            _step("append_file", path="README.md", content="\n## API\nadd / mul / sub / pow2 / div\n"),
            _step("run_shell", cmd="wc -l src/*.py tests/*.py docs/API.md config/agent.toml"),
            _step("run_tests", cmd="PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider", ignore_errors=True),
        ]
    )

    # 4. Deliberately introduce a bad implementation and observe failing CI.
    # This is the causal fault used by the recovery benchmark.
    steps.extend(
        [
            _step(
                "write_file",
                path="lib/formatting.py",
                content=(
                    "def format_result(name, value):\n"
                    "    return f\"{name}:{value}\"\n"
                ),
                tag=FAULT_TAG,
            ),
            _step(
                "append_file",
                path="tests/test_calc.py",
                content=(
                    "\nfrom lib.formatting import format_result\n\n"
                    "def test_format_result():\n"
                    "    assert format_result('total', 3) == 'total=3.00'\n"
                ),
            ),
            _step("run_tests", cmd="PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider", ignore_errors=True, tag="expected_failure"),
            _step(
                "run_shell",
                cmd="mkdir -p build && cat lib/formatting.py > build/format-report.txt",
                tag="derived_artifact",
            ),
            # These writes are intentionally independent of formatting.py.
            _step(
                "write_file",
                path="docs/CHANGELOG.md",
                content="# Changelog\n\n- Added modular arithmetic API.\n- CI formatting check is pending.\n",
                tag="independent_docs",
            ),
            _step(
                "write_file",
                path="config/feature.flags",
                content="calculator_v2=true\nformatting_v2=true\n",
                tag="independent_config",
            ),
            _step("run_shell", cmd="wc -l docs/CHANGELOG.md config/feature.flags"),
            _step("read_file", path="docs/CHANGELOG.md"),
            _step("read_file", path="config/feature.flags"),
            _step(
                "write_file",
                path="docs/attempt-1.md",
                content="# Attempt 1\nObserved a formatting assertion failure.\n",
                tag="independent_notes",
            ),
            _step("read_file", path="docs/attempt-1.md"),
        ]
    )

    # 5. Repair the fault and add an API/validation surface.
    steps.extend(
        [
            _step(
                "write_file",
                path="lib/formatting.py",
                content=(
                    "def format_result(name, value):\n"
                    "    return f\"{name}={value:.2f}\"\n"
                ),
                tag=REPAIR_TAG,
            ),
            _step("run_tests", cmd="PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider", ignore_errors=True),
            _step(
                "write_file",
                path="src/api.py",
                content=(
                    "from src.calc import add, div, mul, pow2, sub\n"
                    "from lib.formatting import format_result\n\n"
                    "__all__ = ['add', 'div', 'format_result', 'mul', 'pow2', 'sub']\n"
                ),
            ),
            _step(
                "append_file",
                path="tests/test_calc.py",
                content=(
                    "\nfrom src.api import format_result\n\n"
                    "def test_public_api_format():\n"
                    "    assert format_result('mean', 2.5) == 'mean=2.50'\n"
                ),
            ),
            _step("run_tests", cmd="PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider", ignore_errors=True),
            _step(
                "write_file",
                path="src/validation.py",
                content=(
                    "def require_number(value):\n"
                    "    if not isinstance(value, (int, float)):\n"
                    "        raise TypeError('number required')\n"
                    "    return value\n"
                ),
            ),
            _step(
                "append_file",
                path="tests/test_calc.py",
                content=(
                    "\nfrom src.validation import require_number\n\n"
                    "def test_validation():\n    assert require_number(4) == 4\n"
                ),
            ),
            _step("run_tests", cmd="PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider", ignore_errors=True),
        ]
    )

    # 6. Exercise delete effects and a final documentation pass.
    steps.extend(
        [
            _step("write_file", path="notes/tmp_debug.txt", content="temporary trace\n"),
            _step("delete_file", path="notes/tmp_debug.txt"),
            _step("run_shell", cmd="find src tests docs config notes -type f | sort"),
            _step(
                "write_file",
                path="docs/ARCHITECTURE.md",
                content=(
                    "# Architecture\n\n"
                    "The calc facade delegates to operation modules.\n"
                    "Formatting and validation are independent services.\n"
                ),
            ),
            _step("read_file", path="docs/ARCHITECTURE.md"),
            _step("append_file", path="docs/CHANGELOG.md", content="- Repaired formatting and added validation.\n"),
            _step("run_tests", cmd="PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider", ignore_errors=True),
        ]
    )

    # 7. Fill out a realistic long tail of inspect/edit/check iterations while
    # preserving the fixed fault and final test as the last step.
    iteration = 1
    while len(steps) < length - 1:
        remaining = length - 1 - len(steps)
        note_path = f"notes/iteration-{iteration:02d}.md"
        if remaining >= 2:
            steps.append(
                _step(
                    "write_file",
                    path=note_path,
                    content=f"# Iteration {iteration}\nReviewed the modular API.\n",
                    tag="long_tail",
                )
            )
            steps.append(_step("read_file", path=note_path))
        else:
            steps.append(_step("run_shell", cmd="python3 -m compileall -q src"))
        iteration += 1

    steps.append(_step("run_tests", cmd="PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider", ignore_errors=True, tag="final_check"))
    assert len(steps) == length, (len(steps), length)
    return steps


def find_tagged_step(steps: Sequence[TrajectoryStep], tag: str) -> int:
    """Return the unique index carrying ``tag`` for semantic experiments."""
    matches = [index for index, step in enumerate(steps) if step.args.get("tag") == tag]
    if len(matches) != 1:
        raise ValueError(f"expected one {tag!r} step, got {matches}")
    return matches[0]


def fault_step_index(steps: Sequence[TrajectoryStep] | None = None) -> int:
    return find_tagged_step(steps or build_long_coding_trajectory(), FAULT_TAG)


def independent_paths() -> Sequence[str]:
    return INDEPENDENT_PATHS