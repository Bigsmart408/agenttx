#!/usr/bin/env python3
"""Artifact-native recovery comparison on shared multi-turn manifests."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from agenttx.runtime import AgentTX  # noqa: E402
from experiments.workloads.intra_turn_recovery_dag import (  # noqa: E402
    IntraTurnPlan,
    build_multi_turn_plan,
    build_multiturn_controlled_waste_plan,
)


SCHEMA = "agenttx.external-recovery-artifacts/v1"
BACKENDS = ("agenttx", "session_try", "yolofs", "branchfs", "waypoint")
MOUNT_BACKEND_LOCK = threading.Lock()
WAYPOINT_LOCK = threading.Lock()
WAYPOINT_IMAGE = "agenttx-waypoint-eval:20260922"
WAYPOINT_EVAL_ROOT = Path("/home/bfq/.cache/agenttx-waypoint-eval")
MOTIVATION_SOURCES = {
    "mb2_task_length": ROOT
    / "experiments/results/motivation/controlled_case/controlled_recovery_raw.csv",
    "mb3_fault_position": ROOT
    / "experiments/results/motivation/controlled_case/controlled_position_raw.csv",
    "mb4_dependency": ROOT
    / "experiments/results/motivation/microbenchmark/motivation_recovery_raw.csv",
}


class ArtifactFailure(RuntimeError):
    def __init__(self, message: str, *, classification: str = "artifact") -> None:
        super().__init__(message)
        self.classification = classification


@dataclass(frozen=True)
class Timing:
    setup_ms: float
    execute_ms: float
    recovery_ms: float

    @property
    def total_ms(self) -> float:
        return self.setup_ms + self.execute_ms + self.recovery_ms


@dataclass(frozen=True)
class BackendOutcome:
    present_ids: frozenset[int]
    timing: Timing
    native_boundary: str
    artifact_commit: str


@dataclass(frozen=True)
class BenchmarkCase:
    suite_slice: str
    plan: IntraTurnPlan


def _source_manifest_hashes(path: Path) -> dict[str, str]:
    manifests: dict[str, str] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            trace_id = str(row["trace_id"])
            manifest_hash = str(row["manifest_hash"])
            previous = manifests.setdefault(trace_id, manifest_hash)
            if previous != manifest_hash:
                raise ArtifactFailure(
                    f"source has inconsistent hashes for {trace_id}: "
                    f"{previous} != {manifest_hash}"
                )
    return manifests


def motivation_400_cases() -> list[BenchmarkCase]:
    cases: list[BenchmarkCase] = []
    for turn_count in range(1, 11):
        for repeat in range(10):
            cases.append(
                BenchmarkCase(
                    "mb2_task_length",
                    build_multiturn_controlled_waste_plan(
                        turn_count, turn_width=16, seed=repeat
                    ),
                )
            )
    for fault_position in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        for repeat in range(10):
            cases.append(
                BenchmarkCase(
                    "mb3_fault_position",
                    build_multiturn_controlled_waste_plan(
                        10,
                        turn_width=16,
                        seed=repeat,
                        fault_position=fault_position,
                    ),
                )
            )
    for dependency_index in range(21):
        dependency_value = dependency_index / 20.0
        for repeat in range(10):
            cases.append(
                BenchmarkCase(
                    "mb4_dependency",
                    build_multi_turn_plan(
                        5,
                        16,
                        fault_position=0.25,
                        dependency_value=dependency_value,
                        shape="layered",
                        interleaving="alternating",
                        seed=repeat,
                    ),
                )
            )
    if len(cases) != 400:
        raise AssertionError(len(cases))
    by_slice: dict[str, list[BenchmarkCase]] = {}
    for case in cases:
        by_slice.setdefault(case.suite_slice, []).append(case)
    for suite_slice, source_path in MOTIVATION_SOURCES.items():
        expected = _source_manifest_hashes(source_path)
        actual = {
            case.plan.trace_id: case.plan.manifest_hash
            for case in by_slice[suite_slice]
        }
        if actual != expected:
            missing = sorted(set(expected) - set(actual))[:3]
            extra = sorted(set(actual) - set(expected))[:3]
            mismatched = sorted(
                trace_id
                for trace_id in set(actual) & set(expected)
                if actual[trace_id] != expected[trace_id]
            )[:3]
            raise ArtifactFailure(
                f"motivation manifest mismatch for {suite_slice}: "
                f"missing={missing}, extra={extra}, hash_mismatch={mismatched}"
            )
    return cases


def run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: float = 120.0,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            [str(part) for part in command],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ArtifactFailure(f"{type(exc).__name__}: {exc}") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "no output").strip()
        raise ArtifactFailure(
            f"command failed ({result.returncode}): {' '.join(command)}: {detail}"
        )
    return result


def git_commit(path: Path) -> str:
    result = run_command(["git", "rev-parse", "HEAD"], cwd=path)
    return result.stdout.strip()


def seed_workspace(workspace: Path, plan: IntraTurnPlan) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    for step in plan.steps:
        (workspace / step.relative_path).write_text("BASE\n", encoding="utf-8")


def execute_steps(workspace: Path, plan: IntraTurnPlan) -> None:
    for step in plan.steps:
        run_command(step.command(plan.steps), cwd=workspace)


def present_ids(workspace: Path, plan: IntraTurnPlan) -> frozenset[int]:
    present: set[int] = set()
    for step in plan.steps:
        path = workspace / step.relative_path
        try:
            if path.read_text(encoding="utf-8").startswith("artifact-"):
                present.add(step.index)
        except FileNotFoundError:
            continue
    return frozenset(present)


def remove_tree(path: Path) -> None:
    if not path.exists():
        return
    subprocess.run(
        ["chmod", "-R", "u+rwX", str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    shutil.rmtree(path, ignore_errors=True)


def run_agenttx(case_root: Path, plan: IntraTurnPlan) -> BackendOutcome:
    workspace = case_root / "workspace"
    session = case_root / "session"
    seed_workspace(workspace, plan)
    started = time.perf_counter()
    tx = AgentTX.begin(workdir=workspace, session_dir=session, trace_reads=False)
    setup_ms = (time.perf_counter() - started) * 1000.0
    try:
        started = time.perf_counter()
        for step in plan.steps:
            reads = [
                str((workspace / plan.steps[parent].relative_path).resolve())
                for parent in step.parent_indices
            ]
            record = tx.run_tool(step.name, step.command(plan.steps), extra_reads=reads)
            if record.returncode != 0:
                raise ArtifactFailure(record.stderr or f"step {step.index} failed")
        execute_ms = (time.perf_counter() - started) * 1000.0

        started = time.perf_counter()
        removed = frozenset(tx.rollback_causal(plan.fault_step_id))
        recovery_ms = (time.perf_counter() - started) * 1000.0
        if removed != frozenset(plan.oracle_closure):
            raise ArtifactFailure(
                f"AgentTX removed {sorted(removed)}, expected {list(plan.oracle_closure)}"
            )
        present: set[int] = set()
        for step in plan.steps:
            entry = tx._overlay_entry((workspace / step.relative_path).resolve())
            if entry is not None and entry.read_text(encoding="utf-8").startswith(
                "artifact-"
            ):
                present.add(step.index)
        return BackendOutcome(
            present_ids=frozenset(present),
            timing=Timing(setup_ms, execute_ms, recovery_ms),
            native_boundary="dependency_selected_steps",
            artifact_commit="local-worktree",
        )
    finally:
        tx.close(destroy=True)


def run_session_try(case_root: Path, plan: IntraTurnPlan) -> BackendOutcome:
    workspace = case_root / "workspace"
    sandbox = case_root / "try-session"
    seed_workspace(workspace, plan)
    sandbox.mkdir()
    try_bin = ROOT / "scripts" / "try-wrapper.sh"
    workload = case_root / "run-workload.sh"
    bodies = [step.command(plan.steps)[2] for step in plan.steps]
    workload.write_text(
        "#!/bin/bash\nset -euo pipefail\n" + "\n".join(bodies) + "\n",
        encoding="utf-8",
    )
    workload.chmod(0o700)

    # try models one speculative session as one effect set. Execute the entire
    # multi-turn manifest inside that native boundary, then discard the set.
    started = time.perf_counter()
    setup_ms = 0.0
    run_command(
        [str(try_bin), "-N", str(sandbox), "--", str(workload)],
        cwd=workspace,
        timeout=300.0,
    )
    execute_ms = (time.perf_counter() - started) * 1000.0

    started = time.perf_counter()
    remove_tree(sandbox)
    recovery_ms = (time.perf_counter() - started) * 1000.0
    return BackendOutcome(
        present_ids=present_ids(workspace, plan),
        timing=Timing(setup_ms, execute_ms, recovery_ms),
        native_boundary="session_effect_set",
        artifact_commit=git_commit(ROOT / "third_party" / "try"),
    )


def yolo_mount_root(project: Path) -> Path:
    return project / ".yolofs" / "mnt" / project.relative_to("/")


def parse_snapshot_id(output: str) -> int:
    match = re.search(r"snapshot\s+(\d+)", output)
    if not match:
        raise ArtifactFailure(f"could not parse YoloFS snapshot id: {output!r}")
    return int(match.group(1))


def run_yolofs(case_root: Path, plan: IntraTurnPlan) -> BackendOutcome:
    project = case_root / "project"
    seed_workspace(project, plan)
    (project / "yolofs.toml").write_text(
        "permission = false\nstaging = true\nauto_snapshot = false\n",
        encoding="utf-8",
    )
    mounted = False
    try:
        with MOUNT_BACKEND_LOCK:
            started = time.perf_counter()
            run_command(["yolo", "mount"], cwd=project)
            mounted = True
            mount_root = yolo_mount_root(project)
            if not mount_root.is_dir():
                raise ArtifactFailure(f"YoloFS mount view missing: {mount_root}")
            setup_ms = (time.perf_counter() - started) * 1000.0

            started = time.perf_counter()
            turn_snapshots: dict[int, int] = {}
            current_turn = -1
            for step in plan.steps:
                if step.turn_index != current_turn:
                    current_turn = step.turn_index
                    snapshot = run_command(
                        ["yolo", "snapshot", f"before-turn-{current_turn}"],
                        cwd=project,
                    )
                    turn_snapshots[current_turn] = parse_snapshot_id(
                        snapshot.stdout + snapshot.stderr
                    )
                run_command(step.command(plan.steps), cwd=mount_root)
            execute_ms = (time.perf_counter() - started) * 1000.0

            affected_turn = plan.steps[plan.fault_step_id].turn_index
            started = time.perf_counter()
            run_command(
                ["yolo", "travel", str(turn_snapshots[affected_turn])], cwd=project
            )
            recovery_ms = (time.perf_counter() - started) * 1000.0
            recovered = present_ids(mount_root, plan)
            return BackendOutcome(
                present_ids=recovered,
                timing=Timing(setup_ms, execute_ms, recovery_ms),
                native_boundary="turn_snapshot",
                artifact_commit=git_commit(ROOT / "third_party" / "yolofs"),
            )
    finally:
        if mounted:
            run_command(["yolo", "abort", "--force"], cwd=project, check=False)
            run_command(["yolo", "unmount"], cwd=project, check=False)


def run_branchfs(case_root: Path, plan: IntraTurnPlan) -> BackendOutcome:
    with MOUNT_BACKEND_LOCK:
        return _run_branchfs_locked(case_root, plan)


def _run_branchfs_locked(case_root: Path, plan: IntraTurnPlan) -> BackendOutcome:
    base = case_root / "base"
    storage = case_root / "storage"
    mountpoint = case_root / "mnt"
    seed_workspace(base, plan)
    storage.mkdir()
    mountpoint.mkdir()
    branchfs = shutil.which("branchfs")
    if not branchfs:
        raise ArtifactFailure("branchfs binary not found", classification="environment")
    mounted = False
    try:
        started = time.perf_counter()
        run_command(
            [branchfs, "mount", "--base", str(base), "--storage", str(storage), str(mountpoint)],
            cwd=case_root,
        )
        mounted = True
        run_command(
            [branchfs, "create", "experiment", str(mountpoint), "--storage", str(storage)],
            cwd=case_root,
        )
        setup_ms = (time.perf_counter() - started) * 1000.0

        started = time.perf_counter()
        execute_steps(mountpoint, plan)
        execute_ms = (time.perf_counter() - started) * 1000.0

        started = time.perf_counter()
        run_command(
            [branchfs, "abort", str(mountpoint), "--storage", str(storage)],
            cwd=case_root,
        )
        recovery_ms = (time.perf_counter() - started) * 1000.0
        return BackendOutcome(
            present_ids=present_ids(mountpoint, plan),
            timing=Timing(setup_ms, execute_ms, recovery_ms),
            native_boundary="leaf_branch",
            artifact_commit=git_commit(ROOT / "third_party" / "branchfs"),
        )
    finally:
        if mounted:
            run_command(
                [branchfs, "unmount", str(mountpoint), "--storage", str(storage)],
                cwd=case_root,
                check=False,
            )
            subprocess.run(
                ["fusermount3", "-u", str(mountpoint)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )


def waypoint_case_script(plan: IntraTurnPlan) -> str:
    fault_turn = plan.steps[plan.fault_step_id].turn_index
    lines = [
        "#!/bin/bash",
        "set -euo pipefail",
        "W=/opt/waypoint-bin/waypoint",
        "ROOTFS=/opt/waypoint-rootfs",
        "SESSION=",
        "cleanup() {",
        '  if [ -n "$SESSION" ]; then "$W" cleanup "$SESSION" --force >/dev/null 2>&1 || true; fi',
        '  chown -R "${HOST_UID}:${HOST_GID}" /tmp >/dev/null 2>&1 || true',
        "}",
        "trap cleanup EXIT",
        "setup_start=$(date +%s%N)",
        'init_out=$("$W" init "$ROOTFS" --shell --quiet)',
        'SESSION=${init_out%%,*}',
        (
            '"$W" exec "$SESSION" main -- '
            + shlex.quote(
                "mkdir -p /workspace; "
                f"for ((i=0; i<{len(plan.steps)}; i++)); do "
                "printf -v name 'step_%03d.txt' \"$i\"; "
                "printf 'BASE\\n' > \"/workspace/$name\"; done"
            )
            + " >/dev/null"
        ),
        "setup_end=$(date +%s%N)",
        "execute_start=$setup_end",
    ]
    current_turn = -1
    for step in plan.steps:
        if step.turn_index != current_turn:
            current_turn = step.turn_index
            lines.append(
                f'"$W" checkpoint "$SESSION" turn-{current_turn} >/dev/null'
            )
        command = "cd /workspace; " + step.command(plan.steps)[2]
        lines.append(
            '"$W" exec "$SESSION" main -- '
            + shlex.quote(command)
            + " >/dev/null"
        )
    lines.extend(
        [
            "execute_end=$(date +%s%N)",
            "recovery_start=$execute_end",
            f'"$W" fork "$SESSION" turn-{fault_turn} --id recovered >/dev/null',
            "recovery_end=$(date +%s%N)",
            (
                '"$W" exec "$SESSION" recovered -- '
                + shlex.quote(
                    f"for ((i=0; i<{len(plan.steps)}; i++)); do "
                    "printf -v name 'step_%03d.txt' \"$i\"; "
                    "f=\"/workspace/$name\"; first=; "
                    "if [ -f \"$f\" ]; then IFS= read -r first < \"$f\" || true; fi; "
                    "if [[ \"$first\" == artifact-* ]]; then "
                    "printf '__AGENTTX_PRESENT__%d\\n' \"$i\"; fi; done"
                )
            ),
            'printf "__AGENTTX_TIMING__%s %s %s\\n" '
            '"$((setup_end-setup_start))" '
            '"$((execute_end-execute_start))" '
            '"$((recovery_end-recovery_start))"',
            '"$W" cleanup "$SESSION" --force >/dev/null',
            "SESSION=",
        ]
    )
    return "\n".join(lines) + "\n"


def run_waypoint(case_root: Path, plan: IntraTurnPlan) -> BackendOutcome:
    script = case_root / "run-waypoint.sh"
    script.write_text(waypoint_case_script(plan), encoding="utf-8")
    script.chmod(0o700)
    bin_dir = WAYPOINT_EVAL_ROOT / "bin"
    rootfs = WAYPOINT_EVAL_ROOT / "rootfs"
    with WAYPOINT_LOCK:
        result = run_command(
            [
                "docker",
                "run",
                "--rm",
                "--privileged",
                "--pid=host",
                "--network=none",
                "-e",
                f"HOST_UID={os.getuid()}",
                "-e",
                f"HOST_GID={os.getgid()}",
                "-e",
                "WAYPOINT_BASH_INIT_SRC=/opt/waypoint-bin/bash_init",
                "-v",
                f"{case_root}:/tmp",
                "-v",
                f"{bin_dir}:/opt/waypoint-bin:ro",
                "-v",
                f"{rootfs}:/opt/waypoint-rootfs:ro",
                WAYPOINT_IMAGE,
                "bash",
                "/tmp/run-waypoint.sh",
            ],
            cwd=case_root,
            timeout=600.0,
        )
    present = frozenset(
        int(match.group(1))
        for match in re.finditer(
            r"^__AGENTTX_PRESENT__(\d+)\r?$", result.stdout, re.MULTILINE
        )
    )
    timing_match = re.search(
        r"^__AGENTTX_TIMING__(\d+) (\d+) (\d+)\r?$",
        result.stdout,
        re.MULTILINE,
    )
    if timing_match is None:
        raise ArtifactFailure("Waypoint result did not contain timing markers")
    timing_values = [int(value) / 1_000_000.0 for value in timing_match.groups()]
    return BackendOutcome(
        present_ids=present,
        timing=Timing(*timing_values),
        native_boundary="turn_checkpoint",
        artifact_commit=git_commit(ROOT / "third_party" / "waypoint"),
    )


RUNNERS: dict[str, Callable[[Path, IntraTurnPlan], BackendOutcome]] = {
    "agenttx": run_agenttx,
    "session_try": run_session_try,
    "yolofs": run_yolofs,
    "branchfs": run_branchfs,
    "waypoint": run_waypoint,
}


def result_row(backend: str, plan: IntraTurnPlan, outcome: BackendOutcome) -> dict:
    all_ids = set(range(len(plan.steps)))
    invalid = set(plan.oracle_closure)
    valid = all_ids - invalid
    present = set(outcome.present_ids)
    removed = all_ids - present
    invalid_present = invalid & present
    valid_present = valid & present
    expected = valid
    return {
        "schema": SCHEMA,
        "backend": backend,
        "artifact_commit": outcome.artifact_commit,
        "native_boundary": outcome.native_boundary,
        "trace_id": plan.trace_id,
        "manifest_hash": plan.manifest_hash,
        "seed": plan.seed,
        "turn_count": plan.turn_count,
        "turn_width": len(plan.steps) // plan.turn_count,
        "fault_step_id": plan.fault_step_id,
        "fault_turn": plan.steps[plan.fault_step_id].turn_index,
        "requested_fault_position": plan.requested_fault_position,
        "actual_fault_position": plan.fault_step_id / max(len(plan.steps) - 1, 1),
        "requested_closure_ratio": plan.requested_closure_ratio,
        "actual_closure_ratio": len(invalid) / len(plan.steps),
        "requested_dependency_value": plan.requested_dependency_value,
        "actual_dependency_value": plan.actual_dependency_value,
        "shape": plan.shape,
        "interleaving": plan.interleaving,
        "closure_size": len(invalid),
        "valid_size": len(valid),
        "present_ids": json.dumps(sorted(present)),
        "removed_ids": json.dumps(sorted(removed)),
        "invalid_removal": 1.0 - len(invalid_present) / max(len(invalid), 1),
        "valid_retention": len(valid_present) / max(len(valid), 1),
        "useful_steps_wasted": len(valid - present),
        "invalid_steps_retained": len(invalid_present),
        "recovery_safe": not invalid_present,
        "exact_selective_state": present == expected,
        "setup_ms": round(outcome.timing.setup_ms, 6),
        "execute_ms": round(outcome.timing.execute_ms, 6),
        "recovery_ms": round(outcome.timing.recovery_ms, 6),
        "total_ms": round(outcome.timing.total_ms, 6),
        "status": "ok",
        "failure_class": "",
        "error": "",
    }


def failure_row(backend: str, plan: IntraTurnPlan, exc: BaseException) -> dict:
    classification = getattr(exc, "classification", "harness")
    return {
        "schema": SCHEMA,
        "backend": backend,
        "artifact_commit": "",
        "native_boundary": "",
        "trace_id": plan.trace_id,
        "manifest_hash": plan.manifest_hash,
        "seed": plan.seed,
        "turn_count": plan.turn_count,
        "turn_width": len(plan.steps) // plan.turn_count,
        "fault_step_id": plan.fault_step_id,
        "fault_turn": plan.steps[plan.fault_step_id].turn_index,
        "requested_fault_position": plan.requested_fault_position,
        "actual_fault_position": plan.fault_step_id / max(len(plan.steps) - 1, 1),
        "requested_closure_ratio": plan.requested_closure_ratio,
        "actual_closure_ratio": len(plan.oracle_closure) / len(plan.steps),
        "requested_dependency_value": plan.requested_dependency_value,
        "actual_dependency_value": plan.actual_dependency_value,
        "shape": plan.shape,
        "interleaving": plan.interleaving,
        "closure_size": len(plan.oracle_closure),
        "valid_size": len(plan.valid_ids),
        "present_ids": "",
        "removed_ids": "",
        "invalid_removal": "",
        "valid_retention": "",
        "useful_steps_wasted": "",
        "invalid_steps_retained": "",
        "recovery_safe": "",
        "exact_selective_state": "",
        "setup_ms": "",
        "execute_ms": "",
        "recovery_ms": "",
        "total_ms": "",
        "status": "failed",
        "failure_class": classification,
        "error": str(exc),
    }


def run_one(
    base_dir: Path, backend: str, plan: IntraTurnPlan
) -> dict:
    case_root = Path(
        tempfile.mkdtemp(
            prefix=f"{backend}-{plan.manifest_hash[:8]}-", dir=base_dir
        )
    )
    try:
        return result_row(backend, plan, RUNNERS[backend](case_root, plan))
    except BaseException as exc:
        return failure_row(backend, plan, exc)
    finally:
        remove_tree(case_root)


def summarize(rows: Iterable[dict]) -> list[dict]:
    grouped: dict[tuple[str, str, int, float, float], list[dict]] = {}
    for row in rows:
        if row["status"] != "ok":
            continue
        key = (
            row["backend"],
            row["suite_slice"],
            int(row["turn_count"]),
            float(row["requested_fault_position"]),
            float(row["requested_dependency_value"]),
        )
        grouped.setdefault(key, []).append(row)
    summary: list[dict] = []
    for (
        backend,
        suite_slice,
        turn_count,
        fault_position,
        dependency_value,
    ), group in sorted(grouped.items()):
        count = len(group)
        summary.append(
            {
                "backend": backend,
                "suite_slice": suite_slice,
                "turn_count": turn_count,
                "requested_fault_position": fault_position,
                "requested_dependency_value": dependency_value,
                "n": count,
                "recovery_safe_rate": sum(
                    row["recovery_safe"] is True
                    or str(row["recovery_safe"]).lower() == "true"
                    for row in group
                )
                / count,
                "exact_selective_state_rate": sum(
                    row["exact_selective_state"] is True
                    or str(row["exact_selective_state"]).lower() == "true"
                    for row in group
                )
                / count,
                "valid_retention_mean": sum(float(row["valid_retention"]) for row in group) / count,
                "invalid_removal_mean": sum(float(row["invalid_removal"]) for row in group) / count,
                "useful_steps_wasted_mean": sum(
                    float(row["useful_steps_wasted"]) for row in group
                )
                / count,
                "recovery_ms_mean": sum(float(row["recovery_ms"]) for row in group) / count,
                "total_ms_mean": sum(float(row["total_ms"]) for row in group) / count,
            }
        )
    return summary


def write_csv(path: Path, rows: Sequence[dict]) -> None:
    if not rows:
        return
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def git_state() -> dict:
    commit = run_command(["git", "rev-parse", "HEAD"], cwd=ROOT).stdout.strip()
    status = run_command(["git", "status", "--porcelain"], cwd=ROOT).stdout
    return {
        "commit": commit,
        "dirty": bool(status),
        "status_sha256": hashlib.sha256(status.encode()).hexdigest(),
    }


def validate_agenttx_try_backend(work_root: Path) -> None:
    """Validate try once before parallel cases and reuse that host result."""
    probe_root = Path(tempfile.mkdtemp(prefix="agenttx-preflight-", dir=work_root))
    sandbox = probe_root / "sandbox"
    workspace = probe_root / "workspace"
    sandbox.mkdir()
    workspace.mkdir()
    try:
        run_command(
            [str(ROOT / "scripts" / "try-wrapper.sh"), "-N", str(sandbox), "--", "/bin/true"],
            cwd=workspace,
            timeout=300.0,
        )
    finally:
        remove_tree(probe_root)
    os.environ["AGENTTX_ASSUME_TRY_AVAILABLE"] = "1"


def validate_waypoint_backend() -> None:
    required = [
        WAYPOINT_EVAL_ROOT / "bin" / "waypoint",
        WAYPOINT_EVAL_ROOT / "bin" / "bash_init",
        WAYPOINT_EVAL_ROOT / "rootfs" / "bin" / "bash",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ArtifactFailure(
            "Waypoint evaluation artifacts are missing: " + ", ".join(missing),
            classification="environment",
        )
    run_command(
        ["docker", "image", "inspect", WAYPOINT_IMAGE], cwd=ROOT, timeout=30.0
    )
    run_command(
        [
            "docker",
            "run",
            "--rm",
            "--privileged",
            "--pid=host",
            "--network=none",
            WAYPOINT_IMAGE,
            "criu",
            "check",
        ],
        cwd=ROOT,
        timeout=60.0,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--suite", choices=("custom", "motivation400"), default="custom"
    )
    parser.add_argument("--backends", nargs="+", choices=BACKENDS, default=list(BACKENDS))
    parser.add_argument("--dependency-values", nargs="+", type=float)
    parser.add_argument("--turn-counts", nargs="+", type=int, default=[5])
    parser.add_argument("--turn-width", type=int, default=16)
    parser.add_argument("--fault-position", type=float, default=0.25)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--work-root",
        type=Path,
        default=Path("/home/bfq/.agenttx-external-artifacts"),
    )
    args = parser.parse_args()
    if not 1 <= args.workers <= 6:
        parser.error("workers must be in [1, 6]")
    if args.repeats < 1:
        parser.error("repeats must be positive")
    if args.suite == "custom" and not args.dependency_values:
        parser.error("custom suite requires --dependency-values")
    if args.dependency_values and any(
        not 0.0 <= value <= 1.0 for value in args.dependency_values
    ):
        parser.error("dependency values must be in [0, 1]")
    if any(not 1 <= value <= 10 for value in args.turn_counts):
        parser.error("turn counts must be in [1, 10]")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.work_root.mkdir(parents=True, exist_ok=True)
    if "agenttx" in args.backends:
        validate_agenttx_try_backend(args.work_root)
    if "waypoint" in args.backends:
        validate_waypoint_backend()
    if args.suite == "motivation400":
        cases = motivation_400_cases()
    else:
        assert args.dependency_values is not None
        cases = [
            BenchmarkCase(
                "custom",
                build_multi_turn_plan(
                    turn_count,
                    args.turn_width,
                    fault_position=args.fault_position,
                    dependency_value=dependency_value,
                    shape="layered",
                    interleaving="alternating",
                    seed=repeat,
                ),
            )
            for turn_count in args.turn_counts
            for dependency_value in args.dependency_values
            for repeat in range(args.repeats)
        ]
    config = {
        "schema": SCHEMA,
        "suite": args.suite,
        "backends": args.backends,
        "dependency_values": args.dependency_values,
        "turn_counts": args.turn_counts,
        "turn_width": args.turn_width,
        "fault_position": args.fault_position,
        "repeats": args.repeats,
        "workers": args.workers,
        "planned_manifests": len(cases),
        "planned_rows": len(cases) * len(args.backends),
        "manifest_sources": {
            name: str(path.relative_to(ROOT))
            for name, path in MOTIVATION_SOURCES.items()
        }
        if args.suite == "motivation400"
        else {},
        "manifest_hashes_verified": args.suite == "motivation400",
        "git": git_state(),
    }
    config_path = args.output_dir / "config.json"
    if args.resume and config_path.is_file():
        previous_config = json.loads(config_path.read_text(encoding="utf-8"))
        for key in ("schema", "suite", "backends", "planned_rows"):
            if previous_config.get(key) != config.get(key):
                parser.error(
                    f"resume config mismatch for {key}: "
                    f"{previous_config.get(key)!r} != {config.get(key)!r}"
                )
    write_json(config_path, config)

    progress_path = args.output_dir / "external_recovery_progress.csv"
    rows: list[dict] = []
    if args.resume and progress_path.is_file():
        with progress_path.open(encoding="utf-8", newline="") as handle:
            rows = [
                row
                for row in csv.DictReader(handle)
                if row.get("status") == "ok"
            ]
    completed_keys = {
        (
            str(row["backend"]),
            str(row["suite_slice"]),
            str(row["trace_id"]),
            str(row["manifest_hash"]),
        )
        for row in rows
    }
    tasks = [
        (backend, case)
        for case in cases
        for backend in args.backends
        if (
            backend,
            case.suite_slice,
            case.plan.trace_id,
            case.plan.manifest_hash,
        )
        not in completed_keys
    ]
    total_rows = len(cases) * len(args.backends)
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(run_one, args.work_root, backend, case.plan): (
                backend,
                case,
            )
            for backend, case in tasks
        }
        for index, future in enumerate(
            as_completed(futures), start=len(rows) + 1
        ):
            row = future.result()
            _, case = futures[future]
            row["suite_slice"] = case.suite_slice
            rows.append(row)
            progress_rows = sorted(
                rows, key=lambda item: (item["trace_id"], item["backend"])
            )
            write_csv(
                progress_path, progress_rows
            )
            print(
                f"[{index}/{total_rows}] {row['backend']} {row['trace_id']} {row['status']}",
                flush=True,
            )

    rows.sort(key=lambda row: (row["trace_id"], row["backend"]))
    summaries = summarize(rows)
    write_csv(args.output_dir / "external_recovery_raw.csv", rows)
    write_csv(args.output_dir / "external_recovery_summary.csv", summaries)
    write_json(
        args.output_dir / "external_recovery.json",
        {"raw": rows, "summary": summaries},
    )
    failed = [row for row in rows if row["status"] != "ok"]
    config["completed_rows"] = len(rows) - len(failed)
    config["failed_rows"] = len(failed)
    write_json(config_path, config)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
