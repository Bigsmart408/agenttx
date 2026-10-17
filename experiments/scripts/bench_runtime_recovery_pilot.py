#!/usr/bin/env python3
"""Diagnostic runtime/model gate; not the registered full-system benchmark.

Uses registered DAG topology, automatic strace capture, real rollback and model
repair. Digest artifacts bound disk use; the synthetic pre-crash schedule and
fault overrides are recorded exactly. This is a new diagnostic protocol, not a
replacement for the historical artifact/replay cohorts or the 8400 formal cells.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from agenttx.agents.external import CodexHarness
from agenttx.harness import CodingAgentHarness
from experiments.scripts.bench_external_recovery_artifacts import motivation_400_cases
from experiments.scripts.experiment_provenance import source_manifest

PROTOCOL = "agenttx.runtime-dag-diagnostic/v1"
POLICIES = ("clean_recovery", "crash_no_rollback", "causal", "ideal_oracle")


def workload_spec(plan):
    return [dict(path=s.relative_path, parents=list(s.parent_indices),
                 payload=hashlib.sha256(f"{plan.seed}:{s.index}".encode()).hexdigest())
            for s in plan.steps]


def expected_artifacts(spec):
    expected = []
    for step in spec:
        body = step["payload"] + "".join(expected[i] for i in step["parents"])
        expected.append(hashlib.sha256(body.encode()).hexdigest() + "\n")
    return expected


def render_workload(spec):
    # The public tool contains the original workload, never oracle fault IDs.
    return '''import hashlib, json, sys
from pathlib import Path
SPEC = ''' + repr(spec) + '''
def expected():
    result = []
    for s in SPEC:
        body = s['payload'] + ''.join(result[i] for i in s['parents'])
        result.append(hashlib.sha256(body.encode()).hexdigest() + '\\n')
    return result
def run(i, override=None):
    s = SPEC[i]
    body = (override if override is not None else s['payload'])
    body += ''.join(Path(SPEC[p]['path']).read_text() for p in s['parents'])
    p = Path(s['path'])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(hashlib.sha256(body.encode()).hexdigest() + '\\n')
if sys.argv[1] == 'run':
    run(int(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else None)
elif sys.argv[1] in ('check', 'check-present'):
    invalid = []
    for s, want in zip(SPEC, expected()):
        p = Path(s['path'])
        if not p.exists() and sys.argv[1] == 'check-present':
            continue
        if not p.is_file() or p.read_text() != want:
            invalid.append(s['path'])
    print(json.dumps({'invalid': invalid}))
    sys.exit(bool(invalid))
else:
    raise SystemExit('usage: workload.py run INDEX | check | check-present')
'''


def append_history(tx, plan, records):
    for turn in plan.turn_ids:
        selected = [(s, r) for s, r in zip(plan.steps, records) if s.turn_index == turn]
        calls, observations = [], []
        for step, record in selected:
            call_id = f"operation-{step.index}"
            calls.append(dict(id=call_id, type="function", function=dict(
                name="run_shell", arguments=json.dumps({"cmd": record.argv}))))
            observations.append(dict(role="tool", tool_call_id=call_id,
                                     content=json.dumps(dict(returncode=record.returncode,
                                         stdout=record.stdout, parents=record.parents))))
        tx.conversation.append_turn(
            [dict(role="assistant", content=f"Execution turn {turn}", tool_calls=calls),
             *observations],
            step_ids=[r.step_id for _, r in selected],
            tool_names=["run_shell"] * len(selected), kind="effect")
    tx._persist()


def snapshot(tx, spec):
    result = {}
    for index, step in enumerate(spec):
        p = tx._overlay_entry(tx.workspace / step["path"])
        # Artifacts start absent in the lower tree. Whiteouts are not files.
        if p is not None and p.is_file():
            result[index] = p.read_text()
    return result


def run_cell(args, plan, policy, adapter, output):
    output.mkdir()
    workspace = output / "workspace"
    workspace.mkdir(mode=0o700)
    spec = workload_spec(plan)
    expected = expected_artifacts(spec)
    tool = render_workload(spec)
    (workspace / "workload.py").write_text(tool)
    task = (f"Complete all {len(spec)} indexed operations of the original DAG in this directory. "
            f"Use {sys.executable} workload.py run INDEX in topological index order for "
            "operations that need repair, preserving valid retained outputs. Each operation "
            "reads its parent artifacts. Check completion with workload.py check. "
            "Keep workload.py unchanged. Stop when the original workload passes.")
    started = time.perf_counter()
    row = dict(protocol=PROTOCOL, diagnostic_only=True, trace_id=plan.trace_id,
               manifest_hash=plan.manifest_hash, policy=policy, harness=args.harness,
               runtime_only=args.runtime_only, model=args.model, reasoning_effort="max",
               trace_backend="strace", explicit_extra_reads=False, timings={})
    h = None
    try:
        h = CodingAgentHarness(workspace, session_dir=output / "session",
                               trace_backend="strace", ephemeral_roots=(Path("/root"),))
        tx = h.tx
        tx.conversation.seed("Complete the original artifact workload using shell tools.", task)
        records = []
        for step in plan.steps:
            argv = [sys.executable, "workload.py", "run", str(step.index)]
            if policy != "clean_recovery" and step.index in plan.fault_roots:
                # Injector-only override, recorded in the actual command. Descendants
                # become stale by reading the corrupted output, never by direct edits.
                argv.append(hashlib.sha256(f"stale:{plan.seed}:{step.index}".encode()).hexdigest())
            record = tx.run_tool("run_shell", argv, trace_reads=True, timeout_s=30)
            if record.returncode:
                raise RuntimeError(f"materialize {step.index}: {record.stderr}")
            records.append(record)
        append_history(tx, plan, records)
        precrash = tx.conversation.to_dict()
        before = snapshot(tx, spec)
        invalid = {i for i, want in enumerate(expected) if before.get(i) != want}
        truth = set() if policy == "clean_recovery" else set(plan.oracle_closure)
        if invalid != truth:
            raise RuntimeError(f"injected semantic closure mismatch: {sorted(invalid)} != {sorted(truth)}")
        captured = {r.step_id: list(r.parents) for r in records}
        missing_edges = [(s.index, p) for s in plan.steps for p in s.parent_indices
                         if p not in captured[s.index]]
        row.update(injected_invalid_ids=sorted(invalid), captured_parents=captured,
                   missing_direct_edges=missing_edges)
        if missing_edges:
            raise RuntimeError("automatic capture missed original DAG edges")
        row["timings"]["materialize_wall_s"] = time.perf_counter() - started
        stamp = time.perf_counter()
        targets = []
        if policy == "causal":
            failed = tx.run_verifier([sys.executable, "workload.py", "check-present"],
                                     trace_reads=True, timeout_s=30)
            if failed.returncode != 1:
                raise RuntimeError("causal localization requires a failing contract verifier")
            recovery = tx.plan_recovery(failed, max_candidates=None, max_probes=args.max_probes,
                                        allow_fallback=False, verifier_timeout_s=30,
                                        verifier_trace_reads=True)
            row["localization"] = dataclasses.asdict(recovery)
            if recovery.validation != "passed" or recovery.mode != "causal":
                raise RuntimeError("causal localization exhausted its diagnostic budget")
            targets = tx.rollback_causal_many(recovery.root_step_ids)
        elif policy == "ideal_oracle":
            targets = tx.rollback_exact(sorted(truth))
        row["timings"]["policy_wall_s"] = time.perf_counter() - stamp
        removed = {i for i in targets if i < len(spec)}
        after = snapshot(tx, spec)
        expected_active = set(range(len(spec))) - removed
        row.update(rollback_ids=targets, invalid_retained=sorted(truth - removed),
                   useful_discarded=sorted(removed - truth),
                   conversation_matches_rollback=set(tx.conversation.active_step_ids()) == expected_active,
                   retained_bytes_unchanged=all(after.get(i) == before.get(i) for i in expected_active))
        if not row["conversation_matches_rollback"] or not row["retained_bytes_unchanged"]:
            raise RuntimeError("rollback workspace/context consistency gate failed")
        (output / "precrash-conversation.json").write_text(json.dumps(precrash, indent=2))
        (output / "recovered-conversation.json").write_text(json.dumps(tx.conversation.to_dict(), indent=2))
        tx.dump_ledger(output / "recovered-ledger.json")
        if not args.runtime_only:
            stamp = time.perf_counter()
            prompt = (task + "\nA failure interrupted the original workload. Continue from "
                      "the current workspace and active history below.\n" +
                      json.dumps(tx.conversation.active_messages(), ensure_ascii=False))
            (output / "prompt.txt").write_text(prompt)
            if args.harness == "sweagent":
                result = adapter.run_in_transaction(h, task, native_context=dict(
                    original_task=task, precrash_conversation=precrash, context_variant="full"))
            else:
                result = adapter.run_in_transaction(h, prompt)
            row["timings"]["model_wall_s"] = time.perf_counter() - stamp
            (output / "model-result.json").write_text(json.dumps(dataclasses.asdict(result), indent=2))
            row.update(model_returncode=result.returncode, prompt_tokens=result.prompt_tokens,
                       completion_tokens=result.completion_tokens, total_tokens=result.total_tokens,
                       usage_source=result.usage_source, tool_calls=result.tool_calls)
            stamp = time.perf_counter()
            check = tx.run_verifier([sys.executable, "workload.py", "check"], trace_reads=True, timeout_s=30)
            # Independent evaluator reads actual bytes. A modified public checker cannot pass this.
            final = snapshot(tx, spec)
            tool_path = tx._overlay_entry(workspace / "workload.py")
            row["verifier_passed"] = (check.returncode == 0 and
                all(final.get(i) == want for i, want in enumerate(expected)) and
                tool_path is not None and tool_path.read_text() == tool)
            row["timings"]["verification_wall_s"] = time.perf_counter() - stamp
            # Persist ledger/session evidence; avoid committing unrelated tool output.
            tx.dump_ledger(output / "final-ledger.json")
            row["completed"] = result.returncode == 0 and row["verifier_passed"]
        else:
            row["completed"] = True
        row["status"] = "passed" if row["completed"] else "model_or_verification_failed"
    except Exception as exc:
        row.update(status="error", completed=False, error=f"{type(exc).__name__}: {exc}")
        (output / "error.txt").write_text(traceback.format_exc())
    finally:
        row["timings"]["full_wall_s"] = time.perf_counter() - started
        if h is not None:
            h.close(destroy=False)
        (output / "result.json").write_text(json.dumps(row, indent=2) + "\n")
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-id", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--harness", choices=("codex", "sweagent"), default="codex")
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--policies", nargs="+", choices=POLICIES, default=["causal", "ideal_oracle"])
    parser.add_argument("--profile-dir", type=Path, required=True)
    parser.add_argument("--swe-agent-root", type=Path)
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--max-model-calls", type=int, default=8)
    parser.add_argument("--max-probes", type=int, default=24)
    parser.add_argument("--runtime-only", action="store_true")
    args = parser.parse_args()
    if len(args.policies) != len(set(args.policies)):
        parser.error("duplicate policies are not allowed")
    if args.output_dir.exists():
        parser.error("use a new output directory; diagnostic runs cannot resume")
    matches = [c.plan for c in motivation_400_cases() if c.plan.trace_id == args.trace_id]
    if len(matches) != 1:
        parser.error("trace-id must name exactly one registered DAG")
    args.output_dir.mkdir(parents=True, mode=0o700)
    sources = source_manifest(ROOT)
    (args.output_dir / "source-manifest.json").write_text(json.dumps(sources, indent=2))
    os.environ.update(CODEX_CONFIG_FILE=str(args.profile_dir / "config.toml"),
                      CODEX_AUTH_FILE=str(args.profile_dir / "auth.json"), AGENTTX_NO_PROXY="1")
    adapter = None
    if not args.runtime_only:
        if args.harness == "sweagent":
            if args.swe_agent_root is None:
                parser.error("SWE-agent needs --swe-agent-root")
            from experiments.scripts.sweagent_native_workload import SWEAgentNativeWorkloadHarness, load_codex_gpt_profile
            profile = load_codex_gpt_profile(args.profile_dir / "sweagent.toml",
                args.profile_dir / "auth.json", model_override=args.model, reasoning_effort_override="max")
            adapter = SWEAgentNativeWorkloadHarness(output_dir=args.output_dir, profile=profile,
                config_path=args.swe_agent_root / "config/bash_only.yaml", swe_agent_root=args.swe_agent_root,
                max_steps=args.max_model_calls, max_model_calls=args.max_model_calls, timeout_s=args.timeout)
        else:
            adapter = CodexHarness(model=args.model, reasoning_effort="max", timeout_s=args.timeout)
    registration = dict(protocol=PROTOCOL, diagnostic_only=True, source_sha256=sources["sha256"],
                        args={k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()})
    registration["python"] = sys.version
    registration["packages"] = {}
    for package in ("sweagent", "swe-rex", "litellm", "openai", "tiktoken"):
        try:
            registration["packages"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            pass
    if adapter is not None:
        registration["harness_config"] = getattr(adapter, "public_config", {})
        binary = Path(getattr(adapter, "binary", ""))
        if binary.is_file():
            registration["harness_binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
            companion = binary.parent / "codex-code-mode-host"
            if companion.is_file():
                registration["code_mode_host_sha256"] = hashlib.sha256(companion.read_bytes()).hexdigest()
    config_name = "sweagent.toml" if args.harness == "sweagent" else "config.toml"
    registration["profile_config_sha256"] = hashlib.sha256((args.profile_dir / config_name).read_bytes()).hexdigest()
    (args.output_dir / "registration.json").write_text(json.dumps(registration, indent=2))
    rows = []
    for policy in args.policies:
        row = run_cell(args, matches[0], policy, adapter, args.output_dir / policy)
        rows.append(row)
        print(json.dumps({k: row.get(k) for k in ("policy", "status", "model_returncode", "verifier_passed", "error")}), flush=True)
    (args.output_dir / "summary.json").write_text(json.dumps(rows, indent=2))
    return 0 if all(r["completed"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
