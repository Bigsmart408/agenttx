#!/usr/bin/env python3
"""Measure total autonomous-recovery tokens after three rollback policies.

Step 24 isolates tokens spent replaying known lost artifacts. This companion
keeps the planning loop intact: after each real filesystem rollback, the same
LLM agent receives the same task, runs tools, diagnoses missing work, repairs
the workspace, and finishes. API usage from every recovery turn is charged.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence

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
from experiments.scripts.bench_token_recovery import (  # noqa: E402
    MODES,
    REPLAY_DOCUMENTS,
    _apply_policy,
)
from experiments.scripts.runtime_preflight import (  # noqa: E402
    format_preflight,
    runtime_preflight,
)
from experiments.workloads.token_recovery_agent import (  # noqa: E402
    CORRECT_PIPELINE,
    DOCUMENT_LINES,
    inject_token_recovery_trajectory,
    seed_token_recovery_repo,
)
from agenttx.providers import configured_provider, provider_names, provider_result_dir, resolve_provider  # noqa: E402

DOCUMENT_PATHS = ("docs/design.md", "docs/changelog.md")


def recovery_task(document_lines: int) -> str:
    """Return the identical autonomous recovery prompt used by every policy."""
    return f"""A filesystem recovery policy has already been applied to this
protected coding session. Diagnose the resulting workspace and finish recovery
with the minimum regenerated work.

1. Run `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python -m pytest -q -p no:cacheprovider`.
2. If a document fails validation, recreate only that document with `write_file`.
3. `docs/design.md` must start with `# Design invariants` and contain exactly
   {document_lines} ordered entries `DESIGN-001:` through `DESIGN-{document_lines:03d}:`.
4. `docs/changelog.md` must start with `# Change log` and contain exactly
   {document_lines} ordered entries `CHANGE-001:` through `CHANGE-{document_lines:03d}:`.
5. Preserve every valid document. Do not rewrite a document that already passes.
6. Ensure `artifacts/rendered.txt` is absent and the tests pass.
7. Do not call a rollback tool: the comparison policy has already run.
8. Call `finish` with `commit=false` and a one-sentence summary when done.

Stay inside the workspace. Do not use shell loops, Python generators, or
`append_file` to generate documents. This experiment charges all API usage from
the complete diagnosis, tool-use, validation, and repair loop.
"""


def regenerated_documents(steps: Sequence, first_step: int) -> List[str]:
    """Return target documents recreated through the required write_file tool."""
    found = set()
    for step in steps[first_step:]:
        if step.tool_name != "write_file" or step.status == "rolled_back":
            continue
        for effect in step.effects:
            normalized = effect.path.lstrip("./")
            for relative in DOCUMENT_PATHS:
                if normalized == relative or normalized.endswith("/" + relative):
                    found.add(relative)
    return [path for path in DOCUMENT_PATHS if path in found]


def _document_contract_valid(path: Path, prefix: str, lines: int) -> bool:
    if not path.exists():
        return False
    content = path.read_text(encoding="utf-8").splitlines()
    title = "# Design invariants" if prefix == "DESIGN" else "# Change log"
    expected = [f"{prefix}-{index:03d}:" for index in range(1, lines + 1)]
    return bool(
        len(content) == lines + 1
        and content[0] == title
        and all(line.startswith(label) for line, label in zip(content[1:], expected))
    )


def _policy_targets_correct(mode: str, injected: dict, targets: Sequence[int]) -> bool:
    target_set = set(targets)
    root = injected.get("root_step", -1)
    prefix = injected.get("prefix_step", -1)
    independent = injected.get("independent_step", -1)
    derived = injected.get("derived_step", -1)
    test_step = injected.get("test_step", -1)
    return bool(
        root in target_set
        and derived in target_set
        and test_step in target_set
        and (
            (mode == "causal" and prefix not in target_set and independent not in target_set)
            or (
                mode == "temporal_checkpoint"
                and prefix not in target_set
                and independent in target_set
            )
            or (
                mode == "whole_branch_abort"
                and prefix in target_set
                and independent in target_set
            )
        )
    )


def run_once(
    mode: str,
    repeat: int,
    model: Optional[str],
    max_turns: int,
    provider: Optional[str],
    document_lines: int = DOCUMENT_LINES,
) -> dict:
    """Run one policy, one complete recovery loop, commit, and validation."""
    from agenttx.agents.llm_agent import LLMToolAgent

    scratch = Path(
        tempfile.mkdtemp(prefix=f"agenttx-token-e2e-{mode}-{repeat}-", dir="/tmp")
    )
    workdir = scratch / "ws"
    workdir.mkdir()
    seed_token_recovery_repo(workdir, document_lines)
    baseline = _tree_snapshot(workdir)
    agent = None
    injected: dict = {}
    rollback_targets: List[int] = []
    policy_ms = 0.0
    recovery_started = 0.0
    recovery_wall_s = 0.0
    error = ""
    finished = False
    finish_called = False
    committed = False
    host_polluted_before_commit = False
    tests_rc: object = ""
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    tool_calls = 0
    model_calls = 0
    recovery_ledger_steps = 0
    regenerated: List[str] = []
    unauthorized_rollback = False
    design_valid = False
    changelog_valid = False
    pipeline_restored = False
    derived_removed = False
    try:
        agent = LLMToolAgent(
            workdir=workdir,
            session_dir=scratch / "session",
            model=model,
            max_turns=max_turns,
            provider=provider,
        )
        injected = inject_token_recovery_trajectory(agent, document_lines)
        graph_valid = bool(
            injected["tests_failed"]
            and injected["root_is_parent_of_derived"]
            and injected["root_is_parent_of_tests"]
            and not injected["independent_is_parent_of_derived"]
        )
        if not graph_valid:
            raise RuntimeError(f"invalid injected dependency graph: {injected}")

        policy_started = time.perf_counter()
        rollback_targets = _apply_policy(agent, mode, injected)
        policy_ms = 1000.0 * (time.perf_counter() - policy_started)
        recovery_first_step = len(agent.harness.tx.ledger.steps)
        recovery_started = time.perf_counter()
        result = agent.run(recovery_task(document_lines), commit=False)
        finished = result.finished
        finish_called = any(
            call.get("function", {}).get("name") == "finish"
            for message in result.messages
            for call in (message.get("tool_calls") or [])
        )
        prompt_tokens = int(result.prompt_tokens)
        completion_tokens = int(result.completion_tokens)
        total_tokens = int(result.total_tokens)
        tool_calls = int(result.tool_calls)
        model_calls = sum(message.get("role") == "assistant" for message in result.messages)
        unauthorized_rollback = any(
            event.get("tool") == "rollback_causal" for event in result.control_events
        )
        recovery_ledger_steps = len(agent.harness.tx.ledger.steps) - recovery_first_step
        regenerated = regenerated_documents(agent.harness.tx.ledger.steps, recovery_first_step)
        host_polluted_before_commit = baseline != _tree_snapshot(workdir)
        if result.committed:
            raise RuntimeError("recovery agent committed before boundary validation")

        active = [
            step.step_id
            for step in agent.harness.tx.ledger.steps
            if step.status != "rolled_back"
            and step.step_id > agent.harness.tx.ledger.committed_frontier
        ]
        if active:
            agent.harness.tx.commit(max(active))
            committed = True

        tests_rc = _run_host_tests(workdir)
        design_valid = _document_contract_valid(
            workdir / "docs" / "design.md", "DESIGN", document_lines
        )
        changelog_valid = _document_contract_valid(
            workdir / "docs" / "changelog.md", "CHANGE", document_lines
        )
        pipeline_restored = (
            workdir / "src" / "pipeline.py"
        ).read_text(encoding="utf-8") == CORRECT_PIPELINE
        derived_removed = not (workdir / "artifacts" / "rendered.txt").exists()
        recovery_wall_s = time.perf_counter() - recovery_started
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:500]
        if recovery_started:
            recovery_wall_s = time.perf_counter() - recovery_started
    finally:
        if agent is not None:
            agent.close(destroy=True)
        _cleanup(scratch)

    expected_regenerated = list(REPLAY_DOCUMENTS[mode])
    policy_targets_correct = _policy_targets_correct(mode, injected, rollback_targets)
    regeneration_compliant = regenerated == expected_regenerated
    success = bool(
        finished
        and finish_called
        and committed
        and prompt_tokens > 0
        and completion_tokens > 0
        and policy_targets_correct
        and regeneration_compliant
        and not unauthorized_rollback
        and not host_polluted_before_commit
        and tests_rc == 0
        and design_valid
        and changelog_valid
        and pipeline_restored
        and derived_removed
        and not error
    )
    return {
        "mode": mode,
        "document_lines": document_lines,
        "repeat": repeat,
        "provider": resolve_provider(provider).name,
        "model": model or resolve_provider(provider).model,
        "max_turns": max_turns,
        "finish_called": finish_called,
        "finished": finished,
        "committed": committed,
        "policy_ms": round(policy_ms, 6),
        "recovery_wall_s": round(recovery_wall_s, 6),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "tool_calls": tool_calls,
        "model_calls": model_calls,
        "recovery_ledger_steps": recovery_ledger_steps,
        "rollback_targets": rollback_targets,
        "policy_targets_correct": policy_targets_correct,
        "expected_regenerated_documents": expected_regenerated,
        "regenerated_documents": regenerated,
        "regenerated_document_count": len(regenerated),
        "regeneration_compliant": regeneration_compliant,
        "unauthorized_rollback": unauthorized_rollback,
        "host_polluted_before_commit": host_polluted_before_commit,
        "design_valid": design_valid,
        "changelog_valid": changelog_valid,
        "pipeline_restored": pipeline_restored,
        "derived_removed": derived_removed,
        "tests_rc": tests_rc,
        "success": success,
        "error": error,
    }


def summarize(rows: Sequence[dict]) -> List[dict]:
    groups: Dict[tuple, List[dict]] = defaultdict(list)
    for row in rows:
        groups[(int(row["document_lines"]), str(row["mode"]))].append(row)

    summary: List[dict] = []
    for document_lines in sorted({key[0] for key in groups}):
        size_rows: List[dict] = []
        for mode in MODES:
            samples = groups.get((document_lines, mode), [])
            if not samples:
                continue
            item = {
                "document_lines": document_lines,
                "mode": mode,
                "provider": samples[0].get("provider", ""),
                "model": samples[0]["model"],
                "repeats": len(samples),
                "success_rate": round(
                    sum(bool(sample["success"]) for sample in samples) / len(samples), 6
                ),
                "host_leak_rate": round(
                    sum(bool(sample["host_polluted_before_commit"]) for sample in samples)
                    / len(samples),
                    6,
                ),
                "regenerated_documents_mean": round(
                    sum(int(sample["regenerated_document_count"]) for sample in samples)
                    / len(samples),
                    6,
                ),
            }
            for metric in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "tool_calls",
                "model_calls",
                "recovery_ledger_steps",
                "policy_ms",
                "recovery_wall_s",
            ):
                values = [float(sample[metric]) for sample in samples]
                item[f"{metric}_mean"] = round(sum(values) / len(values), 6)
                item[f"{metric}_p50"] = round(percentile(values, 0.50), 6)
                item[f"{metric}_p95"] = round(percentile(values, 0.95), 6)
            summary.append(item)
            size_rows.append(item)

        causal = next((item for item in size_rows if item["mode"] == "causal"), None)
        if causal is None:
            continue
        for item in size_rows:
            for metric in ("prompt_tokens", "completion_tokens", "total_tokens"):
                baseline = float(item[f"{metric}_mean"])
                causal_value = float(causal[f"{metric}_mean"])
                saved = baseline - causal_value
                item[f"agenttx_{metric}_saved"] = round(saved, 6)
                item[f"agenttx_{metric}_saved_pct"] = round(
                    saved / baseline if baseline else 0.0, 6
                )
    return summary


def write_outputs(raw_rows: Sequence[dict], summary_rows: Sequence[dict], provider: Optional[str] = None) -> None:
    output = provider_result_dir(ROOT, provider)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "token_end_to_end_raw.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        fields = list(raw_rows[0]) if raw_rows else []
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in raw_rows:
            serialized = dict(row)
            for field in (
                "rollback_targets",
                "expected_regenerated_documents",
                "regenerated_documents",
            ):
                serialized[field] = json.dumps(row[field])
            writer.writerow(serialized)

    with (output / "token_end_to_end.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        fields = list(summary_rows[0]) if summary_rows else []
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(summary_rows)

    (output / "token_end_to_end.json").write_text(
        json.dumps({"summary": list(summary_rows), "raw": list(raw_rows)}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Autonomous recovery token comparison",
        "",
        "Actual API usage from the complete post-policy LLM recovery loop. Unlike Step 24, this includes diagnosis, tool schemas/results, validation, planning, and regenerated content.",
        "",
        "| lines/doc | mode | repeats | success | regenerated docs | prompt mean | completion mean | total mean | AgentTX total saved | saved (%) | recovery p95 (s) |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary_rows:
        lines.append(
            f"| {row['document_lines']} | {row['mode']} | {row['repeats']} | "
            f"{row['success_rate']:.3f} | {row['regenerated_documents_mean']:.2f} | "
            f"{row['prompt_tokens_mean']:.1f} | {row['completion_tokens_mean']:.1f} | "
            f"{row['total_tokens_mean']:.1f} | {row['agenttx_total_tokens_saved']:.1f} | "
            f"{100 * row['agenttx_total_tokens_saved_pct']:.1f}% | "
            f"{row['recovery_wall_s_p95']:.3f} |"
        )
    lines += [
        "",
        "Token saving is the coarse policy's full recovery-loop usage minus AgentTX causal recovery usage. Pre-failure tokens remain sunk cost and are not relabeled as saved.",
    ]
    (output / "token_end_to_end.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--modes", nargs="+", choices=list(MODES), default=list(MODES))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--document-lines", nargs="+", type=int, default=[12, 24, 48])
    parser.add_argument("--model", default=None)
    parser.add_argument("--provider", choices=provider_names(), default=None)
    parser.add_argument("--max-turns", type=int, default=20)
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="check strace/try/overlay support without contacting an API",
    )
    parser.add_argument(
        "--allow-skip",
        action="store_true",
        help="return success when credentials are absent (optional CI only)",
    )
    args = parser.parse_args()
    if args.repeats <= 0 or min(args.document_lines) <= 0 or args.max_turns <= 0:
        parser.error("repeats, document-lines, and max-turns must be positive")

    load_llm_env()
    preflight = runtime_preflight(ROOT)
    print(format_preflight(preflight), file=sys.stderr)
    if args.preflight_only:
        return 0 if preflight["ok"] else 2
    if not preflight["ok"]:
        print("blocked: AgentTX substrate preflight failed", file=sys.stderr)
        return 2
    if not configured_provider(args.provider):
        profile = resolve_provider(args.provider)
        message = "skip" if args.allow_skip else "blocked"
        print(f"{message}: no {profile.name.upper()}_API_KEY", file=sys.stderr)
        return 0 if args.allow_skip else 2

    rows: List[dict] = []
    for document_lines in args.document_lines:
        for mode in args.modes:
            for repeat in range(args.repeats):
                row = run_once(mode, repeat, args.model, args.max_turns, args.provider, document_lines)
                rows.append(row)
                print(json.dumps(row, indent=2), flush=True)
    summary_rows = summarize(rows)
    write_outputs(rows, summary_rows, args.provider)
    return 0 if rows and all(row["success"] for row in rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
