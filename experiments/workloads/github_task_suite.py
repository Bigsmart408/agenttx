"""Legacy GitHub-context sidecar tasks.

Application evaluation now uses official SWE-Bench Lite and Terminal-Bench
instances (``swe_bench_suite`` / ``terminal_bench_suite``).  This module remains
for historical GitHub-context CSV artifacts and still shares the recovery DAG
helper.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Tuple

from experiments.workloads.recovery_inject import (
    DocSpec,
    all_documents_valid as _all_documents_valid,
    all_midcrash_docs,
    inject_recovery_dag,
)


@dataclass(frozen=True)
class GitHubTask:
    name: str
    repo: str
    commit: str
    issue_url: str
    title: str
    task_prompt: str
    buggy_solution: str
    correct_solution: str
    test_body: str
    derived_command: str
    solution_symbol: str
    doc_lines: int
    doc_specs: Tuple[Tuple[str, str], ...]
    max_turns: int

    @property
    def repo_url(self) -> str:
        return f"https://github.com/{self.repo}.git"

    @property
    def doc_paths(self) -> Tuple[str, ...]:
        return tuple(path for path, _ in self.doc_specs)

    @property
    def doc_prefixes(self) -> Tuple[str, ...]:
        return tuple(prefix for _, prefix in self.doc_specs)

    def document_content(self, prefix: str) -> str:
        title = prefix.title().replace("_", " ")
        lines = [f"# {title}"]
        for index in range(1, self.doc_lines + 1):
            lines.append(
                f"{prefix.upper()}-{index:03d}: verified {self.name} work item "
                f"{index:03d} records a repository decision."
            )
        return "\n".join(lines) + "\n"

    def task_file_content(self) -> str:
        return (
            f"# {self.title}\n\n"
            f"Repository context: {self.repo} at {self.commit}.\n"
            f"Reference issue: {self.issue_url}\n\n"
            f"{self.task_prompt.strip()}\n"
        )

    def recovery_prompt(self) -> str:
        docs = "\n".join(
            f"- `{path}` must contain a title followed by exactly {self.doc_lines} "
            f"ordered entries `{prefix.upper()}-001:` through `{prefix.upper()}-{self.doc_lines:03d}:`."
            for path, prefix in self.doc_specs
        )
        return f"""A previous attempt at a GitHub-context maintenance task introduced a faulty producer in this protected coding session.  Useful work may have been lost by the selected recovery policy.

Task: {self.title}
Repository: {self.repo} at commit {self.commit}
Reference context: {self.issue_url}

{self.task_prompt.strip()}

Recovery protocol:
1. Inspect the workspace and run the targeted tests with
   `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python -m pytest -q recovery_tests/test_task.py -p no:cacheprovider`.
2. Implement the task in `agenttx_solution/solution.py` and make the tests pass.
   Inspect only `agenttx_task_spec/`, `agenttx_solution/`, `recovery_tests/`,
   `recovery_notes/`, and `recovery_build/`; do not run a whole-repository
   `find`, `git status`, or broad documentation scan.
3. Preserve every valid artifact.  Recreate a missing artifact only when it is
   absent; do not rewrite an artifact that already satisfies its contract.
{docs}
4. Ensure `recovery_build/derived.txt` is absent before finishing.
5. Do not call any rollback tool: the comparison policy has already run.
6. Call `finish` with `commit=false` and a one-sentence summary when the tests pass.

Stay inside the workspace.  Do not use shell loops, Python generators, or bulk
file-copy commands to manufacture the documents.  This run charges all API
prompt, completion, tool-schema, diagnosis, validation, and repair tokens after
the recovery policy.
"""


SHORT = GitHubTask(
    name="short_requests_timeout",
    repo="psf/requests",
    commit="8068356288978c4f54661ae6f95afe0e0831885e",
    issue_url="https://github.com/psf/requests/issues/5227",
    title="Normalize connect/read timeout values",
    task_prompt=(
        "Implement `normalize_timeout(value)` in `agenttx_solution/solution.py`. "
        "Return None for None, a non-negative float for a scalar number, and a "
        "two-element tuple of non-negative floats for a pair.  Reject negative "
        "values, strings, and tuples of the wrong length with ValueError.  Preserve "
        "the tuple order because connect and read timeouts have different meanings."
    ),
    buggy_solution=(
        "def normalize_timeout(value):\n"
        "    if value is None:\n"
        "        return None\n"
        "    if isinstance(value, tuple):\n"
        "        return tuple(float(item) for item in value)\n"
        "    return float(value)\n"
    ),
    correct_solution=(
        "def normalize_timeout(value):\n"
        "    if value is None:\n"
        "        return None\n"
        "    if isinstance(value, tuple):\n"
        "        if len(value) != 2:\n"
        "            raise ValueError('timeout pair must have two values')\n"
        "        result = tuple(float(item) for item in value)\n"
        "    else:\n"
        "        result = (float(value),)\n"
        "    if any(item < 0 for item in result):\n"
        "        raise ValueError('timeout must be non-negative')\n"
        "    if len(result) == 1:\n"
        "        return result[0]\n"
        "    return result\n"
    ),
    test_body=(
        "import pytest\n"
        "from agenttx_solution.solution import normalize_timeout\n\n"
        "def test_scalar_and_none():\n"
        "    assert normalize_timeout(None) is None\n"
        "    assert normalize_timeout(1) == 1.0\n\n"
        "def test_pair_preserves_order():\n"
        "    assert normalize_timeout((1, 2)) == (1.0, 2.0)\n\n"
        "def test_invalid_values():\n"
        "    for value in (-1, (-1, 2), (1,), (1, 2, 3), '1'):\n"
        "        with pytest.raises(ValueError):\n"
        "            normalize_timeout(value)\n"
    ),
    derived_command=(
        "mkdir -p recovery_build && PYTHONDONTWRITEBYTECODE=1 "
        "PYTHONPATH=. python -c \"from agenttx_solution.solution import normalize_timeout; "
        "open('recovery_build/derived.txt','w').write(str(normalize_timeout((1, 2))))\""
    ),
    solution_symbol="normalize_timeout",
    doc_lines=16,
    doc_specs=(("recovery_notes/design.md", "design"),),
    max_turns=20,
)


MEDIUM = GitHubTask(
    name="medium_flask_config",
    repo="pallets/flask",
    commit="d318b683471101618febed18996405ad26462110",
    issue_url="https://github.com/pallets/flask/issues/3219",
    title="Merge nested application configuration safely",
    task_prompt=(
        "Implement `merge_config(base, override)` in `agenttx_solution/solution.py`. "
        "Return a new dictionary, recursively merge nested dictionaries, replace "
        "scalars and lists from the override, and never mutate either input.  The "
        "function must preserve insertion order from the base and append new override "
        "keys in override order."
    ),
    buggy_solution=(
        "def merge_config(base, override):\n"
        "    result = dict(base)\n"
        "    result.update(override)\n"
        "    return result\n"
    ),
    correct_solution=(
        "def merge_config(base, override):\n"
        "    result = dict(base)\n"
        "    for key, value in override.items():\n"
        "        previous = result.get(key)\n"
        "        if isinstance(previous, dict) and isinstance(value, dict):\n"
        "            result[key] = merge_config(previous, value)\n"
        "        else:\n"
        "            result[key] = value\n"
        "    return result\n"
    ),
    test_body=(
        "from agenttx_solution.solution import merge_config\n\n"
        "def test_nested_merge_and_order():\n"
        "    base = {'debug': False, 'server': {'host': 'localhost', 'port': 5000}, 'keep': 1}\n"
        "    override = {'server': {'port': 8080}, 'debug': True, 'new': ['x']}\n"
        "    result = merge_config(base, override)\n"
        "    assert result == {'debug': True, 'server': {'host': 'localhost', 'port': 8080}, 'keep': 1, 'new': ['x']}\n"
        "    assert list(result) == ['debug', 'server', 'keep', 'new']\n\n"
        "def test_inputs_are_not_mutated():\n"
        "    base = {'nested': {'value': 1}}\n"
        "    override = {'nested': {'other': 2}}\n"
        "    merge_config(base, override)\n"
        "    assert base == {'nested': {'value': 1}}\n"
        "    assert override == {'nested': {'other': 2}}\n"
    ),
    derived_command=(
        "mkdir -p recovery_build && PYTHONDONTWRITEBYTECODE=1 "
        "PYTHONPATH=. python -c \"from agenttx_solution.solution import merge_config; "
        "open('recovery_build/derived.txt','w').write(str(merge_config({'a': {'b': 1}}, {'a': {'c': 2}})))\""
    ),
    solution_symbol="merge_config",
    doc_lines=32,
    doc_specs=(
        ("recovery_notes/design.md", "design"),
        ("recovery_notes/changelog.md", "change"),
    ),
    max_turns=30,
)


LONG = GitHubTask(
    name="long_pytest_plugin_selection",
    repo="pytest-dev/pytest",
    commit="8f84744c418689e7b723d50aca54cd9d6c33af2e",
    issue_url="https://github.com/pytest-dev/pytest/issues/5822",
    title="Select requested plugins while preserving command-line order",
    task_prompt=(
        "Implement `select_plugins(available, requested, strict=False)` in "
        "`agenttx_solution/solution.py`.  Return requested plugin names in their first "
        "appearance order, omit duplicates, and ignore names absent from available. "
        "When strict is true, raise KeyError listing missing names in request order. "
        "Do not mutate either input and accept any iterable of names."
    ),
    buggy_solution=(
        "def select_plugins(available, requested, strict=False):\n"
        "    selected = sorted(set(available).intersection(requested))\n"
        "    if strict and len(selected) != len(set(requested)):\n"
        "        raise KeyError('missing plugin')\n"
        "    return selected\n"
    ),
    correct_solution=(
        "def select_plugins(available, requested, strict=False):\n"
        "    available_set = set(available)\n"
        "    selected = []\n"
        "    missing = []\n"
        "    seen = set()\n"
        "    for name in requested:\n"
        "        if name in seen:\n"
        "            continue\n"
        "        seen.add(name)\n"
        "        if name in available_set:\n"
        "            selected.append(name)\n"
        "        else:\n"
        "            missing.append(name)\n"
        "    if strict and missing:\n"
        "        raise KeyError(','.join(missing))\n"
        "    return selected\n"
    ),
    test_body=(
        "import pytest\n"
        "from agenttx_solution.solution import select_plugins\n\n"
        "def test_order_duplicates_and_iterables():\n"
        "    available = (name for name in ['capture', 'xdist', 'cov'])\n"
        "    assert select_plugins(available, ['cov', 'capture', 'cov', 'missing']) == ['cov', 'capture']\n\n"
        "def test_strict_reports_missing_in_request_order():\n"
        "    with pytest.raises(KeyError, match='first,second'):\n"
        "        select_plugins(['core'], ['first', 'second', 'first'], strict=True)\n\n"
        "def test_inputs_are_not_mutated():\n"
        "    available = ['core', 'capture']\n"
        "    requested = ['capture', 'core']\n"
        "    select_plugins(available, requested)\n"
        "    assert available == ['core', 'capture']\n"
        "    assert requested == ['capture', 'core']\n"
    ),
    derived_command=(
        "mkdir -p recovery_build && PYTHONDONTWRITEBYTECODE=1 "
        "PYTHONPATH=. python -c \"from agenttx_solution.solution import select_plugins; "
        "open('recovery_build/derived.txt','w').write(','.join(select_plugins(['core','cov'], ['cov','core'])))\""
    ),
    solution_symbol="select_plugins",
    doc_lines=64,
    doc_specs=(
        ("recovery_notes/design.md", "design"),
        ("recovery_notes/changelog.md", "change"),
        ("recovery_notes/validation.md", "validation"),
    ),
    max_turns=42,
)


TASKS: Dict[str, GitHubTask] = {
    task.name: task for task in (SHORT, MEDIUM, LONG)
}


def seed_task_workspace(workdir: Path, task: GitHubTask) -> None:
    """Add the task contract without putting it in the speculative ledger."""

    root = Path(workdir) / "agenttx_task_spec"
    root.mkdir(parents=True, exist_ok=True)
    (root / "TASK.md").write_text(task.task_file_content(), encoding="utf-8")


def inject_task_trajectory(agent, task: GitHubTask) -> dict:
    """Create valid test context, a faulty producer, independent work, and a fault."""

    docs = tuple(DocSpec(path, prefix, task.doc_lines) for path, prefix in task.doc_specs)
    return inject_recovery_dag(
        agent,
        docs=docs,
        task_name=task.name,
        prefix_writes=(("recovery_tests/test_task.py", task.test_body),),
        faulty_path="agenttx_solution/solution.py",
        faulty_content=task.buggy_solution,
        derived_cmd=task.derived_command,
        test_cmd=(
            "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. "
            "python -m pytest -q recovery_tests/test_task.py -p no:cacheprovider"
        ),
    )


def document_valid(path: Path, prefix: str, lines: int) -> bool:
    if not path.exists():
        return False
    content = path.read_text(encoding="utf-8").splitlines()
    expected = [f"{prefix.upper()}-{index:03d}:" for index in range(1, lines + 1)]
    entries = [line for line in content[1:] if line.startswith(prefix.upper() + "-")]
    return len(entries) == lines and all(
        line.startswith(label) for line, label in zip(entries, expected)
    )


def all_documents_valid(workdir: Path, task: GitHubTask) -> bool:
    docs = tuple(DocSpec(path, prefix, task.doc_lines) for path, prefix in task.doc_specs)
    return _all_documents_valid(workdir, all_midcrash_docs(docs))
