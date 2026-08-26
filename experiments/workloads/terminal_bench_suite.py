"""Terminal-Bench application workloads for AgentTX recovery.

Tasks are official ``original-tasks`` snapshots from Terminal-Bench 1.
The workspace root corresponds to the task's ``/app`` directory.  Official
``tests/test_outputs.py`` remains the utility predicate after rewriting the
sandbox path.  A recovery DAG is overlaid for causal vs coarse comparison.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover - the benchmark environment provides PyYAML
    yaml = None

from experiments.workloads.recovery_inject import (
    DocSpec,
    all_documents_valid,
    inject_recovery_dag,
    recovery_prompt,
)

TB_REPO = "https://github.com/harbor-framework/terminal-bench-1.git"


@dataclass(frozen=True)
class TBTask:
    task_id: str
    scale: str
    doc_lines: int
    doc_specs: Tuple[Tuple[str, str], ...]
    max_turns: int
    instruction: str
    copy_from: Tuple[str, ...]
    faulty_relpath: str
    faulty_content: str
    oracle_cmd: str
    official_full: bool = False
    difficulty: str = ""

    @property
    def name(self) -> str:
        return self.task_id

    @property
    def suite(self) -> str:
        return "tb"

    def docs(self) -> Tuple[DocSpec, ...]:
        return tuple(
            DocSpec(path, prefix, self.doc_lines) for path, prefix in self.doc_specs
        )


SHORT = TBTask(
    task_id="hello-world",
    scale="short",
    doc_lines=16,
    doc_specs=(("recovery_notes/design.md", "design"),),
    max_turns=16,
    instruction="Create a file called /app/hello.txt. Write \"Hello, world!\" to it.",
    copy_from=(),
    faulty_relpath="hello.txt",
    faulty_content="hello world\n",
    oracle_cmd='printf "%s\\n" "Hello, world!" > hello.txt',
)

MEDIUM = TBTask(
    task_id="csv-to-parquet",
    scale="medium",
    doc_lines=32,
    doc_specs=(
        ("recovery_notes/design.md", "design"),
        ("recovery_notes/changelog.md", "change"),
    ),
    max_turns=24,
    instruction=(
        "Convert the file '/app/data.csv' into a Parquet file named "
        "'/app/data.parquet'. The CSV file contains sample data with headers."
    ),
    copy_from=("task-deps/data.csv",),
    faulty_relpath="data.parquet",
    faulty_content="not a parquet file\n",
    oracle_cmd=(
        "python - <<'PY'\n"
        "import pandas as pd\n"
        "pd.read_csv('data.csv').to_parquet('data.parquet')\n"
        "PY"
    ),
)

LONG = TBTask(
    task_id="log-summary",
    scale="long",
    doc_lines=64,
    doc_specs=(
        ("recovery_notes/design.md", "design"),
        ("recovery_notes/changelog.md", "change"),
        ("recovery_notes/validation.md", "validation"),
    ),
    max_turns=28,
    instruction=(
        "The logs directory contains multiple .log files. Count how many lines "
        "contain ERROR, WARNING, and INFO. Output /app/summary.csv with header "
        "severity,count and one row each for ERROR, WARNING, and INFO."
    ),
    copy_from=("logs",),
    faulty_relpath="summary.csv",
    faulty_content="severity,count\nERROR,0\nWARNING,0\nINFO,0\n",
    oracle_cmd=(
        "python - <<'PY'\n"
        "from pathlib import Path\n"
        "counts={'ERROR':0,'WARNING':0,'INFO':0}\n"
        "for path in Path('logs').glob('*.log'):\n"
        "    for line in path.read_text().splitlines():\n"
        "        for key in counts:\n"
        "            if key in line:\n"
        "                counts[key]+=1\n"
        "                break\n"
        "Path('summary.csv').write_text(\n"
        "    'severity,count\\n' + ''.join(f'{k},{counts[k]}\\n' for k in ['ERROR','WARNING','INFO'])\n"
        ")\n"
        "PY"
    ),
)

TASKS: Dict[str, TBTask] = {task.task_id: task for task in (SHORT, MEDIUM, LONG)}


def _task_scale(difficulty: str, instruction: str) -> Tuple[str, int, int]:
    difficulty = (difficulty or "").lower()
    if difficulty in {"easy", "simple"} and len(instruction) < 1200:
        return "short", 16, 24
    if difficulty in {"hard", "expert"} or len(instruction) > 3000:
        return "long", 64, 48
    return "medium", 32, 36


def _official_fault_path(source: Path, task_id: str) -> str:
    """Find an output path mentioned by the official tests/solution."""
    texts: List[str] = []
    for path in [source / "solution.sh", source / "run-tests.sh"]:
        if path.exists():
            texts.append(path.read_text(encoding="utf-8", errors="replace"))
    tests = source / "tests"
    if tests.is_dir():
        texts.extend(path.read_text(encoding="utf-8", errors="replace") for path in tests.rglob("*.py"))
    candidates: List[str] = []
    for text in texts:
        candidates.extend(re.findall(r"/app/([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*)", text))
        candidates.extend(re.findall(r"(?:>|-o\s+|output\s*=\s*)([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*)", text))
    unique: List[str] = []
    for candidate in candidates:
        candidate = candidate.lstrip("./")
        if candidate and candidate not in unique:
            unique.append(candidate)
    # Prefer obvious generated artifacts and paths that are asserted by tests.
    scored = sorted(
        unique,
        key=lambda value: (
            0 if any(token in value.lower() for token in ("output", "result", "summary", "converted", "parquet", "json")) else 1,
            0 if value.lower().endswith((".txt", ".csv", ".json", ".parquet", ".gz", ".out")) else 1,
            len(value),
        ),
    )
    if scored:
        return scored[0]
    return f".agenttx_faults/{task_id}.txt"


def load_tasks(cache_root: Path) -> Dict[str, TBTask]:
    """Load every task in the checked-out Terminal-Bench original-tasks tree."""
    repo = ensure_tb_repo(cache_root)
    result: Dict[str, TBTask] = {}
    root = repo / "original-tasks"
    for source in sorted(path for path in root.iterdir() if path.is_dir()):
        config_path = source / "task.yaml"
        if not config_path.exists():
            continue
        if yaml is not None:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        else:
            config = {}
        instruction = str(config.get("instruction") or source.name)
        scale, doc_lines, max_turns = _task_scale(str(config.get("difficulty") or ""), instruction)
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
        result[source.name] = TBTask(
            task_id=source.name,
            scale=scale,
            doc_lines=doc_lines,
            doc_specs=docs,
            max_turns=max_turns,
            instruction=instruction,
            copy_from=(),
            faulty_relpath=_official_fault_path(source, source.name),
            faulty_content=f"AGENTTX injected recovery producer for {source.name}\n",
            oracle_cmd="bash solution.sh",
            official_full=True,
            difficulty=str(config.get("difficulty") or ""),
        )
    return result


def ensure_tb_repo(cache_root: Path) -> Path:
    dest = Path(cache_root) / "terminal_bench" / "terminal-bench-1"
    if (dest / ".git").exists() or (dest / "original-tasks").exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth", "1", TB_REPO, str(dest)],
        check=True,
    )
    return dest


def task_source(cache_root: Path, task: TBTask) -> Path:
    repo = ensure_tb_repo(cache_root)
    path = repo / "original-tasks" / task.task_id
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def _rewrite_app_paths(text: str, app_root: Path) -> str:
    root = app_root.as_posix()
    return text.replace("/app", root)


def materialize(task: TBTask, workdir: Path, cache_root: Path) -> Path:
    src = task_source(cache_root, task)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    if task.official_full:
        excluded = {"Dockerfile", "docker-compose.yaml", "run-tests.sh", "solution.sh", "task.yaml", "tests"}
        for source in src.iterdir():
            if source.name in excluded:
                continue
            dest = workdir / source.name
            if source.is_dir():
                shutil.copytree(source, dest, dirs_exist_ok=True)
            elif source.is_file():
                shutil.copy2(source, dest)
    else:
        for rel in task.copy_from:
            source = src / rel
            dest_name = Path(rel).name
            dest = workdir / dest_name
            if source.is_dir():
                shutil.copytree(source, dest, dirs_exist_ok=True)
            elif source.exists():
                shutil.copy2(source, dest)
            else:
                raise FileNotFoundError(source)
    verify_dir = workdir / "tbench_verify"
    verify_dir.mkdir(parents=True, exist_ok=True)
    tests_src = src / "tests"
    for test_path in tests_src.rglob("*.py"):
        rel = test_path.relative_to(tests_src)
        dest = verify_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        rewritten = _rewrite_app_paths(test_path.read_text(encoding="utf-8"), workdir)
        dest.write_text(rewritten, encoding="utf-8")
    return src


def test_command(python: str, task: Optional[TBTask] = None) -> str:
    target = "tbench_verify" if task is not None and task.official_full else "tbench_verify/test_outputs.py"
    return (
        f"PYTHONDONTWRITEBYTECODE=1 {python} -m pytest -q "
        f"{target} -p no:cacheprovider"
    )


def seed_task_workspace(workdir: Path, task: TBTask) -> None:
    root = Path(workdir) / "agenttx_task_spec"
    root.mkdir(parents=True, exist_ok=True)
    (root / "TASK.md").write_text(
        f"# {task.task_id}\n\n"
        "Suite: Terminal-Bench\n"
        "Workspace root corresponds to the official /app directory.\n\n"
        f"{task.instruction.strip()}\n",
        encoding="utf-8",
    )


def inject_task_trajectory(agent, task: TBTask, python: str) -> dict:
    derived = (
        "mkdir -p recovery_build && "
        f"cp '{task.faulty_relpath}' recovery_build/derived.txt"
    )
    return inject_recovery_dag(
        agent,
        docs=task.docs(),
        task_name=task.task_id,
        prefix_writes=(),
        faulty_path=task.faulty_relpath,
        faulty_content=task.faulty_content,
        derived_cmd=derived,
        test_cmd=test_command(python, task),
    )


def task_prompt(task: TBTask, python: str) -> str:
    return recovery_prompt(
        title=task.task_id,
        context="Suite: Terminal-Bench\nWorkspace root corresponds to /app.",
        instruction=task.instruction,
        docs=task.docs(),
        test_cmd=test_command(python, task),
    )


def apply_oracle(agent, task: TBTask, python: str = "python") -> None:
    cmd = task.oracle_cmd
    if cmd.startswith("python "):
        cmd = python + cmd[len("python") :]
    step = agent.harness.call_tool("run_shell", {"cmd": cmd})
    code = getattr(step, "exit_code", 0)
    if int(code) != 0:
        raise RuntimeError(f"oracle command failed rc={code} for {task.task_id}")


def verify(workdir: Path, task: TBTask, python: str) -> dict:
    cmd = test_command(python, task)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        cmd,
        cwd=str(workdir),
        env=env,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=900 if task.official_full else 180,
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
