#!/usr/bin/env python3
"""Audit process-state snapshots, filesystem bytes, history, and paired outcomes."""
from collections import Counter
import argparse
import csv
import hashlib
import json
from pathlib import Path


FAMILIES = ('independent', 'read_through', 'cached', 'mixed')
POLICIES = ('retain_all', 'clear_all')
NAMES = dict(independent='Independent state', read_through='Read-through service',
             cached='Cached faulty data', mixed='Mixed process state')


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(cohort):
    reg = read(cohort / 'registration.json')
    assert reg['expected_trials'] == 240 and reg['model_calls'] == reg['model_api_requests'] == 0
    manifest = read(cohort / 'source-manifest.json')
    assert all(sha(Path(reg['frozen_source']) / item['path']) == item['sha256'] for item in manifest['files'])
    paths = sorted((cohort / 'trials').glob('*/*/*/*/result.json'))
    assert len(paths) == 240, f'Only {len(paths)}/240 results exist'
    rows, evidence, keys = [], [], set()
    for path in paths:
        folder = path.parent
        row = read(path)
        family, seed, repeat, policy = (row[k] for k in ('family', 'seed', 'repeat', 'policy'))
        key = family, seed, repeat, policy
        assert key not in keys
        keys.add(key)
        assert family in FAMILIES and seed in range(10) and repeat in range(3) and policy in POLICIES
        assert row['model_calls'] == row['model_api_requests'] == 0
        before, after = read(folder / 'processes-before.json'), read(folder / 'processes-after.json')
        valid = (f'independent-{seed}\n' * 256)
        n = 32 * (seed + 1)
        expected = [hashlib.sha256((valid + str(i)).encode()).hexdigest() for i in range(n)]
        memos = [expected if family != 'cached' else [], expected]
        assert [x['memo'] for x in before] == memos
        assert before[0]['cached'] == (f'faulty-{seed}\n' if family != 'independent' else None)
        assert before[1]['cached'] is None
        kept = policy == 'retain_all'
        assert [x['memo'] for x in after] == (memos if kept else [[], []])
        assert [x['cached'] for x in after] == ([x['cached'] for x in before] if kept else [None, None])
        assert all((a['pid'] == b['pid']) == kept for a, b in zip(before, after))

        ledger = read(folder / 'ledger.json')['steps']
        by_name = {x['tool_name']: x for x in ledger}
        selection = {by_name[x]['step_id'] for x in ('faulty-write', 'derive-from-fault')}
        if family != 'independent':
            selection.add(by_name['consume-application-input']['step_id'])
        assert set(row['rollback_ids']) == selection
        assert {x['step_id'] for x in ledger if x['status'] == 'rolled_back'} == selection
        conversation = read(folder / 'conversation-after.json')
        active = {c['step_id'] for t in conversation['turns'] for c in t['calls'] if c['status'] == 'active'}
        post_query_ids = {by_name[n]['step_id'] for n in ('query-after-recovery', 'query-independent-service')}
        assert active == {x['step_id'] for x in ledger} - selection - post_query_ids

        upper = folder / 'session/upperdir/results' / folder.relative_to(cohort) / 'workspace'
        def view(name):
            entry = upper / name
            if entry.exists():
                return entry.read_text() if entry.is_file() else None
            lower = folder / 'workspace' / name
            return lower.read_text() if lower.is_file() else None

        assert view('fault.txt') == f'correct-{seed}\n'
        assert view('derived.txt') is None
        assert view('independent.txt') == valid
        final_context = read(folder / 'session/agenttx.json')['conversation']
        calls = {c['tool_name']: c for t in final_context['turns'] for c in t['calls']}
        answer = json.loads(calls['query-after-recovery']['result'])
        independent = json.loads(calls['query-independent-service']['result'])
        actual_correct = answer['value'] == view('fault.txt')
        assert actual_correct == (not kept or family in ('independent', 'read_through'))
        assert independent['memo'] == (expected if kept else [])
        assert answer['value'] == row['actual_result']
        assert row['execution_passed'] and all(row['state_checks'].values())
        assert row['subsequent_result_correct'] == actual_correct
        assert row['valid_memory_retention'] == float(kept)
        assert row['fully_synchronized_and_progress_preserved'] == (actual_correct and kept)
        rows.append(dict(family=family, seed=seed, repeat=repeat, policy=policy,
            correct=actual_correct, memory_retention=float(kept),
            strict_sync=actual_correct and kept, valid_items=sum(map(len, memos)),
            process_policy_ms=1000 * row['process_policy_wall_s']))
        evidence.append(dict(path=str(path.relative_to(cohort)), result_sha256=sha(path),
            process_before_sha256=sha(folder / 'processes-before.json'),
            process_after_sha256=sha(folder / 'processes-after.json'),
            context_after_sha256=sha(folder / 'conversation-after.json'),
            ledger_sha256=sha(folder / 'ledger.json')))
    assert len(keys) == 240
    groups = []
    for family in FAMILIES:
        for policy in POLICIES:
            selected = [r for r in rows if r['family'] == family and r['policy'] == policy]
            assert len(selected) == 30
            groups.append(dict(family=family, policy=policy, n=len(selected),
                correct=sum(r['correct'] for r in selected),
                memory_retention=sum(r['memory_retention'] for r in selected) / len(selected),
                strict_sync=sum(r['strict_sync'] for r in selected),
                mean_process_policy_ms=sum(r['process_policy_ms'] for r in selected) / len(selected)))
    return rows, dict(cohort=str(cohort), source_sha256=reg['source_sha256'],
        audited=240, filesystem_context_checks_passed=240, model_calls=0, model_api_requests=0,
        groups=groups, worker_process_probe=read(cohort / 'worker-process-probe.json'), evidence=evidence)


def table(summary):
    groups = {(r['family'], r['policy']): r for r in summary['groups']}
    lines = [r'\begin{table}[!ht]', r'\centering',
        r'\caption{\textbf{Retaining versus clearing application processes.}',
        r'Each family has 30 paired executions. Correct queries agree with the restored file;',
        r'valid memory reports the fraction of independent entries retained.}',
        r'\label{tab:process-state}', r'\footnotesize', r'\setlength{\tabcolsep}{3pt}',
        r'\begin{tabular}{@{}lrrrr@{}}', r'\toprule',
        r'& \multicolumn{2}{c}{Retain all} & \multicolumn{2}{c}{Clear all} \\',
        r'\cmidrule(lr){2-3}\cmidrule(l){4-5}',
        r'Workload & \shortstack{Correct\\queries} & \shortstack{Valid\\memory} & \shortstack{Correct\\queries} & \shortstack{Valid\\memory} \\',
        r'\midrule']
    for f in FAMILIES:
        a, b = groups[f, 'retain_all'], groups[f, 'clear_all']
        lines.append(f"{NAMES[f]} & {a['correct']}/{a['n']} & {100*a['memory_retention']:.0f}\\% & "
                     f"{b['correct']}/{b['n']} & {100*b['memory_retention']:.0f}\\% " + r'\\')
    return '\n'.join(lines + [r'\bottomrule', r'\end{tabular}', r'\end{table}']) + '\n'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cohort', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--table', required=True, type=Path)
    args = p.parse_args()
    rows, summary = audit(args.cohort.resolve())
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'audit.json').write_text(json.dumps(summary, indent=2) + '\n')
    with (args.output / 'runs.csv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    args.table.write_text(table(summary))
    print(json.dumps({k: v for k, v in summary.items() if k != 'evidence'}, indent=2))


if __name__ == '__main__':
    main()
