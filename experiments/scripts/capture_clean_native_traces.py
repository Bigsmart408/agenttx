#!/usr/bin/env python3
"""Capture fault-free native coding-agent steps inside official task images."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.scripts.experiment_storage import experiment_path
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from experiments.workloads import swe_bench_suite as swe  # noqa: E402
from experiments.workloads import terminal_bench_suite as tb  # noqa: E402

ALLOWED_IMAGE_METADATA_PATCHES = {
    # Sphinx official eval image adds pytest reporting to tox.ini; no task
    # source or assertion changes. Pin both blob IDs so broader edits fail.
    (
        b"tox.ini",
        b"105a02597ecd2cdf5d4a027a227150377be5f9e6",
        b"90ada082109b1ddee1e4ec4e0b5c09d1828f3670",
    ),
}


def image_matches_base_contents(workspace: Path, base_commit: str) -> tuple[bool, str]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=workspace,
        text=True, capture_output=True, check=True,
    ).stdout.strip()
    if head == base_commit:
        return True, head
    changed = subprocess.run(
        ["git", "diff", "--raw", "--no-abbrev", "-z", base_commit, head],
        cwd=workspace, capture_output=True, check=True,
    ).stdout.split(b"\0")
    entries = [item for item in changed if item]
    if len(entries) % 2:
        return False, head
    for header, path in zip(entries[::2], entries[1::2]):
        fields = header.split()
        if len(fields) != 5 or fields[4] != b"M":
            return False, head
        if fields[2] != fields[3] and (
            path, fields[2], fields[3]
        ) not in ALLOWED_IMAGE_METADATA_PATCHES:
            if path != b"pyproject.toml" or not (workspace / ".astropy-root").exists():
                return False, head
            before = subprocess.run(
                ["git", "show", f"{base_commit}:pyproject.toml"],
                cwd=workspace, capture_output=True, check=True,
            ).stdout
            after = subprocess.run(
                ["git", "show", f"{head}:pyproject.toml"],
                cwd=workspace, capture_output=True, check=True,
            ).stdout
            old = b'requires = ["setuptools",'
            new = b'requires = ["setuptools==68.0.0",'
            if before.count(old) != 1 or after != before.replace(old, new, 1):
                return False, head
    return True, head


def run_native_image(
    task_id: str, suite: str, args: argparse.Namespace, scratch: Path,
    workspace: Path, image: str, image_workdir: str, prompt: str,
) -> dict:
    (scratch / "prompt.txt").write_text(prompt, encoding="utf-8")
    stem = f"{args.agent}__{suite}__{task_id}" if args.agent == "codex" else f"{suite}__{task_id}"
    pending = args.output_dir / "_pending" / stem
    pending.mkdir(parents=True, exist_ok=True)
    cidfile = scratch / "docker.cid"
    command = [
            "docker", "run", "--rm", "--pull", "never", "--privileged",
            "--cidfile", str(cidfile),
            "--network", "host", "--tmpfs", "/tmp:exec",
            "--workdir", image_workdir,
            "--mount", f"type=bind,src={workspace},dst={image_workdir}",
            "--mount", f"type=bind,src={scratch / 'prompt.txt'},dst=/task_prompt.txt,readonly",
            "--mount", f"type=bind,src={ROOT},dst=/home/bfq/agenttx,readonly",
            "--mount", "type=bind,src=/home/bfq/miniconda3,dst=/home/bfq/miniconda3,readonly",
            "--mount", "type=bind,src=/home/bfq/SWE-agent,dst=/home/bfq/SWE-agent,readonly",
            "--mount", "type=bind,src=/home/bfq/.codex/config.toml,dst=/home/bfq/.codex/config.toml,readonly",
            "--mount", "type=bind,src=/home/bfq/.codex/auth.json,dst=/home/bfq/.codex/auth.json,readonly",
            "--mount", "type=bind,src=/home/bfq/.local/bin/codex,dst=/host-tools/codex,readonly",
            "--mount", "type=bind,src=/home/bfq/.local/bin/codex-code-mode-host,dst=/host-tools/codex-code-mode-host,readonly",
            "--mount", f"type=bind,src={args.output_dir.resolve()},dst=/results",
            "--mount", "type=bind,src=/usr/bin/strace,dst=/host-tools/strace,readonly",
            "--mount", "type=bind,src=/usr/bin/git,dst=/host-tools/git,readonly",
    ]
    replacement_script = None
    if suite == "tb":
        task = tb.load_tasks(args.cache_dir)[task_id]
        source = tb.task_source(args.cache_dir, task)
        test_script, local_uv = tb.prepare_official_test_script(source, args.cache_dir)
        if test_script != source / "run-tests.sh":
            replacement_script = test_script
        command += [
            "--mount", f"type=bind,src={test_script},dst=/tmp/agenttx-run-tests.sh,readonly",
            "-e", "AGENTTX_VERIFY_TB_NATIVE=1", "-e", "TEST_DIR=/tests",
            "-e", f"AGENTTX_AGENT_TIMEOUT_S={args.task_timeout_s}",
            "-e", f"AGENTTX_TB_TESTS_SOURCE={source / 'tests'}",
        ]
        if local_uv is not None:
            command += ["--mount", f"type=bind,src={local_uv},dst=/tmp/agenttx-uv,readonly"]
    for name in ("libunwind-ptrace.so.0", "libunwind-x86_64.so.8", "libunwind.so.8"):
        command += [
            "--mount", f"type=bind,src=/usr/lib/x86_64-linux-gnu/{name},dst=/host-libs/{name},readonly",
        ]
    command += [
            "-e", f"PYTHONPATH={ROOT / 'src'}:{ROOT}",
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-e", "AGENTTX_MOTIVATION_BWRAP=1",
            "-e", "AGENTTX_ALLOW_EXTERNAL_WRITES=1",
            "-e", "SWE_AGENT_LOG_STREAM_LEVEL=ERROR",
            "-e", f"AGENTTX_HOST_UID={os.getuid()}",
            "-e", f"AGENTTX_HOST_GID={os.getgid()}",
            "-e", f"AGENTTX_TASK_ID={task_id}",
            "-e", f"AGENTTX_SUITE={suite}",
            "-e", f"AGENTTX_AGENT={args.agent}",
            "-e", f"AGENTTX_RECORD_STEM={stem}",
            "-e", f"AGENTTX_WORKSPACE={image_workdir}",
            "--entrypoint", "bash", image,
            str(ROOT / "experiments/scripts/bootstrap_clean_native_container.sh"),
            "--task", task_id, "--suite", suite,
            "--workspace", image_workdir,
            "--prompt-file", "/task_prompt.txt",
            "--output-dir", f"/results/_pending/{stem}", "--model", args.model,
            "--reasoning-effort", args.reasoning_effort,
            "--max-steps", str(args.max_steps),
            "--max-model-calls", str(args.max_model_calls),
    ]
    try:
        with (pending / "docker.stdout.log").open("w", encoding="utf-8") as stdout, (
            pending / "docker.stderr.log"
        ).open("w", encoding="utf-8") as stderr:
            subprocess.run(
                command, check=True,
                timeout=args.task_timeout_s + (3600 if suite == "tb" else 0),
                stdout=stdout, stderr=stderr,
            )
    except subprocess.TimeoutExpired as exc:
        if cidfile.exists():
            container = cidfile.read_text(encoding="utf-8").strip()
            if container:
                subprocess.run(
                    ["docker", "stop", "--time", "3", container],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    check=False, timeout=30,
                )
        raise RuntimeError(
            f"{args.agent}/{suite}/{task_id} exceeded {args.task_timeout_s}s"
        ) from exc
    finally:
        if replacement_script is not None:
            replacement_script.unlink(missing_ok=True)
    path = pending / "native_traces" / f"{stem}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["termination_reason"] = str(record["result"]["summary"])
    record["source_completed"] = bool(
        record["source_completed"]
        and record["termination_reason"].startswith("submitted")
    )
    return record


def capture_terminal_in_image(task_id: str, args: argparse.Namespace) -> dict:
    task = tb.load_tasks(args.cache_dir)[task_id]
    scratch = Path(tempfile.mkdtemp(prefix=f"agenttx-clean-native-{task_id}-"))
    workspace = scratch / "workspace"
    try:
        tb.materialize(task, workspace, args.cache_dir, include_verifier_files=False)
        image, image_workdir = tb.ensure_official_image(task, args.cache_dir)
        image_user = tb.official_image_user(image)
        if image_user not in {"root", "0", "0:0"}:
            raise RuntimeError(
                f"image-native capture cannot preserve non-root image user {image_user!r} "
                f"for {task_id}; running as root would change task semantics"
            )
        initialize_terminal_baseline(workspace, image)
        record = run_native_image(
            task_id, "tb", args, scratch, workspace, image, image_workdir,
            task.instruction,
        )
        if record.get("model_finished", record["source_completed"]) and not record.get("commit_error"):
            try:
                stem = f"{args.agent}__tb__{task_id}" if args.agent == "codex" else f"tb__{task_id}"
                pending = args.output_dir / "_pending" / stem
                tests_rc = int((pending / "verifier.rc").read_text(encoding="utf-8").strip())
                stdout = (pending / "verifier.stdout.log").read_text(encoding="utf-8")
                stderr = (pending / "verifier.stderr.log").read_text(encoding="utf-8")
                verifier_output = stdout + stderr
                infrastructure_error = (
                    tests_rc == 124
                    or "failed to download" in verifier_output
                    or "command not found" in verifier_output
                )
                record["verifier"] = {
                    "runtime": "same_official_task_container", "tests_rc": tests_rc,
                    "tests_ok": tests_rc == 0 and not infrastructure_error,
                    "infrastructure_error": infrastructure_error,
                    "stdout": stdout[-2000:],
                    "stderr": stderr[-2000:],
                }
                record["source_success"] = None if infrastructure_error else bool(
                    tests_rc == 0
                    and str(record["result"]["summary"]).startswith("submitted")
                )
            except Exception as exc:
                record["verifier"] = None
                record["verification_error"] = f"{type(exc).__name__}: {exc}"
                record["source_success"] = None
        else:
            record["verifier"] = None
        return record
    finally:
        # The disposable image runs as root and can create root-owned files.
        shutil.rmtree(scratch, ignore_errors=True)


def initialize_terminal_baseline(workspace: Path, image: str) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=workspace, check=True)
    added = subprocess.run(
        ["git", "add", "-A"], cwd=workspace, capture_output=True, text=True,
        check=False,
    )
    if added.returncode != 0:
        if "Permission denied" not in added.stderr:
            raise RuntimeError(f"cannot index task workspace: {added.stderr.strip()}")
        indexed = subprocess.run(
            [
                "docker", "run", "--rm", "--pull", "never", "--network", "none",
                "--user", "0:0", "--workdir", "/task-workspace",
                "--mount", f"type=bind,src={workspace},dst=/task-workspace",
                "--mount", "type=bind,src=/usr/bin/git,dst=/usr/bin/git,readonly",
                "--entrypoint", "sh", image, "-c",
                "git -c safe.directory=/task-workspace add -A && "
                f"chown -R {os.getuid()}:{os.getgid()} .git",
            ],
            text=True, capture_output=True, check=False, timeout=300,
        )
        if indexed.returncode != 0:
            raise RuntimeError(
                f"cannot index task workspace in image: {indexed.stderr.strip()}"
            )
    subprocess.run(
        ["git", "-c", "user.name=AgentTX Experiment",
         "-c", "user.email=agenttx@example.invalid", "commit", "--quiet",
         "--allow-empty", "-m", "clean task baseline"],
        cwd=workspace, check=True,
    )


def capture_swe_in_image(task_id: str, args: argparse.Namespace) -> dict:
    task = swe.load_tasks(args.cache_dir)[task_id]
    _, instance = swe.ensure_repo(task, args.cache_dir)
    scratch = Path(tempfile.mkdtemp(prefix=f"agenttx-clean-native-{task_id}-"))
    workspace = scratch / "workspace"
    workspace.mkdir()
    image = swe.swe_eval_image(task.instance_id)
    try:
        inspected = subprocess.run(
            ["docker", "image", "inspect", image],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if inspected.returncode != 0:
            raise RuntimeError(f"official SWE-Bench image is not cached locally: {image}")
        created = subprocess.run(
            ["docker", "create", "--entrypoint", "true", image],
            text=True, capture_output=True, check=True,
        )
        container = created.stdout.strip()
        try:
            subprocess.run(
                ["docker", "cp", f"{container}:/testbed/.", str(workspace)],
                check=True,
            )
        finally:
            subprocess.run(
                ["docker", "rm", "-f", container],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
        base_commit = str(instance.get("base_commit") or "")
        matches, image_head = image_matches_base_contents(workspace, base_commit)
        if not matches:
            raise RuntimeError(
                f"SWE-Bench image content differs from instance base: {task_id}"
            )
        record = run_native_image(
            task_id, "swe", args, scratch, workspace, image, "/testbed",
            str(instance.get("problem_statement") or task_id),
        )
        record["repo"] = str(instance.get("repo") or "")
        record["base_commit"] = base_commit
        record["image_head"] = image_head
        if record.get("model_finished", record["source_completed"]) and not record.get("commit_error"):
            try:
                checked = swe._verify_tests_docker(
                    workspace, task, instance, apply_test_patch=True,
                )
                verifier_stderr = str(checked.get("verifier_stderr") or "")
                infrastructure_error = (
                    checked.get("tests_rc") in {"timeout", "error", 4}
                    or "_FailedTest" in verifier_stderr
                    or "not found:" in verifier_stderr
                )
                record["verifier"] = {
                    "runtime": "official_docker",
                    "tests_rc": checked.get("tests_rc"),
                    "tests_ok": bool(checked.get("tests_ok")) and not infrastructure_error,
                    "infrastructure_error": infrastructure_error,
                    "test_patch_applied": bool(checked.get("test_patch_applied")),
                    "stdout": str(checked.get("verifier_stdout") or "")[-2000:],
                    "stderr": verifier_stderr[-2000:],
                }
                record["source_success"] = (
                    None if infrastructure_error else bool(checked.get("tests_ok"))
                )
            except Exception as exc:
                record["verification_error"] = f"{type(exc).__name__}: {exc}"
                record["source_success"] = None
        else:
            record["verifier"] = None
        return record
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def capture_one(task_id: str, args: argparse.Namespace) -> dict:
    if args.suite == "tb":
        return capture_terminal_in_image(task_id, args)
    return capture_swe_in_image(task_id, args)


def selected_task_ids(args: argparse.Namespace) -> list[str]:
    tasks = sorted(
        (tb if args.suite == "tb" else swe).load_tasks(args.cache_dir)
    ) if args.all else list(args.tasks)
    if args.exclude_cohort_manifest:
        manifest = json.loads(args.exclude_cohort_manifest.read_text(encoding="utf-8"))
        excluded = {
            task_id
            for scale in manifest["phase1"][args.suite].values()
            for task_id in scale
        }
        tasks = [task_id for task_id in tasks if task_id not in excluded]
    return tasks[args.shard_index::args.shard_count]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", choices=("sweagent", "codex"), default="sweagent")
    parser.add_argument("--suite", choices=("swe", "tb"), default="swe")
    task_source = parser.add_mutually_exclusive_group(required=True)
    task_source.add_argument("--tasks", nargs="+")
    task_source.add_argument("--all", action="store_true")
    parser.add_argument("--exclude-cohort-manifest", type=Path)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, default=experiment_path("cache", project_root=ROOT))
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--reasoning-effort", default="max")
    parser.add_argument("--max-steps", type=int, default=80)
    parser.add_argument("--max-model-calls", type=int, default=80)
    parser.add_argument("--task-timeout-s", type=int, default=3600)
    args = parser.parse_args()
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        parser.error("shard index must be in [0, shard count)")
    if os.environ.get("AGENTTX_MOTIVATION_BWRAP") != "1":
        raise SystemExit("run in the dedicated bwrap namespace")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    failures = []
    task_ids = selected_task_ids(args)
    print(
        f"native cohort {args.agent}/{args.suite}: {len(task_ids)} tasks "
        f"(shard {args.shard_index}/{args.shard_count})",
        flush=True,
    )
    for task_id in task_ids:
        stem = (
            f"{args.agent}__{args.suite}__{task_id}"
            if args.agent == "codex" else f"{args.suite}__{task_id}"
        )
        path = args.output_dir / "native_traces" / f"{stem}.json"
        if path.exists():
            print(f"existing {task_id}: {path}", flush=True)
            continue
        print(f"clean native start {task_id}", flush=True)
        try:
            record = capture_one(task_id, args)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
            print(
                f"clean native done {task_id}: "
                f"steps={len(record['ledger']['steps'])} "
                f"turns={len(record['conversation']['turns'])}",
                flush=True,
            )
        except Exception as exc:
            failures.append({"task": task_id, "error": repr(exc)})
            traceback.print_exc()
    (args.output_dir / f"capture_failures_{args.suite}.json").write_text(
        json.dumps(failures, indent=2) + "\n", encoding="utf-8"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
