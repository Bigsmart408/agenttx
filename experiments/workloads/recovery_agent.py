"""Seeded failure-and-recovery task for a real LLM AgentTX agent."""

from __future__ import annotations

from pathlib import Path


CORRECT_PIPELINE = """def normalize(value):
    return value.strip().lower()


def render(value):
    return normalize(value)
"""

BUGGY_PIPELINE = """def normalize(value):
    return value.strip().upper()


def render(value):
    return normalize(value)
"""

INDEPENDENT_NOTE = "Independent release checklist completed.\n"

RECOVERY_TASK = """A previous automated action in this AgentTX-protected session
introduced a regression. The current tests fail, but useful independent work was
performed after the faulty action.

Diagnose and recover the session with these requirements:
1. Inspect the files, failing tests, and the AgentTX effect ledger.
2. Identify the earliest faulty producer from ledger step ids and dependency edges.
3. Use rollback_causal on that producer. Do not use whole-session or temporal rollback,
   and do not merely overwrite src/pipeline.py before performing the rollback.
4. Preserve notes/independent.md, while removing any derived artifact that depended on
   the faulty producer.
5. Run PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python -m pytest -q after recovery.
6. Write notes/RECOVERY.md (at most 8 lines) stating which step you rolled back and why.
7. Call finish with commit=false only when tests pass.

Stay inside the workspace. Use inspect_ledger before rollback_causal.
"""


def seed_recovery_repo(workdir: Path) -> None:
    workdir = Path(workdir)
    (workdir / "src").mkdir(parents=True, exist_ok=True)
    (workdir / "tests").mkdir(parents=True, exist_ok=True)
    (workdir / "src" / "__init__.py").write_text("", encoding="utf-8")
    (workdir / "src" / "pipeline.py").write_text(CORRECT_PIPELINE, encoding="utf-8")
    (workdir / "tests" / "test_pipeline.py").write_text(
        "from src.pipeline import normalize, render\n\n"
        "def test_normalize():\n"
        "    assert normalize(' Mixed ') == 'mixed'\n\n"
        "def test_render():\n"
        "    assert render(' AgentTX ') == 'agenttx'\n",
        encoding="utf-8",
    )
    (workdir / "README.md").write_text("# recovery fixture\n", encoding="utf-8")


def inject_recovery_failure(agent) -> dict:
    """Create one faulty root, independent work, and two causal descendants."""

    root = agent.harness.call_tool(
        "write_file",
        {"path": "src/pipeline.py", "content": BUGGY_PIPELINE.rstrip("\n")},
    )
    independent = agent.harness.call_tool(
        "write_file",
        {"path": "notes/independent.md", "content": INDEPENDENT_NOTE.rstrip("\n")},
    )
    derived = agent.harness.call_tool(
        "run_shell",
        {
            "cmd": (
                "mkdir -p artifacts && "
                "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python -c \""
                "from src.pipeline import render; "
                "open('artifacts/rendered.txt','w').write(render(' Mixed '))\""
            )
        },
    )
    failing_tests = agent.harness.call_tool(
        "run_tests",
        {
            "cmd": (
                "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. "
                "python -m pytest -q -p no:cacheprovider"
            )
        },
    )
    return {
        "root_step": root.step_id,
        "independent_step": independent.step_id,
        "derived_step": derived.step_id,
        "test_step": failing_tests.step_id,
        "root_is_parent_of_derived": root.step_id in derived.parents,
        "root_is_parent_of_tests": root.step_id in failing_tests.parents,
        "independent_is_parent_of_derived": independent.step_id in derived.parents,
        "tests_failed": failing_tests.exit_code != 0,
    }
