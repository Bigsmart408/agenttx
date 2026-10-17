#!/usr/bin/env python3
"""Capture Codex CLI tool events and workspace effects in a task image.

Codex owns its native loop. We observe completed CLI tool events; syscall reads
and before/after workspace snapshots are attributed to those event intervals.
Overlapping or unrecognized tools are retained but excluded from analysis.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from agenttx.agents.external import _parse_jsonl  # noqa: E402
from agenttx.trace import parse_strace_effects  # noqa: E402
from experiments.scripts.sweagent_native_workload import load_codex_gpt_profile  # noqa: E402

TRACE_LINE = re.compile(r"^\s*(\d+)\s+(\d+\.\d+)\s+(.*)$")
TOOL_TYPES = {"command_execution", "file_change", "web_search"}
IGNORED_PARTS = {".git", ".agenttx", "__pycache__", ".pytest_cache"}


def workspace_snapshot(workspace: Path) -> dict[str, str]:
    """Fingerprint only git-visible changes, including untracked files."""
    status = subprocess.run(
        ["git", "-c", f"safe.directory={workspace}", "status", "--porcelain=v1",
         "--untracked-files=all", "-z"],
        cwd=workspace, check=True, capture_output=True,
    ).stdout.split(b"\0")
    paths = []
    index = 0
    while index < len(status):
        entry = status[index]
        index += 1
        if not entry:
            continue
        path = os.fsdecode(entry[3:])
        if entry[:2] in {b"R ", b" R", b"C ", b" C"}:
            old_path = os.fsdecode(status[index])
            index += 1
            if entry[:2] in {b"R ", b" R"} and not set(Path(old_path).parts) & IGNORED_PARTS:
                paths.append(old_path)
        relative = Path(path)
        if not set(relative.parts) & IGNORED_PARTS:
            paths.append(path)
    snapshot = {}
    for relative in paths:
        path = workspace / relative
        if path.is_symlink():
            snapshot[relative] = "link:" + os.readlink(path)
        elif path.is_file():
            snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            snapshot[relative] = "deleted"
    return snapshot


def read_effects_by_window(
    trace_path: Path, windows: list[dict], workspace: Path,
) -> list[list[dict]]:
    ordered = sorted(enumerate(windows), key=lambda pair: pair[1]["start"])
    starts = [window["start"] for _, window in ordered]
    max_duration = max((window["end"] - window["start"] for window in windows), default=0)
    lines = [[] for _ in windows]
    with trace_path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = TRACE_LINE.match(line)
            if not match:
                continue
            stamp = float(match.group(2))
            first = bisect.bisect_left(starts, stamp - max_duration)
            end = bisect.bisect_right(starts, stamp)
            for ordered_index in range(first, end):
                index, window = ordered[ordered_index]
                if stamp <= window["end"]:
                    lines[index].append(f"{match.group(1)} {match.group(3)}")
    return [
        [effect.to_dict() for effect in parse_strace_effects("\n".join(group), workspace)]
        for group in lines
    ]


def overlapping_write_exclusions(windows: list[dict], steps: list[dict]) -> list[str]:
    for index, left in enumerate(windows):
        for other in range(index + 1, len(windows)):
            right = windows[other]
            if left["start"] < right["end"] and right["start"] < left["end"]:
                if any(
                    effect["kind"] in {"W", "D"}
                    for step in (steps[index], steps[other])
                    for effect in step["effects"]
                ):
                    return ["overlapping_mutating_tool_events"]
    return []


def assemble_record(
    task: str, suite: str, workspace: Path, events: list[dict],
    windows: list[dict], snapshots: list[dict[str, str]], trace_path: Path,
    returncode: int, profile: dict,
) -> dict:
    ledger_steps = []
    calls = []
    last_writer = {}
    observed_reads = read_effects_by_window(trace_path, windows, workspace)
    for index, (window, before, after) in enumerate(zip(windows, snapshots[:-1], snapshots[1:])):
        reads = observed_reads[index]
        touched = sorted({path for path in before.keys() | after.keys() if before.get(path) != after.get(path)})
        effects = list(reads)
        for relative in touched:
            effects.append({
                "path": str(workspace / relative),
                "kind": "D" if after.get(relative) == "deleted" else "W",
            })
        parents = set()
        for effect in effects:
            parent = last_writer.get(effect["path"])
            if parent is not None and parent != index:
                parents.add(parent)
        for effect in effects:
            if effect["kind"] in {"W", "D"}:
                last_writer[effect["path"]] = index
        item = window["item"]
        ledger_steps.append({
            "step_id": index, "tool_name": "codex:" + item["type"],
            "effects": effects, "parents": sorted(parents), "status": "committed",
            "opaque_context": item["type"] == "web_search",
        })
        # Codex exec emits one CLI turn for the task. Each tool event remains a
        # step within that turn; we do not invent model-turn boundaries.
        calls.append({
            "call_id": str(item.get("id", index)),
            "tool_name": "codex:" + item["type"],
            "arguments": json.dumps(item, sort_keys=True),
            "step_id": index, "parents": sorted(parents), "status": "active",
        })
    usage = _parse_jsonl("\n".join(json.dumps(event) for event in events))[1]
    complete = (
        returncode == 0 and bool(windows)
        and any(e.get("type") == "turn.completed" for e in events)
    )
    return {
        "schema": "agenttx.clean_native_trace/v2", "task": task,
        "suite": "terminal_bench" if suite == "tb" else "swe_bench_lite",
        "source_system": "Codex native / Luna max / official Docker image",
        "source_completed": complete, "source_success": None,
        "model_finished": complete,
        "termination_reason": "submitted" if complete else f"codex exit {returncode}",
        "workspace_root": str(workspace), "config": profile,
        "fault_injected": False, "precrash_progress_seeded": False,
        "trace_method": "Codex JSON event intervals + strace reads + git-visible write snapshots",
        "turn_boundary": "one native codex exec turn; tool events are steps",
        "ledger": {"steps": ledger_steps},
        "conversation": {"turns": [{"turn_id": 0, "kind": "effect", "calls": calls}]},
        "result": {
            "summary": "submitted" if complete else f"codex exit {returncode}",
            "tool_calls": len(ledger_steps), "model_calls": None,
            "prompt_tokens": usage[0], "completion_tokens": usage[1],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--suite", choices=("swe", "tb"), required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--reasoning-effort", default="max")
    parser.add_argument("--max-steps", type=int, default=80)
    parser.add_argument("--max-model-calls", type=int, default=80)
    args = parser.parse_args()
    if os.environ.get("AGENTTX_MOTIVATION_BWRAP") != "1":
        raise SystemExit("native image capture must run in the disposable task container")
    workspace = args.workspace.resolve()
    subprocess.run(
        ["git", "config", "--global", "--add", "safe.directory", str(workspace)],
        check=True,
    )
    profile = load_codex_gpt_profile(
        model_override=args.model, reasoning_effort_override=args.reasoning_effort,
    )
    codex_home = Path("/root/.codex")
    codex_home.mkdir(exist_ok=True)
    for name in ("config.toml", "auth.json"):
        source = Path("/home/bfq/.codex") / name
        (codex_home / name).write_bytes(source.read_bytes())
    os.chmod(codex_home / "auth.json", 0o600)
    env = dict(os.environ)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(key, None)
    env.update({"CODEX_HOME": str(codex_home), "NO_PROXY": "*", "no_proxy": "*"})
    env.pop("OPENAI_API_KEY", None)
    env.pop("CODEX_API_KEY", None)
    trace_path = args.output_dir / "codex.strace"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "strace", "-f", "-ttt", "-yy", "-s", "4096", "-e", "trace=%file,process",
        "-o", str(trace_path), "codex", "exec", "--model", args.model,
        "-c", f'model_reasoning_effort="{args.reasoning_effort}"',
        "-c", "features.plugins=false", "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check", "--json", "-C", str(workspace),
        args.prompt_file.read_text(encoding="utf-8"),
    ]
    stderr_log = (args.output_dir / "codex.stderr.log").open("w+", encoding="utf-8")
    process = subprocess.Popen(
        command, cwd=workspace, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=stderr_log, text=True, bufsize=1,
        start_new_session=True,
    )
    events = []
    windows = []
    snapshots = [workspace_snapshot(workspace)]
    active = {}
    invalid = []
    assert process.stdout is not None
    for line in process.stdout:
        stamp = time.time()
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        events.append(event)
        item = event.get("item") or {}
        event_type = event.get("type")
        if event_type == "item.started" and item.get("type") in TOOL_TYPES:
            active[str(item.get("id"))] = stamp
        elif event_type == "item.completed" and item.get("type") in TOOL_TYPES:
            start = active.pop(str(item.get("id")), None)
            if start is None:
                invalid.append("tool_completion_without_start")
                start = stamp
            windows.append({"start": start, "end": stamp, "item": item})
            snapshots.append(workspace_snapshot(workspace))
            if len(windows) >= args.max_steps:
                invalid.append("step_limit")
                os.killpg(process.pid, signal.SIGTERM)
                break
        elif event_type == "item.completed" and item.get("type") in {
            "mcp_tool_call", "dynamic_tool_call"
        }:
            invalid.append("unsupported_tool:" + str(item.get("type")))
    try:
        returncode = process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        returncode = process.wait(timeout=15)
    stderr_log.flush()
    stderr_log.seek(0)
    stderr = stderr_log.read()
    stderr_log.close()
    if active:
        invalid.append("unfinished_tool_event")
    if "failed to spawn code-mode host" in stderr:
        invalid.append("code_mode_host_unavailable")
    if not windows:
        invalid.append("no_tool_steps")
    if snapshots[-1] != workspace_snapshot(workspace):
        invalid.append("unattributed_workspace_change")
    record = assemble_record(
        args.task, args.suite, workspace, events, windows, snapshots, trace_path,
        returncode, profile.public_dict(),
    )
    invalid.extend(overlapping_write_exclusions(windows, record["ledger"]["steps"]))
    record["capture_valid"] = not invalid
    record["capture_exclusions"] = sorted(set(invalid))
    record["event_windows"] = [
        {
            "id": str(window["item"].get("id")),
            "type": str(window["item"].get("type")),
            "start": window["start"], "end": window["end"],
        }
        for window in windows
    ]
    record["source_completed"] = record["source_completed"] and not invalid
    record["codex_stderr_tail"] = stderr[-2000:]
    (args.output_dir / "codex.events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8",
    )
    path = args.output_dir / "native_traces" / f"codex__{args.suite}__{args.task}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"codex image-native done {args.task}: steps={len(windows)} valid={not invalid}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
