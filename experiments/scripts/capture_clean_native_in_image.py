#!/usr/bin/env python3
"""Run one fault-free SWE-agent trajectory inside its official task image."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from swerex.deployment.config import LocalDeploymentConfig, get_deployment

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from agenttx.agents.swe_agent import create_swe_agent_adapter  # noqa: E402
from experiments.scripts.sweagent_native_workload import (  # noqa: E402
    load_agent_config,
    load_codex_gpt_profile,
)
from sweagent.agent.agents import get_agent_from_config  # noqa: E402
from sweagent.agent.problem_statement import TextProblemStatement  # noqa: E402
from sweagent.environment.swe_env import SWEEnv  # noqa: E402


def prime_docker_resolver(adapter, workspace: Path) -> None:
    """Preserve Docker's mounted DNS configuration in try's overlay baseline."""
    pool = adapter.harness.tx.pool
    assert pool is not None
    source = Path("/etc/resolv.conf")
    if not source.read_text(encoding="utf-8").strip():
        raise RuntimeError("Docker task image has no DNS resolver configuration")
    template = Path("/var/tmp/agenttx-docker-resolv.conf")
    shutil.copyfile(source, template)
    subprocess.run(
        [str(pool.try_bin), "-N", str(pool.session_dir), "--", "cp",
         str(template), "/etc/resolv.conf"],
        cwd=str(workspace), check=True, timeout=30,
    )
    pool.refresh_summary()
    pool._cached_digests = pool.upperdir_digests()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--suite", choices=("swe", "tb"), default="tb")
    parser.add_argument("--workspace", type=Path, default=Path("/app"))
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--reasoning-effort", default="max")
    parser.add_argument("--max-steps", type=int, default=80)
    parser.add_argument("--max-model-calls", type=int, default=80)
    args = parser.parse_args()
    if os.environ.get("AGENTTX_MOTIVATION_BWRAP") != "1":
        raise SystemExit("native image capture must run in the disposable task container")
    workspace = args.workspace
    prompt = args.prompt_file.read_text(encoding="utf-8")
    profile = load_codex_gpt_profile(
        model_override=args.model,
        reasoning_effort_override=args.reasoning_effort,
    )
    os.environ["OPENAI_API_KEY"] = profile.api_key
    config, config_hash = load_agent_config(
        Path("/home/bfq/SWE-agent/config/bash_only.yaml"),
        profile=profile,
        max_model_calls=args.max_model_calls,
    )
    output = args.output_dir / "agent_outputs" / args.suite / args.task
    output.mkdir(parents=True, exist_ok=True)
    agent = get_agent_from_config(config)
    environment = SWEEnv(
        deployment=get_deployment(LocalDeploymentConfig()),
        repo=None,
        post_startup_commands=[],
    )
    adapter = None
    try:
        environment.start()
        environment.communicate(f"cd -- {workspace}", check="raise")
        adapter = create_swe_agent_adapter(
            agent,
            workdir=workspace,
            session_dir=Path("/tmp/agenttx-clean-session"),
            trace_backend="strace",
            ephemeral_roots=(
                Path("/root"), Path("/tmp"),
                Path("/usr/share/texmf/fonts/opentype/public/lm-math"),
                Path("/usr/share/texmf/fonts/opentype/public/lm"),
                Path("/usr/share/texmf/fonts/opentype/public/tex-gyre-math"),
                Path("/usr/share/texmf/fonts/opentype/public/tex-gyre"),
            ),
        )
        adapter.setup(
            environment,
            TextProblemStatement(text=prompt, id=args.task),
            output,
        )
        prime_docker_resolver(adapter, workspace)
        completed = False
        for _ in range(args.max_steps):
            step = adapter.step()
            agent.save_trajectory()
            if bool(getattr(step, "done", False)):
                completed = True
                break
        result = adapter.finish(commit=False)
        commit_error = None
        try:
            adapter.commit()
            result.committed = True
            result.ledger = adapter.harness.tx.ledger.to_dict()
        except Exception as exc:
            commit_error = f"{type(exc).__name__}: {exc}"
        model_finished = completed and str(result.summary).startswith("submitted")
        submitted = model_finished and commit_error is None
        record = {
            "schema": "agenttx.clean_native_trace/v2",
            "task": args.task,
            "suite": "terminal_bench" if args.suite == "tb" else "swe_bench_lite",
            "source_system": "SWE-agent native / Luna max / official Docker image",
            "source_completed": submitted,
            "model_finished": model_finished,
            "step_limit_reached": not completed,
            "termination_reason": str(result.summary),
            "source_success": None,
            "commit_error": commit_error,
            "workspace_root": str(workspace),
            "config": {
                **profile.public_dict(),
                "sweagent_config_hash": config_hash,
            },
            "fault_injected": False,
            "precrash_progress_seeded": False,
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
        path = args.output_dir / "native_traces" / f"{args.suite}__{args.task}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(
            f"image-native done {args.task}: steps={len(result.ledger['steps'])} "
            f"turns={len(record['conversation']['turns'])}",
            flush=True,
        )
        return 0
    finally:
        if adapter is not None:
            adapter.close(destroy=True)
        environment.close()


if __name__ == "__main__":
    raise SystemExit(main())
