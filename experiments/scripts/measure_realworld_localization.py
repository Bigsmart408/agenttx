#!/usr/bin/env python3
"""Measure candidate trials on registered real tasks, stopping before model repair.

These are supplemental measurements, never reconstructed historical counts.
The original injection, transaction, and localizer execute unchanged. Only
adapter creation is blocked and the complete RecoveryPlan is saved before exit.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
DATA = Path("/mnt/data/agenttx-runtime")
PYTHON = "/home/bfq/.venvs/agenttx/bin/python"
SWE_REFERENCE = DATA / "results/codex-swe-full-recovery-20261104-r1/registration.json"
TB_REFERENCE = DATA / "results/tb-dual-native-full-20261030-r72-reviewed-flink-causal-timeout/registration.json"


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)
    if os.geteuid() == 0:
        os.chown(path, 1003, 1003)


def now():
    return datetime.now(timezone.utc).isoformat()


def prepare(cohort):
    from experiments.scripts.experiment_provenance import source_manifest

    cohort.mkdir(parents=True, exist_ok=False)
    sources = [ROOT / "review/realworld-results-20261106/analysis.json",
        DATA / "validation/swe-paper-comparison-20261019/phase-accounting.json",
        ROOT / "review/realworld-results-20261106/codex-swe-cell-audit.json",
        DATA / "validation/tb-category-effects-20261029/20261030T051423Z/source-progress.json",
        SWE_REFERENCE, TB_REFERENCE]
    cells = []
    for harness, path, key in (("sweagent", sources[1], "result"),
                                ("codex", sources[2], "result_path")):
        cells.extend(dict(benchmark="swe", harness=harness, task=r["task"],
            original_result=r[key]) for r in read(path) if r["policy"] == "causal")
    progress = read(sources[3])
    latest = {}
    for r in progress["tasks"] + progress["retry_tasks"]:
        if r["policy"] == "causal" and r.get("result_path"):
            latest[r["harness"], r["task"]] = r
    cells.extend(dict(benchmark="tb", harness=h, task=t,
        original_result=r["result_path"]) for (h, t), r in latest.items())
    assert Counter((c["benchmark"], c["harness"]) for c in cells) == {
        ("swe", "sweagent"): 300, ("swe", "codex"): 300,
        ("tb", "sweagent"): 241, ("tb", "codex"): 241}
    for c in cells:
        original = read(c["original_result"])
        loc = original.get("localization", original)
        assert loc["localization_validation"] == "passed"
        assert not any(k in loc for k in ("attempts", "localization_attempts",
            "localization_probe_count", "probe_count", "first_passing_trial", "total_trials"))
        c.update(key="/".join((c["benchmark"], c["harness"], c["task"])),
            state="pending", original_sha256=sha(c["original_result"]),
            historical_trials=None, original_injected=original["injected"])
    cells.sort(key=lambda c: (c["task"] not in ("psf__requests-1963", "hello-world"),
                             c["task"], c["benchmark"], c["harness"]))
    frozen = Path("/home/bfq/agenttx-workspace/agenttx-executions") / cohort.name / "project"
    frozen.mkdir(parents=True, exist_ok=False)
    manifest = source_manifest(ROOT)
    for entry in manifest["files"]:
        target = frozen / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / entry["path"], target)
    assert source_manifest(frozen)["sha256"] == manifest["sha256"]
    reference = read(TB_REFERENCE)
    inventory = read(reference["inventory"])
    evidence = {r["task"]: r["evidence"] for r in
        inventory["official_baseline_tasks"] + inventory.get("audited_task_baseline_tasks", [])}
    save(cohort / "registration.json", dict(protocol="agenttx.realworld-localization-only/v1",
        registered_at=now(), measurement="supplemental localization-only rerun",
        expected_cells=1082, external_model_calls=0, model_adapter_disabled=True,
        stopped_before_model_repair=True, historical_trial_counts_unavailable=1082,
        original_results_modified=False, prior_task_success_not_used_for_selection=True,
        frozen_source=str(frozen), source_sha256=manifest["sha256"],
        fault_seeds=dict(swe="20261018", tb="20261019"),
        max_candidates=8, max_probes=12, allow_multi_root=True, allow_fallback=False,
        sources={str(p): sha(p) for p in sources},
        swe_images=read(SWE_REFERENCE)["images"], tb_evidence=evidence))
    save(cohort / "source-manifest.json", manifest)
    save(cohort / "cells.json", cells)
    print(json.dumps(dict(cohort=str(cohort), cells=len(cells), frozen_source=str(frozen))))


class LocalizationFinished(BaseException):
    pass


class ModelForbidden(BaseException):
    pass


def instrument(output, benchmark, harness, task):
    from experiments.scripts import bench_official_tasks as bench
    from agenttx.harness import CodingAgentHarness

    original_policy = bench._auto_localize_policy
    original_close = CodingAgentHarness.close

    def close(self, destroy=True):
        return original_close(self, destroy=True)

    CodingAgentHarness.close = close

    def forbidden(*args, **kwargs):
        raise ModelForbidden("model adapter is disabled in localization-only measurements")

    # No adapter constructor or external agent loop is executed.
    bench._create_workload_harness = lambda *a, **k: type("NoModelAdapter", (), {
        "run": forbidden, "run_in_transaction": forbidden})()

    def localized(agent, injected, mode="causal"):
        tx = agent.harness.tx
        original_plan = tx.plan_recovery
        plans = []

        def capture(*args, **kwargs):
            plan = original_plan(*args, **kwargs)
            plans.append(asdict(plan))
            return plan

        tx.plan_recovery = capture
        tx.dump_ledger(output / "before-localization-ledger.json")
        targets, details = original_policy(agent, injected, mode)
        assert len(plans) == 1
        plan = plans[0]
        attempts = plan["attempts"]
        first = next((i + 1 for i, a in enumerate(attempts) if a["validation"] == "passed"), None)
        save(output / "localization-trials.json", dict(protocol="agenttx.realworld-localization-only/v1",
            measured_at=now(), benchmark=benchmark, harness=harness, task=task,
            measurement="supplemental localization-only rerun", external_model_calls=0,
            stopped_before_model_repair=True, injected=injected, localization=details,
            rollback_targets=targets, plan=plan, first_passing_trial=first,
            total_trials=len(attempts)))
        tx.dump_ledger(output / "after-localization-ledger.json")
        raise LocalizationFinished()

    bench._auto_localize_policy = localized


def swe_worker(task, harness, output):
    from experiments.scripts import bench_official_tasks as bench
    from experiments.scripts.run_swe_monitored_local import CACHE
    from experiments.workloads import swe_bench_suite as swe

    output.mkdir(parents=True, exist_ok=True)
    instrument(output, "swe", harness, task)
    try:
        row = bench.run_once(suite="swe", task_name=task, mode="causal", repeat=0,
            cache_root=CACHE, model="disabled", provider=None, max_turns=None,
            trace_backend="strace", python=sys.executable, oracle=False, harness_backend=harness,
            harness_root=None, harness_timeout_s=900, harness_output_dir=output,
            reasoning_effort="max", swe_tasks=swe.load_tasks(CACHE))
    except LocalizationFinished:
        return
    save(output / "incomplete-result.json", row)
    raise RuntimeError("localization was not reached: " + str(row.get("error")))


def tb_entry(arguments, inside=False):
    from experiments.scripts import bench_tb_image_native as tb

    if inside:
        task_file = Path(arguments[arguments.index("--task-file") + 1])
        task = read(task_file)["task_id"]
        harness = arguments[arguments.index("--harness") + 1]
        output = Path(arguments[arguments.index("--output-dir") + 1])
        instrument(output, "tb", harness, task)
    else:
        original_build = tb.build_command

        def build(*args, **kwargs):
            command = original_build(*args, **kwargs)
            index = command.index(str(ROOT / "experiments/scripts/bench_tb_image_native.py"))
            command[index:index + 2] = [str(Path(__file__).resolve()), "tb-inside", "inside"]
            return command

        tb.build_command = build
    sys.argv = [str(ROOT / "experiments/scripts/bench_tb_image_native.py"), *arguments]
    try:
        return tb.main()
    except LocalizationFinished:
        partial = read(output / "result.json")
        partial.update(completed=True, status="localization_measured_without_model",
                       success=None, external_model_calls=0)
        save(output / "result.json", partial)
        return 0


def run(cohort, workers, limit):
    from experiments.scripts import run_codex_swe_recovery as swe
    from experiments.scripts import run_tb_dual_local as tb
    from experiments.scripts.experiment_provenance import source_manifest

    registration = read(cohort / "registration.json")
    assert source_manifest(ROOT)["sha256"] == registration["source_sha256"]
    cells = read(cohort / "cells.json")
    pending = [c for c in cells if c["state"] == "pending"]
    if limit:
        pending = pending[:limit]

    def report():
        # Worker threads publish fields together with dict.update(); copy each
        # row before JSON serialization so concurrent publication cannot change
        # a dictionary's size while the encoder iterates over it.
        save(cohort / "cells.json", [dict(c) for c in cells])
        save(cohort / "progress.json", dict(updated_at=now(), counts=dict(Counter(c["state"] for c in cells)),
            groups={b + "/" + h: dict(Counter(c["state"] for c in cells
                if c["benchmark"] == b and c["harness"] == h)) for b in ("swe", "tb")
                for h in ("sweagent", "codex")}, external_model_calls=0))

    def cell(c):
        folder = cohort / "cells" / c["benchmark"] / c["harness"] / c["task"]
        folder.mkdir(parents=True, exist_ok=False)
        os.chown(folder, 1003, 1003)
        if c["benchmark"] == "swe":
            command = swe.worker_command(PYTHON, "-u", str(Path(__file__).resolve()), "swe-worker",
                "--task", c["task"], "--harness", c["harness"], "--output-dir", str(folder))
            result_path = folder / "localization-trials.json"
        else:
            command = tb.cell_command(ROOT, folder / "native", dict(c, policy="causal"),
                                      registration["tb_evidence"].get(c["task"]))
            index = command.index(str(ROOT / "experiments/scripts/bench_tb_image_native.py"))
            command[index:index + 1] = [str(Path(__file__).resolve()), "tb-host"]
            command = ["runuser", "-u", "bfq", "--", *command]
            result_path = folder / "native/causal/localization-trials.json"
        save(folder / "command.json", command)
        env = dict(os.environ, AGENTTX_FAULT_SEED=registration["fault_seeds"][c["benchmark"]],
                   AGENTTX_RECOVERY_CONTEXT="compact", AGENTTX_SKIP_ORPHAN_REAP="1")
        with (folder / "worker.log").open("w") as log:
            try:
                process = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=900)
                c["returncode"] = process.returncode
            except Exception as exc:
                c["error"] = repr(exc)
        assert sha(c["original_result"]) == c["original_sha256"]
        if result_path.exists():
            result = read(result_path)
            matches = result["injected"] == c["original_injected"]
            c.update(state="completed" if matches else "injection_mismatch", injection_matches_original=matches,
                result_path=str(result_path), result_sha256=sha(result_path),
                first_passing_trial=result["first_passing_trial"], total_trials=result["total_trials"])
        else:
            c["state"] = "measurement_error"
        c["finished_at"] = now()
        return c

    # Distinct task workspaces; no shared cache hydration or model requests.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for c in pending:
            c.update(state="queued", started_at=now())
            futures[pool.submit(cell, c)] = c
        report()
        for future in as_completed(futures):
            c = futures[future]
            try:
                future.result()
            except Exception:
                c.update(state="measurement_error", error=traceback.format_exc())
            report()
            print(json.dumps({k: c.get(k) for k in ("key", "state", "first_passing_trial", "total_trials", "error")}), flush=True)
    report()
    save(cohort / ("canary-execution.json" if limit else "execution.json"),
         dict(finished_at=now(), counts=dict(Counter(c["state"] for c in cells)), external_model_calls=0))


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("tb-host", "tb-inside"):
        return tb_entry(sys.argv[2:], inside=sys.argv[1] == "tb-inside")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "swe-worker"))
    parser.add_argument("--cohort", type=Path)
    parser.add_argument("--task")
    parser.add_argument("--harness", choices=("sweagent", "codex"))
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.action == "prepare":
        return prepare(args.cohort)
    if args.action == "swe-worker":
        return swe_worker(args.task, args.harness, args.output_dir)
    return run(args.cohort, args.workers, args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
