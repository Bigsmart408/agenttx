#!/usr/bin/env python3
"""Run AgentTX recovery on SWE-Bench Lite and Terminal-Bench tasks.

Same protocol as the original recovery/token experiments, on official
workspaces: inject a faulty producer, independent notes, a derived artifact,
and a failing official test; then compare causal, temporal_checkpoint, and
whole_branch_abort.  Success requires the official verifier, independent
document retention, and removal of the derived artifact.

Official labels (SWE repo, TB difficulty/category) are the primary grouping
axis.  AgentTX short/medium/long is only a length budget for injected notes.
Isolation baselines (bare / try) and process SIGKILL are not part of this
runner.

Use --oracle to apply the official gold/oracle solution after the policy
(no LLM).  Live runs use a real external DeepSeek Harness or Codex process.
Use --preflight-only to prefetch instances and check the selected harness.
"""

from __future__ import annotations

import argparse
import csv
csv.field_size_limit(50_000_000)
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".codex_tmp"))

from agenttx.agents.external import ExternalHarnessResult, create_external_harness  # noqa: E402
from agenttx.providers import load_provider_env, provider_result_dir, resolve_provider  # noqa: E402
from experiments.scripts.bench_real_agent import load_llm_env  # noqa: E402


def _mounted_paths_under(root: Path) -> list[str]:
    prefix = str(root.resolve())
    mounts: list[str] = []
    try:
        for line in Path("/proc/mounts").read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = line.split()
            if len(parts) >= 2 and (parts[1] == prefix or parts[1].startswith(prefix + "/")):
                mounts.append(parts[1])
    except OSError:
        pass
    mounts.sort(key=len, reverse=True)
    return mounts


def _unmount_tree(root: Path) -> None:
    for mount in _mounted_paths_under(root):
        subprocess.run(["umount", "-l", mount], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _cleanup(path: Path) -> None:
    """Drop overlay mounts then delete a task scratch directory."""
    if path is None:
        return
    path = Path(path)
    if not path.exists():
        return
    _unmount_tree(path)
    subprocess.run(
        ["bash", "-lc", f"chmod -R u+rwX '{path}' 2>/dev/null || true"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    shutil.rmtree(path, ignore_errors=True)
    if path.exists():
        subprocess.run(["rm", "-rf", str(path)], check=False)


def _reap_orphan_sandboxes(*, keep: Path | None = None) -> None:
    """Remove leftover try/AgentTX scratch dirs after a finished task."""
    # A parallel supervisor owns cleanup across workers.  The legacy global
    # sweep cannot distinguish another worker's live sandbox from an orphan.
    if os.environ.get("AGENTTX_SKIP_ORPHAN_REAP") == "1":
        return
    keep_s = str(keep.resolve()) if keep is not None and keep.exists() else None
    patterns = (
        "tmp.*.try-*",
        "agenttx-swe-*",
        "agenttx-tb-*",
        "agenttx-sandbox-*",
        "agenttx-cmd-*",
        "agenttx-try-probe-*",
        "agenttx-cmd-*",
    )
    roots = []
    tmp = Path("/tmp")
    for pat in patterns:
        roots.extend(tmp.glob(pat))
    for root in roots:
        if not root.is_dir():
            continue
        resolved = str(root.resolve())
        if keep_s and (resolved == keep_s or resolved.startswith(keep_s + "/")):
            continue
        _cleanup(root)

from experiments.scripts.bench_robustness import percentile  # noqa: E402
from experiments.workloads import swe_bench_suite as swe  # noqa: E402
from experiments.workloads import terminal_bench_suite as tb  # noqa: E402
from experiments.workloads.recovery_inject import (  # noqa: E402
    DocSpec,
    build_recovery_manifest,
    all_midcrash_docs,
    dag_is_valid,
    doc_replay_prompt,
    document_content,
    independent_work_discarded,
    midcrash_docs,
    missing_independent_docs,
    read_recovery_documents,
    recovery_manifest_json,
    recovery_context_variant,
    retained_artifact_access,
)

IDEAL_ORACLE_MODE = "ideal_oracle"
POLICY_MODES = (
    "causal",
    "temporal_checkpoint",
    "whole_branch_abort",
    IDEAL_ORACLE_MODE,
)
NO_FAULT_MODE = "no_fault"
CRASH_DIRECT_MODE = "crash_direct"
CLEAN_RECOVERY_MODE = "clean_recovery"
CRASH_NO_ROLLBACK_MODE = "crash_no_rollback"
# Control arms make the injected-fault effect separable from the prompt and
# rollback effects.  Keep the three actual recovery policies as the default
# published set for backwards compatibility.
EXPERIMENT_MODES = (
    NO_FAULT_MODE,
    CRASH_DIRECT_MODE,
    CLEAN_RECOVERY_MODE,
    CRASH_NO_ROLLBACK_MODE,
    *POLICY_MODES,
)
DIRECT_MODES = {NO_FAULT_MODE, CRASH_DIRECT_MODE}
FAULT_MODES = {CRASH_DIRECT_MODE, CRASH_NO_ROLLBACK_MODE, *POLICY_MODES}
RECOVERY_PROMPT_MODES = {CLEAN_RECOVERY_MODE, CRASH_NO_ROLLBACK_MODE, *POLICY_MODES}
MANIFEST_MODES = {CRASH_NO_ROLLBACK_MODE, *POLICY_MODES}
STRICT_MANIFEST_MODES = set(MANIFEST_MODES)
RANDOMIZED_CRASH_MODES = frozenset(FAULT_MODES)
MODES = POLICY_MODES
# The application path is intentionally limited to real black-box harnesses.
# The historical in-process loop remains readable for old result files but is
# not exposed as a benchmark choice anymore.
HARNESSES = ("deepseek_harness", "codex", "sweagent")
DEFAULT_PYTHON = os.environ.get(
    "AGENTTX_PYTHON",
    "/home/pengpeng/miniconda3/envs/agenttx/bin/python"
    if Path("/home/pengpeng/miniconda3/envs/agenttx/bin/python").exists()
    else sys.executable,
)

RANDOM_CRASH_PROBE_STEPS = 1


def _create_workload_harness(
    harness_backend: str,
    *,
    harness_root: Optional[Path],
    model: Optional[str],
    harness_command: Optional[str],
    harness_timeout_s: float,
    output_dir: Path,
    max_turns: int,
    reasoning_effort: str,
):
    if harness_backend != "sweagent":
        return create_external_harness(
            harness_backend,
            root=harness_root,
            model=model,
            command=harness_command,
            timeout_s=harness_timeout_s,
        )
    from experiments.scripts.sweagent_native_workload import (
        SWEAgentNativeWorkloadHarness,
        load_codex_gpt_profile,
    )

    profile = load_codex_gpt_profile(
        model_override=model,
        reasoning_effort_override=reasoning_effort,
    )
    return SWEAgentNativeWorkloadHarness(
        output_dir=output_dir,
        profile=profile,
        max_steps=max_turns,
        max_model_calls=max_turns,
        timeout_s=harness_timeout_s,
    )


def _randomized_crash_docs(task, task_name: str, repeat: int):
    seed = int(os.environ.get("AGENTTX_FAULT_SEED", "0"))
    digest = hashlib.sha256(f"{seed}:{task_name}:{repeat}".encode("utf-8")).digest()
    trial_seed = int.from_bytes(digest[:8], "big")
    rng = random.Random(trial_seed)
    docs = [
        DocSpec(spec.path, spec.prefix, 1)
        for spec in all_midcrash_docs(task.docs())[:2]
    ]
    docs.extend(
        DocSpec(
            f"recovery_notes/localization_probe_{index:02d}.md",
            f"probe{index:02d}",
            1,
        )
        for index in range(1, RANDOM_CRASH_PROBE_STEPS + 1)
    )
    rng.shuffle(docs)
    return docs, rng.randint(1, len(docs) - 1), trial_seed


def _auto_localize_policy(
    agent, injected: dict, mode: str = "causal"
) -> tuple[List[int], dict]:
    """Run AgentTX localization and apply its verified causal plan.

    This path belongs exclusively to the causal policy.  No-rollback and
    coarse rollback arms must not receive the localizer's counterfactual
    probes or discovered root as hidden assistance.
    """
    if mode != "causal":
        raise ValueError("automatic localization is exclusive to causal recovery")
    tx = agent.harness.tx
    test_step_id = int(injected["test_run_step"])
    original = next(
        (record for record in tx.history if record.step_id == test_step_id),
        None,
    )
    if original is None or not original.argv:
        raise RuntimeError("injected failed verifier record is unavailable")
    marker_probe = (
        "from pathlib import Path; "
        "markers=(b'AGENTTX_INJECTED_FAULT',b'AGENTTX injected recovery producer'); "
        "skip={'.git','.venv','.tmp','node_modules','__pycache__'}; "
        "files=(p for p in Path('.').rglob('*') if p.is_file() "
        "and not skip.intersection(p.parts)); "
        "raise SystemExit(any(any(m in p.read_bytes() for m in markers) "
        "for p in files if p.stat().st_size <= 2000000))"
    )
    failed = tx.run_verifier(
        [sys.executable, "-c", marker_probe],
        name="auto-localization-crash-verifier",
        trace_reads=False,
        timeout_s=300,
    )
    if failed.returncode == 0:
        raise RuntimeError("injected crash verifier unexpectedly passed")
    # Candidate ranking uses the real failed test's dependency parents. The
    # generic marker probe supplies only a path-agnostic counterfactual oracle.
    real_parents = set(getattr(original, "parents", ()) or ())
    if not real_parents:
        raise RuntimeError("real failed verifier has no dependency evidence")
    tx.ledger.steps[failed.step_id].parents.update(real_parents)
    failed.parents = sorted(real_parents)
    tx._persist()
    plan = tx.plan_recovery(
        failed,
        max_candidates=8,
        max_probes=12,
        allow_multi_root=True,
        allow_fallback=False,
        verifier_timeout_s=300,
    )
    if not plan.verified:
        raise RuntimeError(f"automatic fault localization was inconclusive: {plan}")
    targets = tx.apply_recovery_plan(plan)
    return targets, {
        "failed_verifier_step": failed.step_id,
        "localized_root_steps": list(plan.root_step_ids),
        "localization_matches_injected_root": list(plan.root_step_ids)
        == [injected["root_step"]],
        "localization_validation": plan.validation,
        "localization_confidence": plan.confidence,
        "fault_location_source": "agenttx_localizer",
        "fault_location_disclosed": True,
    }


def _apply_blind_policy(agent, injected: dict, mode: str) -> tuple[List[int], dict]:
    """Apply a policy boundary without running fault localization.

    The controller uses only the boundary established by the injected crash
    schedule.  The boundary is evaluator/control-plane state and is never
    rendered into the no-rollback or coarse-policy handoff.
    """
    if mode == CRASH_NO_ROLLBACK_MODE:
        targets: List[int] = []
    elif mode == "temporal_checkpoint":
        targets = agent.harness.tx.rollback(int(injected["root_step"]))
    elif mode == "whole_branch_abort":
        active = agent.harness.tx.ledger.active_uncommitted()
        targets = agent.harness.tx.rollback(active[0].step_id) if active else []
    else:
        raise ValueError(f"blind recovery is unsupported for mode {mode!r}")
    return targets, {
        "localized_root_steps": [],
        "localization_matches_injected_root": None,
        "localization_validation": "not_run",
        "localization_confidence": "none",
        "fault_location_source": "hidden",
        "fault_location_disclosed": False,
    }


def _apply_policy(agent, mode: str, root_step: int) -> List[int]:
    """Select which injected effects to keep before the live agent/oracle runs.

    - causal: drop the fault cone, keep independent documents
    - temporal_checkpoint: roll from the fault timestamp (later independent work goes too)
    - whole_branch_abort: drop the whole uncommitted overlay
    """
    if mode in {"causal", IDEAL_ORACLE_MODE}:
        return agent.harness.tx.rollback_causal(root_step)
    if mode == "temporal_checkpoint":
        return agent.harness.tx.rollback(root_step)
    if mode == "whole_branch_abort":
        active = agent.harness.tx.ledger.active_uncommitted()
        return agent.harness.tx.rollback(active[0].step_id) if active else []
    if mode in EXPERIMENT_MODES:
        return []
    raise ValueError(mode)


def official_group(row: dict) -> str:
    """Primary published split: SWE repo, Terminal-Bench difficulty."""
    suite = str(row.get("suite") or "")
    if suite == "swe":
        return str(row.get("repo") or row.get("category") or row.get("task") or "")
    if suite == "tb":
        return str(row.get("difficulty") or "unspecified")
    return str(row.get("task") or "")


def _externalize_prompt(prompt: str) -> str:
    """Remove the legacy synthetic finish-tool contract from live prompts."""
    return prompt.replace(
        "Call `finish` with `commit=false` and a one-sentence summary when the official verifier passes.",
        "When the official verifier passes, return a concise final summary and stop.",
    )


def _snapshot(workdir: Path, extra: Sequence[str] = ()) -> Dict[str, bytes]:
    result: Dict[str, bytes] = {}
    roots = [
        Path(workdir) / "agenttx_task_spec",
        Path(workdir) / "recovery_notes",
        Path(workdir) / "recovery_build",
        Path(workdir) / "tbench_verify",
    ]
    files = [Path(workdir) / rel for rel in extra]
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                result[str(path.relative_to(workdir))] = path.read_bytes()
    for path in files:
        if path.exists() and path.is_file():
            result[str(path.relative_to(workdir))] = path.read_bytes()
    return result


def _paths_touched_after(
    steps: Sequence, first_step: int, last_step: Optional[int] = None
) -> List[str]:
    paths = set()
    for step in steps[first_step:last_step]:
        if getattr(step, "status", "") == "rolled_back":
            continue
        for effect in getattr(step, "effects", []):
            kind = str(
                getattr(
                    getattr(effect, "kind", ""),
                    "value",
                    getattr(effect, "kind", ""),
                )
            )
            if kind not in {"W", "D"}:
                continue
            path = getattr(effect, "path", "")
            if path:
                paths.add(path.lstrip("./"))
    return sorted(paths)


def _retained_artifacts_unchanged(workdir: Path, manifest: dict) -> bool:
    """Compare the committed workspace to the post-recovery REM certificates."""
    artifacts = list(manifest.get("retained") or ())
    if not artifacts:
        return False
    for artifact in artifacts:
        path = Path(workdir) / str(artifact.get("path") or "")
        expected = str(artifact.get("sha256") or "")
        if not path.is_file() or not expected:
            return False
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            return False
    return True


class _ExternalAgent:
    """AgentTX transaction bridge for a real external agent harness.

    The external process owns the agent loop and its tools.  The small local
    object exists only so the recovery workload can seed and roll back the
    protected overlay before the external process runs.
    """

    def __init__(
        self,
        workdir: Path,
        session_dir: Path,
        trace_backend: str,
        adapter,
        ephemeral_roots: Sequence[Path] = (),
    ) -> None:
        from agenttx.harness import CodingAgentHarness

        self.harness = CodingAgentHarness(
            workdir=workdir,
            session_dir=session_dir,
            trace_backend=trace_backend,
            ephemeral_roots=ephemeral_roots,
        )
        self.adapter = adapter

    def run(self, task: str) -> ExternalHarnessResult:
        return self.adapter.run_in_transaction(self.harness, task)

    def close(self, destroy: bool = True) -> None:
        self.harness.close(destroy=destroy)


class _DirectExternalAgent:
    """Run a control arm directly in its disposable benchmark checkout."""

    def __init__(self, workdir: Path, adapter) -> None:
        self.workdir = Path(workdir)
        self.adapter = adapter

    def run(self, task: str) -> ExternalHarnessResult:
        return self.adapter.run(task, self.workdir)

    def close(self, destroy: bool = True) -> None:
        return None


def _seed_direct_crash_state(
    *,
    suite: str,
    workdir: Path,
    task,
    instance: Optional[dict],
    python: str,
    docs: Optional[Sequence[DocSpec]] = None,
    fault_after_docs: Optional[int] = None,
) -> dict:
    """Materialize the crash-control state without constructing AgentTX."""
    ordered_docs = tuple(docs) if docs is not None else tuple(task.docs())
    if fault_after_docs is None:
        before_docs, after_docs = midcrash_docs(ordered_docs)
    else:
        if not 0 < fault_after_docs < len(ordered_docs):
            raise ValueError(
                "fault_after_docs must leave at least one independent document "
                "on each side of the fault"
            )
        before_docs = ordered_docs[:fault_after_docs]
        after_docs = ordered_docs[fault_after_docs:]
    task_id = task.instance_id if suite == "swe" else task.task_id

    def write_docs(specs: Sequence[DocSpec]) -> None:
        for spec in specs:
            path = Path(workdir) / spec.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                document_content(spec.prefix, spec.lines, task_id), encoding="utf-8"
            )

    write_docs(before_docs)

    faulty_path = Path(workdir) / task.faulty_relpath
    faulty_path.parent.mkdir(parents=True, exist_ok=True)
    faulty_content = (
        swe._faulty_content(workdir, task.faulty_relpath)
        if suite == "swe"
        else task.faulty_content
    )
    faulty_path.write_text(faulty_content, encoding="utf-8")

    write_docs(after_docs)

    derived_path = Path(workdir) / "recovery_build" / "derived.txt"
    derived_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(faulty_path, derived_path)
    test_cmd = (
        swe.test_command(task, instance, python)
        if suite == "swe"
        else tb.test_command(python, task)
    )
    failed = subprocess.run(
        ["bash", "-lc", test_cmd],
        cwd=str(workdir),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return {
        "runtime": "bare_direct",
        "root_step": None,
        "faulty_path": task.faulty_relpath,
        "docs_before": [spec.path for spec in before_docs],
        "docs_after": [spec.path for spec in after_docs],
        "derived_paths": ["recovery_build/derived.txt"],
        "tests_failed": failed.returncode != 0,
        "injected_test_rc": failed.returncode,
    }


def prefetch(
    cache_root: Path,
    suites: Sequence[str],
    python: str,
    swe_tasks=None,
    tb_tasks=None,
    skip_environment_errors: bool = False,
) -> None:
    cache_root = Path(cache_root)
    swe_catalog = swe_tasks or swe.TASKS
    tb_catalog = tb_tasks or tb.TASKS
    if "swe" in suites:
        for task in swe_catalog.values():
            try:
                repo, instance = swe.ensure_repo(task, cache_root)
            except Exception as exc:
                if not skip_environment_errors:
                    raise
                print(
                    f"skip environment error during prefetch suite=swe task="
                    f"{getattr(task, 'name', getattr(task, 'instance_id', '?'))}: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
                continue
            print(f"swe {task.instance_id}: {repo} ftp={swe.fail_to_pass(instance)}")
    if "tb" in suites:
        try:
            repo = tb.ensure_tb_repo(cache_root)
        except Exception as exc:
            if not skip_environment_errors:
                raise
            print(
                f"skip environment error during prefetch suite=tb: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            return
        for task in tb_catalog.values():
            try:
                src = tb.task_source(cache_root, task)
            except Exception as exc:
                if not skip_environment_errors:
                    raise
                print(
                    f"skip environment error during prefetch suite=tb task="
                    f"{getattr(task, 'name', getattr(task, 'task_id', '?'))}: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
                continue
            print(f"tb {task.task_id}: {src}")
        print(f"tb repo: {repo}")
        proc = subprocess.run(
            [python, "-c", "import pandas, pyarrow"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if proc.returncode != 0:
            subprocess.run(
                [python, "-m", "pip", "install", "-q", "pandas", "pyarrow"],
                check=False,
            )


def run_once(
    *,
    suite: str,
    task_name: str,
    mode: str,
    repeat: int,
    cache_root: Path,
    model: Optional[str],
    provider: Optional[str],
    max_turns: Optional[int],
    trace_backend: str,
    python: str,
    oracle: bool,
    replay_docs: bool = False,
    harness_backend: str = "deepseek_harness",
    harness_root: Optional[Path] = None,
    harness_command: Optional[str] = None,
    harness_timeout_s: float = 1800.0,
    harness_output_dir: Optional[Path] = None,
    reasoning_effort: str = "max",
    bare_direct_controls: bool = False,
    swe_tasks=None,
    tb_tasks=None,
) -> dict:
    direct_runtime = bool(
        bare_direct_controls
        and mode in DIRECT_MODES
        and not oracle
        and harness_backend != "legacy"
    )
    scratch = Path(
        tempfile.mkdtemp(
            prefix=("benchmark-direct" if direct_runtime else "agenttx")
            + f"-{suite}-{task_name}-{mode}-{repeat}-",
            dir="/tmp",
        )
    )
    # Workspace is the scratch directory itself.  A nested `repo/` child made
    # Codex recreate rolled-back recovery_notes/ as a sibling of repo/, which
    # the commit policy correctly rejects as an outside-workdir write.
    workdir = scratch
    session_dir = (
        None
        if direct_runtime
        else Path(
            tempfile.mkdtemp(
                prefix=f"agenttx-session-{suite}-{task_name}-{mode}-{repeat}-",
                dir="/tmp",
            )
        )
    )
    agent = None
    error = ""
    injected: dict = {}
    targets: List[int] = []
    result = None
    adapter = None
    agent_started = False
    environment_error = False
    committed = False
    finished = False
    finish_called = False
    host_leak = False
    workspace_tmp_env: Dict[str, Optional[str]] = {}
    started = time.perf_counter()
    recovery_started = 0.0
    instance = None
    task = None
    extra_watch: List[str] = []
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    doc_replay_needed = False
    missing_doc_paths: List[str] = []
    doc_replay_prompt_tokens = 0
    doc_replay_completion_tokens = 0
    doc_replay_tokens = 0
    doc_replay_tool_calls = 0
    harness_returncode = 0
    harness_stdout = ""
    harness_stderr = ""
    tool_calls = 0
    usage_source = "none"
    model_calls = 0
    recovery_steps = 0
    regenerated: List[str] = []
    recovery_manifest: dict = {}
    recovery_manifest_text = ""
    recovery_manifest_authoritative = False
    recovery_manifest_intact = False
    crash_trial_seed = None
    fault_after_independent_steps = None
    precrash_progress_seeded = False
    precrash_progress_source = "none"
    precrash_reference_verified = False
    committed_frontier_before_crash = -1
    oracle_post_recovery_verified = False
    post_recovery_verified = False
    localization: dict = {}
    retained_paths_reopened: List[str] = []
    retained_read_effects = 0
    retained_paths_modified: List[str] = []
    independent_unchanged = False
    docs_ok = False
    derived_removed = False
    verdict = {
        "tests_rc": "error",
        "tests_ok": False,
        "documents_valid": False,
        "derived_removed": False,
        "verifier_stdout": "",
        "verifier_stderr": "",
    }
    try:
        if suite == "swe":
            task = (swe_tasks or swe.TASKS)[task_name]
            cache, instance = swe.ensure_repo(task, cache_root)
            swe_verify_mode = (
                os.environ.get("AGENTTX_SWE_VERIFY") or "auto"
            ).strip().lower()
            docker_verify = swe_verify_mode == "docker" or (
                swe_verify_mode == "auto" and swe.docker_available()
            )
            if docker_verify:
                # Resolve verifier infrastructure before spending a model call.
                swe.ensure_eval_image(task.instance_id)
            # Docker is authoritative for the final published verdict, but the
            # injected trajectory and reference-prefix gate execute through the
            # transaction harness on the host.  They therefore still need the
            # task-specific dependency environment.  Using the runner's bare
            # interpreter here made valid gold patches fail before injection
            # (for example SymPy without mpmath and old Django without pytz).
            swe_python = swe.ensure_venv(task, python, cache_root, instance)
            swe.copy_repo(cache, workdir)
            if mode not in DIRECT_MODES | {CLEAN_RECOVERY_MODE}:
                swe.seed_task_workspace(workdir, task, instance)
            extra_watch = [task.faulty_relpath]
            prompt_python = swe_python
            if not oracle:
                task_python = swe.ensure_venv(task, python, cache_root, instance)
                prompt_python = swe.ensure_workspace_venv(workdir, task_python)
                swe.prepare_workspace_runtime(workdir, instance, prompt_python)
            turns = max_turns or task.max_turns
            python = prompt_python
        elif suite == "tb":
            task = (tb_tasks or tb.TASKS)[task_name]
            tb.materialize(task, workdir, cache_root)
            if mode not in DIRECT_MODES | {CLEAN_RECOVERY_MODE}:
                tb.seed_task_workspace(workdir, task)
            extra_watch = [task.faulty_relpath]
            turns = max_turns or task.max_turns
        else:
            raise ValueError(suite)

        # The synthetic injection trajectory runs verifier commands before
        # the external Codex process starts.  Make those subprocesses use the
        # same workspace-local temporary root; otherwise pytest's tmp_path
        # writes are recorded as host leaks and the control arm cannot commit.
        workspace_tmp = workdir / ".tmp"
        workspace_tmp.mkdir(parents=True, exist_ok=True)
        workspace_tmp_env = {
            name: os.environ.get(name) for name in ("TMPDIR", "TEMP", "TMP")
        }
        for name in workspace_tmp_env:
            os.environ[name] = str(workspace_tmp)

        host_baseline = _snapshot(workdir, extra_watch)
        if oracle and not replay_docs:
            from agenttx.harness import CodingAgentHarness

            class _OracleAgent:
                def __init__(self) -> None:
                    self.harness = CodingAgentHarness(
                        workdir=workdir,
                        session_dir=session_dir,
                        trace_backend=trace_backend,
                        ephemeral_roots=(workspace_tmp,),
                    )

                def close(self, destroy: bool = True) -> None:
                    self.harness.close(destroy=destroy)

            agent = _OracleAgent()
        elif oracle and replay_docs:
            from agenttx.agents.llm_agent import LLMToolAgent

            agent = LLMToolAgent(
                workdir=workdir,
                session_dir=session_dir,
                model=model,
                provider=provider,
                max_turns=turns,
                trace_backend=trace_backend,
                ephemeral_roots=(workspace_tmp,),
            )
        elif harness_backend == "legacy":
            from agenttx.agents.llm_agent import LLMToolAgent

            agent = LLMToolAgent(
                workdir=workdir,
                session_dir=session_dir,
                model=model,
                provider=provider,
                max_turns=turns,
                trace_backend=trace_backend,
                ephemeral_roots=(workspace_tmp,),
            )
        elif direct_runtime:
            adapter = _create_workload_harness(
                harness_backend,
                harness_root=harness_root,
                model=model,
                harness_command=harness_command,
                harness_timeout_s=harness_timeout_s,
                output_dir=harness_output_dir or ROOT / "experiments" / "results" / harness_backend,
                max_turns=turns,
                reasoning_effort=reasoning_effort,
            )
            agent = _DirectExternalAgent(workdir=workdir, adapter=adapter)
        else:
            adapter = _create_workload_harness(
                harness_backend,
                harness_root=harness_root,
                model=model,
                harness_command=harness_command,
                harness_timeout_s=harness_timeout_s,
                output_dir=harness_output_dir or ROOT / "experiments" / "results" / harness_backend,
                max_turns=turns,
                reasoning_effort=reasoning_effort,
            )
            agent = _ExternalAgent(
                workdir=workdir,
                session_dir=session_dir,
                trace_backend=trace_backend,
                adapter=adapter,
                ephemeral_roots=(
                    workspace_tmp,
                    *((Path("/root"),) if harness_backend == "sweagent" else ()),
                ),
            )

        seed_reference_progress = (
            mode in MANIFEST_MODES | {CLEAN_RECOVERY_MODE}
            and os.environ.get("AGENTTX_PRECRASH_REFERENCE_PROGRESS", "0")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"}
        )
        if seed_reference_progress:
            # Motivation M3 measures recovery of work completed before a
            # crash, not the unrelated ability to solve a benchmark issue from
            # scratch.  Materialize the benchmark reference solution as the
            # already-completed prefix, commit it, and inject the crash only
            # into the subsequent uncommitted branch.  The source is recorded
            # explicitly so these rows cannot be presented as task-solving
            # results.
            if suite == "swe":
                reference_verdict = swe.verify_reference_solution(task, instance)
                reference_rc = reference_verdict["tests_rc"]
                if reference_rc != 0:
                    detail = (
                        str(reference_verdict.get("verifier_stdout") or "")
                        + "\n"
                        + str(reference_verdict.get("verifier_stderr") or "")
                    )[-3000:]
                    raise RuntimeError(
                        "benchmark reference prefix failed its official-image "
                        f"verifier before crash injection (rc={reference_rc}): {detail}"
                    )
                swe.apply_oracle(agent, instance)
                precrash_progress_source = "benchmark_reference_solution"
            else:
                reference_test_cmd = tb.test_command(python, task)
                reference_check = agent.harness.call_tool(
                    "run_tests", {"cmd": reference_test_cmd}
                )
                reference_rc = int(
                    getattr(
                        reference_check,
                        "returncode",
                        getattr(reference_check, "exit_code", 1),
                    )
                )
                if reference_rc == 0:
                    precrash_progress_source = "existing_verified_workspace"
                else:
                    tb.apply_oracle(agent, task, python, cache_root=cache_root)
                    reference_check = agent.harness.call_tool(
                        "run_tests", {"cmd": reference_test_cmd}
                    )
                    reference_rc = int(
                        getattr(
                            reference_check,
                            "returncode",
                            getattr(reference_check, "exit_code", 1),
                        )
                    )
                    precrash_progress_source = "benchmark_reference_solution"
                if reference_rc != 0:
                    detail = str(
                        getattr(reference_check, "stderr", "")
                        or getattr(reference_check, "stdout", "")
                        or "no verifier output"
                    )[-1200:]
                    raise RuntimeError(
                        "benchmark reference prefix failed its exact verifier "
                        f"before crash injection (rc={reference_rc}): {detail}"
                    )
            precrash_reference_verified = True
            prefix_active = agent.harness.tx.ledger.active_uncommitted()
            if prefix_active:
                agent.harness.tx.commit(prefix_active[-1].step_id)
            precrash_progress_seeded = True
            committed_frontier_before_crash = int(
                agent.harness.tx.ledger.committed_frontier
            )
            host_baseline = _snapshot(workdir, extra_watch)
        crash_docs = (
            list(all_midcrash_docs(task.docs()))
            if mode in RANDOMIZED_CRASH_MODES
            else []
        )
        if (
            mode in RANDOMIZED_CRASH_MODES
            and os.environ.get("AGENTTX_RANDOMIZE_CRASH", "0").strip().lower()
            in {"1", "true", "yes", "on"}
        ):
            crash_docs, fault_after_independent_steps, crash_trial_seed = (
                _randomized_crash_docs(task, task_name, repeat)
            )

        if mode not in FAULT_MODES:
            # Clean controls deliberately have no injected DAG or rollback.
            injected = {}
            targets = []
        elif direct_runtime:
            if mode == CRASH_DIRECT_MODE:
                injected = _seed_direct_crash_state(
                    suite=suite,
                    workdir=workdir,
                    task=task,
                    instance=instance,
                    python=python,
                    docs=crash_docs or None,
                    fault_after_docs=fault_after_independent_steps,
                )
                if not injected["tests_failed"]:
                    raise RuntimeError(
                        "direct crash injection did not produce a failing verifier state"
                    )
            else:
                injected = {}
            targets = []
        else:
            if suite == "swe":
                injected = swe.inject_task_trajectory(
                    agent,
                    task,
                    instance,
                    python,
                    docs=crash_docs or None,
                    fault_after_docs=fault_after_independent_steps,
                )
            else:
                injected = tb.inject_task_trajectory(
                    agent,
                    task,
                    python,
                    docs=crash_docs or None,
                    fault_after_docs=fault_after_independent_steps,
                )
            if not dag_is_valid(injected):
                raise RuntimeError(f"invalid recovery DAG: {injected}")
            if mode == IDEAL_ORACLE_MODE:
                targets = _apply_policy(agent, mode, injected["root_step"])
                localization = {
                    "localized_root_steps": [injected["root_step"]],
                    "localization_matches_injected_root": True,
                    "localization_validation": "oracle_truth",
                    "localization_confidence": "oracle",
                    "fault_location_source": "oracle",
                    "fault_location_disclosed": True,
                }
            elif mode == "causal":
                targets, localization = _auto_localize_policy(agent, injected)
            elif mode in {
                CRASH_NO_ROLLBACK_MODE,
                "temporal_checkpoint",
                "whole_branch_abort",
            }:
                targets, localization = _apply_blind_policy(agent, injected, mode)
            else:
                targets = _apply_policy(agent, mode, injected["root_step"])
        # Official-task session is unchanged. Isolated replay runs only when
        # the policy discarded independent steps; those tokens are the savings.
        if not direct_runtime and independent_work_discarded(
            injected, agent.harness.tx.ledger
        ):
            missing_docs = missing_independent_docs(workdir, crash_docs, agent=agent)
        else:
            missing_docs = []
        missing_doc_paths = [spec.path for spec in missing_docs]
        doc_replay_needed = bool(missing_docs)
        if missing_docs and (not oracle or replay_docs):
            replay_prompt = doc_replay_prompt(docs=missing_docs, task_name=task.name)
            if (not oracle) and harness_backend != "legacy":
                replay_prompt = _externalize_prompt(replay_prompt)
            if oracle or harness_backend == "legacy":
                replay_result = agent.run(replay_prompt, commit=False)
            else:
                replay_result = agent.run(replay_prompt)
            doc_replay_prompt_tokens = int(replay_result.prompt_tokens)
            doc_replay_completion_tokens = int(replay_result.completion_tokens)
            doc_replay_tokens = int(replay_result.total_tokens)
            doc_replay_tool_calls = int(replay_result.tool_calls)
        if mode in POLICY_MODES and not oracle:
            original_test = next(
                (
                    record
                    for record in agent.harness.tx.history
                    if record.step_id == int(injected["test_run_step"])
                ),
                None,
            )
            if original_test is None or not original_test.argv:
                raise RuntimeError("recovery policy lost the injected verifier command")
            recovery_check = agent.harness.tx.run_verifier(
                original_test.argv,
                name=f"{mode}-post-recovery",
                trace_reads=False,
                timeout_s=300,
            )
            recovery_state_verified = recovery_check.returncode == 0
            # The injected verifier is deliberately path-blind and checks the
            # synthetic crash marker.  It proves the rollback state is clean,
            # but proves the official task only when a reference solution was
            # already verified before injection.
            post_recovery_verified = bool(
                recovery_state_verified and precrash_reference_verified
            )
            oracle_post_recovery_verified = bool(
                mode == IDEAL_ORACLE_MODE and post_recovery_verified
            )
            if mode in {"causal", IDEAL_ORACLE_MODE} and not recovery_state_verified:
                raise RuntimeError(
                    f"{mode} recovered state did not clear the injected crash"
                )
        # Build the handoff from the final workspace state that the official
        # agent will actually receive. Runtime verification reads happen before
        # recovery_first, so they are not charged as agent reopen behavior.
        docs = crash_docs
        document_contents = (
            {} if direct_runtime else read_recovery_documents(agent, docs)
        )
        state_paths = {
            str(injected.get("faulty_path") or "").lstrip("./"),
            *[str(path).lstrip("./") for path in injected.get("derived_paths") or ()],
        }
        state_paths.discard("")
        path_exists = (
            {path: (workdir / path).exists() for path in state_paths}
            if direct_runtime
            else {
                path: agent.harness.tx.path_exists(workdir / path)
                for path in state_paths
            }
        )
        control_manifest_path = (
            None
            if direct_runtime
            else agent.harness.tx.pool.session_dir / "recovery_manifest.json"
        )
        if mode in MANIFEST_MODES:
            recovery_manifest = build_recovery_manifest(
                policy=mode,
                ledger=agent.harness.tx.ledger,
                injected=injected,
                docs=docs,
                document_contents=document_contents,
                workdir=workdir,
                rollback_targets=targets,
                path_exists=path_exists,
                oracle_fault_step_id=(
                    injected["root_step"] if mode == IDEAL_ORACLE_MODE else None
                ),
                localized_fault_step_ids=(
                    localization.get("localized_root_steps", [])
                    if mode == "causal"
                    else ()
                ),
                fault_location_source=localization.get(
                    "fault_location_source", "hidden"
                ),
                oracle_post_recovery_verified=oracle_post_recovery_verified,
                post_recovery_verified=post_recovery_verified,
            )
            recovery_manifest_authoritative = bool(recovery_manifest["authoritative"])
            recovery_manifest_text = recovery_manifest_json(recovery_manifest)
            # The manifest is control-plane state, not task data. Keep the
            # authoritative copy beside AgentTX session metadata so a black-box
            # agent cannot delete it with repository cleanup commands.
            control_manifest_path.write_text(recovery_manifest_text, encoding="utf-8")
            if mode in STRICT_MANIFEST_MODES and not oracle and not recovery_manifest_authoritative:
                raise RuntimeError(
                    "AgentTX recovery state mismatch: refusing to start a fresh live session"
                )
        if mode in DIRECT_MODES and suite == "swe":
            prompt = swe.direct_task_prompt(task, instance)
        elif mode in DIRECT_MODES:
            prompt = tb.direct_task_prompt(task)
        elif suite == "swe":
            prompt = swe.task_prompt(
                task,
                instance,
                prompt_python,
                mode=mode,
                recovery_manifest=(
                    recovery_manifest if mode in MANIFEST_MODES else None
                ),
                oracle_fault_step_id=(
                    injected["root_step"] if mode == IDEAL_ORACLE_MODE else None
                ),
                reference_progress_verified=precrash_reference_verified,
            )
        else:
            prompt = tb.task_prompt(
                task,
                python,
                mode=mode,
                recovery_manifest=(
                    recovery_manifest if mode in MANIFEST_MODES else None
                ),
                oracle_fault_step_id=(
                    injected["root_step"] if mode == IDEAL_ORACLE_MODE else None
                ),
                reference_progress_verified=precrash_reference_verified,
            )
        if not oracle and harness_backend != "legacy":
            prompt = _externalize_prompt(prompt)
        # External harnesses must retain their real workspace effects.  The
        # old in-process loop may disable post-recovery read tracing for its
        # historical timing numbers; the application path keeps tracing on.
        if harness_backend == "legacy":
            agent.harness.tx.trace_reads = False
            if agent.harness.tx.pool is not None:
                agent.harness.tx.pool.trace_reads = False
        recovery_first = 0 if direct_runtime else len(agent.harness.tx.ledger.steps)
        recovery_started = time.perf_counter()
        agent_started = True
        if oracle:
            # With a verified pre-crash prefix, ideal recovery must restore the
            # already-correct state by rollback alone. Reapplying gold here
            # would mask a broken rollback and makes reference preflight
            # impossible for Terminal-Bench tasks whose current workspace is
            # already valid but has no executable solution.sh.
            if not (precrash_reference_verified and mode == IDEAL_ORACLE_MODE):
                if suite == "swe":
                    swe.apply_oracle(agent, instance)
                else:
                    tb.apply_oracle(agent, task, python, cache_root=cache_root)
            finished = True
            finish_called = True
            total_tokens = 0
            prompt_tokens = 0
            completion_tokens = 0
            tool_calls = 0
            usage_source = "oracle"
            model_calls = 0
            messages: list = []
        else:
            if harness_backend == "legacy":
                result = agent.run(prompt, commit=False)
                finish_called = any(
                    call.get("function", {}).get("name") == "finish"
                    for message in result.messages
                    for call in (message.get("tool_calls") or [])
                )
                messages = result.messages
                model_calls = sum(
                    message.get("role") == "assistant" for message in result.messages
                )
            else:
                result = agent.run(prompt)
                # External harnesses terminate after their own final answer;
                # they do not expose AgentTX's synthetic finish tool.
                finish_called = bool(result.finished)
                messages = []
                model_calls = 0
            finished = bool(result.finished)
            total_tokens = int(result.total_tokens)
            prompt_tokens = int(result.prompt_tokens)
            completion_tokens = int(result.completion_tokens)
            tool_calls = int(result.tool_calls)
            usage_source = str(getattr(result, "usage_source", "none"))
            if harness_backend != "legacy":
                harness_returncode = int(result.returncode)
                harness_stdout = str(result.stdout)[-2000:]
                harness_stderr = str(result.stderr)[-2000:]
                if (
                    not result.finished
                    and harness_returncode != 0
                    and total_tokens == 0
                    and model_calls == 0
                ):
                    environment_error = True
        recovery_user_end = (
            0 if direct_runtime else len(agent.harness.tx.ledger.steps)
        )
        access = (
            {
                "retained_paths_reopened": [],
                "retained_read_effects": 0,
                "retained_paths_modified": [],
            }
            if direct_runtime
            else retained_artifact_access(
                agent.harness.tx.ledger.steps,
                first_step=recovery_first,
                last_step=recovery_user_end,
                retained_paths=[
                    item["path"] for item in recovery_manifest.get("retained", [])
                ],
                workdir=workdir,
            )
        )
        retained_paths_reopened = access["retained_paths_reopened"]
        retained_read_effects = int(access["retained_read_effects"])
        retained_paths_modified = access["retained_paths_modified"]
        if mode not in MANIFEST_MODES:
            recovery_manifest_intact = False
        else:
            try:
                control_manifest = control_manifest_path.read_text(encoding="utf-8")
            except OSError:
                control_manifest = ""
            recovery_manifest_intact = control_manifest == recovery_manifest_text
        if retained_paths_modified:
            error = (
                "RecoveryProtectionError: retained artifacts modified by fresh session: "
                + ", ".join(retained_paths_modified)
            )
        elif mode in STRICT_MANIFEST_MODES and not recovery_manifest_intact:
            error = "RecoveryProtectionError: recovery manifest was modified or removed"
        host_leak = False if direct_runtime else _snapshot(workdir, extra_watch) != host_baseline
        if direct_runtime:
            committed = finished
        else:
            active = [
                step.step_id
                for step in agent.harness.tx.ledger.steps
                if step.status != "rolled_back"
                and step.step_id > agent.harness.tx.ledger.committed_frontier
            ]
            if active and not error:
                if finished:
                    agent.harness.tx.commit(max(active))
                    committed = True
                else:
                    error = (
                        "RecoveryProtectionError: external session did not finish; "
                        "refusing to commit its partial sandbox"
                    )
        if suite == "swe":
            verdict = swe.verify(
                workdir,
                task,
                instance,
                python,
                require_recovery_artifacts=mode in MANIFEST_MODES,
                recovery_docs=crash_docs if mode in MANIFEST_MODES else None,
            )
        else:
            verdict = tb.verify(
                workdir,
                task,
                python,
                require_recovery_artifacts=mode in MANIFEST_MODES,
                cache_root=cache_root,
                recovery_docs=crash_docs if mode in MANIFEST_MODES else None,
            )
        if _verifier_environment_error(verdict):
            environment_error = True
            if not error:
                detail = str(verdict.get("verifier_stderr") or "official verifier failed")
                error = f"VerifierEnvironmentError: {detail}"[:800]
        independent_unchanged = (
            True
            if mode in DIRECT_MODES | {CLEAN_RECOVERY_MODE}
            else _retained_artifacts_unchanged(workdir, recovery_manifest)
        )
        docs_ok = bool(verdict["documents_valid"])
        derived_removed = bool(verdict["derived_removed"])
        recovery_steps = (
            recovery_user_end - recovery_first
            if mode in POLICY_MODES
            else 0
        )
        regenerated = (
            []
            if direct_runtime
            else _paths_touched_after(
                agent.harness.tx.ledger.steps, recovery_first, recovery_user_end
            )
        )
        success = bool(
            finished
            and finish_called
            and committed
            and verdict["tests_ok"]
            and docs_ok
            and (derived_removed or mode in DIRECT_MODES)
            and independent_unchanged
            and (mode not in STRICT_MANIFEST_MODES or recovery_manifest_authoritative)
            and (mode not in STRICT_MANIFEST_MODES or recovery_manifest_intact)
            and not retained_paths_modified
            and not error
            # Every live-agent arm, including clean/causal/ideal, must pay for
            # a real handoff and response. A zero-usage success means the
            # adapter or protocol bypassed the model and is not comparable.
            and (oracle or total_tokens > 0)
        )
    except Exception as exc:  # keep failed repeats in the raw output
        error = f"{type(exc).__name__}: {exc}"
        if len(error) > 2000:
            error = f"{error[:200]} ... [truncated] ... {error[-1780:]}"
        environment_error = not agent_started and not oracle
        independent_unchanged = False
        docs_ok = False
        derived_removed = False
        recovery_steps = 0
        regenerated = []
        total_tokens = int(result.total_tokens) if result is not None else 0
        prompt_tokens = int(result.prompt_tokens) if result is not None else 0
        completion_tokens = int(result.completion_tokens) if result is not None else 0
        tool_calls = int(result.tool_calls) if result is not None else 0
        usage_source = str(getattr(result, "usage_source", "none")) if result is not None else "none"
        harness_returncode = int(result.returncode) if result is not None else 127
        harness_stdout = str(result.stdout)[-2000:] if result is not None else ""
        harness_stderr = str(result.stderr)[-2000:] if result is not None else ""
        model_calls = 0
        success = False
        verdict = {
            "tests_rc": "error",
            "tests_ok": False,
            "documents_valid": False,
            "derived_removed": False,
            "verifier_stdout": "",
            "verifier_stderr": "",
        }
        messages = []
    finally:
        wall_s = time.perf_counter() - started
        recovery_wall_s = time.perf_counter() - recovery_started if recovery_started else 0.0
        if agent is not None:
            try:
                agent.close(destroy=True)
            except Exception:
                pass
        _cleanup(scratch)
        _cleanup(session_dir)
        _reap_orphan_sandboxes(keep=None)
        for name, value in workspace_tmp_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    if oracle:
        provider_name = "oracle"
        model_name = "oracle"
    elif harness_backend == "legacy":
        provider_name = resolve_provider(provider, ROOT).name
        model_name = model or resolve_provider(provider, ROOT).model
    else:
        provider_name = adapter.name if adapter is not None else harness_backend
        model_name = adapter.model if adapter is not None else (model or "unknown")
    scale = getattr(task, "scale", "") if task is not None else ""
    repo = ""
    commit = ""
    difficulty = ""
    category = ""
    version = ""
    if suite == "swe" and instance is not None:
        repo = instance.get("repo", "")
        commit = instance.get("base_commit", "")
        version = str(instance.get("version") or "")
        # Lite has no official easy/medium/hard; repo is the published split.
        difficulty = ""
        category = repo
    elif suite == "tb" and task is not None:
        repo = f"terminal-bench:{task.task_id}"
        difficulty = str(getattr(task, "difficulty", "") or "")
        category = str(getattr(task, "category", "") or "")
    return {
        "suite": suite,
        "task": task_name,
        "scale": scale,
        "difficulty": difficulty,
        "category": category,
        "version": version,
        "official_group": official_group(
            {
                "suite": suite,
                "task": task_name,
                "repo": repo,
                "difficulty": difficulty,
                "category": category,
            }
        ),
        "repo": repo,
        "commit": commit,
        "mode": mode,
        "recovery_context_variant": (
            recovery_context_variant(mode) if mode in RECOVERY_PROMPT_MODES else "none"
        ),
        "fault_injected": mode in FAULT_MODES,
        "fault_origin": "harness_injected_crash" if mode in FAULT_MODES else "none",
        "crash_trial_seed": crash_trial_seed,
        "fault_after_independent_steps": fault_after_independent_steps,
        "precrash_progress_seeded": precrash_progress_seeded,
        "precrash_progress_source": precrash_progress_source,
        "precrash_reference_verified": precrash_reference_verified,
        "committed_frontier_before_crash": committed_frontier_before_crash,
        "injected_root_step": injected.get("root_step"),
        "localized_root_steps": localization.get("localized_root_steps", []),
        "localization_matches_injected_root": localization.get(
            "localization_matches_injected_root"
        ),
        "localization_validation": localization.get("localization_validation", ""),
        "localization_confidence": localization.get("localization_confidence", ""),
        "localization_failed_verifier_step": localization.get("failed_verifier_step"),
        "fault_location_source": localization.get("fault_location_source", "none"),
        "fault_location_disclosed": localization.get(
            "fault_location_disclosed", False
        ),
        "oracle_fault_step_disclosed": mode == IDEAL_ORACLE_MODE,
        "oracle_post_recovery_verified": oracle_post_recovery_verified,
        "post_recovery_verified": post_recovery_verified,
        "repeat": repeat,
        "oracle": oracle,
        "provider": provider_name,
        "model": model_name,
        "harness_backend": "oracle" if oracle else harness_backend,
        "execution_boundary": (
            "oracle"
            if oracle
            else (
                "external_task_direct"
                if direct_runtime
                else ("tool" if harness_backend == "legacy" else "external_task")
            )
        ),
        "trace_backend": "none" if direct_runtime else trace_backend,
        "wall_s": round(wall_s, 6),
        "recovery_wall_s": round(recovery_wall_s, 6),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "doc_replay_needed": doc_replay_needed,
        "missing_doc_paths": missing_doc_paths,
        "doc_replay_prompt_tokens": doc_replay_prompt_tokens,
        "doc_replay_completion_tokens": doc_replay_completion_tokens,
        "doc_replay_tokens": doc_replay_tokens,
        "doc_replay_tool_calls": doc_replay_tool_calls,
        "tool_calls": tool_calls,
        "usage_source": usage_source,
        "model_calls": model_calls,
        "harness_returncode": harness_returncode,
        "harness_stdout": harness_stdout,
        "harness_stderr": harness_stderr,
        "recovery_ledger_steps": recovery_steps,
        "rollback_targets": targets,
        "regenerated_paths": regenerated,
        "recovery_manifest_state_id": recovery_manifest.get("state_id", ""),
        "recovery_manifest_authoritative": recovery_manifest_authoritative,
        "recovery_manifest_intact": recovery_manifest_intact,
        "retained_paths_reopened": retained_paths_reopened,
        "retained_read_effects": retained_read_effects,
        "retained_paths_modified": retained_paths_modified,
        "independent_retained": docs_ok,
        "independent_unchanged": independent_unchanged,
        "documents_valid": docs_ok,
        "derived_removed": derived_removed,
        "host_leak_before_commit": host_leak,
        "tests_rc": verdict.get("tests_rc"),
        "tests_ok": verdict.get("tests_ok"),
        "finished": finished,
        "finish_called": finish_called,
        "committed": committed,
        "success": success,
        "environment_error": environment_error,
        "injected": injected,
        "error": error,
        "verifier_stdout": verdict.get("verifier_stdout", ""),
        "verifier_stderr": verdict.get("verifier_stderr", ""),
    }


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def _percentile(values: Iterable[float], quantile: float) -> float:
    """Linearly interpolated percentile for token/time summaries."""
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _success_tokens(rows: Sequence[dict]) -> List[float]:
    return [
        float(r.get("total_tokens", 0) or 0)
        for r in rows
        if r.get("success") and r.get("total_tokens") is not None
    ]


def _combined_tokens(row: dict) -> float:
    """Session usage plus any separate document-replay model usage."""
    return float(row.get("total_tokens", 0) or 0) + float(
        row.get("doc_replay_tokens", 0) or 0
    )


def _success_combined_tokens(rows: Sequence[dict]) -> List[float]:
    return [_combined_tokens(row) for row in rows if row.get("success")]


def _pair_key(row: dict) -> tuple:
    """Match a coarse row to the same official instance and repeat."""
    return (
        row.get("suite"),
        row.get("task"),
        row.get("scale"),
        row.get("harness_backend"),
        row.get("model"),
        row.get("repeat"),
    )


def _avoided_tokens(mode_rows: Sequence[dict], causal_rows: Sequence[dict], field: str = "total_tokens") -> List[float]:
    """Coarse success tokens minus causal, only on paired successes."""
    causal_by_pair = {_pair_key(row): row for row in causal_rows}
    avoided: List[float] = []
    for row in mode_rows:
        peer = causal_by_pair.get(_pair_key(row))
        if not peer or not row.get("success") or not peer.get("success"):
            continue
        avoided.append(float(row.get(field) or 0) - float(peer.get(field) or 0))
    return avoided


def _avoided_combined_tokens(mode_rows: Sequence[dict], causal_rows: Sequence[dict]) -> List[float]:
    """Combined coarse usage minus causal usage, only on paired successes."""
    causal_by_pair = {_pair_key(row): row for row in causal_rows}
    avoided: List[float] = []
    for row in mode_rows:
        peer = causal_by_pair.get(_pair_key(row))
        if not peer or not row.get("success") or not peer.get("success"):
            continue
        avoided.append(_combined_tokens(row) - _combined_tokens(peer))
    return avoided


def _mode_block(mode_rows: Sequence[dict], causal_rows: Sequence[dict]) -> dict:
    tokens_all = [float(r.get("total_tokens", 0) or 0) for r in mode_rows if r.get("total_tokens")]
    tokens_ok = _success_tokens(mode_rows)
    prompt_ok = [
        float(r.get("prompt_tokens", 0) or 0) for r in mode_rows if r.get("success")
    ]
    completion_ok = [
        float(r.get("completion_tokens", 0) or 0) for r in mode_rows if r.get("success")
    ]
    avoided = _avoided_tokens(mode_rows, causal_rows)
    replay_all = [float(r.get("doc_replay_tokens", 0) or 0) for r in mode_rows]
    replay_ok = [
        float(r.get("doc_replay_tokens", 0) or 0) for r in mode_rows if r.get("success")
    ]
    avoided_replay = _avoided_tokens(mode_rows, causal_rows, "doc_replay_tokens")
    combined_all = [_combined_tokens(row) for row in mode_rows]
    combined_ok = _success_combined_tokens(mode_rows)
    avoided_combined = _avoided_combined_tokens(mode_rows, causal_rows)
    return {
        "repeats": len(mode_rows),
        "success_rate": round(_mean([float(bool(r.get("success"))) for r in mode_rows]), 4),
        "tests_pass_rate": round(_mean([float(bool(r.get("tests_ok"))) for r in mode_rows]), 4),
        "independent_retention_rate": round(
            _mean([float(bool(r.get("independent_retained"))) for r in mode_rows]), 4
        ),
        "invalid_removed_rate": round(
            _mean([float(bool(r.get("derived_removed"))) for r in mode_rows]), 4
        ),
        "total_tokens_mean": round(_mean(tokens_all), 3),
        "success_tokens_mean": round(_mean(tokens_ok), 3),
        "success_prompt_tokens_mean": round(_mean(prompt_ok), 3),
        "success_completion_tokens_mean": round(_mean(completion_ok), 3),
        "avoided_tokens_mean": round(_mean(avoided), 3) if avoided else 0.0,
        "doc_replay_tokens_mean": round(_mean(replay_all), 3),
        "success_doc_replay_tokens_mean": round(_mean(replay_ok), 3),
        "avoided_replay_tokens_mean": round(_mean(avoided_replay), 3) if avoided_replay else 0.0,
        "saved_tokens_mean": round(_mean(avoided_replay), 3) if avoided_replay else 0.0,
        "combined_tokens_mean": round(_mean(combined_all), 3),
        "success_combined_tokens_mean": round(_mean(combined_ok), 3),
        "avoided_combined_tokens_mean": round(_mean(avoided_combined), 3) if avoided_combined else 0.0,
        "paired_success_repeats": len(avoided),
    }


def token_summaries(rows: Sequence[dict]) -> List[dict]:
    """Per-task token aggregates.  Means used as savings use success rows only."""
    groups: Dict[tuple, List[dict]] = defaultdict(list)
    for row in rows:
        key = (
            row.get("suite", ""),
            row.get("task", ""),
            row.get("official_group") or official_group(row),
            row.get("scale", ""),
            row.get("mode", ""),
            row.get("harness_backend", ""),
            row.get("model", ""),
        )
        groups[key].append(row)
    by_peer: Dict[tuple, List[dict]] = defaultdict(list)
    for row in rows:
        peer_key = (
            row.get("suite", ""),
            row.get("task", ""),
            row.get("official_group") or official_group(row),
            row.get("scale", ""),
            row.get("harness_backend", ""),
            row.get("model", ""),
        )
        by_peer[peer_key].append(row)
    summaries: List[dict] = []
    for (suite, task, group, scale, mode, harness, model), mode_rows in sorted(groups.items()):
        peer_key = (suite, task, group, scale, harness, model)
        causal_rows = [r for r in by_peer[peer_key] if r.get("mode") == "causal"]
        block = _mode_block(mode_rows, causal_rows)
        tokens = _success_tokens(mode_rows) or [
            float(r.get("total_tokens", 0) or 0) for r in mode_rows
        ]
        combined_tokens = _success_combined_tokens(mode_rows) or [
            _combined_tokens(row) for row in mode_rows
        ]
        recovery = [float(r.get("recovery_wall_s", 0) or 0) for r in mode_rows]
        usage_sources = sorted({str(r.get("usage_source", "none")) for r in mode_rows})
        item = {
            "suite": suite,
            "task": task,
            "official_group": group,
            "scale": scale,
            "difficulty": mode_rows[0].get("difficulty", ""),
            "category": mode_rows[0].get("category", ""),
            "version": mode_rows[0].get("version", ""),
            "mode": mode,
            "harness_backend": harness,
            "model": model,
            "usage_sources": ",".join(usage_sources),
            "total_tokens_p50": round(_percentile(tokens, 0.50), 3),
            "total_tokens_p95": round(_percentile(tokens, 0.95), 3),
            "total_tokens_p99": round(_percentile(tokens, 0.99), 3),
            "combined_tokens_p50": round(_percentile(combined_tokens, 0.50), 3),
            "combined_tokens_p95": round(_percentile(combined_tokens, 0.95), 3),
            "combined_tokens_p99": round(_percentile(combined_tokens, 0.99), 3),
            "recovery_wall_s_p50": round(_percentile(recovery, 0.50), 6),
            "recovery_wall_s_p95": round(_percentile(recovery, 0.95), 6),
        }
        item.update(block)
        summaries.append(item)
    return summaries


def _axis_summaries(rows: Sequence[dict], axis: str) -> List[dict]:
    grouped: Dict[tuple, Dict[str, List[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if axis == "official":
            label = row.get("official_group") or official_group(row)
        else:
            label = str(row.get("scale") or "")
        key = (
            row.get("suite", ""),
            label,
            row.get("harness_backend", "") or ("oracle" if row.get("oracle") else "legacy"),
            row.get("model", ""),
        )
        grouped[key][row["mode"]].append(row)
    out = []
    for (suite, label, harness, model), modes in grouped.items():
        causal_rows = modes.get("causal", [])
        for mode, mode_rows in modes.items():
            block = _mode_block(mode_rows, causal_rows)
            item = {
                "axis": axis,
                "suite": suite,
                "official_group": label if axis == "official" else (mode_rows[0].get("official_group") or official_group(mode_rows[0])),
                "scale": label if axis == "length" else mode_rows[0].get("scale", ""),
                "harness_backend": harness,
                "model": model,
                "mode": mode,
            }
            item.update(block)
            out.append(item)
    return out


def summarize(rows: Sequence[dict]) -> dict:
    grouped: Dict[tuple, Dict[str, List[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        key = (
            row.get("suite", ""),
            row.get("task", ""),
            row.get("official_group") or official_group(row),
            row.get("scale", ""),
            row.get("harness_backend", "") or ("oracle" if row.get("oracle") else "legacy"),
            row.get("model", ""),
        )
        grouped[key][row["mode"]].append(row)
    task_summaries = []
    for (suite, task, group, scale, harness, model), modes in grouped.items():
        causal_rows = modes.get("causal", [])
        for mode, mode_rows in modes.items():
            block = _mode_block(mode_rows, causal_rows)
            item = {
                "suite": suite,
                "task": task,
                "official_group": group,
                "scale": scale,
                "difficulty": mode_rows[0].get("difficulty", ""),
                "category": mode_rows[0].get("category", ""),
                "version": mode_rows[0].get("version", ""),
                "harness_backend": harness,
                "model": model,
                "mode": mode,
                "causal_minus_mode_tokens_mean": round(-block["avoided_combined_tokens_mean"], 3),
                "causal_minus_mode_session_tokens_mean": round(-block["avoided_tokens_mean"], 3),
            }
            item.update(block)
            task_summaries.append(item)
    return {
        "rows": list(rows),
        "task_summaries": task_summaries,
        "official_group_summaries": _axis_summaries(rows, "official"),
        "length_summaries": _axis_summaries(rows, "length"),
        "token_summaries": token_summaries(rows),
    }


def _result_dir(provider: Optional[str], oracle: bool, harness_backend: str, result_subdir: Optional[str] = None) -> Path:
    if result_subdir:
        return ROOT / "experiments" / "results" / result_subdir
    if oracle:
        return ROOT / "experiments" / "results" / "official"
    if harness_backend != "legacy":
        return ROOT / "experiments" / "results" / harness_backend
    return provider_result_dir(ROOT, provider)


def write_outputs(
    summary: dict,
    provider: Optional[str],
    oracle: bool,
    harness_backend: str = "deepseek_harness",
    result_subdir: Optional[str] = None,
) -> Path:
    out = _result_dir(provider, oracle, harness_backend, result_subdir)
    out.mkdir(parents=True, exist_ok=True)
    rows = summary["rows"]
    fields = (
        list(dict.fromkeys(key for row in rows for key in row.keys()))
        if rows
        else ["suite", "task", "mode"]
    )
    raw_name = "official_tasks_raw.csv"
    with (out / raw_name).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        for row in rows:
            dumped = dict(row)
            for key, value in list(dumped.items()):
                if isinstance(value, (dict, list)):
                    dumped[key] = json.dumps(value)
            writer.writerow(dumped)
    (out / "official_tasks.json").write_text(
        json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8"
    )
    token_rows = summary.get("token_summaries", [])
    token_fields = [
        "suite", "task", "official_group", "scale", "difficulty", "category", "version", "mode",
        "harness_backend", "model", "repeats", "paired_success_repeats", "usage_sources",
        "success_rate", "success_tokens_mean", "success_prompt_tokens_mean",
        "success_completion_tokens_mean", "avoided_tokens_mean",
        "doc_replay_tokens_mean", "success_doc_replay_tokens_mean", "avoided_replay_tokens_mean",
        "saved_tokens_mean", "combined_tokens_mean", "success_combined_tokens_mean",
        "avoided_combined_tokens_mean",
        "total_tokens_mean", "total_tokens_p50", "total_tokens_p95", "total_tokens_p99",
        "combined_tokens_p50", "combined_tokens_p95", "combined_tokens_p99",
        "recovery_wall_s_p50", "recovery_wall_s_p95",
    ]
    with (out / "official_token_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=token_fields, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(token_rows)
    (out / "official_token_summary.json").write_text(
        json.dumps(token_rows, indent=2) + "\n", encoding="utf-8"
    )
    token_lines = [
        f"# Official token summary ({harness_backend})",
        "",
        "| suite | official_group | task | scale | mode | success | combined tokens | session tokens | missing-doc replay | combined Δ vs causal | combined p50 | combined p95 |",
        "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in token_rows:
        token_lines.append(
            f"| {item['suite']} | {item.get('official_group', '')} | {item['task']} | "
            f"{item['scale']} | {item['mode']} | {item['success_rate']:.2f} | "
            f"{item.get('success_combined_tokens_mean', 0)} | {item.get('success_tokens_mean', 0)} | "
            f"{item.get('success_doc_replay_tokens_mean', 0)} | {item.get('avoided_combined_tokens_mean', 0)} | "
            f"{item['combined_tokens_p50']} | {item['combined_tokens_p95']} |"
        )
    (out / "official_token_summary.md").write_text("\n".join(token_lines) + "\n", encoding="utf-8")
    def _write_axis(name: str, items: Sequence[dict], label_key: str) -> None:
        lines = [
            f"# Official recovery ({harness_backend}) grouped by {name}",
            "",
            f"| suite | {label_key} | harness | model | mode | repeats | success | tests | retention | invalid removed | combined tokens | session tokens | missing-doc replay | combined Δ vs causal |",
            "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for item in items:
            lines.append(
                f"| {item['suite']} | {item.get(label_key, '')} | "
                f"{item['harness_backend']} | {item['model']} | {item['mode']} | {item['repeats']} | "
                f"{item['success_rate']:.2f} | {item['tests_pass_rate']:.2f} | "
                f"{item['independent_retention_rate']:.2f} | {item.get('invalid_removed_rate', 0):.2f} | "
                f"{item.get('success_combined_tokens_mean', 0)} | {item.get('success_tokens_mean', 0)} | "
                f"{item.get('success_doc_replay_tokens_mean', 0)} | {item.get('avoided_combined_tokens_mean', 0)} |"
            )
        (out / name).write_text("\n".join(lines) + "\n", encoding="utf-8")

    lines = [
        f"# Official SWE-Bench Lite + Terminal-Bench recovery ({harness_backend})",
        "",
        "| suite | official_group | task | scale | harness | model | mode | repeats | success | tests | retention | invalid removed | success tokens | missing-doc replay | saved tokens (replay vs causal) | full session Δ vs causal |",
        "|---|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in summary["task_summaries"]:
        lines.append(
            f"| {item['suite']} | {item.get('official_group', '')} | {item['task']} | {item['scale']} | "
            f"{item['harness_backend']} | {item['model']} | {item['mode']} | {item['repeats']} | "
            f"{item['success_rate']:.2f} | {item['tests_pass_rate']:.2f} | "
            f"{item['independent_retention_rate']:.2f} | {item.get('invalid_removed_rate', 0):.2f} | "
            f"{item.get('success_tokens_mean', 0)} | {item.get('success_doc_replay_tokens_mean', 0)} | "
            f"{item.get('saved_tokens_mean', item.get('avoided_replay_tokens_mean', 0))} | {item.get('avoided_tokens_mean', 0)} |"
        )
    (out / "official_tasks.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _write_axis("official_group_summary.md", summary.get("official_group_summaries", []), "official_group")
    _write_axis("length_summary.md", summary.get("length_summaries", []), "scale")
    (out / "official_group_summary.json").write_text(
        json.dumps(summary.get("official_group_summaries", []), indent=2) + "\n", encoding="utf-8"
    )
    (out / "length_summary.json").write_text(
        json.dumps(summary.get("length_summaries", []), indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {out / raw_name}")
    return out


def _load_prior_rows(path: Path) -> list:
    """Load completed output robustly enough to resume long harness runs."""
    if not path.exists():
        return []
    try:
        # Harness diagnostics and recovery metadata can exceed csv's conservative
        # 128 KiB default field limit.
        csv.field_size_limit(sys.maxsize)
        with path.open(newline="", encoding="utf-8") as handle:
            prior_rows = list(csv.DictReader(handle))
        bool_fields = {
            "oracle",
            "environment_error",
            "independent_retained",
            "independent_unchanged",
            "documents_valid",
            "derived_removed",
            "host_leak_before_commit",
            "tests_ok",
            "finished",
            "finish_called",
            "committed",
            "success",
            "doc_replay_needed",
            "recovery_manifest_authoritative",
            "recovery_manifest_intact",
        }
        int_fields = {
            "repeat",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "doc_replay_prompt_tokens",
            "doc_replay_completion_tokens",
            "doc_replay_tokens",
            "doc_replay_tool_calls",
            "tool_calls",
            "model_calls",
            "harness_returncode",
            "recovery_ledger_steps",
            "retained_read_effects",
        }
        for row in prior_rows:
            if not row.get("harness_backend"):
                row["harness_backend"] = "oracle" if row.get("oracle") else "legacy"
            for field in bool_fields:
                if field in row:
                    row[field] = str(row[field]).lower() == "true"
            for field in int_fields:
                if field in row:
                    try:
                        row[field] = int(row[field])
                    except (TypeError, ValueError):
                        pass
    except (OSError, csv.Error):
        return []
    return prior_rows


def _completed_attempt(row: dict) -> bool:
    """Return whether a row is complete, independent of task correctness."""
    error = str(row.get("error") or "")
    outcome_error = error.startswith((
        "PermissionError: commit blocked by policy",
        "RecoveryProtectionError:",
    ))
    return bool(
        row.get("finished")
        and row.get("finish_called")
        and not row.get("environment_error")
        and (not error or outcome_error)
    )


def _terminal_attempt(row: dict) -> bool:
    """Return whether resume must preserve and skip an attempted cell."""
    if _completed_attempt(row):
        return True
    mode = str(row.get("mode") or "")
    try:
        returncode = int(row.get("harness_returncode", 0))
    except (TypeError, ValueError):
        returncode = 0
    return mode in {"temporal_checkpoint", "whole_branch_abort"} and returncode == 124


def _verifier_environment_error(verdict: dict) -> bool:
    """Return whether the official verifier failed to execute at all."""
    return str(verdict.get("tests_rc") or "").lower() in {"error", "timeout"}


def _result_exit_code(rows: Sequence[dict], *, oracle: bool, allow_failures: bool) -> int:
    """Return the process status after a complete benchmark sweep."""
    if oracle or allow_failures:
        return 0
    return 1 if any(not row["success"] for row in rows) else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=("swe", "tb", "all"), default="all")
    parser.add_argument(
        "--task-set",
        choices=("full", "representative", "selected"),
        default="full",
        help="full catalogs (default), representative smoke catalog, or only --tasks from the full catalogs",
    )
    parser.add_argument("--tasks", nargs="+", default=None)
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=list(EXPERIMENT_MODES),
        default=list(POLICY_MODES),
        help=(
            "control/recovery modes: no_fault, crash_direct, clean_recovery, "
            "crash_no_rollback, causal, temporal_checkpoint, whole_branch_abort, "
            "ideal_oracle."
        ),
    )
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-turns", type=int, default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--provider", default=None)
    parser.add_argument(
        "--harness",
        choices=HARNESSES,
        default=os.environ.get("AGENTTX_HARNESS", "deepseek_harness"),
        help="external agent harness (no implicit in-process fallback)",
    )
    parser.add_argument(
        "--harness-root",
        type=Path,
        default=None,
        help="DeepSeek Harness checkout (defaults to DEEPSEEK_HARNESS_ROOT)",
    )
    parser.add_argument(
        "--harness-command",
        default=None,
        help="override the external harness command; task is appended as one argument",
    )
    parser.add_argument("--harness-timeout", type=float, default=1800.0)
    parser.add_argument(
        "--reasoning-effort",
        default="max",
        help="reasoning effort used by the native SWEAgent backend",
    )
    parser.add_argument(
        "--bare-direct-controls",
        action="store_true",
        help=(
            "run no_fault/crash_direct directly in the disposable checkout, "
            "without AgentTX transactions or try"
        ),
    )
    parser.add_argument(
        "--allow-external-writes",
        action="store_true",
        help=(
            "allow the harness to commit writes outside its workspace (except "
            "the hard deny list); equivalent to AGENTTX_ALLOW_EXTERNAL_WRITES=1"
        ),
    )
    parser.add_argument(
        "--result-subdir",
        default=None,
        help="write under experiments/results/<name> instead of the harness folder",
    )
    parser.add_argument("--trace-backend", choices=("strace", "bpf_persistent"), default="strace")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "experiments" / "cache")
    parser.add_argument("--python", default=DEFAULT_PYTHON)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--oracle", action="store_true", help="apply official gold/oracle after policy")
    parser.add_argument(
        "--replay-docs",
        action="store_true",
        help="replay missing independent documents with the live agent even when --oracle is set",
    )
    parser.add_argument(
        "--no-fault",
        action="store_true",
        help="run a clean official-task baseline without injecting or recovering a fault",
    )
    parser.add_argument(
        "--skip-environment-errors",
        action="store_true",
        help="skip cases that fail before the live agent starts or whose harness is unavailable",
    )
    parser.add_argument(
        "--allow-result-failures",
        action="store_true",
        help=(
            "return zero after a complete sweep even when individual task results fail; "
            "startup errors and uncaught worker failures remain non-zero"
        ),
    )
    args = parser.parse_args(argv)
    if args.allow_external_writes:
        os.environ["AGENTTX_ALLOW_EXTERNAL_WRITES"] = "1"
    if args.no_fault and (args.oracle or args.replay_docs):
        raise SystemExit("--no-fault cannot be combined with --oracle or --replay-docs")
    run_modes = (NO_FAULT_MODE,) if args.no_fault else tuple(args.modes)
    if args.bare_direct_controls and any(mode not in DIRECT_MODES for mode in run_modes):
        raise SystemExit(
            "--bare-direct-controls only supports no_fault and crash_direct"
        )
    load_provider_env(ROOT)
    load_llm_env()
    if args.oracle and args.replay_docs:
        profile = resolve_provider(args.provider, ROOT)
        if not profile.api_key:
            print("missing provider API key for --replay-docs", file=sys.stderr)
            return 2
    os.environ.setdefault("TRY_SKIP_MOUNTS", "/data")

    suites = ["swe", "tb"] if args.suite == "all" else [args.suite]
    if args.task_set in {"full", "selected"}:
        swe_catalog = swe.load_tasks(args.cache_dir) if "swe" in suites else {}
        tb_catalog = tb.load_tasks(args.cache_dir) if "tb" in suites else {}
    else:
        swe_catalog = swe.TASKS
        tb_catalog = tb.TASKS
    if "swe" in suites and args.task_set in {"full", "selected"}:
        verify_mode = (os.environ.get("AGENTTX_SWE_VERIFY") or "auto").strip().lower()
        if args.task_set == "full" and verify_mode == "host":
            print(
                "full SWE-bench requires official Docker eval images; "
                "AGENTTX_SWE_VERIFY=host is not valid for a published run",
                file=sys.stderr,
            )
            return 2
        require_docker = args.task_set == "full" or verify_mode == "docker"
        if require_docker and not swe.docker_available():
            print(
                "full SWE-bench requires a working Docker daemon; refusing host fallback",
                file=sys.stderr,
            )
            return 2
        if require_docker:
            os.environ["AGENTTX_SWE_VERIFY"] = "docker"
    if args.task_set == "selected":
        if not args.tasks:
            raise SystemExit("--task-set selected requires --tasks")
        requested = set(args.tasks)
        swe_catalog = {
            name: task for name, task in swe_catalog.items()
            if name in requested or f"swe:{name}" in requested
        }
        tb_catalog = {
            name: task for name, task in tb_catalog.items()
            if name in requested or f"tb:{name}" in requested
        }
    if args.preflight_only:
        prefetch(
            args.cache_dir,
            suites,
            args.python,
            swe_catalog,
            tb_catalog,
            skip_environment_errors=args.skip_environment_errors,
        )
        if args.oracle:
            return 0
        if args.harness == "legacy":
            profile = resolve_provider(args.provider, ROOT)
            print(f"configured provider: {profile.name}; api_key={'yes' if profile.api_key else 'no'}")
            return 0 if profile.api_key else 2
        if args.harness == "sweagent":
            try:
                adapter = _create_workload_harness(
                    args.harness,
                    harness_root=args.harness_root,
                    model=args.model,
                    harness_command=args.harness_command,
                    harness_timeout_s=args.harness_timeout,
                    output_dir=_result_dir(
                        args.provider, args.oracle, args.harness, args.result_subdir
                    ),
                    max_turns=args.max_turns or 30,
                    reasoning_effort=args.reasoning_effort,
                )
                probe = {"available": True, **adapter.public_config}
            except Exception as exc:
                probe = {"available": False, "error": f"{type(exc).__name__}: {exc}"}
        else:
            adapter = create_external_harness(
                args.harness,
                root=args.harness_root,
                model=args.model,
                command=args.harness_command,
                timeout_s=args.harness_timeout,
            )
            probe = adapter.preflight(ROOT)
        print(json.dumps(probe, indent=2, sort_keys=True))
        return 0 if probe.get("available") else 2

    selected: List[tuple[str, str]] = []
    if args.tasks:
        for name in args.tasks:
            if name in swe_catalog:
                selected.append(("swe", name))
            elif name in tb_catalog:
                selected.append(("tb", name))
            elif ":" in name:
                suite, task = name.split(":", 1)
                selected.append((suite, task))
            else:
                raise SystemExit(f"unknown task {name}")
    else:
        if "swe" in suites:
            selected.extend(("swe", name) for name in swe_catalog)
        if "tb" in suites:
            selected.extend(("tb", name) for name in tb_catalog)

    if not args.oracle:
        if args.harness == "legacy":
            if not resolve_provider(args.provider, ROOT).api_key:
                print("missing provider API key; configure .agent.env or ~/.agenttx_llm.env", file=sys.stderr)
                return 2
        else:
            if args.harness == "sweagent":
                try:
                    adapter = _create_workload_harness(
                        args.harness,
                        harness_root=args.harness_root,
                        model=args.model,
                        harness_command=args.harness_command,
                        harness_timeout_s=args.harness_timeout,
                        output_dir=_result_dir(
                            args.provider, args.oracle, args.harness, args.result_subdir
                        ),
                        max_turns=args.max_turns or 30,
                        reasoning_effort=args.reasoning_effort,
                    )
                    probe = {"available": True, **adapter.public_config}
                except Exception as exc:
                    probe = {"available": False, "error": f"{type(exc).__name__}: {exc}"}
            else:
                adapter = create_external_harness(
                    args.harness,
                    root=args.harness_root,
                    model=args.model,
                    command=args.harness_command,
                    timeout_s=args.harness_timeout,
                )
                probe = adapter.preflight(ROOT)
            if not probe.get("available"):
                print(json.dumps(probe, indent=2, sort_keys=True), file=sys.stderr)
                return 2

    prefetch(
        args.cache_dir,
        sorted({suite for suite, _ in selected}),
        args.python,
        swe_catalog,
        tb_catalog,
        skip_environment_errors=args.skip_environment_errors,
    )
    out_dir_early = _result_dir(args.provider, args.oracle, args.harness, args.result_subdir)
    out_path_early = out_dir_early / "official_tasks_raw.csv"

    # Preserve every attempt for audit. Completed cells and coarse-policy
    # timeouts are terminal; failed causal attempts remain retryable.
    rows = _load_prior_rows(out_path_early)
    if args.skip_environment_errors:
        retained_rows = [row for row in rows if not row.get("environment_error")]
        if len(retained_rows) != len(rows):
            rows = retained_rows
            write_outputs(
                summarize(rows),
                args.provider,
                args.oracle,
                args.harness,
                args.result_subdir,
            )
    cleanup_eval_images = os.environ.get("AGENTTX_KEEP_EVAL_IMAGES", "1") == "0"
    done_keys = {
        (
            row.get("suite"),
            row.get("task"),
            row.get("mode"),
            str(row.get("repeat")),
            row.get("harness_backend", args.harness),
        )
        for row in rows
        if _terminal_attempt(row)
    }
    prev_eval_group = None
    for suite, name in selected:
        eval_group = swe.swe_eval_group(name) if suite == "swe" else None
        if eval_group != prev_eval_group:
            if prev_eval_group is not None and cleanup_eval_images:
                print(f"pruning docker layers after group {prev_eval_group}", flush=True)
                swe.prune_unused_eval_layers()
            prev_eval_group = eval_group
        for repeat in range(args.repeats):
            for mode in run_modes:
                key = (suite, name, mode, str(repeat), args.harness if not args.oracle else "oracle")
                if key in done_keys:
                    print(
                        f"skip existing harness={args.harness} suite={suite} task={name} "
                        f"mode={mode} repeat={repeat}",
                        flush=True,
                    )
                    continue
                print(
                    f"running harness={args.harness} suite={suite} task={name} "
                    f"mode={mode} repeat={repeat} oracle={args.oracle}",
                    flush=True,
                )
                row = run_once(
                    suite=suite,
                    task_name=name,
                    mode=mode,
                    repeat=repeat,
                    cache_root=args.cache_dir,
                    model=args.model,
                    provider=args.provider,
                    max_turns=args.max_turns,
                    trace_backend=args.trace_backend,
                    python=args.python,
                    oracle=args.oracle,
                    replay_docs=args.replay_docs,
                    harness_backend=args.harness,
                    harness_root=args.harness_root,
                    harness_command=args.harness_command,
                    harness_timeout_s=args.harness_timeout,
                    harness_output_dir=out_dir_early,
                    reasoning_effort=args.reasoning_effort,
                    bare_direct_controls=args.bare_direct_controls,
                    swe_tasks=swe_catalog,
                    tb_tasks=tb_catalog,
                )
                if args.skip_environment_errors and row.get("environment_error"):
                    print(
                        f"skipping environment error suite={suite} task={name} "
                        f"mode={mode} repeat={repeat}: {row.get('error', '')}",
                        flush=True,
                    )
                    continue
                rows.append(row)
                done_keys.add(key)
                summary_partial = summarize(rows)
                write_outputs(summary_partial, args.provider, args.oracle, args.harness, args.result_subdir)
        if suite == "swe" and cleanup_eval_images:
            print(f"removing docker image {swe.swe_eval_image(name)}", flush=True)
            swe.remove_eval_image(name)
    if prev_eval_group is not None and cleanup_eval_images:
        print(f"pruning docker layers after group {prev_eval_group}", flush=True)
        swe.prune_unused_eval_layers()
    out_dir = _result_dir(args.provider, args.oracle, args.harness, args.result_subdir)
    out_path = out_dir / "official_tasks_raw.csv"
    prior = []
    if out_path.exists():
        try:
            with out_path.open(newline="", encoding="utf-8") as handle:
                prior = list(csv.DictReader(handle))
            bool_fields = {
                "oracle",
                "independent_retained",
                "independent_unchanged",
                "documents_valid",
                "derived_removed",
                "host_leak_before_commit",
                "tests_ok",
                "finished",
                "finish_called",
                "committed",
                "success",
                "doc_replay_needed",
                "recovery_manifest_authoritative",
                "recovery_manifest_intact",
            }
            int_fields = {
                "repeat",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "doc_replay_prompt_tokens",
                "doc_replay_completion_tokens",
                "doc_replay_tokens",
                "doc_replay_tool_calls",
                "tool_calls",
                "model_calls",
                "recovery_ledger_steps",
                "retained_read_effects",
            }
            for row in prior:
                if not row.get("harness_backend"):
                    row["harness_backend"] = "oracle" if row.get("oracle") else "legacy"
                for field in bool_fields:
                    if field in row:
                        row[field] = str(row[field]).lower() == "true"
                for field in int_fields:
                    if field in row:
                        try:
                            row[field] = int(row[field])
                        except (TypeError, ValueError):
                            pass
        except (OSError, csv.Error):
            prior = []
    merged = {}
    for row in prior + rows:
        if not row.get("harness_backend"):
            row["harness_backend"] = "oracle" if row.get("oracle") else "legacy"
        key = (
            row.get("suite"),
            row.get("task"),
            row.get("mode"),
            str(row.get("repeat")),
            row.get("model"),
            row.get("harness_backend", "legacy"),
        )
        merged[key] = row
    rows = list(merged.values())
    summary = summarize(rows)
    write_outputs(summary, args.provider, args.oracle, args.harness, args.result_subdir)
    print(json.dumps(summary["task_summaries"], indent=2))
    # Oracle mode intentionally exercises policies that discard the injected
    # fault branch, so their application-level success predicate is expected
    # to be false.  The oracle command still produced a complete result set;
    # reserve a non-zero exit status for failed live-harness runs.
    return _result_exit_code(
        rows,
        oracle=args.oracle,
        allow_failures=args.allow_result_failures,
    )


if __name__ == "__main__":
    raise SystemExit(main())
