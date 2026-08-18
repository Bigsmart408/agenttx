#!/usr/bin/env python3
"""Measure real LLM tokens needed after causal versus coarse recovery."""

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


MODES = ("causal", "temporal_checkpoint", "whole_branch_abort")
REPLAY_DOCUMENTS = {
    "causal": [],
    "temporal_checkpoint": ["docs/changelog.md"],
    "whole_branch_abort": ["docs/design.md", "docs/changelog.md"],
}
WRITE_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "Create or overwrite one text file under the workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
}


def _document_valid(path: Path, prefix: str, document_lines: int) -> bool:
    if not path.exists():
        return False
    items = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith(prefix + "-")
    ]
    return len(items) >= document_lines and len(set(items)) >= document_lines


def _apply_policy(agent, mode: str, injected: dict) -> List[int]:
    if mode == "causal":
        return agent.harness.tx.rollback_causal(injected["root_step"])
    if mode == "temporal_checkpoint":
        return agent.harness.tx.rollback(injected["root_step"])
    if mode == "whole_branch_abort":
        return agent.harness.tx.rollback(0)
    raise ValueError(mode)


def _replay_document(agent, path: str, document_lines: int) -> dict:
    """Use real model tool calls to regenerate one lost agent artifact."""

    prefix = "DESIGN" if path.endswith("design.md") else "CHANGE"
    title = "Design invariants" if prefix == "DESIGN" else "Change log"
    prompt = f"""A filesystem rollback lost one previously completed coding-agent
artifact. Regenerate only `{path}` by calling `write_file` exactly once.

The first line must be `# {title}`. Then write exactly {document_lines} distinct
entries beginning `{prefix}-001:` through `{prefix}-{document_lines:03d}:` in order.
Each entry should be one concise sentence recording a distinct verified repository
decision. Do not use a code fence, do not write another path, and do not explain.
"""
    client = agent._client()
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    arguments = {}
    attempts = 0
    for attempts in range(1, 4):
        suffix = ""
        if attempts > 1:
            suffix = (
                "\nA previous attempt failed structural validation. Check that every "
                "required numbered prefix appears exactly once before calling the tool."
            )
        response = client.chat.completions.create(
            model=agent.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are replaying a lost coding-agent output. Use the supplied "
                        "write_file tool and regenerate only the requested artifact."
                    ),
                },
                {"role": "user", "content": prompt + suffix},
            ],
            tools=[WRITE_FILE_TOOL],
            tool_choice="auto",
        )
        usage = getattr(response, "usage", None)
        attempt_prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        attempt_completion = int(getattr(usage, "completion_tokens", 0) or 0)
        attempt_total = int(getattr(usage, "total_tokens", 0) or 0)
        prompt_tokens += attempt_prompt
        completion_tokens += attempt_completion
        total_tokens += attempt_total or attempt_prompt + attempt_completion
        message = response.choices[0].message
        calls = list(message.tool_calls or [])
        if len(calls) != 1 or calls[0].function.name != "write_file":
            continue
        try:
            candidate = json.loads(calls[0].function.arguments or "{}")
        except json.JSONDecodeError:
            continue
        content = str(candidate.get("content", ""))
        labels = [
            line.split(":", 1)[0]
            for line in content.splitlines()
            if line.startswith(prefix + "-") and ":" in line
        ]
        expected_labels = [
            f"{prefix}-{index:03d}" for index in range(1, document_lines + 1)
        ]
        if candidate.get("path") == path and labels == expected_labels:
            arguments = candidate
            break
    else:
        raise RuntimeError("replay model failed structural validation 3 times")

    record = agent.harness.call_tool("write_file", arguments)
    if record.returncode != 0:
        raise RuntimeError(f"write_file failed: {record.stderr[-300:]}")
    return {
        "path": path,
        "attempts": attempts,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens or prompt_tokens + completion_tokens,
    }


def run_once(
    mode: str,
    repeat: int,
    model: Optional[str],
    provider: Optional[str],
    document_lines: int = DOCUMENT_LINES,
    trace_backend: str = "strace",
) -> dict:
    from agenttx.agents.llm_agent import LLMToolAgent

    scratch = Path(
        tempfile.mkdtemp(prefix=f"agenttx-token-{mode}-{repeat}-", dir="/tmp")
    )
    workdir = scratch / "ws"
    workdir.mkdir()
    seed_token_recovery_repo(workdir, document_lines)
    baseline = _tree_snapshot(workdir)
    agent = None
    started = time.perf_counter()
    error = ""
    injected: dict = {}
    rollback_targets: List[int] = []
    committed = False
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    tool_calls = 0
    model_calls = 0
    ledger_steps = 0
    regenerated_documents: List[str] = []
    regeneration_compliant = False
    host_polluted_before_commit = False
    tests_rc: object = ""
    design_valid = False
    changelog_valid = False
    pipeline_restored = False
    derived_removed = False
    replay_usage: List[dict] = []
    try:
        agent = LLMToolAgent(
            workdir=workdir,
            session_dir=scratch / "session",
            model=model,
            provider=provider,
            trace_backend=trace_backend,
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
        rollback_targets = _apply_policy(agent, mode, injected)

        for path in REPLAY_DOCUMENTS[mode]:
            usage = _replay_document(agent, path, document_lines)
            replay_usage.append(usage)
            regenerated_documents.append(path)
            prompt_tokens += int(usage["prompt_tokens"])
            completion_tokens += int(usage["completion_tokens"])
            total_tokens += int(usage["total_tokens"])
            tool_calls += 1
            model_calls += int(usage["attempts"])
        ledger_steps = len(agent.harness.tx.ledger.steps)
        expected = set(REPLAY_DOCUMENTS[mode])
        regeneration_compliant = set(regenerated_documents) == expected

        host_polluted_before_commit = baseline != _tree_snapshot(workdir)
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
        design_valid = _document_valid(
            workdir / "docs" / "design.md", "DESIGN", document_lines
        )
        changelog_valid = _document_valid(
            workdir / "docs" / "changelog.md", "CHANGE", document_lines
        )
        pipeline_restored = (
            workdir / "src" / "pipeline.py"
        ).read_text(encoding="utf-8") == CORRECT_PIPELINE
        derived_removed = not (workdir / "artifacts" / "rendered.txt").exists()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        if agent is not None:
            agent.close(destroy=True)
        _cleanup(scratch)

    root = injected.get("root_step", -1)
    prefix = injected.get("prefix_step", -1)
    independent = injected.get("independent_step", -1)
    derived = injected.get("derived_step", -1)
    test_step = injected.get("test_step", -1)
    target_set = set(rollback_targets)
    policy_targets_correct = bool(
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
    success = bool(
        committed
        and (mode == "causal" or (prompt_tokens > 0 and completion_tokens > 0))
        and policy_targets_correct
        and regeneration_compliant
        and not host_polluted_before_commit
        and tests_rc == 0
        and design_valid
        and changelog_valid
        and pipeline_restored
        and derived_removed
    )
    return {
        "mode": mode,
        "document_lines": document_lines,
        "repeat": repeat,
        "provider": resolve_provider(provider).name,
        "model": model or resolve_provider(provider).model,
        "wall_s": round(time.perf_counter() - started, 6),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "tool_calls": tool_calls,
        "model_calls": model_calls,
        "ledger_steps": ledger_steps,
        "rollback_targets": rollback_targets,
        "policy_targets_correct": policy_targets_correct,
        "regenerated_documents": regenerated_documents,
        "regenerated_document_count": len(regenerated_documents),
        "replay_usage": replay_usage,
        "regeneration_compliant": regeneration_compliant,
        "host_polluted_before_commit": host_polluted_before_commit,
        "tests_rc": tests_rc,
        "success": success,
        "error": error,
    }


def summarize(rows: Sequence[dict]) -> List[dict]:
    groups: Dict[tuple, List[dict]] = defaultdict(list)
    for row in rows:
        groups[(int(row.get("document_lines", DOCUMENT_LINES)), str(row["mode"]))].append(row)
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
                "regenerated_documents_mean": round(
                    sum(int(sample["regenerated_document_count"]) for sample in samples)
                    / len(samples),
                    6,
                ),
                "host_leak_rate": round(
                    sum(bool(sample["host_polluted_before_commit"]) for sample in samples)
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
                "wall_s",
            ):
                values = [
                    float(
                        sample.get(
                            metric,
                            sample.get("tool_calls", 0)
                            if metric == "model_calls"
                            else 0,
                        )
                    )
                    for sample in samples
                ]
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
    raw_fields = list(raw_rows[0]) if raw_rows else []
    with (output / "token_recovery_raw.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=raw_fields, lineterminator="\n")
        writer.writeheader()
        for row in raw_rows:
            serialized = dict(row)
            serialized["rollback_targets"] = json.dumps(row["rollback_targets"])
            serialized["regenerated_documents"] = json.dumps(
                row["regenerated_documents"]
            )
            serialized["replay_usage"] = json.dumps(row["replay_usage"])
            writer.writerow(serialized)
    fields = list(summary_rows[0]) if summary_rows else []
    with (output / "token_recovery.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(summary_rows)
    (output / "token_recovery.json").write_text(
        json.dumps({"summary": list(summary_rows), "raw": list(raw_rows)}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Real-agent replay-token cost after recovery",
        "",
        f"Actual API usage from controlled `{summary_rows[0]['provider'] if summary_rows else 'provider'}` `write_file` replay calls. Common deterministic validation and AgentTX runtime work are excluded, so the metric isolates LLM work that must be regenerated only because a recovery policy discarded an otherwise valid artifact.",
        "",
        "`temporal_checkpoint` is an optimistic immediate pre-fault checkpoint policy; `whole_branch_abort` represents coarse leaf/session abort. These are native recovery-granularity emulations, not executions of external artifacts.",
        "",
        "| lines/doc | mode | repeats | success | regenerated docs | prompt mean | completion mean | total mean | AgentTX total saved | saved (%) |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary_rows:
        lines.append(
            f"| {row['document_lines']} | {row['mode']} | {row['repeats']} | {row['success_rate']:.3f} | "
            f"{row['regenerated_documents_mean']:.2f} | {row['prompt_tokens_mean']:.1f} | "
            f"{row['completion_tokens_mean']:.1f} | {row['total_tokens_mean']:.1f} | "
            f"{row['agenttx_total_tokens_saved']:.1f} | "
            f"{100 * row['agenttx_total_tokens_saved_pct']:.1f}% |"
        )
    lines += [
        "",
        "Token saving means avoided post-recovery replay tokens. Tokens already spent before failure, common validation work, and runtime latency are outside this metric and must be reported separately.",
    ]
    (output / "token_recovery.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--modes", nargs="+", choices=list(MODES), default=list(MODES))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--document-lines", nargs="+", type=int, default=[DOCUMENT_LINES]
    )
    parser.add_argument("--model", default=None)
    parser.add_argument("--provider", choices=provider_names(), default=None)
    parser.add_argument("--trace-backend", choices=("strace", "bpf_persistent"), default="strace")
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
    if args.repeats <= 0 or min(args.document_lines) <= 0:
        parser.error("repeats and document-lines must be positive")
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
                row = run_once(
                    mode,
                    repeat,
                    args.model,
                    args.provider,
                    document_lines,
                    args.trace_backend,
                )
                rows.append(row)
                print(
                    json.dumps({key: value for key, value in row.items() if key != "error"}, indent=2),
                    flush=True,
                )
                if row["error"]:
                    print(f"error: {row['error']}", file=sys.stderr, flush=True)
    summary_rows = summarize(rows)
    write_outputs(rows, summary_rows, args.provider)
    return 0 if rows and all(row["success"] for row in rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
