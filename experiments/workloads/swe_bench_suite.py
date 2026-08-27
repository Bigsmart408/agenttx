"""SWE-Bench Lite application workloads for AgentTX recovery.

Each task is an official Lite instance: clone at ``base_commit``, apply the
official ``test_patch``, and score with ``FAIL_TO_PASS``.  A recovery DAG is
overlaid so causal vs coarse policies can be compared without replacing the
official utility predicate.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

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

REPO_PIP = {
    "astropy/astropy": [
        "numpy",
        "hypothesis",
        "pytest",
        "pytest-astropy",
        "packaging",
        "pyerfa",
        "Jinja2",
    ],
    "django/django": ["asgiref", "sqlparse", "tblib", "pytz", "typing_extensions"],
    "matplotlib/matplotlib": ["numpy", "pytest", "cycler", "pyparsing", "python-dateutil"],
    "mwaskom/seaborn": ["numpy", "pandas", "matplotlib", "pytest"],
    "pallets/flask": [
        "pytest", "werkzeug", "click", "jinja2", "itsdangerous", "blinker", "markupsafe",
    ],
    "psf/requests": ["pytest"],
    "pydata/xarray": ["numpy", "pandas", "pytest", "packaging"],
    "pylint-dev/pylint": ["pytest", "astroid", "isort", "mccabe", "toml", "dill", "platformdirs"],
    "pytest-dev/pytest": ["iniconfig", "packaging", "pluggy", "exceptiongroup", "tomli", "attrs"],
    "scikit-learn/scikit-learn": ["numpy", "scipy", "pytest", "joblib", "threadpoolctl"],
    "sphinx-doc/sphinx": ["pytest", "docutils", "Jinja2", "Pygments"],
    "sympy/sympy": ["mpmath", "pytest"],
}


DOCKER_TESTBED_PYTHON = "/opt/miniconda3/envs/testbed/bin/python"


def swe_eval_image(instance_id: str) -> str:
    """Official SWE-bench eval image. Docker names cannot contain '__'."""
    docker_id = str(instance_id).replace("__", "_1776_").lower()
    return f"swebench/sweb.eval.x86_64.{docker_id}:latest"


def swe_eval_group(instance_id: str) -> str:
    """Repo-level key so shared env layers stay until that repo is finished."""
    name = str(instance_id)
    if "__" not in name:
        return name
    owner, rest = name.split("__", 1)
    if "-" in rest:
        repo, maybe_num = rest.rsplit("-", 1)
        if maybe_num.isdigit():
            return f"{owner}__{repo}"
    return name


def docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def ensure_eval_image(instance_id: str, timeout: int = 1800) -> str:
    """Pull the instance eval image if it is not already local."""
    image = swe_eval_image(instance_id)
    inspect = subprocess.run(
        ["docker", "image", "inspect", image],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if inspect.returncode == 0:
        return image
    print(f"pulling {image}", flush=True)
    subprocess.run(["docker", "pull", image], check=True, timeout=timeout)
    return image


def remove_eval_image(instance_id: str) -> None:
    """Drop the instance image; parent env layers stay until prune."""
    if not docker_available():
        return
    image = swe_eval_image(instance_id)
    subprocess.run(
        ["docker", "rmi", "-f", image],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=120,
    )


def prune_unused_eval_layers() -> None:
    """Delete dangling layers after a repo group no longer needs them."""
    if not docker_available():
        return
    subprocess.run(
        ["docker", "image", "prune", "-f"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=300,
    )


def _docker_container_name(instance_id: str) -> str:
    token = str(instance_id).replace("__", "-").replace("/", "-").lower()
    return f"agenttx-eval-{token}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def ensure_python_deps(
    task: SWETask,
    python: str,
    cache_root: Optional[Path] = None,
    instance: Optional[dict] = None,
) -> str:
    return ensure_venv(task, python, cache_root, instance)


def _venv_key(task: SWETask, instance: Optional[dict]) -> str:
    if task.instance_id in PYTHON_DEPS:
        return task.instance_id
    repo = str((instance or {}).get("repo") or "")
    version = str((instance or {}).get("version") or "")
    if repo:
        return f"{repo.replace('/', '__')}__{version or 'default'}"
    return "_catalog_pytest"


def ensure_venv(
    task: SWETask,
    python: str,
    cache_root: Optional[Path] = None,
    instance: Optional[dict] = None,
) -> str:
    """Per-repo/version interpreter so official tests can import the project."""
    cache_root = Path(cache_root or ROOT_CACHE_HINT)
    instance = instance or {}
    env_key = _venv_key(task, instance)
    venv = Path(cache_root) / "swe_bench" / "venvs" / env_key
    py = venv / "bin" / "python"
    marker = venv / ".agenttx_pkgs"
    repo = str(instance.get("repo") or "")
    pkgs = PYTHON_DEPS.get(task.instance_id) or list(REPO_PIP.get(repo) or ["pytest"])
    wanted = "\n".join(pkgs) + "\n"
    if py.exists() and marker.exists() and marker.read_text(encoding="utf-8") == wanted:
        return str(py)
    venv.parent.mkdir(parents=True, exist_ok=True)
    if not py.exists():
        subprocess.run([python, "-m", "venv", str(venv)], check=True)
    pip = str(venv / "bin" / "pip")
    subprocess.run([pip, "install", "-q", "--upgrade", "pip"], check=False)
    subprocess.run([pip, "install", "-q", *pkgs], check=True)
    marker.write_text(wanted, encoding="utf-8")
    return str(py)


def ensure_workspace_venv(workdir: Path, python: str) -> str:
    """Interpreter inside the protected workspace so live-agent pip cannot leak.

    Official Docker verify still uses ``DOCKER_TESTBED_PYTHON`` in the SWE-Bench
    image.  This venv is only for the agent's own test and pip commands, which
    the commit policy requires to stay under ``workdir``.
    """
    workdir = Path(workdir)
    venv = workdir / ".venv"
    py = venv / "bin" / "python"
    if py.exists():
        return str(py)
    subprocess.run(
        [python, "-m", "venv", "--system-site-packages", str(venv)],
        check=True,
    )
    return str(py)


def _strip_live_agent_scratch(workdir: Path) -> None:
    """Drop agent-only dirs so they are not copied into the official eval image."""
    for name in (".venv", ".codex", ".cache", ".tmp"):
        path = Path(workdir) / name
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)


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
    astropy_version = Path(workdir) / "astropy" / "_version.py"
    if (Path(workdir) / "astropy").is_dir() and not astropy_version.exists():
        astropy_version.write_text('version = "4.3.0"\n', encoding="utf-8")


def fail_to_pass(instance: dict) -> List[str]:
    raw = instance["FAIL_TO_PASS"]
    if isinstance(raw, str):
        return list(json.loads(raw))
    return list(raw)


def django_test_labels(names: Sequence[str], instance: Optional[dict] = None) -> List[str]:
    """Convert SWE-Bench Django FAIL_TO_PASS ids into runtests.py labels.

    Official Lite ids come in three shapes:
    - ``test_foo (module.Class)``
    - ``test_foo (module.Class.test_foo)``  (already fully qualified)
    - a docstring, which runtests.py treats as a module name and must be skipped
    """
    labels: List[str] = []
    for raw in names:
        name = str(raw).strip()
        if " (" in name and name.endswith(")"):
            method, rest = name.rsplit(" (", 1)
            path = rest[:-1]
            if path.endswith("." + method):
                labels.append(path)
            else:
                labels.append(f"{path}.{method}")
        elif name.startswith("test_") and " " not in name:
            labels.append(name)
    if not labels and instance:
        labels = _django_modules_from_test_patch(str(instance.get("test_patch") or ""))
    return labels


def _django_modules_from_test_patch(patch: str) -> List[str]:
    modules: List[str] = []
    for line in patch.splitlines():
        if line.startswith("+++ b/tests/"):
            rel = line[len("+++ b/tests/") :].strip()
            if rel and rel != "/dev/null":
                modules.append(rel.split("/", 1)[0].replace(".py", ""))
    return list(dict.fromkeys(modules))


def _sympy_test_targets(instance: dict, names: Sequence[str]) -> List[str]:
    files: List[str] = []
    for line in str(instance.get("test_patch") or "").splitlines():
        if line.startswith("+++ b/") and "/tests/" in line:
            path = line[6:].strip()
            if path and path != "/dev/null":
                files.append(path)
    if files:
        return list(dict.fromkeys(files))
    return [str(name) for name in names if str(name).strip()]


def _quoted(parts: Sequence[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def test_command(task: SWETask, instance: dict, python: str = sys.executable) -> str:
    """Repo-aware official verifier.  Node ids are always shell-quoted."""
    repo = str(instance.get("repo") or "")
    names = fail_to_pass(instance)
    py = shlex.quote(python)
    if repo == "django/django":
        return (
            f"PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. {py} tests/runtests.py "
            f"--verbosity 1 --settings=test_sqlite --parallel 1 "
            f"{_quoted(django_test_labels(names, instance))}"
        )
    if repo == "sympy/sympy":
        return (
            f"PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. {py} bin/test -C --verbose "
            f"{_quoted(_sympy_test_targets(instance, names))}"
        )
    pythonpath = task.pythonpath
    if pythonpath == "auto":
        pythonpath = "src:." if repo in {"pytest-dev/pytest", "pallets/flask"} else "."
    return (
        f"PYTHONDONTWRITEBYTECODE=1 PYTHONPATH={pythonpath} "
        f"{py} -m pytest -q --tb=line -p no:cacheprovider {_quoted(names)}"
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
    # Open the producer with the traced Python so the test step parents the
    # faulty write even when the official runner never imports that file.
    read_producer = (
        f"{shlex.quote(python)} -c "
        + shlex.quote(
            "from pathlib import Path; Path(%r).read_text(encoding='utf-8')"
            % task.faulty_relpath
        )
    )
    return inject_recovery_dag(
        agent,
        docs=docs,
        task_name=task.instance_id,
        prefix_writes=(),
        faulty_path=task.faulty_relpath,
        faulty_content=_faulty_content(agent.harness.workdir, task.faulty_relpath),
        derived_cmd=derived,
        test_cmd=f"{read_producer} && {official}",
    )


def task_prompt(
    task: SWETask,
    instance: dict,
    python: str,
    mode: str = "causal",
    recovery_manifest: Optional[Mapping[str, object]] = None,
) -> str:
    if recovery_manifest is not None:
        note_rule = (
            "Do not write logs or build artifacts under /tmp; keep them inside the workspace. "
            "The machine-generated AgentTX recovery state is authoritative for recovery artifacts."
        )
    elif mode == "causal":
        note_rule = (
            "Do not write logs or build artifacts under /tmp; keep them inside the workspace. "
            "Independent recovery notes were retained; do not open or rewrite them."
        )
    else:
        note_rule = (
            "Do not write logs or build artifacts under /tmp; keep them inside the workspace. "
            "If a recovery note is missing, create it with lines after the title starting exactly "
            "DESIGN-001: / CHANGE-001: and no '1. ' numbering prefix. If it already exists, do not open or rewrite it."
        )
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
            "Use only the interpreter in the verifier command for tests and pip installs. Do not pip-install into a host conda or system Python.",
            note_rule,
        ),
        mode=mode,
        recovery_manifest=recovery_manifest,
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


def _verify_tests_host(workdir: Path, task: SWETask, instance: dict, python: str) -> dict:
    cmd = test_command(task, instance, python)
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
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
    return {
        "tests_rc": result.returncode,
        "tests_ok": result.returncode == 0,
        "verifier_stdout": (result.stdout or "")[-2000:],
        "verifier_stderr": (result.stderr or "")[-2000:],
        "verifier_backend": "host",
    }


def _verify_tests_docker(workdir: Path, task: SWETask, instance: dict) -> dict:
    """Run FAIL_TO_PASS in the official eval image, overlaying the AgentTX workdir.

    Compiled extensions stay in the image; ``docker cp`` only overwrites files
    present in the host tree, so ``.so`` artifacts are kept.
    """
    image = ensure_eval_image(task.instance_id)
    name = _docker_container_name(task.instance_id)
    cmd = test_command(task, instance, DOCKER_TESTBED_PYTHON)
    _strip_live_agent_scratch(workdir)
    try:
        subprocess.run(
            ["docker", "rm", "-f", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        created = subprocess.run(
            [
                "docker",
                "create",
                "--name",
                name,
                "--workdir",
                "/testbed",
                "--entrypoint",
                "bash",
                image,
                "-lc",
                "sleep infinity",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if created.returncode != 0:
            raise RuntimeError((created.stderr or created.stdout or "docker create failed")[-2000:])
        started = subprocess.run(
            ["docker", "start", name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if started.returncode != 0:
            raise RuntimeError((started.stderr or started.stdout or "docker start failed")[-2000:])
        copied = subprocess.run(
            ["docker", "cp", f"{workdir}/.", f"{name}:/testbed/"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if copied.returncode != 0:
            raise RuntimeError((copied.stderr or copied.stdout or "docker cp failed")[-2000:])
        result = subprocess.run(
            ["docker", "exec", "-w", "/testbed", name, "bash", "-lc", cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=600,
            check=False,
        )
        return {
            "tests_rc": result.returncode,
            "tests_ok": result.returncode == 0,
            "verifier_stdout": (result.stdout or "")[-2000:],
            "verifier_stderr": (result.stderr or "")[-2000:],
            "verifier_backend": "docker",
            "docker_image": image,
        }
    except subprocess.TimeoutExpired:
        return {
            "tests_rc": "timeout",
            "tests_ok": False,
            "verifier_stdout": "",
            "verifier_stderr": "docker eval timed out",
            "verifier_backend": "docker",
            "docker_image": image,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "tests_rc": "error",
            "tests_ok": False,
            "verifier_stdout": "",
            "verifier_stderr": str(exc)[-2000:],
            "verifier_backend": "docker",
            "docker_image": image,
        }
    finally:
        subprocess.run(
            ["docker", "rm", "-f", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def verify(workdir: Path, task: SWETask, instance: dict, python: str) -> dict:
    mode = (os.environ.get("AGENTTX_SWE_VERIFY") or "auto").strip().lower()
    if mode == "docker" or (mode == "auto" and docker_available()):
        tests = _verify_tests_docker(workdir, task, instance)
    else:
        tests = _verify_tests_host(workdir, task, instance, python)
    docs_ok = all_documents_valid(workdir, task.docs())
    derived_removed = not (Path(workdir) / "recovery_build" / "derived.txt").exists()
    tests["documents_valid"] = docs_ok
    tests["derived_removed"] = derived_removed
    return tests
