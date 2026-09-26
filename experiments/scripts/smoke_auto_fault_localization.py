#!/usr/bin/env python3
"""Run one SWE-bench case through verifier-guided fault localization."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from agenttx.harness import CodingAgentHarness  # noqa: E402
from experiments.workloads import swe_bench_suite as swe  # noqa: E402
from experiments.workloads.recovery_inject import (  # noqa: E402
    DocSpec,
    all_midcrash_docs,
    dag_is_valid,
    document_text_valid,
    read_recovery_documents,
)


FAULT_MARKER = "AGENTTX_INJECTED_FAULT"
RANDOM_INDEPENDENT_STEPS = 6


def _randomized_independent_docs(task, task_id: str, seed: int, crash_index: int = 0):
    digest = hashlib.sha256(
        f"{seed}:{task_id}:{crash_index}".encode("utf-8")
    ).digest()
    case_seed = int.from_bytes(digest[:8], "big")
    rng = random.Random(case_seed)
    # Preserve the standard recovery artifact contract (including the
    # synthetic post-crash note for one-document tasks), then randomize only
    # the operation order and crash insertion point.
    docs = list(all_midcrash_docs(task.docs()))
    docs.extend(
        DocSpec(
            f"recovery_notes/localization_probe_{index:02d}.md",
            f"probe{index:02d}",
            4,
        )
        for index in range(1, RANDOM_INDEPENDENT_STEPS + 1)
    )
    rng.shuffle(docs)
    fault_after_docs = rng.randint(1, len(docs) - 1)
    return tuple(docs), fault_after_docs, case_seed


def _crash_verifier(python: str, faulty_relpath: str) -> list[str]:
    program = (
        "from pathlib import Path; "
        f"text = Path({faulty_relpath!r}).read_text(encoding='utf-8'); "
        f"raise SystemExit({FAULT_MARKER!r} in text)"
    )
    return [python, "-c", program]


def run_smoke(
    *,
    task_id: str,
    cache_root: Path,
    python: str,
    keep_workdir: bool,
    seed: int,
    crashes: int = 1,
) -> dict:
    if crashes < 1:
        raise ValueError("crashes must be positive")
    tasks = swe.load_tasks(cache_root)
    task = tasks[task_id]
    cache, instance = swe.ensure_repo(task, cache_root)
    task_python = swe.ensure_venv(task, python, cache_root, instance)
    started = time.perf_counter()
    root = None
    workdir = None
    harness = None

    def start_trial():
        trial_root = Path(
            tempfile.mkdtemp(prefix=f"agenttx-auto-{task_id}-", dir="/tmp")
        )
        trial_workdir = trial_root / "workdir"
        trial_session = trial_root / "session"
        swe.copy_repo(cache, trial_workdir)
        swe.seed_task_workspace(trial_workdir, task, instance)
        workspace_tmp = trial_workdir / ".tmp"
        workspace_tmp.mkdir(parents=True, exist_ok=True)
        for name in ("TMPDIR", "TEMP", "TMP"):
            os.environ[name] = str(workspace_tmp)
        trial_harness = CodingAgentHarness(
            workdir=trial_workdir,
            session_dir=trial_session,
            trace_backend="strace",
            ephemeral_roots=(workspace_tmp,),
        )
        return trial_root, trial_workdir, trial_harness

    try:
        verifier_argv = _crash_verifier(task_python, task.faulty_relpath)
        crash_trials = []
        for crash_index in range(crashes):
            if harness is not None:
                harness.close(destroy=True)
                harness = None
            if root is not None:
                shutil.rmtree(root, ignore_errors=True)
            root, workdir, harness = start_trial()
            agent = SimpleNamespace(harness=harness)
            faulty_path = workdir / task.faulty_relpath
            randomized_docs, fault_after_docs, case_seed = _randomized_independent_docs(
                task, task_id, seed, crash_index
            )
            injected = swe.inject_task_trajectory(
                agent,
                task,
                instance,
                task_python,
                docs=randomized_docs,
                fault_after_docs=fault_after_docs,
            )
            if not dag_is_valid(injected):
                raise RuntimeError(
                    f"invalid injected recovery DAG at crash {crash_index + 1}: {injected}"
                )

            failed = harness.tx.run_verifier(
                verifier_argv,
                name=f"injected-crash-state-{crash_index + 1:02d}",
                read_paths=[str(faulty_path)],
                trace_reads=False,
            )
            if failed.returncode == 0:
                raise RuntimeError(
                    f"injected crash-state verifier unexpectedly passed at crash {crash_index + 1}"
                )

            # The localizer receives only the current failed verifier record and ledger.
            plan = harness.tx.plan_recovery(
                failed,
                max_candidates=8,
                max_probes=12,
                allow_multi_root=True,
                allow_fallback=False,
                verifier_timeout_s=300,
            )
            if not plan.verified:
                raise RuntimeError(
                    f"fault localization was inconclusive at crash {crash_index + 1}: {plan}"
                )
            rollback_targets = harness.tx.apply_recovery_plan(plan)

            recovered = harness.tx.run_verifier(
                verifier_argv,
                name=f"post-recovery-crash-state-{crash_index + 1:02d}",
                read_paths=[str(faulty_path)],
                trace_reads=False,
            )
            document_contents = read_recovery_documents(agent, randomized_docs)
            docs_retained = all(
                document_text_valid(
                    document_contents.get(spec.path, ""), spec.prefix, spec.lines
                )
                for spec in randomized_docs
            )
            derived_removed = not harness.tx.path_exists(
                workdir / "recovery_build" / "derived.txt"
            )
            crash_trials.append(
                {
                    "crash_number": crash_index + 1,
                    "case_seed": case_seed,
                    "fault_after_independent_steps": fault_after_docs,
                    "independent_operation_order": [
                        spec.path for spec in randomized_docs
                    ],
                    "injected_root_step": injected["root_step"],
                    "failed_verifier_step": failed.step_id,
                    "failed_verifier_rc": failed.returncode,
                    "recovery_mode": plan.mode,
                    "localized_root_steps": list(plan.root_step_ids),
                    "localization_matches_injected_root": list(plan.root_step_ids)
                    == [injected["root_step"]],
                    "rollback_targets": rollback_targets,
                    "counterfactual_attempts": [
                        {
                            "mode": attempt.mode,
                            "roots": list(attempt.root_step_ids),
                            "targets": list(attempt.rollback_targets),
                            "validation": attempt.validation,
                            "verifier_returncode": attempt.verifier_returncode,
                            "error": attempt.error,
                        }
                        for attempt in plan.attempts
                    ],
                    "post_recovery_verifier_rc": recovered.returncode,
                    "independent_documents_retained": docs_retained,
                    "derived_fault_artifact_removed": derived_removed,
                }
            )

        assert workdir is not None
        assert harness is not None
        swe.apply_oracle(agent, instance)
        # The host-side task command is only a convenience verifier and is not
        # portable across all SWE-Bench repositories. Commit the recovered
        # transaction, then use the official Docker evaluator as the verdict.
        harness.tx.commit()
        official = swe.verify(
            workdir,
            task,
            instance,
            task_python,
            require_recovery_artifacts=True,
        )

        result = {
            "task": task_id,
            "random_seed": seed,
            "crashes_requested": crashes,
            "crashes_completed": len(crash_trials),
            "crash_trials": crash_trials,
            "all_localizations_match": all(
                trial["localization_matches_injected_root"] for trial in crash_trials
            ),
            "all_recoveries_pass": all(
                trial["post_recovery_verifier_rc"] == 0
                and trial["independent_documents_retained"]
                and trial["derived_fault_artifact_removed"]
                for trial in crash_trials
            ),
            "official_tests_ok": bool(official["tests_ok"]),
            "official_tests_rc": official["tests_rc"],
            "official_verifier_backend": official.get("verifier_backend"),
            "official_documents_valid": bool(official["documents_valid"]),
            "official_derived_removed": bool(official["derived_removed"]),
            "elapsed_s": round(time.perf_counter() - started, 3),
            "workdir": str(workdir) if keep_workdir else None,
        }
        result["success"] = all(
            (
                result["crashes_completed"] == result["crashes_requested"],
                result["all_localizations_match"],
                result["all_recoveries_pass"],
                result["official_tests_ok"],
                result["official_documents_valid"],
                result["official_derived_removed"],
            )
        )
        return result
    finally:
        if harness is not None:
            harness.close(destroy=True)
        if root is not None and not keep_workdir:
            shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", default="pytest-dev__pytest-8906")
    parser.add_argument(
        "--cache-dir", type=Path, default=ROOT / "experiments" / "cache"
    )
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--keep-workdir", action="store_true")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--crashes", type=int, default=1)
    args = parser.parse_args()

    result = run_smoke(
        task_id=args.task,
        cache_root=args.cache_dir,
        python=args.python,
        keep_workdir=args.keep_workdir,
        seed=args.seed,
        crashes=args.crashes,
    )
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
