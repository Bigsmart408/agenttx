"""Shared inject–policy–repair DAG for application-workload recovery.

Official SWE-Bench / Terminal-Bench instances provide the workspace and the
utility predicate.  This module overlays one faulty producer, later independent
documents, a derived artifact, and a failing verification command so causal vs
coarse recovery can be compared on the same ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple


@dataclass(frozen=True)
class DocSpec:
    path: str
    prefix: str
    lines: int


def document_content(prefix: str, lines: int, task_name: str) -> str:
    title = prefix.title().replace("_", " ")
    rows = [f"# {title}"]
    for index in range(1, lines + 1):
        rows.append(
            f"{prefix.upper()}-{index:03d}: verified {task_name} work item "
            f"{index:03d} records a repository decision."
        )
    return "\n".join(rows) + "\n"


def document_valid(path: Path, prefix: str, lines: int) -> bool:
    if not path.exists():
        return False
    content = path.read_text(encoding="utf-8").splitlines()
    expected = [f"{prefix.upper()}-{index:03d}:" for index in range(1, lines + 1)]
    entries = [line for line in content[1:] if line.startswith(prefix.upper() + "-")]
    return len(entries) == lines and all(
        line.startswith(label) for line, label in zip(entries, expected)
    )


def all_documents_valid(workdir: Path, docs: Sequence[DocSpec]) -> bool:
    return all(
        document_valid(Path(workdir) / spec.path, spec.prefix, spec.lines)
        for spec in docs
    )


def _step_failed(step) -> bool:
    code = getattr(step, "exit_code", None)
    if code is None:
        code = getattr(step, "returncode", 1)
    return int(code) != 0


def inject_recovery_dag(
    agent,
    *,
    docs: Sequence[DocSpec],
    task_name: str,
    prefix_writes: Sequence[Tuple[str, str]] = (),
    faulty_path: str,
    faulty_content: str,
    derived_cmd: str,
    test_cmd: str,
) -> dict:
    """Write tests/context, a faulty producer, independent docs, derived state, then fail tests."""

    prefix_steps = []
    for path, content in prefix_writes:
        prefix_steps.append(
            agent.harness.call_tool("write_file", {"path": path, "content": content})
        )
    faulty = agent.harness.call_tool(
        "write_file",
        {"path": faulty_path, "content": faulty_content},
    )
    independent_steps = []
    for spec in docs:
        independent_steps.append(
            agent.harness.call_tool(
                "write_file",
                {
                    "path": spec.path,
                    "content": document_content(spec.prefix, spec.lines, task_name),
                },
            )
        )
    derived = agent.harness.call_tool("run_shell", {"cmd": derived_cmd})
    failing = agent.harness.call_tool("run_tests", {"cmd": test_cmd})
    return {
        "prefix_steps": [step.step_id for step in prefix_steps],
        "root_step": faulty.step_id,
        "independent_steps": [step.step_id for step in independent_steps],
        "derived_step": derived.step_id,
        "test_run_step": failing.step_id,
        "root_is_parent_of_derived": faulty.step_id in derived.parents,
        "root_is_parent_of_tests": faulty.step_id in failing.parents,
        "independent_is_parent_of_derived": any(
            step.step_id in derived.parents for step in independent_steps
        ),
        "tests_failed": _step_failed(failing),
    }


def dag_is_valid(injected: dict) -> bool:
    return bool(
        injected.get("tests_failed")
        and injected.get("root_is_parent_of_derived")
        and injected.get("root_is_parent_of_tests")
        and not injected.get("independent_is_parent_of_derived")
    )


def recovery_prompt(
    *,
    title: str,
    context: str,
    instruction: str,
    docs: Sequence[DocSpec],
    test_cmd: str,
    extra_rules: Iterable[str] = (),
) -> str:
    doc_lines = "\n".join(
        f"- `{spec.path}` must contain a title followed by exactly {spec.lines} "
        f"ordered entries `{spec.prefix.upper()}-001:` through "
        f"`{spec.prefix.upper()}-{spec.lines:03d}:`."
        for spec in docs
    )
    extras = "\n".join(f"{index}. {rule}" for index, rule in enumerate(extra_rules, start=4))
    extra_block = f"\n{extras}\n" if extras else "\n"
    return f"""A previous attempt at this official benchmark task introduced a faulty producer in this protected coding session.  Useful work may have been lost by the selected recovery policy.

Task: {title}
{context}

Official instruction:
{instruction.strip()}

Recovery protocol:
1. Inspect the workspace and run the official verifier with
   `{test_cmd}`.
2. Complete the official task.  Preserve every valid independent artifact.  Recreate a missing artifact only when it is absent; do not rewrite an artifact that already satisfies its contract.
{doc_lines}
3. Ensure `recovery_build/derived.txt` is absent before finishing.{extra_block}5. Do not call any rollback tool: the comparison policy has already run.
6. Call `finish` with `commit=false` and a one-sentence summary when the official verifier passes.

Stay inside the workspace.  Do not use shell loops, Python generators, or bulk
file-copy commands to manufacture the documents.  This run charges all API
prompt, completion, tool-schema, diagnosis, validation, and repair tokens after
the recovery policy.
"""
