#!/usr/bin/env python3
"""Replay successful SWE-agent trajectories through the native AgentTX adapter."""

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

from swerex.deployment.config import LocalDeploymentConfig, get_deployment


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from agenttx.agents.swe_agent import create_swe_agent_adapter  # noqa: E402
from sweagent.environment.swe_env import SWEEnv  # noqa: E402
from sweagent.run.run_replay import RunReplay  # noqa: E402
from sweagent.run.run_single import RunSingleConfig  # noqa: E402


def clone_workspace(source: Path, destination: Path) -> None:
    subprocess.run(
        ["git", "clone", "--quiet", "--shared", str(source), str(destination)],
        check=True,
    )
    subprocess.run(
        ["git", "checkout", "--quiet", "HEAD"], cwd=destination, check=True
    )


def _rewrite_workspace(value: object, workspace: Path) -> object:
    if isinstance(value, str):
        return value.replace("/testbed", str(workspace))
    if isinstance(value, list):
        return [_rewrite_workspace(item, workspace) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite_workspace(item, workspace) for key, item in value.items()}
    return value


def normalized_trajectory(source: Path, destination: Path, workspace: Path) -> dict:
    data = json.loads(source.read_text(encoding="utf-8"))
    replay_config = data.get("replay_config")
    if isinstance(replay_config, str):
        replay_config = json.loads(replay_config)
        data["replay_config"] = replay_config
    bundles = (
        replay_config.get("agent", {}).get("tools", {}).get("bundles", [])
        if isinstance(replay_config, dict)
        else []
    )
    local_tools = Path("/home/bfq/SWE-agent/tools")
    for bundle in bundles:
        if not isinstance(bundle, dict) or not bundle.get("path"):
            continue
        bundle["path"] = str(local_tools / Path(str(bundle["path"])).name)
    data["history"] = _rewrite_workspace(data.get("history") or [], workspace)
    parse_type = (
        replay_config.get("agent", {})
        .get("tools", {})
        .get("parse_function", {})
        .get("type")
        if isinstance(replay_config, dict)
        else None
    )
    if parse_type == "function_calling":
        data["history"] = [
            item
            for item in data["history"]
            if not (
                isinstance(item, dict)
                and item.get("role") == "assistant"
                and not item.get("tool_calls")
            )
        ]
    destination.write_text(json.dumps(data) + "\n", encoding="utf-8")
    return data


def replay_one(
    trajectory: Path,
    repo_cache: Path,
    output_dir: Path,
    max_steps: int,
) -> dict:
    if os.environ.get("AGENTTX_MOTIVATION_BWRAP") != "1":
        raise RuntimeError("native replay must run in the dedicated bwrap namespace")
    # `/root` is a private tmpfs in that namespace. SWE-ReX requires bundle
    # upload destinations not to exist when a new case begins.
    shutil.rmtree("/root/tools", ignore_errors=True)
    Path("/root/state.json").unlink(missing_ok=True)
    Path("/root/.swe-agent-env").unlink(missing_ok=True)
    task = trajectory.stem
    source_repo = repo_cache / task
    if not source_repo.is_dir():
        raise FileNotFoundError(f"missing cached repository: {source_repo}")
    scratch = Path(tempfile.mkdtemp(prefix=f"agenttx-native-replay-{task}-", dir="/tmp"))
    workspace = scratch / "workspace"
    session = scratch / "session"
    replay_output = output_dir / "native_replay" / task
    replay_output.mkdir(parents=True, exist_ok=True)
    clone_workspace(source_repo, workspace)
    normalized_path = scratch / f"{task}.traj"
    source = normalized_trajectory(trajectory, normalized_path, workspace)

    deployment = get_deployment(LocalDeploymentConfig())
    env = SWEEnv(deployment=deployment, repo=None, post_startup_commands=[])
    adapter = None
    replay = RunReplay(
        traj_path=normalized_path,
        deployment=deployment,
        output_dir=replay_output,
        _catch_errors=False,
        _require_zero_exit_code=False,
    )
    replay._create_actions_file()
    agent = replay._get_agent()
    agent.replay_config = RunSingleConfig(
        agent=replay.config.agent,
        problem_statement=replay.config.problem_statement,
        env=replay.config.env,
    )
    try:
        env.start()
        env.communicate(f"cd -- {workspace}", check="raise")
        adapter = create_swe_agent_adapter(
            agent,
            workdir=workspace,
            session_dir=session,
            trace_backend="strace",
            ephemeral_roots=(scratch,),
        )
        adapter.setup(env, replay.config.problem_statement, replay_output)
        completed = 0
        replay_model = agent.model
        replay_actions = len(replay_model._replays[0])
        while replay_model._action_idx < replay_actions:
            if completed >= max_steps:
                raise RuntimeError(f"native replay exceeded max_steps={max_steps}")
            step_output = adapter.step()
            completed += 1
            agent.save_trajectory()
            if bool(getattr(step_output, "done", False)):
                break
        result = adapter.finish(commit=False)
        record = {
            "schema": "agenttx.motivation.native_replay/v1",
            "task": task,
            "source_trajectory": str(trajectory.resolve()),
            "source_success": True,
            "source_system": "SWE-agent 1.0 + Claude 3.7 Sonnet",
            "source_info": source.get("info") or {},
            "replay_completed": True,
            "native_adapter": "SWEAgentNativeAdapter",
            "ledger": result.ledger,
            "conversation": adapter.harness.tx.conversation.to_dict(),
            "result": {
                "summary": result.summary,
                "tool_calls": result.tool_calls,
                "model_calls": result.model_calls,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
            },
        }
        (output_dir / "native_traces").mkdir(parents=True, exist_ok=True)
        (output_dir / "native_traces" / f"{task}.json").write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )
        return record
    finally:
        if adapter is not None:
            adapter.close(destroy=True)
        try:
            env.close()
        finally:
            shutil.rmtree(scratch, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trajectory-dir", type=Path, required=True)
    parser.add_argument(
        "--repo-cache",
        type=Path,
        default=ROOT / "experiments" / "cache" / "swe_bench" / "repos",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--tasks", nargs="*", default=[])
    parser.add_argument("--max-steps", type=int, default=100)
    args = parser.parse_args()
    trajectories = sorted(args.trajectory_dir.glob("*.traj"))
    if args.tasks:
        wanted = set(args.tasks)
        trajectories = [path for path in trajectories if path.stem in wanted]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    failures = []
    for index, trajectory in enumerate(trajectories, 1):
        print(f"native replay {index}/{len(trajectories)} {trajectory.stem}", flush=True)
        try:
            record = replay_one(
                trajectory, args.repo_cache, args.output_dir, args.max_steps
            )
            turns = record["conversation"]["turns"]
            print(
                f"completed {trajectory.stem}: ledger={len(record['ledger']['steps'])} "
                f"turns={len(turns)} max_calls={max((len(t['calls']) for t in turns), default=0)}",
                flush=True,
            )
        except Exception as exc:
            failures.append({"task": trajectory.stem, "error": repr(exc)})
            traceback.print_exc()
    (args.output_dir / f"native_replay_failures_{os.getpid()}.json").write_text(
        json.dumps(failures, indent=2) + "\n", encoding="utf-8"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
