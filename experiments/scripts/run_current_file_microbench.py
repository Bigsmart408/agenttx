#!/usr/bin/env python3
"""Current-code AgentTX file-report recovery on the canonical 400 fixed DAGs.

Only the file paths enter recover_file(). Generator labels and canonical bytes
score the result afterwards, independently of the captured dependency graph.
The historical strategy-only runner supplies isolated containers and scheduling.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from experiments.scripts import run_rq1_strategy_only_20261110 as scheduler
from experiments.scripts.experiment_provenance import source_manifest

SCRIPT = 'experiments/scripts/run_current_file_microbench.py'
PROTOCOL = 'agenttx.current-file-microbench/v1'
scheduler.SCRIPT = SCRIPT


def prepare(args):
    cohort = args.cohort.resolve()
    plans = scheduler.cases()
    old = scheduler.read(scheduler.REFERENCE / 'registration.json')
    cohort.mkdir(parents=True, exist_ok=False, mode=0o700)
    source = ROOT.parent / 'agenttx-executions' / cohort.name / 'project'
    source.mkdir(parents=True, exist_ok=False)
    paths = {row['path'] for row in source_manifest(ROOT)['files']}
    paths.update(str(p.relative_to(ROOT)) for p in (ROOT / 'src').rglob('*')
                 if p.is_file() and p.suffix in {'.c', '.so'})
    from experiments.scripts.bench_external_recovery_artifacts import MOTIVATION_SOURCES
    paths.update(str(p.relative_to(ROOT)) for p in MOTIVATION_SOURCES.values())
    frozen = []
    for relative in sorted(paths):
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
        frozen.append(dict(path=relative, sha256=scheduler.digest(target)))
    source_hash = hashlib.sha256(json.dumps(frozen, sort_keys=True).encode()).hexdigest()
    scheduler.save(cohort / 'source-manifest.json', dict(sha256=source_hash, files=frozen))
    image = subprocess.check_output(scheduler.DOCKER + [
        'image', 'inspect', old['image_id'], '--format', '{{.Id}}'], text=True).strip()
    assert image == old['image_id']

    def cell(case, repeat, phase):
        return dict(key=f'{case.plan.trace_id}/causal/repeat-{repeat:02d}',
                    trace_id=case.plan.trace_id, manifest_hash=case.plan.manifest_hash,
                    family=case.suite_slice, operation_count=len(case.plan.steps),
                    policy='causal', repeat=repeat, harness='scripted', phase=phase, state='pending')

    gate = [next(c for c in plans if c.suite_slice == family) for family in
            ('mb2_task_length', 'mb3_fault_position', 'mb4_dependency')]
    gate.append(plans[-1])
    stages = dict(runtime_gate=[cell(c, 0, 'runtime_gate') for c in gate],
                  main=[cell(c, r, 'main') for r in range(args.repeats) for c in plans])
    reg = dict(protocol=PROTOCOL, recovery_protocol='agenttx.file-report-step-controls/v1',
               registered_at=scheduler.now(), cohort=str(cohort), frozen_source=str(source),
               source_sha256=source_hash, image_id=image, python=old['python'],
               python_mounts=old['python_mounts'], uid=os.getuid(), gid=os.getgid(),
               policies=['causal'], harnesses=['scripted'], repeats=args.repeats,
               workers=args.workers, manifest_count=400, expected_cells=400 * args.repeats,
               runtime_gate_cells=len(gate), execution_kind='fixed-trajectory-runtime-only',
               model=None, model_calls=0, api_requests=0, network_mode='none',
               native_agents_started=False, model_profiles_mounted=False,
               cell_timeout_s=3600, ordinary_retries=0, previous_results_inherited=False,
               checkpoint_step=-1, recovery_input='problem file paths only',
               scoring='generator closure, canonical artifact bytes, active ledger and context; '
                       'generator labels are never passed to the recovery planner',
               commands_per_repeat=sum(len(c.plan.steps) for c in plans))
    scheduler.save(cohort / 'registration.json', reg)
    scheduler.save(cohort / 'cells.json', stages)
    (cohort / 'logs').mkdir()
    (cohort / 'events.jsonl').touch()
    print(json.dumps(reg), flush=True)


def worker(args):
    from agenttx.harness import CodingAgentHarness
    from experiments.scripts import bench_runtime_recovery_system as workload
    plan = next(c.plan for c in scheduler.cases() if c.plan.trace_id == args.trace_id)
    args.output.mkdir()
    workspace = args.output / 'workspace'
    workspace.mkdir(mode=0o700)
    spec = workload.workload_spec(plan)
    tool = workload.render_workload(spec)
    (workspace / 'workload.py').write_text(tool)
    row = dict(protocol=PROTOCOL, trace_id=plan.trace_id, manifest_hash=plan.manifest_hash,
               policy='causal', harness='scripted', runtime_only=True, model=None,
               model_calls=0, api_requests=0, native_agent_started=False,
               trace_backend='strace', explicit_extra_reads=False,
               candidate_probe_count=0, localization_probe_count=0, timings={})
    started, harness = time.perf_counter(), None
    try:
        harness = CodingAgentHarness(workspace, session_dir=args.output / 'session',
                                     trace_backend='strace', ephemeral_roots=(Path('/root'),))
        tx = harness.tx
        tx.conversation.seed('Execute the fixed artifact workflow.', 'Preserve independent work on recovery.')
        records = workload.materialize(tx, plan, 'causal')
        workload.append_history(tx, plan, records)
        before = workload.snapshot(tx, spec)
        expected = workload.expected_artifacts(spec)
        truth = set(plan.oracle_closure)
        invalid = {i for i, want in enumerate(expected) if before.get(i) != want}
        captured = {r.step_id: list(r.parents) for r in records}
        missing = [(s.index, p) for s in plan.steps for p in s.parent_indices
                   if p not in captured[s.index]]
        row.update(injected_invalid_ids=sorted(invalid), captured_parents=captured,
                   missing_direct_edges=missing)
        if invalid != truth:
            raise RuntimeError('injected bytes differ from the registered workflow closure')
        (args.output / 'precrash-conversation.json').write_text(json.dumps(tx.conversation.to_dict(), indent=2))
        tx.dump_ledger(args.output / 'precrash-ledger.json')
        row['timings']['materialize_wall_s'] = time.perf_counter() - started
        # A simulated user identifies files. Step IDs/closure are evaluator-only.
        files = [plan.steps[i].relative_path for i in plan.fault_roots]
        row['policy_input'] = dict(kind='file', files=files, checkpoint_step=-1)
        stamp = time.perf_counter()
        outcome = tx.recover_file(files, checkpoint_step=-1)
        row['timings']['policy_wall_s'] = time.perf_counter() - stamp
        row['file_recovery'] = outcome.to_dict()
        removed = {s.step_id for s in tx.ledger.steps if s.status == 'rolled_back'}
        active = {s.step_id for s in tx.ledger.steps if s.status != 'rolled_back'}
        visible = set(tx.conversation.active_step_ids())
        valid = set(range(len(spec))) - truth
        after = workload.snapshot(tx, spec)
        # These checks use only the registered DAG truth and actual bytes.
        absent = sorted(i for i in truth if tx._overlay_entry(workspace / spec[i]['path']) is not None)
        changed = sorted(i for i in valid if after.get(i) != expected[i])
        residual = sorted(truth & active)
        context_residual = sorted(truth & visible)
        dangling = [(parent, s.index) for s in plan.steps if s.index in active
                    for parent in s.parent_indices if parent in removed]
        row.update(rollback_ids=sorted(removed), invalid_retained=residual,
                   useful_discarded=sorted(removed - truth),
                   operation_count=len(spec), invalid_count=len(truth), valid_count=len(valid),
                   invalid_removal=len(truth & removed) / len(truth) if truth else 1.,
                   valid_retention=len(valid - removed) / len(valid) if valid else 1.,
                   selection_precision=len(truth & removed) / len(removed) if removed else 0.,
                   recovery_safe=not residual and not context_residual and not absent and not dangling,
                   exact_selective_state=removed == truth,
                   conversation_matches_rollback=visible == active == set(range(len(spec))) - removed,
                   retained_bytes_unchanged=all(after.get(i) == before.get(i) for i in active),
                   independent_reference=dict(residual_step_ids=residual,
                       residual_context_step_ids=context_residual, residual_artifact_ids=absent,
                       retained_artifact_mismatches=changed, dangling_dependency_edges=dangling),
                   completed=outcome.applied and outcome.status == 'recovered')
        checks = dict(runtime_finished=row['completed'], file_audit_valid=outcome.audit['valid'],
                      selection_matches_reference=removed == truth,
                      independent_files_match=not absent and not changed,
                      no_residual_commands=not residual and not context_residual and not dangling,
                      filesystem_context_agree=row['conversation_matches_rollback'],
                      retained_bytes_preserved=row['retained_bytes_unchanged'],
                      captured_required_dependencies=not missing,
                      workload_unchanged=(workspace / 'workload.py').read_text() == tool)
        row.update(policy_checks=checks, policy_contract_passed=all(checks.values()),
                   recovery_complete=row['recovery_safe'], status='passed' if all(checks.values()) else 'failed')
        (args.output / 'recovered-conversation.json').write_text(json.dumps(tx.conversation.to_dict(), indent=2))
        tx.dump_ledger(args.output / 'recovered-ledger.json')
    except Exception as exc:
        row.update(status='error', completed=False, policy_contract_passed=False,
                   recovery_complete=False, error=f'{type(exc).__name__}: {exc}')
        (args.output / 'error.txt').write_text(traceback.format_exc())
    finally:
        row['timings']['full_wall_s'] = time.perf_counter() - started
        if harness is not None:
            harness.close(destroy=False)
        scheduler.save(args.output / 'result.json', row)
        for directory, dirs, names in os.walk(args.output, followlinks=False):
            for p in [Path(directory), *(Path(directory) / n for n in dirs + names)]:
                try:
                    os.chown(p, args.result_uid, args.result_gid, follow_symlinks=False)
                except FileNotFoundError:
                    pass
    print(json.dumps({k: row.get(k) for k in ('trace_id', 'status', 'error', 'policy_checks', 'timings')}), flush=True)
    return 0 if row['policy_contract_passed'] else 1


def summarize(cohort):
    stages = scheduler.read(cohort / 'cells.json')
    groups = defaultdict(list)
    errors = []
    for cell in stages['main']:
        if not cell.get('result'):
            continue
        row = scheduler.read(cell['result'])
        groups[cell['family']].append(row)
        if not row.get('policy_contract_passed'):
            errors.append(dict(key=cell['key'], error=row.get('error'), checks=row.get('policy_checks')))
    summary = dict(protocol=PROTOCOL, generated_at=scheduler.now(),
                   progress=scheduler.read(cohort / 'progress.json'), families={}, errors=errors)
    groups['all'] = [r for group in groups.values() for r in group]
    for family, rows in groups.items():
        times = sorted(r['timings']['policy_wall_s'] for r in rows if 'policy_wall_s' in r['timings'])
        summary['families'][family] = dict(completed=len(rows),
            passed=sum(r['policy_contract_passed'] for r in rows),
            recovery_complete=sum(r['recovery_complete'] for r in rows),
            exact_selection=sum(r.get('exact_selective_state', False) for r in rows),
            invalid_commands=sum(r.get('invalid_count', 0) for r in rows),
            invalid_retained=sum(len(r.get('invalid_retained', [])) for r in rows),
            independent_commands=sum(r.get('valid_count', 0) for r in rows),
            independent_discarded=sum(len(r.get('useful_discarded', [])) for r in rows),
            recovery_median_ms=1000 * statistics.median(times) if times else None,
            recovery_p95_ms=1000 * times[min(len(times) - 1, int(.95 * len(times)))] if times else None)
    scheduler.save(cohort / 'summary.json', summary)
    print(json.dumps(summary, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    for action in ('prepare', 'run', 'summarize'):
        p = sub.add_parser(action)
        p.add_argument('--cohort', required=True, type=Path)
        if action == 'prepare':
            p.add_argument('--workers', type=int, default=16, choices=range(1, 129))
            p.add_argument('--repeats', type=int, default=3, choices=range(1, 11))
        if action == 'run':
            p.add_argument('--workers', type=int, choices=range(1, 129))
    p = sub.add_parser('worker')
    p.add_argument('--trace-id', required=True)
    p.add_argument('--policy', choices=['causal'], required=True)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--result-uid', required=True, type=int)
    p.add_argument('--result-gid', required=True, type=int)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args)
    elif args.action == 'run':
        scheduler.run(args)
        summarize(args.cohort)
    elif args.action == 'summarize':
        summarize(args.cohort)
    else:
        return worker(args)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
