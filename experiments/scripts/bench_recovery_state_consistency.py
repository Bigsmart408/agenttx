#!/usr/bin/env python3
"""Live A/B: after causal recovery, does the agent know what survived?

Same overlay rollback, four-plus handoff variants:

  aligned    conversation rewind + REM (knows what was kept vs rolled back)
  rem_fresh  wipe chat; REM-only fresh session (official v1 black-box handoff)
  prose      wipe chat; handwritten "notes are complete" prompt, no hashes
  blind      wipe chat; no state certificate; must explore
  stale      overlay rolled back, conversation not rewound (thinks crash work still exists)

The task is not the official SWE/TB verifier. It is: leave retained notes
untouched, do not recreate derived/fault artifacts, finish. Metrics are
retained reopen/modify, post-recovery tokens, and extra tool calls.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import traceback
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from agenttx.agents.llm_agent import LLMToolAgent
from agenttx.conversation import ConversationLog
from agenttx.providers import load_provider_env, resolve_provider
from experiments.scripts.bench_official_tasks import _apply_policy
from experiments.workloads.recovery_inject import (
    DocSpec,
    RECOVERY_MANIFEST_PATH,
    all_midcrash_docs,
    build_recovery_manifest,
    dag_is_valid,
    inject_recovery_dag,
    missing_independent_docs,
    read_recovery_documents,
    recovery_manifest_json,
    render_recovery_manifest_prompt,
    retained_artifact_access,
)


HANDOFFS = ("aligned", "rem_fresh", "prose", "blind", "stale")
DOCS = (
    DocSpec("recovery_notes/design.md", "design", 8),
    DocSpec("recovery_notes/changelog.md", "change", 8),
)
CONTINUATION = """A crash was recovered. Your only job:

1. Independent notes that already exist under `recovery_notes/` must not be opened, verified, or rewritten.
2. Recreate a note only if it is actually absent.
3. Do not create `recovery_build/derived.txt`.
4. Do not restore `pkg/fault.py`.
5. Call `finish` with a one-sentence summary of what survived and what was rolled back.

If the notes are already complete, call `finish` immediately.
"""


def continuation_prompt(handoff: str, manifest: dict, docs) -> str:
    if handoff in {"aligned", "rem_fresh"}:
        return render_recovery_manifest_prompt(manifest) + "\n\n" + CONTINUATION
    if handoff == "prose":
        lines = "\n".join(
            f"- `{spec.path}` is already complete. Do not open, verify, or rewrite it."
            for spec in docs
        )
        return (
            "The causal recovery policy retained the independent recovery notes. "
            "They are already complete and valid.\n"
            f"{lines}\n\n"
            + CONTINUATION
        )
    return CONTINUATION


def build_manifest(agent, injected, docs, targets):
    document_contents = read_recovery_documents(agent, docs)
    workdir = agent.harness.workdir
    state_paths = {
        str(injected.get("faulty_path") or "").lstrip("./"),
        *[str(path).lstrip("./") for path in injected.get("derived_paths") or ()],
        *[spec.path for spec in docs],
    }
    state_paths.discard("")
    path_exists = {
        path: agent.harness.tx.path_exists(workdir / path) for path in state_paths
    }
    manifest = build_recovery_manifest(
        policy="causal",
        ledger=agent.harness.tx.ledger,
        injected=injected,
        docs=docs,
        document_contents=document_contents,
        workdir=workdir,
        rollback_targets=targets,
        path_exists=path_exists,
    )
    return manifest, path_exists


def apply_handoff(agent, handoff: str, snap: dict) -> None:
    if handoff == "stale":
        agent.harness.tx.conversation = ConversationLog.from_dict(snap)
    elif handoff in {"rem_fresh", "prose", "blind"}:
        agent.harness.tx.conversation = ConversationLog()
    persist = getattr(agent.harness.tx, "_persist", None)
    if callable(persist):
        persist()


def run_cell(out_dir: Path, handoff: str, repeat: int, *, max_turns: int, model: str | None) -> dict:
    workdir = out_dir / "work" / f"{handoff}_r{repeat}" / "ws"
    session = out_dir / "work" / f"{handoff}_r{repeat}" / "sess"
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "README.md").write_text("tiny recovery-state workspace\n", encoding="utf-8")
    crash_docs = all_midcrash_docs(DOCS)
    agent = None
    agent = LLMToolAgent(
        workdir=workdir,
        session_dir=session,
        max_turns=max_turns,
        provider="deepseek",
        model=model,
    )
    # Reopen metrics need read tracing on the live session.
    agent.harness.tx.trace_reads = True
    if agent.harness.tx.pool is not None:
        agent.harness.tx.pool.trace_reads = True
    t0 = time.perf_counter()
    row = {
        "task": "recovery-state-consistency",
        "policy": "causal",
        "handoff": handoff,
        "repeat": repeat,
        "model": agent.model,
        "provider": agent.provider.name,
        "ok": False,
        "error": "",
    }
    try:
        injected = inject_recovery_dag(
            agent,
            docs=DOCS,
            task_name="state-consistency",
            prefix_writes=(),
            faulty_path="pkg/fault.py",
            faulty_content="BROKEN\n",
            derived_cmd="mkdir -p recovery_build && cat pkg/fault.py > recovery_build/derived.txt",
            test_cmd="python -c \"print(open('pkg/fault.py').read()); raise SystemExit(1)\"",
            bind_conversation=True,
        )
        if not dag_is_valid(injected):
            raise RuntimeError(f"invalid inject DAG: {injected}")
        snap = deepcopy(agent.harness.tx.conversation.to_dict())
        targets = _apply_policy(agent, "causal", injected["root_step"])
        apply_handoff(agent, handoff, snap)
        manifest, path_exists = build_manifest(agent, injected, crash_docs, targets)
        if handoff in {"aligned", "rem_fresh"}:
            text = recovery_manifest_json(manifest).rstrip("\n")
            written = agent.harness.call_tool(
                "write_file",
                {"path": RECOVERY_MANIFEST_PATH, "content": text},
            )
            if int(getattr(written, "returncode", 1)) != 0:
                raise RuntimeError("failed to write recovery manifest")
        recovery_first = len(agent.harness.tx.ledger.steps)
        prompt = continuation_prompt(handoff, manifest, crash_docs)
        result = agent.run(prompt, commit=False)
        access = retained_artifact_access(
            agent.harness.tx.ledger.steps,
            first_step=recovery_first,
            last_step=None,
            retained_paths=[spec.path for spec in crash_docs],
            workdir=workdir,
        )
        missing = [
            spec.path
            for spec in missing_independent_docs(workdir, crash_docs, agent=agent)
        ]
        derived_present = bool(
            agent.harness.tx.path_exists(workdir / "recovery_build" / "derived.txt")
        )
        fault_present = bool(agent.harness.tx.path_exists(workdir / "pkg" / "fault.py"))
        post_tools = [
            step.tool_name
            for step in agent.harness.tx.ledger.steps[recovery_first:]
            if getattr(step, "status", "") != "rolled_back"
        ]
        notes_complete = missing == []
        unmodified = access["retained_paths_modified"] == []
        row.update(
            {
                "ok": True,
                "finished": bool(result.finished),
                "summary": result.summary,
                "tool_calls": int(result.tool_calls),
                "prompt_tokens": int(result.prompt_tokens),
                "completion_tokens": int(result.completion_tokens),
                "total_tokens": int(result.total_tokens),
                "recovery_manifest_authoritative": bool(manifest.get("authoritative")),
                "recovery_manifest_state_id": manifest.get("state_id"),
                "retained_paths_reopened": access["retained_paths_reopened"],
                "retained_read_effects": access["retained_read_effects"],
                "retained_paths_modified": access["retained_paths_modified"],
                "missing_docs": missing,
                "derived_present": derived_present,
                "fault_present": fault_present,
                "notes_complete": notes_complete,
                "post_recovery_tools": post_tools,
                "rollback_targets": list(targets),
                "conversation_generation": int(agent.harness.tx.conversation.generation),
                "active_step_ids": agent.harness.tx.conversation.active_step_ids(),
                "consistent": bool(
                    notes_complete and unmodified and not derived_present
                ),
            }
        )
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["traceback"] = traceback.format_exc()
    finally:
        row["wall_s"] = round(time.perf_counter() - t0, 3)
        if agent is not None:
            try:
                agent.close(destroy=True)
            except Exception:
                pass
    return row


def _mean(values):
    values = [float(v) for v in values]
    if not values:
        return None
    return round(statistics.mean(values), 2)


def _median(values):
    values = [float(v) for v in values]
    if not values:
        return None
    return round(statistics.median(values), 2)


def write_summary(out_dir: Path, rows: list[dict]) -> str:
    lines = [
        "# Recovery-state consistency (causal, live)",
        "",
        "Same mid-crash overlay and causal rollback. Handoffs differ only in",
        "whether the agent is told (and shown) what survived.",
        "",
        "| handoff | n | consistent | reopen=0 | median tokens | mean tokens | mean tools | mean reopened paths | notes kept |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    by = {}
    for row in rows:
        by.setdefault(row["handoff"], []).append(row)
    for handoff in HANDOFFS:
        group = by.get(handoff) or []
        if not group:
            continue
        ok = [r for r in group if r.get("ok")]
        n = len(group)
        consistent = sum(1 for r in ok if r.get("consistent"))
        reopen0 = sum(1 for r in ok if r.get("retained_paths_reopened") == [])
        token_values = [r.get("total_tokens") or 0 for r in ok]
        tokens = _mean(token_values)
        median_tokens = _median(token_values)
        tools = _mean([r.get("tool_calls") or 0 for r in ok])
        reopened = _mean([len(r.get("retained_paths_reopened") or []) for r in ok])
        notes = sum(1 for r in ok if r.get("notes_complete"))
        lines.append(
            f"| {handoff} | {n} | {consistent}/{len(ok) or n} | {reopen0}/{len(ok) or n} | "
            f"{median_tokens} | {tokens} | {tools} | {reopened} | {notes}/{len(ok) or n} |"
        )
    lines.extend(["", "## rows", ""])
    for row in rows:
        status = "ok" if row.get("ok") else row.get("error") or "fail"
        lines.append(
            f"- `{row['handoff']}` r{row['repeat']}: {status}; "
            f"tokens={row.get('total_tokens')}; tools={row.get('tool_calls')}; "
            f"reopened={row.get('retained_paths_reopened')}; "
            f"modified={row.get('retained_paths_modified')}; "
            f"missing={row.get('missing_docs')}; finished={row.get('finished')}"
        )
    text = "\n".join(lines) + "\n"
    (out_dir / "summary.md").write_text(text, encoding="utf-8")
    return text


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--handoffs",
        default=",".join(HANDOFFS),
        help="comma-separated subset of " + ",".join(HANDOFFS),
    )
    parser.add_argument(
        "--result-dir",
        default=str(ROOT / "experiments" / "results" / "recovery_state_ab"),
    )
    parser.add_argument("--max-turns", type=int, default=6)
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)

    load_provider_env(ROOT)
    profile = resolve_provider("deepseek", ROOT)
    if not profile.api_key:
        print("no DEEPSEEK_API_KEY; refusing live A/B", file=sys.stderr)
        return 2

    handoffs = tuple(item.strip() for item in args.handoffs.split(",") if item.strip())
    unknown = [item for item in handoffs if item not in HANDOFFS]
    if unknown:
        print(f"unknown handoffs: {unknown}", file=sys.stderr)
        return 2

    out_dir = Path(args.result_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "raw.jsonl"
    rows = []
    print(
        f"live recovery-state A/B: handoffs={handoffs} repeats={args.repeats} "
        f"model={args.model or profile.model} -> {out_dir}",
        flush=True,
    )
    with raw_path.open("w", encoding="utf-8") as handle:
        for repeat in range(args.repeats):
            for handoff in handoffs:
                print(f"... {handoff} r{repeat}", flush=True)
                row = run_cell(
                    out_dir,
                    handoff,
                    repeat,
                    max_turns=args.max_turns,
                    model=args.model,
                )
                rows.append(row)
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                print(
                    f"    ok={row.get('ok')} tokens={row.get('total_tokens')} "
                    f"tools={row.get('tool_calls')} reopened={row.get('retained_paths_reopened')} "
                    f"consistent={row.get('consistent')} err={row.get('error')!r}",
                    flush=True,
                )
    (out_dir / "raw.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    summary = write_summary(out_dir, rows)
    print(summary)
    return 0 if all(row.get("ok") for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
