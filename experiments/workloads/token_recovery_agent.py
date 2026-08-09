"""Workload whose retained documents have measurable LLM replay cost."""

from __future__ import annotations

from pathlib import Path


DOCUMENT_LINES = 48

CORRECT_PIPELINE = """def normalize(value):
    return value.strip().lower()
"""

BUGGY_PIPELINE = """def normalize(value):
    return value.strip().upper()
"""


def document_content(kind: str, document_lines: int = DOCUMENT_LINES) -> str:
    if kind not in {"DESIGN", "CHANGE"}:
        raise ValueError(kind)
    title = "Design invariants" if kind == "DESIGN" else "Change log"
    lines = [f"# {title}"]
    for index in range(1, document_lines + 1):
        lines.append(
            f"{kind}-{index:03d}: retained agent work item {index:03d} "
            "records a distinct verified repository decision."
        )
    return "\n".join(lines) + "\n"


TOKEN_RECOVERY_TASK = f"""A filesystem recovery policy has already been applied
to this protected coding session. Finish recovery with minimum regenerated work.

1. Run: PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python -m pytest -q -p no:cacheprovider
2. If a document is invalid, recreate it with write_file. Do not use shell loops,
   Python generators, or append_file for document generation.
3. docs/design.md must contain at least {DOCUMENT_LINES} distinct lines beginning
   DESIGN-001 through DESIGN-{DOCUMENT_LINES:03d}.
4. docs/changelog.md must contain at least {DOCUMENT_LINES} distinct lines beginning
   CHANGE-001 through CHANGE-{DOCUMENT_LINES:03d}.
5. Do not rewrite a document that already passes the tests. Preserve valid work.
6. Ensure artifacts/rendered.txt is absent and the tests pass.
7. Write notes/RECOVERY.md in at most 6 lines, then call finish with commit=false.

This experiment measures actual API prompt/completion tokens needed after the
policy has recovered the filesystem. Stay inside the workspace and do not call
another rollback tool.
"""


def seed_token_recovery_repo(
    workdir: Path, document_lines: int = DOCUMENT_LINES
) -> None:
    workdir = Path(workdir)
    for directory in ("src", "tests", "docs", "artifacts", "notes"):
        (workdir / directory).mkdir(parents=True, exist_ok=True)
    (workdir / "src" / "__init__.py").write_text("", encoding="utf-8")
    (workdir / "src" / "pipeline.py").write_text(
        CORRECT_PIPELINE, encoding="utf-8"
    )
    # Placeholders avoid parent-directory effects while still failing validation
    # if a coarse rollback removes the speculative document versions.
    (workdir / "docs" / "design.md").write_text("PENDING\n", encoding="utf-8")
    (workdir / "docs" / "changelog.md").write_text("PENDING\n", encoding="utf-8")
    (workdir / "tests" / "test_recovery_state.py").write_text(
        "from pathlib import Path\n"
        "from src.pipeline import normalize\n\n"
        f"EXPECTED = {document_lines}\n\n"
        "def _items(path, prefix):\n"
        "    lines = Path(path).read_text(encoding='utf-8').splitlines()\n"
        "    return [line for line in lines if line.startswith(prefix + '-')]\n\n"
        "def test_pipeline_restored():\n"
        "    assert normalize(' Mixed ') == 'mixed'\n\n"
        "def test_design_retained():\n"
        "    items = _items('docs/design.md', 'DESIGN')\n"
        "    assert len(items) >= EXPECTED\n"
        "    assert len(set(items)) >= EXPECTED\n\n"
        "def test_changelog_retained():\n"
        "    items = _items('docs/changelog.md', 'CHANGE')\n"
        "    assert len(items) >= EXPECTED\n"
        "    assert len(set(items)) >= EXPECTED\n",
        encoding="utf-8",
    )


def inject_token_recovery_trajectory(
    agent, document_lines: int = DOCUMENT_LINES
) -> dict:
    """Create valid prefix work, a fault, later independent work, and descendants."""

    prefix = agent.harness.call_tool(
        "write_file",
        {
            "path": "docs/design.md",
            "content": document_content("DESIGN", document_lines).rstrip(),
        },
    )
    root = agent.harness.call_tool(
        "write_file",
        {"path": "src/pipeline.py", "content": BUGGY_PIPELINE.rstrip()},
    )
    independent = agent.harness.call_tool(
        "write_file",
        {
            "path": "docs/changelog.md",
            "content": document_content("CHANGE", document_lines).rstrip(),
        },
    )
    derived = agent.harness.call_tool(
        "run_shell",
        {
            "cmd": (
                "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python -c \""
                "from src.pipeline import normalize; "
                "open('artifacts/rendered.txt','w').write(normalize(' Mixed '))\""
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
        "prefix_step": prefix.step_id,
        "root_step": root.step_id,
        "independent_step": independent.step_id,
        "derived_step": derived.step_id,
        "test_step": failing_tests.step_id,
        "root_is_parent_of_derived": root.step_id in derived.parents,
        "root_is_parent_of_tests": root.step_id in failing_tests.parents,
        "independent_is_parent_of_derived": independent.step_id in derived.parents,
        "tests_failed": failing_tests.returncode != 0,
    }
