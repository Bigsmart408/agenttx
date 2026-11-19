#!/usr/bin/env python3
"""Compare file-report recovery policies before any model continuation.

Runs each policy in a disposable fork of the same session. The built-in
demo also scores a workflow reference defined independently of captured
dependencies. Our method receives a file report; controls receive exact
step hints. No model calls, candidate search, or marker acceptance oracle.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'src'), str(ROOT)]

from agenttx.conversation import record_tool_record
from agenttx.file_recovery import (
    POLICIES, FileRecoveryAudit, apply_file_recovery_plan, select_step_hint_targets,
)
from agenttx.runtime import AgentTX


def compare(tx, paths, checkpoint_step, policies, *, fault_step_ids=None,
            affected_step_ids=None, reference=None):
    reference_plan = tx.plan_file_recovery(paths, checkpoint_step=checkpoint_step)
    rows = []
    for policy in policies:
        if policy == 'causal':
            plan = reference_plan
            policy_input = dict(kind='file', user_report=plan.user_message(tx.workspace))
        else:
            targets = select_step_hint_targets(
                tx, fault_step_ids or (), policy=policy, affected_step_ids=affected_step_ids)
            plan = replace(reference_plan, policy=policy, rollback_targets=tuple(targets))
            policy_input = dict(kind='step', fault_step_ids=list(fault_step_ids or ()))
            if policy in {'turn_checkpoint', 'ideal_oracle'}:
                policy_input['affected_step_ids'] = list(affected_step_ids or ())
        audit = FileRecoveryAudit.capture(tx, plan)
        with tx.fork() as branch:
            error = ''
            try:
                apply_file_recovery_plan(branch, plan)
            except (OSError, ValueError) as exc:
                error = f'{type(exc).__name__}: {exc}'
            checked = audit.check(branch)
            row = dict(policy=policy, policy_input=policy_input,
                       plan=asdict(plan), audit=checked, error=error)
            if reference is not None:
                # Workflow labels score the outcome independently of captured
                # dependencies. They never enter our file-report planner.
                active = {s.step_id for s in branch.ledger.steps if s.status != 'rolled_back'}
                residual = sorted(set(reference['affected_commands']) & active)
                command = (
                    "test \"$(cat config.txt)\" = reference && "
                    "test ! -e derived.txt && test ! -e copied.txt && test ! -e final.txt"
                )
                verified = branch.run_tool('offline-reference-state', ['bash', '-c', command],
                                           trace_reads=False)
                row['independent_reference'] = dict(
                    residual_step_ids=residual, files_match=verified.returncode == 0,
                    valid=not residual and verified.returncode == 0,
                    expected_affected_step_ids=reference['affected_commands'],
                )
            rows.append(row)
    return dict(protocol='agenttx.file-report-step-controls/v1', model_calls=0, candidate_probe_count=0,
                user_report=reference_plan.user_message(tx.workspace),
                checkpoint_step=checkpoint_step, results=rows)


def demo(policies):
    with tempfile.TemporaryDirectory(prefix='agenttx-file-report-demo-') as folder:
        root = Path(folder)
        workspace = root/'workspace'
        workspace.mkdir()
        (workspace/'config.txt').write_text('reference\n')
        with AgentTX.begin(workspace, session_dir=root/'session', trace_backend='strace') as tx:
            tx.conversation.seed('Use project tools.', 'Finish the project.')
            def run(command):
                record = tx.run_tool('run_shell', ['bash', '-c', command])
                if record.returncode:
                    raise RuntimeError(record.stderr)
                record_tool_record(tx.conversation, record, {'cmd': command})
                tx._persist()
                return record.step_id
            checkpoint = -1
            run("printf 'before\n' > before.txt")
            source = run("printf 'wrong\n' > config.txt; printf 'file_result_reference_123456789'")
            run("printf 'independent\n' > keep.txt")
            derived = run('cat config.txt > derived.txt')
            copied = run("printf 'file_result_reference_123456789' > copied.txt")
            final = run('cat copied.txt > final.txt')
            return compare(tx, ['config.txt'], checkpoint, policies,
                           fault_step_ids=[source], affected_step_ids=[source, derived, copied, final],
                           reference={'affected_commands': [source, derived, copied, final]})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path)
    parser.add_argument('--files', nargs='+')
    parser.add_argument('--checkpoint-step', type=int, default=None)
    parser.add_argument('--fault-steps', type=int, nargs='+', help='explicit step hints for control policies')
    parser.add_argument('--affected-steps', type=int, nargs='+', help='registered invalid steps for turn checkpoints')
    parser.add_argument('--policies', nargs='+', choices=POLICIES, default=list(POLICIES))
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.demo:
        if args.session or args.files or args.fault_steps or args.affected_steps:
            parser.error('--demo cannot be combined with an existing session/file report')
        result = demo(args.policies)
    else:
        if not args.session or not args.files:
            parser.error('provide --session and --files, or use --demo')
        if any(p != 'causal' for p in args.policies) and not args.fault_steps:
            parser.error('control policies require --fault-steps; only causal receives a file report')
        if 'turn_checkpoint' in args.policies and not args.affected_steps:
            parser.error('turn_checkpoint requires --affected-steps, as in the registered baseline')
        tx = AgentTX.load(args.session)
        try:
            checkpoint = tx.ledger.committed_frontier if args.checkpoint_step is None else args.checkpoint_step
            result = compare(tx, args.files, checkpoint, args.policies,
                             fault_step_ids=args.fault_steps, affected_step_ids=args.affected_steps)
        finally:
            tx.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(dict(output=str(args.output), results=[dict(
        policy=r['policy'], valid=r['audit']['valid'],
        residual_commands=len(r['audit']['residual_step_ids']),
        independent_steps_discarded=len(r['audit']['independent_steps_discarded']),
    ) for r in result['results']]), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
