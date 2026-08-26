"""SWE-Bench Lite application workloads for AgentTX recovery.

Each task is an official Lite instance: clone at ``base_commit``, apply the
official ``test_patch``, and score with ``FAIL_TO_PASS``.  A recovery DAG is
overlaid so causal vs coarse policies can be compared without replacing the
official utility predicate.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from experiments.workloads.recovery_inject import (
    DocSpec,
    all_documents_valid,
    dag_is_valid,
    inject_recovery_dag,
    recovery_prompt,
)

HF_ROWS = (
    "https://datasets-server.huggingface.co/rows"
    "?dataset=princeton-nlp/SWE-bench_Lite&config=default&split=test"
    "&offset={offset}&length=100"
)


@dataclass(frozen=True)
class SWETask:
    instance_id: str
    scale: str
    doc_lines: int
    doc_specs: Tuple[Tuple[str, str], ...]
    pythonpath: str
    max_turns: int
    faulty_relpath: str

    @property
    def name(self) -> str:
        return self.instance_id

    @property
    def suite(self) -> str:
        return "swe"

    def docs(self) -> Tuple[DocSpec, ...]:
        return tuple(
            DocSpec(path, prefix, self.doc_lines) for path, prefix in self.doc_specs
        )


SHORT = SWETask(
    instance_id="pytest-dev__pytest-8906",
    scale="short",
    doc_lines=16,
    doc_specs=(("recovery_notes/design.md", "design"),),
    pythonpath="src",
    max_turns=24,
    faulty_relpath="src/_pytest/python.py",
)

MEDIUM = SWETask(
    instance_id="pallets__flask-4992",
    scale="medium",
    doc_lines=32,
    doc_specs=(
        ("recovery_notes/design.md", "design"),
        ("recovery_notes/changelog.md", "change"),
    ),
    pythonpath="src",
    max_turns=32,
    faulty_relpath="src/flask/config.py",
)

LONG = SWETask(
    instance_id="pylint-dev__pylint-5859",
    scale="long",
    doc_lines=64,
    doc_specs=(
        ("recovery_notes/design.md", "design"),
        ("recovery_notes/changelog.md", "change"),
        ("recovery_notes/validation.md", "validation"),
    ),
    pythonpath=".",
    max_turns=42,
    faulty_relpath="pylint/checkers/misc.py",
)

TASKS: Dict[str, SWETask] = {task.instance_id: task for task in (SHORT, MEDIUM, LONG)}


def _full_scale(statement: str, fail_to_pass_count: int) -> Tuple[str, int, int]:
    """Assign a stable workload bucket and recovery budget to a Lite row."""
    size = len(statement or "") + 300 * fail_to_pass_count
    if size < 1800:
        return "short", 16, 24
    if size < 5000:
        return "medium", 32, 36
    return "long", 64, 48


def _fault_path_from_patch(patch: str, instance_id: str) -> str:
    """Select a source file touched by the official patch for DAG injection."""
    candidates: List[str] = []
    for line in (patch or "").splitlines():
        if line.startswith("+++ b/"):
            path = line[6:].strip()
            if path and path != "/dev/null":
                candidates.append(path)
    if not candidates:
        return f".agenttx_faults/{instance_id.replace('/', '_')}.py"
    non_tests = [path for path in candidates if not path.startswith(("test/", "tests/"))]
    return (non_tests or candidates)[0]


def fetch_all_instances(cache_root: Path) -> List[dict]:
    """Fetch and cache the complete SWE-Bench Lite test split manifest."""
    manifest = Path(cache_root) / "swe_bench" / "lite_test_manifest.json"
    if manifest.exists():
        return json.loads(manifest.read_text(encoding="utf-8"))
    rows: Dict[str, dict] = {}
    for offset in range(0, 1000, 100):
        data = json.loads(_urlopen(HF_ROWS.format(offset=offset)).read())
        batch = data.get("rows") or []
        for item in batch:
            row = item.get("row") or {}
            if row.get("instance_id"):
                rows[row["instance_id"]] = row
        if len(batch) < 100:
            break
    result = [rows[key] for key in sorted(rows)]
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def load_tasks(cache_root: Path) -> Dict[str, SWETask]:
    """Build the full official Lite catalog without cloning repositories."""
    tasks: Dict[str, SWETask] = {}
    for row in fetch_all_instances(cache_root):
        instance_id = str(row["instance_id"])
        statement = str(row.get("problem_statement") or "")
        raw_fail = row.get("FAIL_TO_PASS", [])
        if isinstance(raw_fail, str):
            try:
                fail_count = len(json.loads(raw_fail))
            except json.JSONDecodeError:
                fail_count = 1
        else:
            fail_count = len(raw_fail or [])
        scale, doc_lines, max_turns = _full_scale(statement, fail_count)
        if scale == "short":
            docs = (("recovery_notes/design.md", "design"),)
        elif scale == "medium":
            docs = (("recovery_notes/design.md", "design"), ("recovery_notes/changelog.md", "change"))
        else:
            docs = (
                ("recovery_notes/design.md", "design"),
                ("recovery_notes/changelog.md", "change"),
                ("recovery_notes/validation.md", "validation"),
            )
        tasks[instance_id] = SWETask(
            instance_id=instance_id,
            scale=scale,
            doc_lines=doc_lines,
            doc_specs=docs,
            pythonpath="auto",
            max_turns=max_turns,
            faulty_relpath=_fault_path_from_patch(str(row.get("patch") or ""), instance_id),
        )
    return tasks

ROOT_CACHE_HINT = Path("/home/pengpeng/agenttx/experiments/cache")

PYTHON_DEPS = {
    "pytest-dev__pytest-8906": [
        "iniconfig",
        "packaging",
        "pluggy",
        "py",
        "exceptiongroup",
        "tomli",
        "attrs",
    ],
    "pallets__flask-4992": [
        "werkzeug==2.3.8",
        "click==8.1.7",
        "jinja2==3.1.4",
        "itsdangerous==2.1.2",
        "blinker==1.7.0",
        "markupsafe==2.1.5",
        "pytest==7.4.4",
    ],
    "pylint-dev__pylint-5859": [
        "astroid==2.11.7",
        "wrapt==1.14.1",
        "lazy-object-proxy==1.9.0",
        "isort==5.10.1",
        "mccabe==0.7.0",
        "toml==0.10.2",
        "dill==0.3.6",
        "platformdirs==2.5.4",
        "typing-extensions==4.4.0",
        "pytest",
    ],
}


def ensure_python_deps(task: SWETask, python: str, cache_root: Optional[Path] = None) -> str:
    return ensure_venv(task, python, cache_root)


def ensure_venv(task: SWETask, python: str, cache_root: Optional[Path] = None) -> str:
    cache_root = Path(cache_root or ROOT_CACHE_HINT)
    # Full Lite catalogs contain hundreds of instances, but the dynamic
    # loader deliberately uses the same minimal pytest contract for each one.
    # Reusing one catalog-level environment avoids 300 duplicate venv/pip
    # builds while preserving the specialized environments for the three
    # representative tasks with pinned package sets.
    env_key = task.instance_id if task.instance_id in PYTHON_DEPS else "_catalog_pytest"
    venv = Path(cache_root) / "swe_bench" / "venvs" / env_key
    py = venv / "bin" / "python"
    if py.exists():
        return str(py)
    venv.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([python, "-m", "venv", str(venv)], check=True)
    pip = str(venv / "bin" / "pip")
    pkgs = PYTHON_DEPS.get(task.instance_id) or ["pytest"]
    subprocess.run([pip, "install", "-q", "--upgrade", "pip"], check=False)
    subprocess.run([pip, "install", "-q", *pkgs], check=True)
    return str(py)


def _urlopen(url: str, timeout: int = 60):
    req = urllib.request.Request(url, headers={"User-Agent": "agenttx"})
    last = None
    for attempt in range(5):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            last = exc
            import time as _time

            _time.sleep(1.5 * (attempt + 1))
    raise last


def fetch_instance(instance_id: str, cache_root: Path) -> dict:
    path = Path(cache_root) / "swe_bench" / f"{instance_id}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    found = None
    for offset in range(0, 400, 100):
        data = json.loads(_urlopen(HF_ROWS.format(offset=offset)).read())
        for item in data.get("rows") or []:
            row = item["row"]
            if row["instance_id"] == instance_id:
                found = row
                break
        if found is not None:
            break
    if found is None:
        raise KeyError(f"SWE-Bench Lite instance not found: {instance_id}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(found, indent=2) + "\n", encoding="utf-8")
    return found


def _git(args: Sequence[str], cwd: Optional[Path] = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout.strip()


def ensure_repo(task: SWETask, cache_root: Path) -> Tuple[Path, dict]:
    instance = fetch_instance(task.instance_id, cache_root)
    cache_root = Path(cache_root)
    repo_dir = cache_root / "swe_bench" / "repos" / task.instance_id
    url = f"https://github.com/{instance['repo']}.git"
    commit = instance["base_commit"]
    if not (repo_dir / ".git").exists():
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", url, str(repo_dir)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
    head = _git(["rev-parse", "HEAD"], cwd=repo_dir)
    if head != commit:
        subprocess.run(
            ["git", "fetch", "--depth", "1", "origin", commit],
            cwd=str(repo_dir),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Fall back to a full fetch if the shallow object is missing.
        try:
            _git(["checkout", "--detach", commit], cwd=repo_dir)
        except subprocess.CalledProcessError:
            subprocess.run(
                ["git", "fetch", "origin", commit],
                cwd=str(repo_dir),
                check=True,
                stdout=subprocess.DEVNULL,
            )
            _git(["checkout", "--detach", commit], cwd=repo_dir)
    marker = repo_dir / ".agenttx_test_patch"
    if not marker.exists():
        patch = instance["test_patch"]
        subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=str(repo_dir),
            input=patch,
            text=True,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        marker.write_text(task.instance_id + "\n", encoding="utf-8")
    return repo_dir, instance


def copy_repo(cache: Path, workdir: Path) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    for item in cache.iterdir():
        if item.name in {".git", ".agenttx_test_patch"}:
            continue
        dest = workdir / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)
    ensure_generated_files(workdir)


def ensure_generated_files(workdir: Path) -> None:
    version = Path(workdir) / "src/_pytest/_version.py"
    if (Path(workdir) / "src/_pytest").is_dir() and not version.exists():
        version.write_text(
            'version = "7.0.0"\nversion_tuple = (7, 0, 0)\n',
            encoding="utf-8",
        )


def fail_to_pass(instance: dict) -> List[str]:
    raw = instance["FAIL_TO_PASS"]
    if isinstance(raw, str):
        return list(json.loads(raw))
    return list(raw)


def test_command(task: SWETask, instance: dict, python: str = sys.executable) -> str:
    nodes = " ".join(fail_to_pass(instance))
    pythonpath = task.pythonpath
    if pythonpath == "auto":
        # Including both roots is harmless when one is absent and covers the
        # two layouts used by the Lite repositories without a per-repo table.
        pythonpath = "src:."
    return (
        f"PYTHONDONTWRITEBYTECODE=1 PYTHONPATH={pythonpath} "
        f"{python} -m pytest -q {nodes} -p no:cacheprovider"
    )


def seed_task_workspace(workdir: Path, task: SWETask, instance: dict) -> None:
    root = Path(workdir) / "agenttx_task_spec"
    root.mkdir(parents=True, exist_ok=True)
    statement = instance.get("problem_statement") or ""
    (root / "TASK.md").write_text(
        f"# {task.instance_id}\n\n"
        f"Repository: {instance['repo']} at {instance['base_commit']}\n"
        f"SWE-Bench Lite FAIL_TO_PASS: {', '.join(fail_to_pass(instance))}\n\n"
        f"{statement.strip()}\n",
        encoding="utf-8",
    )


def _faulty_content(workdir: Path, relpath: str) -> str:
    path = Path(workdir) / relpath
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    return (
        "# AGENTTX_INJECTED_FAULT: this write is the recovery-unit producer.\n"
        "def agenttx_injected_fault():\n"
        "    raise RuntimeError('agenttx injected fault')\n\n"
        + original
    )


def inject_task_trajectory(agent, task: SWETask, instance: dict, python: str) -> dict:
    docs = task.docs()
    derived = (
        "mkdir -p recovery_build && "
        f"cp '{task.faulty_relpath}' recovery_build/derived.txt"
    )
    official = test_command(task, instance, python)
    return inject_recovery_dag(
        agent,
        docs=docs,
        task_name=task.instance_id,
        prefix_writes=(),
        faulty_path=task.faulty_relpath,
        faulty_content=_faulty_content(agent.harness.workdir, task.faulty_relpath),
        derived_cmd=derived,
        test_cmd=f"cat '{task.faulty_relpath}' >/dev/null && {official}",
    )


def task_prompt(task: SWETask, instance: dict, python: str) -> str:
    return recovery_prompt(
        title=task.instance_id,
        context=(
            f"Suite: SWE-Bench Lite\n"
            f"Repository: {instance['repo']} at {instance['base_commit']}"
        ),
        instruction=instance.get("problem_statement") or task.instance_id,
        docs=task.docs(),
        test_cmd=test_command(task, instance, python),
        extra_rules=(
            "Do not apply a hidden gold patch file; implement the issue in the repository sources.",
        ),
    )


def apply_oracle(agent, instance: dict) -> None:
    agent.harness.call_tool(
        "write_file",
        {"path": ".agenttx_oracle.diff", "content": instance["patch"]},
    )
    step = agent.harness.call_tool(
        "run_shell",
        {"cmd": "patch -p1 --forward --batch < .agenttx_oracle.diff"},
    )
    code = getattr(step, "exit_code", 0)
    if int(code) != 0:
        raise RuntimeError(f"gold patch failed rc={code}")
    agent.harness.call_tool("delete_file", {"path": ".agenttx_oracle.diff"})


def verify(workdir: Path, task: SWETask, instance: dict, python: str) -> dict:
    cmd = test_command(task, instance, python)
    pythonpath = task.pythonpath
    if pythonpath == "auto":
        pythonpath = "src:."
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": pythonpath}
    result = subprocess.run(
        cmd,
        cwd=str(workdir),
        env=env,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=300,
        check=False,
    )
    docs_ok = all_documents_valid(workdir, task.docs())
    derived_removed = not (Path(workdir) / "recovery_build" / "derived.txt").exists()
    return {
        "tests_rc": result.returncode,
        "tests_ok": result.returncode == 0,
        "documents_valid": docs_ok,
        "derived_removed": derived_removed,
        "verifier_stdout": (result.stdout or "")[-2000:],
        "verifier_stderr": (result.stderr or "")[-2000:],
    }
