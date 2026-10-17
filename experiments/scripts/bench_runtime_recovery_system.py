#!/usr/bin/env python3
"""Seven-policy protocol validation on canonical artifact DAGs.

Preserves the canonical concatenate-parents-then-own-payload semantics. New
outputs and registrations are required; this does not relabel diagnostic runs
or fill the frozen formal cohort. A common wall budget and no count cap are
used by both harnesses. Formal token-budget matching remains a separate gate.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.metadata
import json
import os
import shlex
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
from experiments.scripts.experiment_credentials import private_values, redact_credentials, purge_private_codex_auth

PROTOCOL = "agenttx.runtime-canonical-seven/v1"
POLICIES = ("clean_recovery", "crash_no_rollback", "turn_checkpoint",
            "temporal_checkpoint", "whole_branch_abort", "causal", "ideal_oracle")


def workload_spec(plan):
    return [dict(path=s.relative_path, parents=list(s.parent_indices),
                 turn=s.turn_index, payload=f"artifact-{s.index:03d}-{s.role}\n")
            for s in plan.steps]


def expected_artifacts(spec):
    expected = []
    for step in spec:
        expected.append("".join(expected[i] for i in step["parents"]) + step["payload"])
    return expected


def render_workload(spec):
    return '''import json, os, sys
from pathlib import Path
SPEC = ''' + repr(spec) + '''
def expected():
    result = []
    for s in SPEC:
        result.append(''.join(result[i] for i in s['parents']) + s['payload'])
    return result
def run(i):
    s = SPEC[i]
    body = ''.join(Path(SPEC[p]['path']).read_text() for p in s['parents'])
    body += os.environ.get('AGENTTX_INJECT_PAYLOAD', s['payload'])
    p = Path(s['path'])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
mode = sys.argv[1]
if mode == 'run' and len(sys.argv) == 3:
    run(int(sys.argv[2]))
elif mode in ('check', 'check-present', 'check-turn', 'check-step'):
    invalid = []
    for i, (s, want) in enumerate(zip(SPEC, expected())):
        if mode == 'check-turn' and s['turn'] != int(sys.argv[2]): continue
        if mode == 'check-step' and i != int(sys.argv[2]): continue
        p = Path(s['path'])
        if not p.exists() and mode == 'check-present': continue
        if not p.is_file() or p.read_text() != want: invalid.append(i)
    # The same black-box contract is available to every policy. No role/payload
    # or injector truth is returned, and contract checking never writes data.
    print(json.dumps({'invalid': invalid}))
    sys.exit(bool(invalid))
else:
    raise SystemExit('usage: workload.py run INDEX | check | check-present | check-turn T | check-step I')
'''


def materialize(tx, plan, policy):
    records = []
    for step in plan.steps:
        # Stop at identical scheduled boundaries in clean and faulty arms.
        # The next worker sees this ephemeral injector-only environment; neither
        # argv nor the recorded tool observation discloses the faulty index.
        boundary = step.index in plan.fault_roots
        if boundary:
            tx.pool._stop_worker()
        try:
            if boundary and policy != "clean_recovery":
                os.environ["AGENTTX_INJECT_PAYLOAD"] = "prior-version-value\n"
            record = tx.run_tool("run_shell", [sys.executable, "workload.py", "run", str(step.index)],
                                 trace_reads=True, timeout_s=30)
        finally:
            if boundary:
                tx.pool._stop_worker()
                os.environ.pop("AGENTTX_INJECT_PAYLOAD", None)
        if record.returncode:
            raise RuntimeError(f"materialize {step.index}: {record.stderr}")
        records.append(record)
    return records


def apply_coarse_policy(tx, plan, policy):
    """Use executable public contracts, never generator root/closure fields."""
    probes = []
    def fails(mode, index):
        record = tx.run_verifier([sys.executable, "workload.py", mode, str(index)],
                                 trace_reads=True, timeout_s=30)
        probes.append(dict(mode=mode, index=index, step_id=record.step_id,
                           returncode=record.returncode))
        if record.returncode not in (0, 1):
            raise RuntimeError("checkpoint contract infrastructure failure")
        return record.returncode == 1
    if policy == "whole_branch_abort":
        active = tx.ledger.active_uncommitted()
        targets = tx.rollback(active[0].step_id) if active else []
    elif policy == "temporal_checkpoint":
        boundary = next((s.index for s in plan.steps if fails("check-step", s.index)), None)
        targets = tx.rollback(boundary) if boundary is not None else []
    elif policy == "turn_checkpoint":
        failed_turns = {t for t in plan.turn_ids if fails("check-turn", t)}
        # append_history creates exactly one effect turn per manifest turn.
        turns = [t for t in tx.conversation.turns if t.kind == "effect"]
        targets = tx.rollback_turns([t.turn_id for label, t in zip(plan.turn_ids, turns)
                                    if label in failed_turns])
    else:
        raise ValueError(policy)
    return targets, probes


def audit_commands(events, retained_ids):
    violations, commands, replayed = [], [], []
    seen = set()
    for event in events:
        item = event.get("item") or {}
        if event.get("type") != "item.completed":
            continue
        if item.get("type") in {"file_change", "mcp_tool_call", "web_search"}:
            violations.append(dict(reason="tool bypasses the public workload interface", item=item))
        if item.get("type") != "command_execution":
            continue
        key = item.get("id")
        if key is not None and key in seen:
            continue
        seen.add(key)
        raw = item.get("command", "")
        commands.append(raw)
        try:
            words = shlex.split(raw) if isinstance(raw, str) else list(raw)
            if len(words) == 3 and Path(words[0]).name in {"bash", "sh"} and words[1] in {"-lc", "-c"}:
                words = shlex.split(words[2])
            if words == ["submit"]:
                continue
            if len(words) < 3 or not Path(words[0]).name.startswith("python") or words[1] != "workload.py":
                raise ValueError("only public workload commands are allowed")
            mode = words[2]
            if mode in {"check", "check-present"} and len(words) == 3:
                continue
            if mode not in {"run", "check-step", "check-turn"} or len(words) != 4 or not words[3].isdigit():
                raise ValueError("one operation or contract check per command is required")
            if mode == "run":
                index = int(words[3]); replayed.append(index)
                if index in retained_ids:
                    raise ValueError("rewrote a retained valid artifact")
        except (ValueError, TypeError) as exc:
            violations.append(dict(command=raw, reason=str(exc)))
    if not commands:
        violations.append(dict(reason="no auditable completed shell events"))
    return dict(commands=commands, replayed_ids=replayed, violations=violations, passed=not violations)


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
            "Use only these public workload commands (check-step INDEX and check-turn TURN are also allowed). "
            "Run exactly one command per tool call. Do not read/edit workload.py or inspect/write artifacts directly. "
            "Do not use loops, chained commands, or batch operations. Keep workload.py unchanged. "
            "Stop when the original workload passes; SWE-agent should then submit.")
    started = time.perf_counter()
    row = dict(protocol=PROTOCOL, protocol_validation_only=True, formal_eligible=False, trace_id=plan.trace_id,
               manifest_hash=plan.manifest_hash, policy=policy, harness=args.harness,
               runtime_only=args.runtime_only, model=args.model, reasoning_effort="max",
               trace_backend="strace", explicit_extra_reads=False, timings={})
    h = None
    try:
        h = CodingAgentHarness(workspace, session_dir=output / "session",
                               trace_backend="strace", ephemeral_roots=(Path("/root"),))
        tx = h.tx
        tx.conversation.seed("Complete the original artifact workload using shell tools.", task)
        records = materialize(tx, plan, policy)
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
                raise RuntimeError("causal localization exhausted its registered probe budget")
            targets = tx.rollback_causal_many(recovery.root_step_ids)
        elif policy in {"turn_checkpoint", "temporal_checkpoint", "whole_branch_abort"}:
            targets, probes = apply_coarse_policy(tx, plan, policy)
            row["checkpoint_probes"] = probes
            row["boundary_source"] = "public_contract_checks" if probes else "branch_start"
        elif policy == "ideal_oracle":
            targets = tx.rollback_exact(sorted(truth))
        row["timings"]["policy_wall_s"] = time.perf_counter() - stamp
        removed = {i for i in targets if i < len(spec)}
        after = snapshot(tx, spec)
        expected_active = set(range(len(spec))) - removed
        row.update(rollback_ids=targets, invalid_retained=sorted(truth - removed),
                   useful_discarded=sorted(removed - truth),
                   conversation_matches_rollback=set(tx.conversation.active_step_ids()).intersection(range(len(spec))) == expected_active,
                   retained_bytes_unchanged=all(after.get(i) == before.get(i) for i in expected_active))
        if not row["conversation_matches_rollback"] or not row["retained_bytes_unchanged"]:
            raise RuntimeError("rollback workspace/context consistency gate failed")
        (output / "precrash-conversation.json").write_text(json.dumps(precrash, indent=2))
        (output / "recovered-conversation.json").write_text(json.dumps(tx.conversation.to_dict(), indent=2))
        tx.dump_ledger(output / "recovered-ledger.json")
        if not args.runtime_only:
            import tiktoken
            encoding = tiktoken.get_encoding("o200k_base")
            stamp = time.perf_counter()
            base_prompt = task + "\nAn interruption occurred. Continue from the current workspace and active history below.\n"
            policy_payload = json.dumps(tx.conversation.active_messages(), ensure_ascii=False)
            prompt = base_prompt + policy_payload
            row["handoff_token_accounting"] = dict(encoding="o200k_base", estimated=True,
                base_prompt_tokens=len(encoding.encode(base_prompt)),
                policy_payload_tokens=len(encoding.encode(policy_payload)),
                note="Billed model usage below includes harness templates and replay; these counts describe the handoff only.")
            (output / "prompt.txt").write_text(prompt)
            if args.harness == "sweagent":
                result = adapter.run_in_transaction(h, task, native_context=dict(
                    original_task=task, precrash_conversation=precrash, context_variant="full"))
            else:
                result = adapter.run_in_transaction(h, prompt)
            row["timings"]["model_wall_s"] = time.perf_counter() - stamp
            (output / "model-result.json").write_text(json.dumps(dataclasses.asdict(result), indent=2))
            audit = audit_commands(result.events, expected_active - truth)
            (output / "command-audit.json").write_text(json.dumps(audit, indent=2))
            row["command_audit_passed"] = audit["passed"]
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
            row["completed"] = result.returncode == 0 and row["verifier_passed"] and audit["passed"]
        else:
            row["completed"] = True
        row["status"] = "passed" if row["completed"] else "model_or_verification_failed"
    except Exception as exc:
        row.update(status="error", completed=False, error=f"{type(exc).__name__}: {exc}")
        (output / "error.txt").write_text(traceback.format_exc())
    finally:
        row["timings"]["full_wall_s"] = time.perf_counter() - started
        if h is not None:
            row["runtime_tool_calls"] = len(h.tx.history)
            secrets = private_values()
            row["runtime_records"] = redact_credentials([dataclasses.asdict(r) for r in h.tx.history], secrets)
            h.close(destroy=False)
            removed = purge_private_codex_auth(output, secrets)
            if removed:
                (output / "private-auth-cleanup.json").write_text(json.dumps(removed, indent=2))
        (output / "result.json").write_text(json.dumps(row, indent=2) + "\n")
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-id", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--harness", choices=("codex", "sweagent"), default="codex")
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--policies", nargs="+", choices=POLICIES, default=list(POLICIES))
    parser.add_argument("--profile-dir", type=Path, required=True)
    parser.add_argument("--swe-agent-root", type=Path)
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--max-probes", type=int, default=24)
    parser.add_argument("--runtime-only", action="store_true")
    args = parser.parse_args()
    if len(args.policies) != len(set(args.policies)):
        parser.error("duplicate policies are not allowed")
    if args.output_dir.exists():
        parser.error("use a new output directory; protocol validation runs cannot resume")
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
                max_steps=None, max_model_calls=0, timeout_s=args.timeout)
        else:
            adapter = CodexHarness(model=args.model, reasoning_effort="max", timeout_s=args.timeout)
    registration = dict(protocol=PROTOCOL, protocol_validation_only=True, formal_eligible=False, source_sha256=sources["sha256"],
                        args={k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()})
    registration["python"] = sys.version
    registration["count_limit"] = None
    registration["token_budget"] = None
    registration["formal_remaining_gate"] = "shared token-budget enforcement and token-matched policy payload accounting"
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
