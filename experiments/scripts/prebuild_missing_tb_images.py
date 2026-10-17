"""Build missing Terminal-Bench task images without a network proxy."""

from __future__ import annotations

import argparse
import fcntl
import os
import signal
import subprocess
import sys
from pathlib import Path

from experiments.workloads import terminal_bench_suite as tb

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.scripts.experiment_storage import experiment_path


def direct_environment() -> dict[str, str]:
    env = os.environ.copy()
    for name in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy",
    ):
        env.pop(name, None)
    env.update({"NO_PROXY": "*", "no_proxy": "*", "AGENTTX_NO_PROXY": "1"})
    return env


def image_workdir(image: str) -> str | None:
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Config.WorkingDir}}", image],
        text=True,
        capture_output=True,
        check=False,
        env=direct_environment(),
    )
    return result.stdout.strip() if result.returncode == 0 else None


def build_one(task_id: str, cache_root: Path) -> int:
    task = tb.load_tasks(cache_root)[task_id]
    try:
        image, workdir = tb.ensure_official_image(task, cache_root)
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", flush=True)
        return 1
    print(f"{image} {workdir}", flush=True)
    return 0


def build_missing(
    cache_root: Path, output_dir: Path, task_ids: list[str], timeout: int
) -> None:
    tasks = tb.load_tasks(cache_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    counts = {"ready": 0, "built": 0, "busy": 0, "failed": 0,
              "timeout": 0, "unsupported": 0, "invalid": 0}
    for task_id in task_ids:
        task = tasks[task_id]
        source = tb.task_source(cache_root, task)
        try:
            tb._docker_build_paths(source)
        except (FileNotFoundError, ValueError) as exc:
            counts["unsupported"] += 1
            print(f"UNSUPPORTED {task_id}: {exc}", flush=True)
            continue
        image = tb._official_image_tag(task, source)
        workdir = image_workdir(image)
        if workdir is not None:
            status = "ready" if workdir.startswith("/") and workdir != "/" else "invalid"
            counts[status] += 1
            print(f"{status.upper()} {task_id}: {workdir}", flush=True)
            continue
        lock_path = cache_root / "terminal_bench" / "image_locks" / f"{task_id}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                counts["busy"] += 1
                print(f"BUSY {task_id}", flush=True)
                continue
        print(f"BUILD {task_id}", flush=True)
        with (output_dir / f"{task_id}.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()),
                 "--one-task", task_id, "--cache-root", str(cache_root)],
                cwd=ROOT,
                env=direct_environment(),
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                result = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                counts["timeout"] += 1
                print(f"TIMEOUT {task_id}: {timeout}s", flush=True)
                continue
        status = "built" if result == 0 else "failed"
        counts[status] += 1
        print(f"{status.upper()} {task_id}: {output_dir / (task_id + '.log')}", flush=True)
    print(f"SUMMARY {counts}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-root", type=Path, default=experiment_path("cache", project_root=ROOT))
    parser.add_argument("--output-dir", type=Path, default=experiment_path("logs", project_root=ROOT) / "tb_direct_prebuild")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--tasks", nargs="*")
    parser.add_argument("--one-task", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.one_task:
        return build_one(args.one_task, args.cache_root)
    tasks = tb.load_tasks(args.cache_root)
    build_missing(args.cache_root, args.output_dir, args.tasks or sorted(tasks), args.timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
