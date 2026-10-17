#!/usr/bin/env python3
"""Check Docker verifier images and task dependencies before experiments."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.scripts.experiment_storage import experiment_path
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from experiments.workloads import swe_bench_suite as swe  # noqa: E402
from experiments.workloads import terminal_bench_suite as tb  # noqa: E402


def select_tasks(
    *, cache_dir: Path, suite: str, task_set: str, requested: Sequence[str] = ()
) -> tuple[dict[str, object], dict[str, object], list[tuple[str, str]]]:
    suites = {"swe", "tb"} if suite == "all" else {suite}
    if task_set == "representative":
        swe_catalog = dict(swe.TASKS) if "swe" in suites else {}
        tb_catalog = dict(tb.TASKS) if "tb" in suites else {}
    else:
        swe_catalog = swe.load_tasks(cache_dir) if "swe" in suites else {}
        tb_catalog = tb.load_tasks(cache_dir) if "tb" in suites else {}

    if task_set == "selected":
        if not requested:
            raise ValueError("--task-set selected requires --tasks")
        selected_swe: dict[str, object] = {}
        selected_tb: dict[str, object] = {}
        for value in requested:
            if ":" in value:
                requested_suite, name = value.split(":", 1)
                catalog = swe_catalog if requested_suite == "swe" else tb_catalog if requested_suite == "tb" else None
                if requested_suite not in suites or catalog is None or name not in catalog:
                    raise ValueError(f"unknown task: {value}")
                (selected_swe if requested_suite == "swe" else selected_tb)[name] = catalog[name]
                continue
            matches = [
                (candidate_suite, catalog[value])
                for candidate_suite, catalog in (("swe", swe_catalog), ("tb", tb_catalog))
                if value in catalog
            ]
            if len(matches) != 1:
                reason = "ambiguous" if matches else "unknown"
                raise ValueError(f"{reason} task: {value}; use a swe: or tb: prefix")
            candidate_suite, task = matches[0]
            (selected_swe if candidate_suite == "swe" else selected_tb)[value] = task
        swe_catalog, tb_catalog = selected_swe, selected_tb

    selected = [
        *(("swe", name) for name in swe_catalog),
        *(("tb", name) for name in tb_catalog),
    ]
    if not selected:
        raise ValueError("task selection is empty")
    return swe_catalog, tb_catalog, selected


def _check(name: str, ok: bool, detail: str) -> dict[str, object]:
    return {"name": name, "ok": bool(ok), "detail": detail[-2000:]}


def global_checks(args: argparse.Namespace, suites: set[str]) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    active_prefix = os.environ.get("VIRTUAL_ENV") or os.environ.get("CONDA_PREFIX", "")
    resolved_python = shutil.which(args.python) or args.python
    # venv interpreters are commonly symlinks into a standalone/base Python.
    # The environment prefix belongs to the invoked bin/, not that target.
    expected_prefix = str(Path(resolved_python).absolute().parent.parent)
    checks.append(
        _check(
            "python_environment",
            bool(active_prefix) and Path(active_prefix).resolve() == Path(expected_prefix),
            f"active={active_prefix or 'none'} expected={expected_prefix}",
        )
    )
    modules = ["agenttx", "pytest"]
    if "tb" in suites:
        modules.extend(["pandas", "pyarrow", "yaml"])
    try:
        probe_env = {**os.environ, "PYTHONPATH": f"{ROOT / 'src'}:{ROOT}"}
        conda_lib = str(Path(expected_prefix) / "lib")
        probe_env["LD_LIBRARY_PATH"] = conda_lib + (
            os.pathsep + probe_env["LD_LIBRARY_PATH"]
            if probe_env.get("LD_LIBRARY_PATH") else ""
        )
        python_probe = subprocess.run(
            [
                args.python,
                "-c",
                "; ".join(f"import {module}" for module in modules) + "; print('imports ok')",
            ],
            cwd=ROOT,
            env=probe_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        checks.append(
            _check(
                "python_imports",
                python_probe.returncode == 0,
                python_probe.stdout.strip() or python_probe.stderr.strip(),
            )
        )
    except OSError as exc:
        checks.append(_check("python_imports", False, f"{type(exc).__name__}: {exc}"))

    checks.append(_check("docker_daemon", swe.docker_available(), "docker info"))
    return checks


def _run(command: Sequence[str], *, cwd: Path | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def validate_swe_repo(task: object, cache_dir: Path) -> tuple[Path, dict]:
    instance = swe.fetch_instance(task.instance_id, cache_dir)
    repo = cache_dir / "swe_bench" / "repos" / task.instance_id
    if not (repo / ".git").exists():
        raise FileNotFoundError(f"repository missing: {repo}")
    head = _run(["git", "rev-parse", "HEAD"], cwd=repo)
    if head.returncode != 0 or head.stdout.strip() != str(instance["base_commit"]):
        raise RuntimeError(
            f"repository HEAD={head.stdout.strip() or 'unknown'}, expected={instance['base_commit']}"
        )
    marker = repo / ".agenttx_test_patch"
    if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != task.instance_id:
        raise RuntimeError(f"test-patch marker missing or stale: {marker}")
    if (repo / ".gitmodules").is_file():
        status = _run(["git", "submodule", "status", "--recursive"], cwd=repo)
        invalid = [line for line in status.stdout.splitlines() if line[:1] in {"-", "+", "U"}]
        if status.returncode != 0 or invalid:
            raise RuntimeError("submodules are missing or not pinned: " + "; ".join(invalid))
    return repo, instance


def validate_swe_venv(task: object, instance: Mapping[str, object], cache_dir: Path) -> str:
    env_key = swe._venv_key(task, instance)
    venv = cache_dir / "swe_bench" / "venvs" / env_key
    python = venv / "bin" / "python"
    marker = venv / ".agenttx_pkgs"
    repo_name = str(instance.get("repo") or "")
    packages = swe.PYTHON_DEPS.get(task.instance_id) or list(
        swe.REPO_PIP.get(repo_name) or ["pytest"]
    )
    expected = "\n".join(packages) + "\n"
    if not python.is_file() or not marker.is_file():
        raise FileNotFoundError(f"prepared Python environment missing: {venv}")
    if marker.read_text(encoding="utf-8") != expected:
        raise RuntimeError(f"dependency marker is stale: {marker}")
    env = os.environ.copy()
    python_lib = str(python.resolve().parent.parent / "lib")
    env["LD_LIBRARY_PATH"] = python_lib + (
        os.pathsep + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else ""
    )
    checked = subprocess.run(
        [str(python), "-m", "pip", "check"],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )
    if checked.returncode != 0:
        raise RuntimeError((checked.stderr or checked.stdout or "pip check failed")[-2000:])
    return str(venv)


def ensure_swe_venv_ready(
    task: object,
    instance: Mapping[str, object],
    cache_dir: Path,
    python: str,
    *,
    prepare_missing: bool,
) -> tuple[str, bool]:
    try:
        return validate_swe_venv(task, instance, cache_dir), False
    except Exception:
        if not prepare_missing:
            raise
    swe.ensure_venv(task, python, cache_dir, dict(instance))
    return validate_swe_venv(task, instance, cache_dir), True


def validate_docker_image(instance_id: str) -> str:
    image = swe.swe_eval_image(instance_id)
    inspected = _run(["docker", "image", "inspect", image])
    if inspected.returncode != 0:
        raise FileNotFoundError(f"official eval image missing: {image}")
    smoke = _run(
        [
            "docker", "run", "--rm", "--entrypoint", "bash", image, "-lc",
            "test -d /testbed && test -x /opt/miniconda3/envs/testbed/bin/python",
        ],
        timeout=120,
    )
    if smoke.returncode != 0:
        raise RuntimeError((smoke.stderr or smoke.stdout or "Docker verifier smoke failed")[-2000:])
    return image


def validate_tb_source(task: object, cache_dir: Path) -> Path:
    source = tb.task_source(cache_dir, task)
    required = [source / "task.yaml", source / "docker-compose.yaml", source / "run-tests.sh"]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing task dependencies: " + ", ".join(missing))
    empty = [str(path) for path in required if path.stat().st_size == 0]
    if empty:
        raise RuntimeError("empty task dependencies: " + ", ".join(empty))
    return source


def validate_tb_docker(source: Path) -> str:
    compose_env = {
        **os.environ,
        "T_BENCH_TASK_DOCKER_CLIENT_IMAGE_NAME": "agenttx-environment-check",
        "T_BENCH_TASK_DOCKER_CLIENT_CONTAINER_NAME": "agenttx-environment-check",
        "T_BENCH_DOCKER_NAME_PREFIX": "agenttx-environment-check",
        "T_BENCH_TASK_DOCKER_NAME_PREFIX": "agenttx-environment-check",
        "T_BENCH_TEST_DIR": "/tests",
        "T_BENCH_TASK_LOGS_PATH": "/tmp",
        "T_BENCH_CONTAINER_LOGS_PATH": "/logs",
        "T_BENCH_TASK_AGENT_LOGS_PATH": "/tmp",
        "T_BENCH_CONTAINER_AGENT_LOGS_PATH": "/agent-logs",
    }
    checked = subprocess.run(
        ["docker", "compose", "-f", str(source / "docker-compose.yaml"), "config", "-q"],
        cwd=str(source),
        env=compose_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )
    if checked.returncode != 0:
        raise RuntimeError((checked.stderr or checked.stdout or "Docker Compose config failed")[-2000:])
    return str(source / "docker-compose.yaml")


def check_task(
    *,
    suite: str,
    name: str,
    cache_dir: Path,
    swe_catalog: Mapping[str, object],
    tb_catalog: Mapping[str, object],
    dependency_cache: dict[str, tuple[bool, str]],
    python: str = sys.executable,
    prepare_missing: bool = False,
) -> dict[str, object]:
    started = time.perf_counter()
    failed_stages: list[str] = []
    details: list[str] = []
    docker_ok = False
    dependencies_ok = False
    if suite == "swe":
        task = swe_catalog[name]
        try:
            _, instance = validate_swe_repo(task, cache_dir)
        except Exception as exc:
            failed_stages.append("repository")
            details.append(f"repository: {type(exc).__name__}: {exc}")
            try:
                instance = swe.fetch_instance(task.instance_id, cache_dir)
            except Exception as instance_exc:
                instance = None
                details.append(f"instance: {type(instance_exc).__name__}: {instance_exc}")

        if instance is not None:
            env_key = swe._venv_key(task, instance)
            cached = dependency_cache.get(env_key)
            if cached is None:
                try:
                    venv, prepared = ensure_swe_venv_ready(
                        task,
                        instance,
                        cache_dir,
                        python,
                        prepare_missing=prepare_missing,
                    )
                    detail = f"{venv}{' (prepared)' if prepared else ''}"
                    cached = (True, detail)
                except Exception as exc:
                    cached = (False, f"{type(exc).__name__}: {exc}")
                dependency_cache[env_key] = cached
            dependencies_ok = cached[0]
            if dependencies_ok:
                details.append(f"dependencies: {cached[1]}")
            else:
                failed_stages.append("dependencies")
                details.append(f"dependencies: {cached[1]}")
        else:
            failed_stages.append("dependencies")
            details.append("dependencies: instance metadata unavailable")

        try:
            image = validate_docker_image(name)
            docker_ok = True
            details.append(f"docker: {image}")
        except Exception as exc:
            failed_stages.append("docker_verifier")
            details.append(f"docker: {type(exc).__name__}: {exc}")
    else:
        try:
            source = validate_tb_source(tb_catalog[name], cache_dir)
            dependencies_ok = True
            details.append(str(source))
        except Exception as exc:
            failed_stages.append("task_source")
            details.append(f"{type(exc).__name__}: {exc}")
            source = None
        if source is not None:
            try:
                details.append(f"docker: {validate_tb_docker(source)}")
                task = tb_catalog[name]
                image, image_workdir = tb.ensure_official_image(task, cache_dir)
                details.append(f"official task image: {image} ({image_workdir})")
                docker_ok = True
                image_user = tb.official_image_user(image)
                if image_user not in {"root", "0", "0:0"}:
                    failed_stages.append("image_native_runner_user")
                    details.append(
                        f"image user {image_user!r} is not supported by the current "
                        "image-native agent runner; forcing root would change task semantics"
                    )
            except Exception as exc:
                failed_stages.append("docker_verifier")
                details.append(f"docker: {type(exc).__name__}: {exc}")

    ok = not failed_stages

    return {
        "suite": suite,
        "task": name,
        "ok": ok,
        "stage": "complete" if ok else ",".join(dict.fromkeys(failed_stages)),
        "duration_s": round(time.perf_counter() - started, 3),
        "dependencies_ok": dependencies_ok,
        "docker_ok": docker_ok,
        "detail": " | ".join(details)[-2000:],
    }


def build_report(
    *, checks: Sequence[Mapping[str, object]], rows: Sequence[Mapping[str, object]], expected: int
) -> dict[str, object]:
    failed_checks = [item["name"] for item in checks if not item.get("ok") and item.get("required", True)]
    failed_tasks = [f"{item['suite']}:{item['task']}" for item in rows if not item.get("ok")]
    complete = len(rows) == expected
    return {
        "ok": complete and not failed_checks and not failed_tasks,
        "expected_tasks": expected,
        "checked_tasks": len(rows),
        "passed_tasks": sum(bool(item.get("ok")) for item in rows),
        "failed_checks": failed_checks,
        "failed_tasks": failed_tasks,
        "checks": list(checks),
        "tasks": list(rows),
    }


def write_report(output: Path, report: Mapping[str, object]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "environment_check.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    rows = list(report["tasks"])
    fields = [
        "suite", "task", "ok", "stage", "duration_s", "dependencies_ok",
        "docker_ok", "detail",
    ]
    with (output / "environment_check.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    lines = [
        "# Experiment environment check",
        "",
        f"Overall: **{'PASS' if report['ok'] else 'FAIL'}**",
        "",
        f"Tasks: {report['passed_tasks']}/{report['expected_tasks']} passed",
        "",
        "| Suite | Task | Result | Failed stage | Detail |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        detail = str(row["detail"]).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {row['suite']} | {row['task']} | {'PASS' if row['ok'] else 'FAIL'} "
            f"| {row['stage']} | {detail} |"
        )
    (output / "environment_check.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("swe", "tb", "all"), default="all")
    parser.add_argument(
        "--task-set", choices=("representative", "full", "selected"), default="representative"
    )
    parser.add_argument("--tasks", nargs="*", default=())
    parser.add_argument("--cache-dir", type=Path, default=experiment_path("cache", project_root=ROOT))
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--prepare-missing",
        action="store_true",
        help="build missing or stale SWE-Bench cached venvs before validating them",
    )
    parser.add_argument("--output", type=Path, default=experiment_path("results", project_root=ROOT) / "environment_check")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.cache_dir = args.cache_dir.resolve()
    try:
        swe_catalog, tb_catalog, selected = select_tasks(
            cache_dir=args.cache_dir,
            suite=args.suite,
            task_set=args.task_set,
            requested=args.tasks,
        )
    except Exception as exc:
        report = build_report(
            checks=[_check("task_catalog", False, f"{type(exc).__name__}: {exc}")],
            rows=[],
            expected=0,
        )
        write_report(args.output, report)
        print(f"task selection failed: {exc}", file=sys.stderr)
        return 2

    suites = {suite for suite, _ in selected}
    checks = global_checks(args, suites)
    rows: list[dict[str, object]] = []
    if all(item.get("ok") or not item.get("required", True) for item in checks):
        dependency_cache: dict[str, tuple[bool, str]] = {}
        for index, (suite, name) in enumerate(selected, start=1):
            print(f"[{index}/{len(selected)}] checking {suite}:{name}", flush=True)
            row = check_task(
                suite=suite,
                name=name,
                cache_dir=args.cache_dir,
                swe_catalog=swe_catalog,
                tb_catalog=tb_catalog,
                dependency_cache=dependency_cache,
                python=args.python,
                prepare_missing=args.prepare_missing,
            )
            rows.append(row)
            print(f"  {'PASS' if row['ok'] else 'FAIL'} stage={row['stage']} {row['detail']}", flush=True)

    report = build_report(checks=checks, rows=rows, expected=len(selected))
    write_report(args.output, report)
    print(
        f"environment check {'passed' if report['ok'] else 'failed'}: "
        f"{report['passed_tasks']}/{report['expected_tasks']} tasks; "
        f"report={args.output / 'environment_check.json'}",
        flush=True,
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
