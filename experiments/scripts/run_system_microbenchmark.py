#!/usr/bin/env python3
"""Frozen, paired canonical-400 campaign; two policies and one native harness."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
PYTHON = Path("/home/bfq/.venvs/agenttx/bin/python")
PROFILE = Path("/home/bfq/.config/agenttx/experiment-20261017")
SWE = Path("/home/bfq/agenttx-workspace/agenttx-deps/SWE-agent-3ea751c087f32b16e039a2233dd6eefecef325d5")
DOCKER = ["docker", "--host", os.environ.get("AGENTTX_DOCKER_HOST", "unix:///run/agenttx-docker/docker.sock")]
POLICIES = ("causal", "temporal_checkpoint")
POLICY_CHOICES = ("causal", "temporal_checkpoint", "crash_no_rollback",
                  "turn_checkpoint", "whole_branch_abort", "causal_workspace_only")


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n")
    tmp.replace(path)


def storage_free_gib(path):
    path = Path(path)
    while True:
        try:
            return shutil.disk_usage(path).free / 1024**3
        except PermissionError:
            if path == path.parent:
                raise
            path = path.parent


def prepare(args):
    from experiments.scripts.bench_external_recovery_artifacts import motivation_400_cases, MOTIVATION_SOURCES
    cases = motivation_400_cases()
    policies = tuple(args.policies)
    if len(policies) != len(set(policies)):
        raise ValueError("duplicate policies")
    reference = None
    if args.reference_cohort:
        reference = json.loads((args.reference_cohort / "registration.json").read_text())
        old_inventory = json.loads((args.reference_cohort / "source-manifest.json").read_text())
        reference_source = Path(reference["frozen_source"])
        if any(digest(reference_source / x["path"]) != x["sha256"] for x in old_inventory["files"]):
            raise RuntimeError("reference source changed")
        if any(digest(PROFILE / k) != v for k, v in reference["profile_sha256"].items()):
            raise RuntimeError("reference model profile changed")
        prior_cells = json.loads((args.reference_cohort / "cells.json").read_text())["main"]
        if {c.plan.trace_id:c.plan.manifest_hash for c in cases} != {c["trace_id"]:c["manifest_hash"] for c in prior_cells}:
            raise RuntimeError("canonical manifests differ from reference")
    cohort = args.cohort.resolve()
    cohort.mkdir(mode=0o700, parents=True, exist_ok=False)
    source = Path("/home/bfq/agenttx-workspace/agenttx-executions") / cohort.name / "project"
    source.mkdir(parents=True, exist_ok=False)
    if reference:
        shutil.copytree(reference_source, source, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copy2(Path(__file__), source / "experiments/scripts/run_system_microbenchmark.py")
        if args.include_context_ablation:
            shutil.copy2(ROOT / "experiments/scripts/bench_runtime_context_ablation.py",
                         source / "experiments/scripts/bench_runtime_context_ablation.py")
        differences = [x["path"] for x in old_inventory["files"] if digest(source/x["path"]) != x["sha256"]]
        if differences != ["experiments/scripts/run_system_microbenchmark.py"]:
            raise RuntimeError(f"unexpected changes to reference runtime: {differences}")
        save(cohort / "reference-source-audit.json", dict(reference=str(args.reference_cohort),
            unchanged_reference_files=len(old_inventory["files"])-1, changed_files=differences,
            ablation_entrypoint="experiments/scripts/bench_runtime_context_ablation.py" if args.include_context_ablation else None))
    else:
        for name in ("src", "scripts", "experiments/scripts", "experiments/workloads", "third_party/try"):
            shutil.copytree(ROOT / name, source / name,
                            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
        for p in (ROOT / "experiments").glob("*.py"):
            shutil.copy2(p, source / "experiments" / p.name)
        for name in ("environment.yml", "requirements-agent.txt"):
            if (ROOT / name).exists():
                shutil.copy2(ROOT / name, source / name)
        for p in MOTIVATION_SOURCES.values():
            target = source / p.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
    inventory = [{"path": str(p.relative_to(source)), "sha256": digest(p)}
                 for p in sorted(source.rglob("*")) if p.is_file() and not p.is_symlink()]
    source_hash = hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()
    save(cohort / "source-manifest.json", dict(sha256=source_hash, files=inventory))
    groups = {}
    for case in cases:
        groups.setdefault(case.suite_slice, []).append(case)
    gate_cases = []
    for group in groups.values():
        gate_cases.extend(group[i] for i in (0, len(group)//3, 2*len(group)//3, len(group)-1))

    def cell(case, policy, repeat, phase):
        row = dict(key=f"{case.plan.trace_id}/{policy}/repeat-{repeat:02d}",
                    trace_id=case.plan.trace_id, manifest_hash=case.plan.manifest_hash,
                    family=case.suite_slice, operation_count=len(case.plan.steps),
                    policy=policy, repeat=repeat, harness="sweagent", phase=phase,
                    state="pending")
        if phase == "runtime_gate":
            truth = set(case.plan.oracle_closure)
            failed_turns = {s.turn_index for s in case.plan.steps if s.index in truth}
            row["expected_original_rollback"] = (sorted(truth) if policy in {"causal", "causal_workspace_only"}
                else [] if policy == "crash_no_rollback" else list(range(len(case.plan.steps))) if policy == "whole_branch_abort"
                else [s.index for s in case.plan.steps if s.turn_index in failed_turns] if policy == "turn_checkpoint"
                else list(range(min(truth), len(case.plan.steps))))
        return row
    cells = [cell(c, p, repeat, "main") for repeat in range(3)
             for c in cases for p in policies]
    gates = [cell(c, p, 0, "runtime_gate") for c in gate_cases for p in policies]
    canary = [cell(cases[0], p, 0, "model_canary") for p in policies]
    if args.include_context_ablation:
        if not reference:
            raise ValueError("context ablation requires a completed reference")
        cells = [cell(c, "causal_workspace_only", repeat, "main") for repeat in range(3) for c in gate_cases] + cells
        gates += [cell(c, "causal_workspace_only", 0, "runtime_gate") for c in gate_cases]
        canary += [cell(gate_cases[0], "causal_workspace_only", 0, "model_canary")]
    image = subprocess.check_output(DOCKER + ["image", "inspect", "agenttx-system-micro:20261030",
                                    "--format", "{{.Id}}"], text=True).strip()
    if reference and image != reference["image_id"]:
        raise RuntimeError("reference image changed")
    prefixes = json.loads(subprocess.check_output([str(PYTHON), "-c",
        "import sys,json;print(json.dumps([sys.prefix,sys.base_prefix]))"], text=True))
    alias = Path(os.readlink(PYTHON))
    if not alias.is_absolute():
        alias = PYTHON.parent / alias
    registration = dict(protocol="agenttx.canonical400-paired-native/v2", registered_at=now(),
        cohort=str(cohort), frozen_source=str(source), source_sha256=source_hash,
        model="gpt-5.6-luna", reasoning_effort="max", harnesses=["sweagent"],
        policies=list(policies) + (["causal_workspace_only"] if args.include_context_ablation else []),
        contexts={p:"full" for p in (*policies, "causal_workspace_only")},
        repeats=3, manifest_count=400, expected_cells=len(cells), workers=args.workers,
        runtime_gate_cells=len(gates), model_canary_cells=len(canary),
        model_wall_s=3600, token_budget=8_000_000, model_call_cap=None,
        token_budget_enforcement="Before every request: reserve serialized input bytes plus 16384, cap completion; record actual API usage and flag any overrun.",
        max_probes="2 * operation_count + 8", image_id=image,
        python=str(PYTHON), python_mounts=sorted(set(prefixes + [str(alias.parent.parent.absolute())])),
        profile_sha256={name:digest(PROFILE/name) for name in ("config.toml", "sweagent.toml", "auth.json")},
        uid=os.getuid(), gid=os.getgid(), ordinary_retries=0,
        prior_results_inherited=False, pilot_results_fill_main=False,
        scope_change="Supplementary controls and a 12-case workspace-only context ablation." if reference else "Paired canonical recovery campaign.",
        comparison_reference=str(args.reference_cohort) if reference else None,
        ablation_cases=[c.plan.trace_id for c in gate_cases] if args.include_context_ablation else [],
        expected_cells_by_policy=dict(Counter(c["policy"] for c in cells)),
        ablation_outcomes="Report task completion and recovery_contract_passed separately; stale context is intentional.",
        formal_eligible=False,
        qualification_note="New reduced scope. Qualification requires completed canonical coverage, paired input/prompt audits and complete bounded API accounting; not asserted at launch.")
    save(cohort / "registration.json", registration)
    save(cohort / "cells.json", dict(runtime_gate=gates, model_canary=canary, main=cells))
    (cohort / "logs").mkdir()
    (cohort / "events.jsonl").touch()
    print(json.dumps(dict(cohort=str(cohort), frozen_source=str(source), cells=len(cells),
                         gates=len(gates), image=image)), flush=True)


def container_worker(args):
    # Runs as root in the dedicated container. Private values never enter logs.
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    from experiments.scripts.experiment_credentials import redact_credentials
    auth = json.loads((PROFILE / "auth.json").read_text())
    secrets = {auth.get("OPENAI_API_KEY", ""), os.environ.get("OPENAI_API_KEY", "")}
    secrets.discard("")
    entrypoint = "bench_runtime_context_ablation.py" if args.policy == "causal_workspace_only" else "bench_runtime_recovery_system.py"
    command = [sys.executable, "-u", str(ROOT / "experiments/scripts" / entrypoint),
        "--trace-id", args.trace_id, "--output-dir", str(args.output), "--harness", "sweagent",
        "--policies", args.policy, "--profile-dir", str(PROFILE),
        "--swe-agent-root", str(SWE), "--timeout", "3600", "--token-budget", "8000000",
        "--max-probes", str(2 * args.operations + 8)]
    if args.runtime_only:
        command += ["--runtime-only"]
    try:
        with args.log.open("w") as log:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, bufsize=1)
            for line in process.stdout:
                log.write(redact_credentials(line, secrets))
                log.flush()
            code = process.wait()
    finally:
        if args.output.exists():
            for directory, dirs, files in os.walk(args.output, followlinks=False):
                for p in [Path(directory), *(Path(directory)/x for x in dirs+files)]:
                    try:
                        os.chown(p, int(os.environ["AGENTTX_RESULT_UID"]),
                                 int(os.environ["AGENTTX_RESULT_GID"]), follow_symlinks=False)
                    except FileNotFoundError:
                        pass
    return code


def execute(reg, cell):
    cohort = Path(reg["cohort"])
    output = cohort / cell["phase"] / cell["key"]
    output.parent.mkdir(parents=True, exist_ok=True)
    name = "atx-micro-" + hashlib.sha256((str(cohort)+cell["phase"]+cell["key"]).encode()).hexdigest()[:18]
    log = cohort / "logs" / (name + ".log")
    container_output = Path("/results") / output.relative_to(cohort)
    container_log = Path("/results") / log.relative_to(cohort)
    source = Path(reg["frozen_source"])
    command = DOCKER + ["run", "--rm", "--pull", "never", "--privileged", "--name", name,
        "--network", "host", "--tmpfs", "/root:exec", "--tmpfs", "/tmp:exec",
        "--tmpfs", "/sys", "--tmpfs", "/run", "--workdir", str(source)]
    readonly = {source, PROFILE, SWE, *map(Path, reg["python_mounts"])}
    cache = Path("/mnt/data/agenttx-runtime/tiktoken-cache")
    if cache.is_dir():
        readonly.add(cache)
    for p in sorted(readonly):
        command += ["--mount", f"type=bind,src={p},dst={p},readonly"]
    command += ["--mount", f"type=bind,src={cohort},dst=/results"]
    codex = Path("/home/bfq/.local/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex")
    for p in (codex, codex.parent / "codex-code-mode-host"):
        command += ["--mount", f"type=bind,src={p},dst=/usr/local/bin/{p.name},readonly"]
    env = dict(PYTHONPATH=f"{source}/src:{source}", PYTHONDONTWRITEBYTECODE="1",
        AGENTTX_MOTIVATION_BWRAP="1", AGENTTX_NO_PROXY="1", AGENTTX_SKIP_ORPHAN_REAP="1",
        AGENTTX_WORKER_PYTHON=reg["python"], AGENTTX_TRACE_SPOOL_ROOT="/var/tmp",
        AGENTTX_RESULT_UID=str(reg["uid"]), AGENTTX_RESULT_GID=str(reg["gid"]),
        CODEX_BIN="/usr/local/bin/codex", SWE_AGENT_LOG_STREAM_LEVEL="DEBUG",
        TIKTOKEN_CACHE_DIR=str(cache), TRY_BIND_READONLY=":".join(map(str, readonly)))
    for key, value in env.items():
        command += ["--env", f"{key}={value}"]
    command += ["--entrypoint", reg["python"], reg["image_id"], "-u",
        str(source / "experiments/scripts/run_system_microbenchmark.py"), "container-worker",
        "--trace-id", cell["trace_id"], "--policy", cell["policy"], "--output", str(container_output),
        "--log", str(container_log), "--operations", str(cell["operation_count"])]
    if cell["phase"] == "runtime_gate":
        command += ["--runtime-only"]
    begun = time.monotonic()
    try:
        run = subprocess.run(command, capture_output=True, text=True,
            timeout=4200 + 30 * (2*cell["operation_count"]+8))
        result_path = output / cell["policy"] / "result.json"
        if not result_path.exists():
            return dict(state="infrastructure_error", error=run.stderr[-2000:],
                        returncode=run.returncode, log=str(log), wall_s=time.monotonic()-begun)
        row = json.loads(result_path.read_text())
        infrastructure = row.get("status") == "error" or row.get("model_returncode") == 127
        usage = row.get("api_usage", {})
        if cell["phase"] != "runtime_gate" and (not usage.get("complete") or usage.get("budget_exceeded")):
            infrastructure = True
        if cell["phase"] == "runtime_gate" and cell["policy"] == "causal" and not row.get("exact_selective_state"):
            infrastructure = True
        if cell["phase"] == "runtime_gate" and "expected_original_rollback" in cell:
            actual = sorted(i for i in row.get("rollback_ids", []) if i < cell["operation_count"])
            expected_context = cell["policy"] != "causal_workspace_only"
            if (actual != cell["expected_original_rollback"] or
                row.get("conversation_matches_rollback") != expected_context or
                not row.get("retained_bytes_unchanged")):
                infrastructure = True
        return dict(state="infrastructure_error" if infrastructure else "passed" if row.get("completed") else "task_failed",
                    result=str(result_path), log=str(log), wall_s=time.monotonic()-begun,
                    tokens=row.get("total_tokens"), localization_match=row.get("localization_matches_injected_roots"),
                    error=row.get("error"), returncode=run.returncode)
    except Exception as exc:
        subprocess.run(DOCKER + ["rm", "-f", name], capture_output=True, timeout=30)
        return dict(state="infrastructure_error", error=f"{type(exc).__name__}: {exc}",
                    log=str(log), wall_s=time.monotonic()-begun)


def run_campaign(args):
    cohort = args.cohort.resolve()
    reg = json.loads((cohort / "registration.json").read_text())
    stages = json.loads((cohort / "cells.json").read_text())
    docker_root = subprocess.check_output(DOCKER + ["info", "--format", "{{.DockerRootDir}}"], text=True).strip()
    storage = dict(results=storage_free_gib(cohort), docker=storage_free_gib(docker_root))
    started = now()
    stop = None
    def progress(phase):
        counts = {key:dict(Counter(c["state"] for c in values)) for key, values in stages.items()}
        completed = [c for c in stages["main"] if c["state"] in {"passed", "task_failed"}]
        total_wall = sum(c.get("wall_s", 0) for c in completed)
        rate_seconds = total_wall / len(completed) if completed else None
        save(cohort / "progress.json", dict(updated_at=now(), started_at=started, phase=phase,
            counts=counts, stop_reason=stop, workers=reg["workers"],
            storage_free_gib=storage, docker_data_root=docker_root,
            known_main_tokens=sum(c.get("tokens") or 0 for c in stages["main"]),
            measured_mean_cell_wall_s=rate_seconds,
            approximate_remaining_hours=(len(stages["main"])-len(completed))*rate_seconds/reg["workers"]/3600 if rate_seconds else None))
        save(cohort / "cells.json", stages)
    for phase in ("runtime_gate", "model_canary", "main"):
        pending = iter(c for c in stages[phase] if c["state"] == "pending")
        futures = {}
        with ThreadPoolExecutor(max_workers=reg["workers"]) as pool:
            while True:
                storage = dict(results=storage_free_gib(cohort), docker=storage_free_gib(docker_root))
                low_disks = [name for name, available in storage.items() if available < 100]
                if not stop and low_disks:
                    stop = "free_disk_below_100_GiB:" + ",".join(low_disks)
                if not stop and any(digest(PROFILE/k) != v for k,v in reg["profile_sha256"].items()):
                    stop = "private_profile_changed"
                while not stop and len(futures) < reg["workers"]:
                    try:
                        cell = next(pending)
                    except StopIteration:
                        break
                    cell.update(state="running", started_at=now())
                    futures[pool.submit(execute, reg, cell)] = cell
                progress(phase)
                if not futures:
                    break
                done, _ = wait(futures, timeout=20, return_when=FIRST_COMPLETED)
                for future in done:
                    cell = futures.pop(future)
                    cell.update(future.result(), finished_at=now())
                    with (cohort / "events.jsonl").open("a") as handle:
                        handle.write(json.dumps(cell) + "\n")
                    print(json.dumps(dict(phase=phase,key=cell["key"],state=cell["state"],
                                          wall_s=cell.get("wall_s"),error=cell.get("error"))),flush=True)
                    if cell["state"] == "infrastructure_error":
                        stop = "infrastructure_error_review_required"
                    if phase == "runtime_gate" and cell["state"] != "passed":
                        stop = "runtime_gate_failed"
            progress(phase)
        if stop:
            break
    progress("stopped" if stop else "finished")
    save(cohort / "execution.json", dict(finished_at=now(), stop_reason=stop))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("prepare", "run"):
        p = sub.add_parser(action)
        p.add_argument("--cohort", type=Path, required=True)
        p.add_argument("--workers", type=int, default=8)
        p.add_argument("--policies", nargs="+", choices=POLICY_CHOICES, default=list(POLICIES))
        p.add_argument("--reference-cohort", type=Path)
        p.add_argument("--include-context-ablation", action="store_true")
    p = sub.add_parser("container-worker")
    p.add_argument("--trace-id", required=True)
    p.add_argument("--policy", choices=POLICY_CHOICES, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--log", type=Path, required=True)
    p.add_argument("--operations", type=int, required=True)
    p.add_argument("--runtime-only", action="store_true")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args)
    elif args.action == "run":
        run_campaign(args)
    else:
        return container_worker(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
