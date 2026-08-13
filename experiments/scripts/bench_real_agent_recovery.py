#!/usr/bin/env python3
"""Measure whether a real LLM agent can select and execute causal recovery."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Sequence


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from experiments.scripts.bench_real_agent import (  # noqa: E402
    _cleanup,
    _run_host_tests,
    _tree_snapshot,
    load_llm_env,
)
from experiments.scripts.bench_robustness import percentile  # noqa: E402
from experiments.workloads.recovery_agent import (  # noqa: E402
    CORRECT_PIPELINE,
    INDEPENDENT_NOTE,
    RECOVERY_TASK,
    inject_recovery_failure,
    seed_recovery_repo,
)
from agenttx.providers import configured_provider, provider_names, provider_result_dir, resolve_provider  # noqa: E402


def run_once(repeat: int, model: Optional[str], max_turns: int, provider: Optional[str]) -> dict:
    from agenttx.agents.llm_agent import LLMToolAgent

    scratch = Path(tempfile.mkdtemp(prefix=f"agenttx-real-recovery-{repeat}-", dir="/tmp"))
    workdir = scratch / "ws"
    workdir.mkdir()
    seed_recovery_repo(workdir)
    baseline = _tree_snapshot(workdir)
    agent = None
    started = time.perf_counter()
    error = ""
    finished = False
    tool_calls = 0
    ledger_steps = 0
    inspect_calls = 0
    rollback_calls = 0
    chosen_step = ""
    rollback_targets: list[int] = []
    committed = False
    host_polluted_before_commit = False
    independent_retained = False
    derived_removed = False
    tests_rc = ""
    pipeline_restored = False
    recovery_note_written = False
    injection = {}
    try:
        agent = LLMToolAgent(
            workdir=workdir,
            session_dir=scratch / "session",
            model=model,
            provider=provider,
            max_turns=max_turns,
        )
        injection = inject_recovery_failure(agent)
        if not (
            injection["tests_failed"]
            and injection["root_is_parent_of_derived"]
            and injection["root_is_parent_of_tests"]
            and not injection["independent_is_parent_of_derived"]
        ):
            raise RuntimeError(f"invalid injected dependency graph: {injection}")

        result = agent.run(RECOVERY_TASK, commit=False)
        finished = result.finished
        tool_calls = result.tool_calls
        ledger_steps = len(result.ledger.get("steps", []))
        inspections = [
            event for event in result.control_events if event.get("tool") == "inspect_ledger"
        ]
        rollbacks = [
            event for event in result.control_events if event.get("tool") == "rollback_causal"
        ]
        inspect_calls = len(inspections)
        rollback_calls = len(rollbacks)
        if rollbacks:
            chosen_step = int(rollbacks[0]["step_id"])
            rollback_targets = [int(target) for target in rollbacks[0]["targets"]]

        host_polluted_before_commit = baseline != _tree_snapshot(workdir)
        independent_retained = agent.harness.tx.path_exists(workdir / "notes" / "independent.md")
        derived_removed = not agent.harness.tx.path_exists(workdir / "artifacts" / "rendered.txt")
        active = [
            step.step_id
            for step in agent.harness.tx.ledger.steps
            if step.status != "rolled_back"
            and step.step_id > agent.harness.tx.ledger.committed_frontier
        ]
        if active:
            up_to = max(active)
            agent.harness.policy.assert_committable(agent.harness.tx.ledger, up_to)
            agent.harness.tx.commit(up_to)
            committed = True

        tests_rc = _run_host_tests(workdir)
        pipeline_restored = (
            workdir / "src" / "pipeline.py"
        ).read_text(encoding="utf-8") == CORRECT_PIPELINE
        independent_retained = independent_retained and (
            workdir / "notes" / "independent.md"
        ).read_text(encoding="utf-8") == INDEPENDENT_NOTE
        derived_removed = derived_removed and not (
            workdir / "artifacts" / "rendered.txt"
        ).exists()
        recovery_note_written = (workdir / "notes" / "RECOVERY.md").is_file()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        if agent is not None:
            agent.close(destroy=True)
        _cleanup(scratch)

    root = injection.get("root_step", -1)
    independent = injection.get("independent_step", -1)
    derived = injection.get("derived_step", -1)
    test_step = injection.get("test_step", -1)
    selected_root = chosen_step == root
    causal_targets_correct = bool(
        rollback_targets
        and root in rollback_targets
        and derived in rollback_targets
        and test_step in rollback_targets
        and independent not in rollback_targets
    )
    success = bool(
        finished
        and inspect_calls > 0
        and rollback_calls == 1
        and selected_root
        and causal_targets_correct
        and not host_polluted_before_commit
        and committed
        and independent_retained
        and derived_removed
        and pipeline_restored
        and recovery_note_written
        and tests_rc == 0
    )
    return {
        "repeat": repeat,
        "provider": resolve_provider(provider).name,
        "model": model or resolve_provider(provider).model,
        "wall_s": round(time.perf_counter() - started, 6),
        "finished": finished,
        "tool_calls": tool_calls,
        "ledger_steps": ledger_steps,
        "inspect_calls": inspect_calls,
        "rollback_calls": rollback_calls,
        "injected_root_step": root,
        "chosen_step": chosen_step,
        "rollback_targets": rollback_targets,
        "selected_root": selected_root,
        "causal_targets_correct": causal_targets_correct,
        "host_polluted_before_commit": host_polluted_before_commit,
        "committed": committed,
        "independent_retained": independent_retained,
        "derived_removed": derived_removed,
        "pipeline_restored": pipeline_restored,
        "recovery_note_written": recovery_note_written,
        "tests_rc": tests_rc,
        "success": success,
        "error": error,
    }


def summarize(rows: Sequence[dict]) -> dict:
    walls = [float(row["wall_s"]) for row in rows]
    count = max(len(rows), 1)
    rate = lambda key: round(sum(bool(row[key]) for row in rows) / count, 6)
    return {
        "suite": "real_agent_causal_recovery",
        "repeats": len(rows),
        "model": rows[0].get("model", "") if rows else "",
        "provider": rows[0].get("provider", "") if rows else "",
        "wall_p50_s": round(percentile(walls, 0.50), 6),
        "wall_p95_s": round(percentile(walls, 0.95), 6),
        "success_rate": rate("success"),
        "root_selection_rate": rate("selected_root"),
        "causal_target_rate": rate("causal_targets_correct"),
        "independent_retention_rate": rate("independent_retained"),
        "derived_removal_rate": rate("derived_removed"),
        "tests_pass_rate": round(sum(row["tests_rc"] == 0 for row in rows) / count, 6),
        "host_leak_rate": rate("host_polluted_before_commit"),
        "rows": list(rows),
    }


def write_outputs(summary: dict, provider: Optional[str] = None) -> None:
    output = provider_result_dir(ROOT, provider)
    output.mkdir(parents=True, exist_ok=True)
    rows = summary["rows"]
    (output / "real_agent_recovery.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    fields = list(rows[0]) if rows else []
    with (output / "real_agent_recovery.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            serialized = dict(row)
            serialized["rollback_targets"] = json.dumps(row["rollback_targets"])
            writer.writerow(serialized)
    lines = [
        "# Real-agent causal recovery",
        "",
        f"Provider: `{summary['provider']}`; model: `{summary['model']}`; repeats: {summary['repeats']}.",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| wall p50 / p95 (s) | {summary['wall_p50_s']} / {summary['wall_p95_s']} |",
        f"| full recovery success rate | {summary['success_rate']} |",
        f"| faulty-root selection rate | {summary['root_selection_rate']} |",
        f"| correct causal-target rate | {summary['causal_target_rate']} |",
        f"| independent-work retention rate | {summary['independent_retention_rate']} |",
        f"| invalid-derived removal rate | {summary['derived_removal_rate']} |",
        f"| tests pass rate | {summary['tests_pass_rate']} |",
        f"| host leak rate before commit | {summary['host_leak_rate']} |",
        "",
        "Each repeat starts from a fresh workspace. The agent must inspect the ledger, choose the injected faulty root, invoke causal rollback exactly once, preserve an independent note, remove a derived artifact, and pass tests before commit.",
    ]
    (output / "real_agent_recovery.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print(f"wrote {output / 'real_agent_recovery.csv'}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-turns", type=int, default=30)
    parser.add_argument("--model", default=None)
    parser.add_argument("--provider", choices=provider_names(), default=None)
    args = parser.parse_args()
    if args.repeats <= 0 or args.max_turns <= 0:
        parser.error("repeats and max-turns must be positive")
    load_llm_env()
    if not configured_provider(args.provider):
        profile = resolve_provider(args.provider)
        print(f"skip: no {profile.name.upper()}_API_KEY", file=sys.stderr)
        return 0

    rows: List[dict] = []
    for repeat in range(args.repeats):
        row = run_once(repeat, args.model, args.max_turns, args.provider)
        rows.append(row)
        print(json.dumps({key: value for key, value in row.items() if key != "error"}, indent=2), flush=True)
        if row["error"]:
            print(f"error: {row['error']}", file=sys.stderr, flush=True)
    write_outputs(summarize(rows), args.provider)
    return 0 if rows and all(row["success"] for row in rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
